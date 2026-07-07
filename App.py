#!/usr/bin/env python3
"""
Outlook Attachment Downloader — Desktop App
Uses Outlook COM automation (no password, no IMAP).
Requires: Windows + Outlook desktop installed.
"""

import webview
import json, os, re, hashlib, threading
import pythoncom
import win32com.client
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

DATA_FILE = os.path.join(os.path.expanduser('~'), '.outlook_dl_com.json')
DEFAULT_DL = os.path.join(os.path.expanduser('~'), 'OutlookDownloads')
data_lock = threading.Lock()
DEFAULTS = {'rules': [], 'downloaded': {}, 'last_check': {}}


def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                d = json.load(f)
                for k, v in DEFAULTS.items():
                    d.setdefault(k, v if not isinstance(v, dict) else dict(v))
                return d
        except Exception:
            pass
    return json.loads(json.dumps(DEFAULTS))


def save_data():
    with data_lock:
        try:
            with open(DATA_FILE, 'w', encoding='utf-8') as f:
                json.dump(app_data, f, indent=2, ensure_ascii=False, default=str)
        except Exception as e:
            print(f"Save error: {e}")


app_data = load_data()


# ── COM Helpers ──────────────────────────────────────────

def walk_folders(folder, depth=0):
    result = []
    try:
        result.append({
            'name': folder.Name, 'entry_id': folder.EntryID,
            'path': folder.FolderPath, 'depth': depth,
            'has_children': folder.Folders.Count > 0
        })
    except Exception:
        return result
    try:
        for i in range(1, folder.Folders.Count + 1):
            result.extend(walk_folders(folder.Folders.Item(i), depth + 1))
    except Exception:
        pass
    return result


def list_all_folders():
    pythoncom.CoInitialize()
    try:
        ol = win32com.client.Dispatch("Outlook.Application")
        ns = ol.GetNamespace("MAPI")
        folders = []
        for i in range(1, ns.Stores.Count + 1):
            try:
                folders.extend(walk_folders(ns.Stores.Item(i).GetRootFolder(), 0))
            except Exception as e:
                folders.append({'name': f'[Error: {e}]', 'entry_id': '',
                                'path': '', 'depth': 0, 'has_children': False})
        return folders
    finally:
        pythoncom.CoUninitialize()


def check_outlook():
    pythoncom.CoInitialize()
    try:
        ol = win32com.client.Dispatch("Outlook.Application")
        ns = ol.GetNamespace("MAPI")
        n = ns.Stores.Count
        names = []
        for i in range(1, n + 1):
            try:
                names.append(ns.Stores.Item(i).DisplayName)
            except Exception:
                names.append('?')
        return {'ok': True, 'msg': f'{n} account(s): {", ".join(names)}'}
    except Exception as e:
        return {'ok': False, 'msg': str(e)}
    finally:
        pythoncom.CoUninitialize()


def build_dasl(keywords, mode):
    prop = "urn:schemas:httpmail:subject"
    esc = [k.replace("'", "''") for k in keywords]
    parts = [f'"{prop}" LIKE \'%{k}%\'' for k in esc]
    return (' AND ' if mode == 'all' else ' OR ').join(parts)


def safe_fn(name):
    if not name:
        return 'unnamed'
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip('. ')


def com2dt(cd):
    try:
        if hasattr(cd, 'year'):
            return datetime(cd.year, cd.month, cd.day, cd.hour, cd.minute, cd.second)
        return datetime.fromtimestamp(int(cd))
    except Exception:
        return None


# ── Download Manager ─────────────────────────────────────

class DownloadManager:
    def __init__(self):
        self.running = False
        self.stop_flag = False
        self.progress = []
        self.plock = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=4)

    def get_progress(self):
        with self.plock:
            return list(self.progress)

    def clear_progress(self):
        with self.plock:
            self.progress.clear()

    def _u(self, rid, **kw):
        with self.plock:
            for p in self.progress:
                if p['rule_id'] == rid:
                    p.update(kw, ts=datetime.now().strftime('%H:%M:%S'))
                    return

    def _a(self, item):
        with self.plock:
            self.progress.append(item)

    def stop(self):
        self.stop_flag = True

    def run_rule(self, rule):
        rid, rname = rule['id'], rule['name']
        self._a({'rule_id': rid, 'name': rname, 'status': 'connecting',
                 'msg': 'Accessing Outlook...', 'dl': 0, 'skip': 0, 'err': 0, 'pct': 0, 'ts': ''})
        pythoncom.CoInitialize()
        try:
            try:
                ol = win32com.client.Dispatch("Outlook.Application")
                ns = ol.GetNamespace("MAPI")
            except Exception as e:
                self._u(rid, status='error', msg=f'Outlook unavailable: {e}'); return

            try:
                folder = ns.GetFolderFromID(rule['entry_id'])
            except Exception:
                self._u(rid, status='error', msg='Folder not found — may have been moved. Re-select in rule.'); return

            try:
                items = folder.Items
            except Exception as e:
                self._u(rid, status='error', msg=f'Cannot read folder: {e}'); return

            kws = [k.strip() for k in rule.get('subject_keywords', '').split(',') if k.strip()]
            mode = rule.get('match_mode', 'any')
            if kws:
                try:
                    items = items.Restrict(build_dasl(kws, mode))
                except Exception as e:
                    self._u(rid, status='error', msg=f'Filter error: {e}'); return

            try:
                total = items.Count
            except Exception:
                total = 0

            if total == 0:
                self._u(rid, status='done', msg='No matching emails', pct=100)
                app_data.setdefault('last_check', {})[rid] = datetime.now().isoformat()
                save_data(); return

            try:
                items.Sort("[ReceivedTime]", True)
            except Exception:
                pass

            last = app_data.get('last_check', {}).get(rid)
            since = None
            if last:
                try:
                    since = datetime.fromisoformat(last) - timedelta(hours=1)
                except Exception:
                    pass

            self._u(rid, status='processing',
                    msg=f'Scanning {total} emails since {since.strftime("%Y-%m-%d %H:%M") if since else "beginning"}')

            dest = rule['destination']
            os.makedirs(dest, exist_ok=True)
            rdl = app_data.setdefault('downloaded', {}).setdefault(rid, {})
            dl = sk = er = 0
            proc = 0

            for i in range(1, total + 1):
                if self.stop_flag:
                    self._u(rid, status='stopped', msg='Stopped by user'); return
                try:
                    item = items.Item(i)
                    if item.Class != 43:
                        continue

                    rx = None
                    try:
                        rx = com2dt(item.ReceivedTime)
                        if since and rx and rx < since:
                            break
                    except Exception:
                        pass

                    eid = item.EntryID
                    if eid in rdl:
                        sk += 1; continue

                    adl = 0
                    try:
                        for j in range(1, item.Attachments.Count + 1):
                            if self.stop_flag:
                                self._u(rid, status='stopped', msg='Stopped by user'); return
                            att = item.Attachments.Item(j)
                            fn = safe_fn(att.FileName)
                            fk = hashlib.md5((eid + fn).encode()).hexdigest()[:12]
                            if fk in rdl.get(eid, {}):
                                sk += 1; continue
                            pfx = rx.strftime('%Y%m%d_%H%M%S') if rx else datetime.now().strftime('%Y%m%d_%H%M%S')
                            sp = os.path.join(dest, f'{pfx}_{fn}')
                            c, base, ext = 1, *os.path.splitext(sp)
                            while os.path.exists(sp):
                                sp = f'{base}_{c}{ext}'; c += 1
                            att.SaveAsFile(sp)
                            dl += 1; adl += 1
                            rdl.setdefault(eid, {})[fk] = datetime.now().isoformat()
                    except Exception:
                        er += 1

                    if adl == 0:
                        rdl.setdefault(eid, {})['_c'] = datetime.now().isoformat()

                except Exception:
                    er += 1

                proc += 1
                self._u(rid, status='processing',
                        msg=f'{proc}/{total}  |  DL: {dl}  Skip: {sk}  Err: {er}',
                        dl=dl, skip=sk, err=er, pct=min(int(proc / total * 100), 99))

            app_data.setdefault('downloaded', {})[rid] = rdl
            app_data.setdefault('last_check', {})[rid] = datetime.now().isoformat()
            save_data()
            self._u(rid, status='done', pct=100,
                    msg=f'Done — DL: {dl}  Skip: {sk}  Err: {er}')

        except Exception as e:
            self._u(rid, status='error', msg=str(e))
        finally:
            pythoncom.CoUninitialize()

    def run_all(self, rules):
        if self.running:
            return False
        self.running = True
        self.stop_flag = False
        self.clear_progress()

        def _w():
            try:
                fs = [self.pool.submit(self.run_rule, r) for r in rules]
                for f in as_completed(fs):
                    try:
                        f.result()
                    except Exception as e:
                        self._a({'rule_id': 'sys', 'name': 'System', 'status': 'error',
                                 'msg': str(e), 'dl': 0, 'skip': 0, 'err': 0, 'pct': 0, 'ts': ''})
            finally:
                self.running = False

        threading.Thread(target=_w, daemon=True).start()
        return True


mgr = DownloadManager()


# ── JS API ───────────────────────────────────────────────

class Api:
    def load_data(self):
        d = load_data()
        return {'rules': d.get('rules', []), 'last_check': d.get('last_check', {})}

    def check_outlook(self):
        return check_outlook()

    def get_folders(self):
        try:
            return {'ok': True, 'folders': list_all_folders()}
        except Exception as e:
            return {'ok': False, 'msg': str(e)}

    def save_rules(self, rules):
        app_data['rules'] = rules; save_data(); return {'ok': True}

    def delete_rule(self, rid):
        app_data['rules'] = [r for r in app_data['rules'] if r['id'] != rid]
        app_data.get('downloaded', {}).pop(rid, None)
        app_data.get('last_check', {}).pop(rid, None)
        save_data(); return {'ok': True}

    def run_all(self):
        rules = [r for r in app_data['rules'] if r.get('enabled', True)]
        if not rules: return {'ok': False, 'msg': 'No enabled rules'}
        if mgr.running: return {'ok': False, 'msg': 'Already running'}
        return {'ok': mgr.run_all(rules), 'count': len(rules)}

    def stop_all(self):
        mgr.stop(); return {'ok': True}

    def get_progress(self):
        return {'running': mgr.running, 'items': mgr.get_progress()}

    def pick_folder(self):
        try:
            import tkinter as tk
            from tkinter import filedialog
            root = tk.Tk(); root.withdraw()
            try: root.attributes('-topmost', True)
            except Exception: pass
            p = filedialog.askdirectory(title='Select Download Destination')
            root.destroy(); return p or ''
        except Exception:
            return ''

    def reset_tracking(self, rid):
        app_data.get('downloaded', {}).pop(rid, None)
        app_data.get('last_check', {}).pop(rid, None)
        save_data(); return {'ok': True}

    def get_stats(self):
        t = 0
        for msgs in app_data.get('downloaded', {}).values():
            for files in msgs.values():
                t += sum(1 for k in files if not k.startswith('_'))
        return {'total_files': t, 'total_rules': len(app_data['rules']),
                'enabled': sum(1 for r in app_data['rules'] if r.get('enabled', True))}


if __name__ == '__main__':
    os.makedirs(DEFAULT_DL, exist_ok=True)
    hp = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html')
    if not os.path.exists(hp):
        print(f"ERROR: {hp} not found.\nPlace index.html next to this script.")
        raise SystemExit(1)
    webview.create_window('Outlook Attachment Downloader', hp, js_api=Api(),
                          width=1300, height=840, min_size=(1000, 660), text_select=True)
    print("Launching Outlook Attachment Downloader...")
    webview.start(debug=False)

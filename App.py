#!/usr/bin/env python3
"""
Outlook Attachment Downloader — Local FastAPI Web App
Database: SQLite (energy_data.db) | Tables: settings, rules, tracking, logs
Outlook: via Desktop COM (no password/IMAP required)
"""

from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse
import uvicorn
import sqlite3
import json, os, re, hashlib, threading
import pythoncom
import win32com.client
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# ═══════════════════════════════════════════════════════════
# Database Setup
# ═══════════════════════════════════════════════════════════
DB_PATH = os.path.join(os.path.expanduser('~'), 'energy_data.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    # Settings table
    c.execute('''CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value TEXT)''')
    # Rules table
    c.execute('''CREATE TABLE IF NOT EXISTS rules (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    folder_path TEXT NOT NULL,
                    entry_id TEXT NOT NULL,
                    subject_keywords TEXT DEFAULT '',
                    match_mode TEXT DEFAULT 'any',
                    destination TEXT NOT NULL,
                    enabled INTEGER DEFAULT 1,
                    last_check TEXT)''')
    # Tracking table (prevents re-downloads)
    c.execute('''CREATE TABLE IF NOT EXISTS tracking (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id TEXT NOT NULL,
                    mail_entry_id TEXT NOT NULL,
                    file_hash TEXT NOT NULL,
                    filename TEXT,
                    downloaded_at TEXT,
                    UNIQUE(rule_id, mail_entry_id, file_hash))''')
    # Logs table
    c.execute('''CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rule_id TEXT,
                    level TEXT DEFAULT 'info',
                    message TEXT,
                    timestamp TEXT DEFAULT CURRENT_TIMESTAMP)''')
    
    # Add index for fast tracking lookups
    c.execute('CREATE INDEX IF NOT EXISTS idx_tracking_rule ON tracking(rule_id, mail_entry_id)')
    conn.commit()
    conn.close()

init_db()

# ═══════════════════════════════════════════════════════════
# FastAPI App
# ═══════════════════════════════════════════════════════════
api = FastAPI()

# Global state for progress tracking
progress_state = {"running": False, "items": []}
progress_lock = threading.Lock()
stop_event = threading.Event()
executor = ThreadPoolExecutor(max_workers=4)

# ═══════════════════════════════════════════════════════════
# COM Helpers
# ═══════════════════════════════════════════════════════════
def walk_folders(folder, depth=0):
    result = []
    try:
        result.append({
            'name': folder.Name, 'entry_id': folder.EntryID,
            'path': folder.FolderPath, 'depth': depth,
            'has_children': folder.Folders.Count > 0
        })
    except Exception: return result
    try:
        for i in range(1, folder.Folders.Count + 1):
            result.extend(walk_folders(folder.Folders.Item(i), depth + 1))
    except Exception: pass
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
                folders.append({'name': f'[Error: {e}]', 'entry_id': '', 'path': '', 'depth': 0, 'has_children': False})
        return folders
    finally:
        pythoncom.CoUninitialize()

def check_outlook():
    pythoncom.CoInitialize()
    try:
        ol = win32com.client.Dispatch("Outlook.Application")
        ns = ol.GetNamespace("MAPI")
        n = ns.Stores.Count
        names = [ns.Stores.Item(i).DisplayName for i in range(1, n + 1) if _safe_get_store_name(ns, i)]
        return {'ok': True, 'msg': f'{n} account(s): {", ".join(names)}'}
    except Exception as e:
        return {'ok': False, 'msg': str(e)}
    finally:
        pythoncom.CoUninitialize()

def _safe_get_store_name(ns, i):
    try: return ns.Stores.Item(i).DisplayName
    except: return '?'

def build_dasl(keywords, mode):
    prop = "urn:schemas:httpmail:subject"
    esc = [k.replace("'", "''") for k in keywords]
    parts = [f'"{prop}" LIKE \'%{k}%\'' for k in esc]
    return (' AND ' if mode == 'all' else ' OR ').join(parts)

def safe_fn(name):
    if not name: return 'unnamed'
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).strip('. ')

def com2dt(cd):
    try:
        if hasattr(cd, 'year'): return datetime(cd.year, cd.month, cd.day, cd.hour, cd.minute, cd.second)
        return datetime.fromtimestamp(int(cd))
    except: return None

# ═══════════════════════════════════════════════════════════
# Background Download Logic
# ═══════════════════════════════════════════════════════════
def add_log(rule_id, level, message):
    try:
        conn = get_db()
        conn.execute("INSERT INTO logs (rule_id, level, message) VALUES (?, ?, ?)", 
                     (rule_id, level, message))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"Log error: {e}")

def is_downloaded(rule_id, mail_eid, file_hash):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM tracking WHERE rule_id=? AND mail_entry_id=? AND file_hash=?", 
                (rule_id, mail_eid, file_hash))
    exists = cur.fetchone() is not None
    conn.close()
    return exists

def mark_mail_checked(rule_id, mail_eid):
    # Mark an email as processed even if it had no attachments
    if not is_downloaded(rule_id, mail_eid, '_checked'):
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO tracking (rule_id, mail_entry_id, file_hash, filename, downloaded_at) VALUES (?, ?, ?, NULL, ?)",
                     (rule_id, mail_eid, '_checked', datetime.now().isoformat()))
        conn.commit()
        conn.close()

def record_download(rule_id, mail_eid, file_hash, filename):
    conn = get_db()
    conn.execute("INSERT OR IGNORE INTO tracking (rule_id, mail_entry_id, file_hash, filename, downloaded_at) VALUES (?, ?, ?, ?, ?)",
                 (rule_id, mail_eid, file_hash, filename, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def update_last_check(rule_id):
    conn = get_db()
    conn.execute("UPDATE rules SET last_check=? WHERE id=?", (datetime.now().isoformat(), rule_id))
    conn.commit()
    conn.close()

def process_rule(rule_id):
    pythoncom.CoInitialize()
    conn = get_db()
    rule = conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()
    conn.close()

    if not rule:
        with progress_lock:
            progress_state['items'] = [p for p in progress_state['items'] if p['rule_id'] != rule_id]
        return

    def upd(**kw):
        with progress_lock:
            for p in progress_state['items']:
                if p['rule_id'] == rule_id:
                    p.update(kw, ts=datetime.now().strftime('%H:%M:%S'))
                    return

    with progress_lock:
        progress_state['items'].append({
            'rule_id': rule_id, 'name': rule['name'], 'status': 'connecting',
            'msg': 'Connecting to Outlook...', 'dl': 0, 'skip': 0, 'err': 0, 'pct': 0, 'ts': ''
        })

    add_log(rule_id, 'info', f"Starting rule: {rule['name']}")

    try:
        ol = win32com.client.Dispatch("Outlook.Application")
        ns = ol.GetNamespace("MAPI")
        folder = ns.GetFolderFromID(rule['entry_id'])
        items = folder.Items

        # Subject filter via DASL (fast, server-side)
        kws = [k.strip() for k in (rule['subject_keywords'] or '').split(',') if k.strip()]
        if kws:
            items = items.Restrict(build_dasl(kws, rule['match_mode']))

        total = items.Count
        if total == 0:
            upd(status='done', msg='No matching emails', pct=100)
            update_last_check(rule_id)
            return

        # Sort newest first for fast early-exit
        try: items.Sort("[ReceivedTime]", True)
        except: pass

        # Calculate date cutoff
        last_check_str = rule['last_check']
        since = None
        if last_check_str:
            try: since = datetime.fromisoformat(last_check_str) - timedelta(hours=1)
            except: pass

        upd(status='processing', msg=f'Scanning {total} emails since {since.strftime("%Y-%m-%d %H:%M") if since else "beginning"}')

        dest = rule['destination']
        os.makedirs(dest, exist_ok=True)
        dl = sk = er = proc = 0

        for i in range(1, total + 1):
            if stop_event.is_set():
                upd(status='stopped', msg='Stopped by user')
                add_log(rule_id, 'warn', "Stopped by user.")
                return

            try:
                item = items.Item(i)
                if item.Class != 43: continue # Skip non-mail

                # FAST EXIT: Stop scanning if we hit emails older than last check
                rx = com2dt(item.ReceivedTime)
                if since and rx and rx < since:
                    break

                eid = item.EntryID
                
                # Skip if we already processed this email entirely
                if is_downloaded(rule_id, eid, '_checked'):
                    sk += 1
                    continue

                adl = 0
                try:
                    for j in range(1, item.Attachments.Count + 1):
                        if stop_event.is_set():
                            upd(status='stopped', msg='Stopped by user'); return
                        
                        att = item.Attachments.Item(j)
                        fn = safe_fn(att.FileName)
                        fh = hashlib.md5((eid + fn).encode()).hexdigest()[:12]

                        if is_downloaded(rule_id, eid, fh):
                            sk += 1
                            continue

                        pfx = rx.strftime('%Y%m%d_%H%M%S') if rx else datetime.now().strftime('%Y%m%d_%H%M%S')
                        sp = os.path.join(dest, f'{pfx}_{fn}')
                        c, base, ext = 1, *os.path.splitext(sp)
                        while os.path.exists(sp):
                            sp = f'{base}_{c}{ext}'; c += 1

                        att.SaveAsFile(sp)
                        dl += 1; adl += 1
                        record_download(rule_id, eid, fh, fn)
                except Exception as e:
                    er += 1
                    add_log(rule_id, 'error', f"Attachment error on {eid}: {str(e)}")

                if adl == 0:
                    mark_mail_checked(rule_id, eid)

            except Exception as e:
                er += 1

            proc += 1
            upd(status='processing',
                msg=f'{proc}/{total} | DL: {dl} Skip: {sk} Err: {er}',
                dl=dl, skip=sk, err=er, pct=min(int(proc / total * 100), 99))

        update_last_check(rule_id)
        upd(status='done', pct=100, msg=f'Done — DL: {dl} Skip: {sk} Err: {er}')
        add_log(rule_id, 'info', f"Finished. DL: {dl}, Skipped: {sk}, Errors: {er}")

    except Exception as e:
        upd(status='error', msg=str(e))
        add_log(rule_id, 'error', str(e))
    finally:
        pythoncom.CoUninitialize()

def run_all_rules():
    global progress_state
    with progress_lock:
        progress_state['running'] = True
        progress_state['items'] = []
    
    stop_event.clear()
    
    conn = get_db()
    rules = conn.execute("SELECT * FROM rules WHERE enabled=1").fetchall()
    conn.close()

    if not rules:
        with progress_lock: progress_state['running'] = False
        return

    futures = [executor.submit(process_rule, rule['id']) for rule in rules]
    for f in as_completed(futures):
        try: f.result()
        except Exception as e:
            with progress_lock:
                progress_state['items'].append({'rule_id':'sys','name':'System','status':'error','msg':str(e),'dl':0,'skip':0,'err':0,'pct':0,'ts':''})
    
    with progress_lock: progress_state['running'] = False

# ═══════════════════════════════════════════════════════════
# API Endpoints
# ═══════════════════════════════════════════════════════════
@api.get("/", response_class=HTMLResponse)
async def serve_ui():
    return HTML_CONTENT

@api.get("/api/status")
async def api_status():
    return check_outlook()

@api.get("/api/folders")
async def api_folders():
    try:
        return {"ok": True, "folders": list_all_folders()}
    except Exception as e:
        return {"ok": False, "msg": str(e)}

@api.get("/api/rules")
async def api_get_rules():
    conn = get_db()
    rules = [dict(r) for r in conn.execute("SELECT * FROM rules ORDER BY name").fetchall()]
    conn.close()
    return rules

@api.post("/api/rules")
async def api_save_rule(rule: dict):
    conn = get_db()
    conn.execute('''INSERT OR REPLACE INTO rules (id, name, folder_path, entry_id, subject_keywords, match_mode, destination, enabled, last_check)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                 (rule.get('id'), rule.get('name'), rule.get('folder'), rule.get('entry_id'),
                  rule.get('subject_keywords', ''), rule.get('match_mode', 'any'),
                  rule.get('destination'), 1 if rule.get('enabled') else 0, rule.get('last_check')))
    conn.commit()
    conn.close()
    return {"ok": True}

@api.post("/api/rules/{rule_id}/toggle")
async def api_toggle_rule(rule_id: str):
    conn = get_db()
    conn.execute("UPDATE rules SET enabled = CASE WHEN enabled=1 THEN 0 ELSE 1 END WHERE id=?", (rule_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@api.delete("/api/rules/{rule_id}")
async def api_delete_rule(rule_id: str):
    conn = get_db()
    conn.execute("DELETE FROM tracking WHERE rule_id=?", (rule_id,))
    conn.execute("DELETE FROM rules WHERE id=?", (rule_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@api.post("/api/run")
async def api_run(background_tasks: BackgroundTasks):
    with progress_lock:
        if progress_state['running']: return {"ok": False, "msg": "Already running"}
    background_tasks.add_task(run_all_rules)
    return {"ok": True}

@api.post("/api/stop")
async def api_stop():
    stop_event.set()
    return {"ok": True}

@api.get("/api/progress")
async def api_progress():
    with progress_lock:
        return {"running": progress_state['running'], "items": progress_state['items']}

@api.post("/api/rules/{rule_id}/reset")
async def api_reset(rule_id: str):
    conn = get_db()
    conn.execute("DELETE FROM tracking WHERE rule_id=?", (rule_id,))
    conn.execute("UPDATE rules SET last_check=NULL WHERE id=?", (rule_id,))
    conn.commit()
    conn.close()
    return {"ok": True}

@api.get("/api/stats")
async def api_stats():
    conn = get_db()
    total_files = conn.execute("SELECT COUNT(*) FROM tracking WHERE file_hash != '_checked'").fetchone()[0]
    total_rules = conn.execute("SELECT COUNT(*) FROM rules").fetchone()[0]
    enabled = conn.execute("SELECT COUNT(*) FROM rules WHERE enabled=1").fetchone()[0]
    conn.close()
    return {"total_files": total_files, "total_rules": total_rules, "enabled": enabled}

@api.get("/api/logs")
async def api_logs():
    conn = get_db()
    logs = [dict(r) for r in conn.execute("SELECT * FROM logs ORDER BY id DESC LIMIT 100").fetchall()]
    conn.close()
    return logs


# ═══════════════════════════════════════════════════════════
# Frontend HTML (Vue.js)
# ═══════════════════════════════════════════════════════════
HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Outlook Attachment Downloader</title>
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#0c1017;--sf:#131a24;--cd:#19222f;--cdh:#1e2a3a;--bd:#253040;--bdh:#354560;--tx:#dce6f0;--mt:#6b7f96;--dm:#3a4d62;--ac:#00d68f;--ach:#22ffaa;--acd:rgba(0,214,143,.1);--dn:#ff5c5c;--dnd:rgba(255,92,92,.1);--wn:#f0b429;--wnD:rgba(240,180,41,.1);--in:#4da6ff;--ind:rgba(77,166,255,.1);--r:8px;--rs:5px;--f:'Segoe UI',system-ui,sans-serif;--m:'Cascadia Code',Consolas,monospace}
html,body{height:100%;overflow:hidden;background:var(--bg);color:var(--tx);font-family:var(--f);font-size:13px;line-height:1.5}
#app{display:flex;flex-direction:column;height:100vh}
.hdr{height:52px;min-height:52px;background:var(--sf);border-bottom:1px solid var(--bd);display:flex;align-items:center;padding:0 20px;gap:14px}
.logo{font-size:14px;font-weight:700;color:var(--ac);letter-spacing:-.3px;white-space:nowrap}
.logo span{color:var(--mt);font-weight:400;font-size:12px}
.hdr-sp{flex:1}
.pill{display:inline-flex;align-items:center;gap:4px;padding:2px 9px;border-radius:10px;font-size:10px;font-weight:700;background:var(--acd);color:var(--ac)}
.pill.w{background:var(--wnD);color:var(--wn)}.pill.i{background:var(--ind);color:var(--in)}
.dot{width:7px;height:7px;border-radius:50%;display:inline-block}
.dot.on{background:var(--ac);box-shadow:0 0 6px var(--ac)}.dot.off{background:var(--dn);box-shadow:0 0 6px var(--dn)}
.ol-st{display:flex;align-items:center;gap:6px;font-size:11px;color:var(--mt);padding:4px 10px;border-radius:var(--rs);background:var(--bg);border:1px solid var(--bd)}
.btn{display:inline-flex;align-items:center;gap:5px;padding:6px 14px;border-radius:var(--rs);border:1px solid var(--bd);background:var(--cd);color:var(--tx);font-size:12px;font-weight:600;cursor:pointer;transition:all .15s;white-space:nowrap;font-family:var(--f)}
.btn:hover{border-color:var(--bdh);background:var(--cdh)}.btn:disabled{opacity:.35;cursor:not-allowed;transform:none!important;box-shadow:none!important}
.btn-p{background:var(--ac);color:#000;border-color:var(--ac)}.btn-p:hover{background:var(--ach);border-color:var(--ach);transform:translateY(-1px);box-shadow:0 4px 14px rgba(0,214,143,.25)}
.btn-d{color:var(--dn);border-color:rgba(255,92,92,.3);background:var(--dnd)}.btn-d:hover{background:rgba(255,92,92,.18)}
.btn-sm{padding:4px 10px;font-size:11px}.btn-ic{padding:4px 7px}
.body{display:flex;flex:1;overflow:hidden}
.side{width:300px;min-width:300px;background:var(--sf);border-right:1px solid var(--bd);overflow-y:auto;padding:12px}
.main{flex:1;overflow-y:auto;padding:20px 24px}
.card{background:var(--cd);border:1px solid var(--bd);border-radius:var(--r);padding:13px;margin-bottom:10px}
.card-t{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.8px;color:var(--mt);margin-bottom:10px;display:flex;align-items:center;gap:5px}
.fld{margin-bottom:9px}.fld label{display:block;font-size:10px;font-weight:600;color:var(--mt);margin-bottom:3px}
input[type=text],select,textarea{width:100%;background:var(--bg);border:1px solid var(--bd);border-radius:var(--rs);padding:6px 9px;color:var(--tx);font-size:12px;font-family:var(--f);outline:none;transition:border .15s}
input:focus,select:focus,textarea:focus{border-color:var(--ac)}select{cursor:pointer;appearance:auto}
textarea{resize:vertical;min-height:52px}
.fld-row{display:flex;gap:7px}.fld-row .fld{flex:1}.fld-inl{display:flex;gap:5px;align-items:flex-end}.fld-inl .fld{flex:1;margin-bottom:0}
.tog{position:relative;display:inline-block;width:32px;height:17px;flex-shrink:0}
.tog input{display:none}.tog .tr{position:absolute;inset:0;background:var(--bd);border-radius:9px;cursor:pointer;transition:.2s}
.tog input:checked+.tr{background:var(--ac)}.tog .tr::after{content:'';position:absolute;width:13px;height:13px;border-radius:50%;background:#fff;top:2px;left:2px;transition:.2s}
.tog input:checked+.tr::after{transform:translateX(15px)}
.flist{max-height:340px;overflow-y:auto}
.fi{padding:4px 7px;border-radius:var(--rs);cursor:pointer;display:flex;align-items:center;gap:5px;font-size:11px;color:var(--mt);transition:.1s}
.fi:hover{background:var(--acd);color:var(--tx)}.fi.sel{background:var(--acd);color:var(--ac);font-weight:600}
.rc{border:1px solid var(--bd);border-radius:var(--r);background:var(--cd);margin-bottom:9px;overflow:hidden;transition:border .2s}
.rc:hover{border-color:var(--bdh)}.rc.running{border-color:var(--in);box-shadow:0 0 0 1px var(--ind)}
.rc.error{border-color:var(--dn)}.rc.done{border-color:var(--ac)}.rc.stopped{border-color:var(--wn)}
.rh{display:flex;align-items:center;padding:11px 13px;gap:9px}.rn{flex:1;font-weight:600;font-size:13px}
.rm{padding:0 13px 9px;display:flex;flex-wrap:wrap;gap:11px;font-size:10px;color:var(--mt)}
.rmi{display:flex;align-items:center;gap:3px}.rmi strong{color:var(--tx);font-weight:600}
.pw{padding:0 13px 11px}.pb{height:3px;background:var(--bd);border-radius:2px;overflow:hidden}
.pf{height:100%;background:var(--ac);border-radius:2px;transition:width .4s}
.pf.error{background:var(--dn)}.pf.running{background:linear-gradient(90deg,var(--ac),var(--in));background-size:200% 100%;animation:shim 1.5s infinite}
.pf.connecting,.pf.searching{background:var(--in)}
@keyframes shim{0%{background-position:200% 0}100%{background-position:-200% 0}}
.pm{font-size:10px;color:var(--mt);margin-top:5px;font-family:var(--m)}
.badge{display:inline-flex;align-items:center;gap:3px;padding:1px 7px;border-radius:9px;font-size:9px;font-weight:700;text-transform:uppercase;letter-spacing:.5px}
.badge.done{background:var(--acd);color:var(--ac)}.badge.running{background:var(--ind);color:var(--in)}
.badge.error{background:var(--dnd);color:var(--dn)}.badge.stopped{background:var(--wnD);color:var(--wn)}
.badge.connecting,.badge.searching{background:var(--ind);color:var(--in)}
.mo{position:fixed;inset:0;background:rgba(0,0,0,.6);display:flex;align-items:center;justify-content:center;z-index:100;backdrop-filter:blur(3px);animation:fi .15s}
@keyframes fi{from{opacity:0}to{opacity:1}}
.md{background:var(--cd);border:1px solid var(--bd);border-radius:12px;padding:22px;width:500px;max-height:85vh;overflow-y:auto;animation:su .2s}
@keyframes su{from{opacity:0;transform:translateY(10px) scale(.97)}to{opacity:1;transform:none}}
.md-t{font-size:15px;font-weight:700;margin-bottom:16px}
.md-a{display:flex;justify-content:flex-end;gap:7px;margin-top:18px;padding-top:14px;border-top:1px solid var(--bd)}
.tc{position:fixed;top:12px;right:12px;z-index:200;display:flex;flex-direction:column;gap:7px;pointer-events:none}
.tt{padding:9px 14px;border-radius:var(--r);font-size:11px;font-weight:500;animation:ti .25s;max-width:320px;pointer-events:auto;border:1px solid}
@keyframes ti{from{opacity:0;transform:translateX(16px)}to{opacity:1;transform:none}}
.tt.ok{background:#081f15;border-color:var(--ac);color:var(--ac)}.tt.er{background:#1f0808;border-color:var(--dn);color:var(--dn)}.tt.in{background:#081520;border-color:var(--in);color:var(--in)}
.sp{display:inline-block;width:11px;height:11px;border:2px solid var(--bd);border-top-color:var(--ac);border-radius:50%;animation:rot .6s linear infinite}
@keyframes rot{to{transform:rotate(360deg)}}
.empty{text-align:center;padding:44px 20px;color:var(--dm)}.empty-i{font-size:38px;margin-bottom:8px;opacity:.35}
.empty p{font-size:13px;margin-bottom:3px}.empty .h{font-size:11px}
.sec-h{display:flex;align-items:center;justify-content:space-between;margin-bottom:13px}
.sec-t{font-size:14px;font-weight:700}
.ls{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100vh;gap:14px;background:var(--bg)}
.ls .sp{width:26px;height:26px;border-width:3px}
::-webkit-scrollbar{width:5px}::-webkit-scrollbar-track{background:transparent}::-webkit-scrollbar-thumb{background:var(--bd);border-radius:3px}
.hint{font-size:10px;color:var(--dm);margin-top:3px;line-height:1.4}
</style>
</head>
<body>
<div id="app">
  <div v-if="!ready" class="ls"><div class="sp"></div><span style="color:var(--mt);font-size:12px">Connecting to Server...</span></div>
  <template v-else>
    <header class="hdr">
      <div class="logo">Outlook DL <span>/ Attachment Downloader</span></div>
      <div class="hdr-sp"></div>
      <div class="ol-st"><span class="dot" :class="olOk?'on':'off'"></span>{{ olMsg || 'Checking...' }}</div>
      <span class="pill i">{{ stats.enabled }} active</span>
      <span class="pill">{{ stats.total_files }} files</span>
      <button class="btn btn-d btn-sm" v-if="isRunning" @click="stopAll"><span class="sp" style="border-top-color:var(--dn)"></span> Stop</button>
      <button class="btn btn-p" v-else @click="runAll" :disabled="rules.length===0">&#9654; Run All Rules</button>
    </header>
    <div class="body">
      <aside class="side">
        <div class="card">
          <div class="card-t">&#128172; Outlook Status</div>
          <button class="btn btn-sm" style="width:100%" @click="checkOL" :disabled="olBusy">{{ olBusy ? 'Checking...' : 'Check Connection' }}</button>
          <p class="hint" style="margin-top:6px">Uses your local Outlook desktop app. Ensure Outlook is open and signed in.</p>
        </div>
        <div class="card">
          <div class="card-t">&#128193; Folders</div>
          <button class="btn btn-sm" style="width:100%;margin-bottom:7px" @click="fetchFolders" :disabled="fBusy">{{ fBusy ? 'Loading...' : 'Load All Folders' }}</button>
          <div v-if="folders.length" class="flist">
            <div v-for="f in folders" :key="f.entry_id||f.path"
                 class="fi" :class="{sel: selFolder&&selFolder.entry_id===f.entry_id}"
                 @click="selFolder=f" :style="{'padding-left':(7+f.depth*14)+'px'}">
              {{ f.name }}
            </div>
          </div>
          <div v-else style="font-size:10px;color:var(--dm);text-align:center;padding:10px 0">Click "Load All Folders" to browse</div>
        </div>
        <div class="card" style="background:var(--bg);border-color:var(--bd)">
          <p class="hint"><strong style="color:var(--mt)">How it works:</strong><br>1. Check Outlook connection<br>2. Load & select a folder<br>3. Create a rule with keywords<br>4. Click "Run All Rules"<br><br>Only <strong style="color:var(--ac)">new emails</strong> are scanned each time. Already-downloaded files are skipped via the tracking database.</p>
        </div>
      </aside>
      <main class="main">
        <div class="sec-h">
          <div class="sec-t">Download Rules</div>
          <button class="btn btn-sm" @click="openModal()">+ New Rule</button>
        </div>
        <div v-if="rules.length===0" class="empty">
          <div class="empty-i">&#128230;</div>
          <p>No rules configured yet</p>
          <p class="h">Create a rule to start downloading attachments</p>
        </div>
        <div v-for="rule in rules" :key="rule.id" class="rc" :class="ruleCls(rule.id)">
          <div class="rh">
            <label class="tog"><input type="checkbox" :checked="rule.enabled" @change="toggleRule(rule.id)"><span class="tr"></span></label>
            <span class="rn">{{ rule.name }}</span>
            <span v-if="badge(rule.id)" class="badge" :class="badge(rule.id).c">{{ badge(rule.id).l }}</span>
            <button class="btn btn-sm btn-ic" @click="openModal(rule)" title="Edit">&#9998;</button>
            <button class="btn btn-sm btn-ic btn-d" @click="deleteRule(rule.id)" title="Delete">&#10005;</button>
          </div>
          <div class="rm">
            <span class="rmi">&#128193; <strong>{{ rule.folder_path }}</strong></span>
            <span class="rmi" v-if="rule.subject_keywords">&#128270; {{ rule.subject_keywords }} <strong>({{ rule.match_mode }})</strong></span>
            <span class="rmi">&#128194; {{ shortPath(rule.destination) }}</span>
            <span class="rmi" v-if="rule.last_check">&#128339; {{ fmtDate(rule.last_check) }}</span>
          </div>
          <div v-if="prog(rule.id)" class="pw">
            <div class="pb"><div class="pf" :class="prog(rule.id).status" :style="{width:prog(rule.id).pct+'%'}"></div></div>
            <div class="pm">{{ prog(rule.id).msg }}</div>
          </div>
        </div>
        <div v-if="rules.length" style="margin-top:18px;padding-top:14px;border-top:1px solid var(--bd)">
          <div class="card-t" style="margin-bottom:7px">&#9888; Reset Tracking</div>
          <p class="hint" style="margin-bottom:7px">Clear a rule's tracking history in the database to force a full re-scan.</p>
          <div style="display:flex;flex-wrap:wrap;gap:5px">
            <button v-for="r in rules" :key="'z'+r.id" class="btn btn-sm btn-d" @click="resetTrack(r.id)">{{ r.name }}</button>
          </div>
        </div>
      </main>
    </div>
    <div class="mo" v-if="showMo" @click.self="showMo=false">
      <div class="md">
        <div class="md-t">{{ editRule ? 'Edit Rule' : 'New Rule' }}</div>
        <div class="fld"><label>Rule Name</label><input type="text" v-model="fm.name" placeholder="e.g. Invoice Attachments"></div>
        <div class="fld">
          <label>Folder</label>
          <input type="text" v-model="fm.folder" placeholder="Click a folder in the sidebar">
          <p class="hint">Select a folder from the sidebar to auto-fill</p>
        </div>
        <div class="fld-row">
          <div class="fld"><label>Subject Keywords (comma separated)</label><input type="text" v-model="fm.subject_keywords" placeholder="invoice, receipt">
            <p class="hint">Leave empty to match all emails</p>
          </div>
          <div class="fld" style="max-width:110px"><label>Match Mode</label>
            <select v-model="fm.match_mode"><option value="any">Any word</option><option value="all">All words</option></select>
          </div>
        </div>
        <div class="fld">
          <label>Download Destination (Local Path)</label>
          <input type="text" v-model="fm.destination" placeholder="C:\\Users\\...\\Downloads\\Invoices">
          <p class="hint">Full path to where attachments should be saved</p>
        </div>
        <div class="md-a">
          <button class="btn" @click="showMo=false">Cancel</button>
          <button class="btn btn-p" @click="saveRule" :disabled="!fm.name||!fm.folder||!fm.destination">{{ editRule ? 'Update' : 'Create' }}</button>
        </div>
      </div>
    </div>
    <div class="tc"><div v-for="t in toasts" :key="t.id" class="tt" :class="t.t">{{ t.m }}</div></div>
  </template>
</div>
<script src="https://unpkg.com/vue@3/dist/vue.global.prod.js"></script>
<script>
const{createApp,ref,reactive,onMounted,onUnmounted,watch}=Vue;
createApp({setup(){
  const ready=ref(false),rules=ref([]),folders=ref([]),selFolder=ref(null),
        olBusy=ref(false),fBusy=ref(false),olOk=ref(false),olMsg=ref('Not checked'),
        isRunning=ref(false),progs=ref([]),showMo=ref(false),editRule=ref(null),toasts=ref([]),
        stats=reactive({total_files:0,total_rules:0,enabled:0});
  const fm=reactive({name:'',folder:'',entry_id:'',subject_keywords:'',match_mode:'any',destination:''});
  let poll=null,tid=0;

  const fetch=async(url,o={})=>{const r=await fetch(url,{headers:{'Content-Type':'application/json',...o.headers},...o});return r.json()};
  const post=async(url,body)=>fetch(url,{method:'POST',body:JSON.stringify(body)});
  const del=async(url)=>fetch(url,{method:'DELETE'});

  function toast(m,t='in'){const id=++tid;toasts.value.push({id,m,t});setTimeout(()=>{toasts.value=toasts.value.filter(x=>x.id!==id)},4000)}

  async function checkOL(){olBusy.value=true;try{const r=await fetch('/api/status');olOk.value=r.ok;olMsg.value=r.msg;if(r.ok)toast('Outlook connected','ok')}catch(e){olOk.value=false;olMsg.value='Server error'}olBusy.value=false}
  async function fetchFolders(){fBusy.value=true;try{const r=await fetch('/api/folders');if(r.ok){folders.value=r.folders;toast(r.folders.length+' folders loaded','ok')}else toast(r.msg,'er')}catch(e){toast('Failed','er')}fBusy.value=false}

  watch(selFolder,f=>{if(f&&showMo.value){fm.folder=f.name;fm.entry_id=f.entry_id}});

  async function loadRules(){try{rules.value=await fetch('/api/rules')}catch(e){}}
  async function toggleRule(id){try{await post('/api/rules/'+id+'/toggle');rules.value.find(r=>r.id===id).enabled=!rules.value.find(r=>r.id===id).enabled;await loadStats()}catch(e){}}
  async function deleteRule(id){rules.value=rules.value.filter(r=>r.id!==id);try{await del('/api/rules/'+id);toast('Deleted','ok');await loadStats()}catch(e){}}

  function openModal(r){
    editRule.value=r||null;
    if(r){fm.name=r.name;fm.folder=r.folder_path;fm.entry_id=r.entry_id;fm.subject_keywords=r.subject_keywords||'';fm.match_mode=r.match_mode||'any';fm.destination=r.destination}
    else{fm.name='';fm.folder='';fm.entry_id='';fm.subject_keywords='';fm.match_mode='any';fm.destination='';if(selFolder.value){fm.folder=selFolder.value.name;fm.entry_id=selFolder.value.entry_id}}
    showMo.value=true
  }
  async function saveRule(){
    if(!fm.name||!fm.folder||!fm.destination)return;
    const d={id:editRule.value?editRule.value.id:'r_'+Date.now()+'_'+Math.random().toString(36).slice(2,7),name:fm.name,folder_path:fm.folder,entry_id:fm.entry_id||fm.folder,subject_keywords:fm.subject_keywords,match_mode:fm.match_mode,destination:fm.destination,enabled:true,last_check:editRule.value?editRule.value.last_check:null};
    await post('/api/rules',d);showMo.value=false;await loadRules();await loadStats();toast(editRule.value?'Updated':'Created','ok')
  }

  async function runAll(){try{const r=await post('/api/run');if(r.ok){isRunning.value=true;startPoll();toast('Started rule(s)','ok')}else toast(r.msg||'Cannot start','er')}catch(e){toast('Error','er')}}
  async function stopAll(){try{await post('/api/stop');toast('Stopping...','in')}catch(e){}}
  async function resetTrack(id){if(!confirm('Reset tracking for this rule? All attachments will be re-scanned on next run.'))return;try{await post('/api/rules/'+id+'/reset');toast('Tracking reset','ok')}catch(e){}}

  async function loadStats(){try{Object.assign(stats,await fetch('/api/stats'))}catch(e){}}

  function startPoll(){if(poll)return;poll=setInterval(async()=>{try{const r=await fetch('/api/progress');progs.value=r.items||[];isRunning.value=r.running;if(!r.running&&poll){clearInterval(poll);poll=null;await loadRules();await loadStats();const errs=(r.items||[]).filter(i=>i.status==='error');if(errs.length)toast(errs.length+' rule(s) failed','er');else if(r.items&&r.items.length)toast('All rules completed','ok')}}catch(e){}},350)}

  const prog=id=>progs.value.find(p=>p.rule_id===id)||null;
  const badge=id=>{const p=prog(id);if(!p)return null;const m={connecting:{l:'Connecting',c:'connecting'},searching:{l:'Searching',c:'searching'},processing:{l:'Running',c:'running'},done:{l:'Done',c:'done'},error:{l:'Error',c:'error'},stopped:{l:'Stopped',c:'stopped'}};return m[p.status]||null};
  const ruleCls=id=>{const p=prog(id);return p?p.status:''};
  function shortPath(p){if(!p)return'';const parts=p.replace(/\\\\/g,'/').split('/');if(parts.length<=3)return p;return'.../'+parts.slice(-2).join('/')}
  function fmtDate(iso){try{const d=new Date(iso);return d.toLocaleDateString(undefined,{month:'short',day:'numeric'})+' '+d.toLocaleTimeString(undefined,{hour:'2-digit',minute:'2-digit'})}catch(e){return iso}}

  async function init(){await loadRules();await loadStats();try{const r=await fetch('/api/status');olOk.value=r.ok;olMsg.value=r.msg}catch(e){olMsg.value='Offline'}ready.value=true}

  onMounted(init);
  onUnmounted(()=>{if(poll)clearInterval(poll)});

  return{ready,rules,folders,selFolder,olBusy,fBusy,olOk,olMsg,isRunning,progs,showMo,editRule,fm,toasts,stats,
    checkOL,fetchFolders,loadRules,toggleRule,deleteRule,openModal,saveRule,runAll,stopAll,resetTrack,prog,badge,ruleCls,shortPath,fmtDate}
}}).mount('#app');
</script>
</body>
</html>"""


if __name__ == '__main__':
    print("Starting Outlook Attachment Downloader Web App...")
    print("Database: energy_data.db")
    print("Open http://localhost:5000 in your browser")
    uvicorn.run(api, host="0.0.0.0", port=5000, log_level="warning")

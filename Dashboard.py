#!/usr/bin/env python3
"""
No-Code Dashboard Designer — Schema Agnostic
=============================================
Dynamically discovers columns from any table structure.
Time levels: 15-min, Hourly, Daily, Weekly, Monthly, Yearly
Aggregations: Sum, Avg, Min, Max, Count, Weighted Avg
Run: pip install nicegui pandas numpy && python dashboard_designer.py
"""

import json, uuid
from datetime import datetime
from typing import Dict, Optional
import pandas as pd
import numpy as np
from nicegui import ui, app

# ═══════════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════════
WIDGET_TYPES = {
    'line_chart':     {'label': 'Line Chart',      'icon': 'show_chart',   'color': '#3B82F6'},
    'bar_chart':      {'label': 'Bar Chart',       'icon': 'bar_chart',    'color': '#10B981'},
    'pie_chart':      {'label': 'Pie Chart',       'icon': 'pie_chart',    'color': '#F59E0B'},
    'doughnut_chart': {'label': 'Doughnut Chart',  'icon': 'donut_large',  'color': '#EC4899'},
    'kpi_card':       {'label': 'KPI Card',        'icon': 'speed',        'color': '#8B5CF6'},
    'summary_table':  {'label': 'Summary Table',   'icon': 'table_chart',  'color': '#06B6D4'},
}
COLORS = ['#3B82F6','#EF4444','#10B981','#F59E0B','#8B5CF6',
          '#EC4899','#06B6D4','#F97316','#14B8A6','#6366F1']
COLORS_A = [c + '30' for c in COLORS]
AGG_OPTS = ['sum', 'avg', 'min', 'max', 'count', 'weighted_avg']
AGG_LABELS = {'sum':'Sum','avg':'Average','min':'Minimum','max':'Maximum',
              'count':'Count','weighted_avg':'Weighted Average'}
TIME_LEVELS = {'15min':'15-min (raw)', 'hourly':'Hourly', 'daily':'Daily',
               'weekly':'Weekly', 'monthly':'Monthly', 'yearly':'Yearly'}
WIDTH_OPTS = {3:'¼ Width', 4:'⅓ Width', 6:'½ Width', 8:'⅔ Width', 12:'Full Width'}
TIME_BLOCKS = [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]

# ═══════════════════════════════════════════════════════════════
# DATA GENERATION — Realistic power-sector 15-min data
# ═══════════════════════════════════════════════════════════════
def generate_data():
    R = np.random.RandomState(42)
    dates = pd.date_range('2025-01-06', periods=45, freq='D').strftime('%Y-%m-%d').tolist()
    tables = {}

    # Tables 1-4: date, time_block, mw, rate_rs_mwh, total (+ extras)
    for plant, base_mw, base_rate in [
        ('plant_a', 250, 3200), ('plant_b', 500, 4100),
        ('plant_c', 180, 3500), ('plant_d', 350, 3800)]:
        rows = []
        for d in dates:
            for tb in TIME_BLOCKS:
                h = int(tb[:2])
                lf = 1.0 + 0.3 * np.sin((h - 6) * np.pi / 12)  # peak at noon
                mw = max(0, round(base_mw * lf * R.normal(1, 0.08), 2))
                rate = round(base_rate * R.normal(1, 0.15), 2)
                total = round(mw * rate / 4, 2)  # 15-min = 0.25 hr
                rows.append({'date': d, 'time_block': tb, 'mw': mw,
                             'rate_rs_mwh': rate, 'total': total})
        tables[plant] = pd.DataFrame(rows)

    # Table 5: date, block, mw (scheduling data, different column name)
    rows = []
    for d in dates:
        for tb in TIME_BLOCKS:
            h = int(tb[:2])
            lf = 1.0 + 0.25 * np.sin((h - 6) * np.pi / 12)
            rows.append({'date': d, 'block': tb,
                         'scheduled_mw': round(1200 * lf * R.normal(1, 0.05), 2),
                         'actual_mw': round(1200 * lf * R.normal(1, 0.12), 2),
                         'mw': round(1200 * lf * R.normal(1, 0.09), 2)})
    tables['scheduling'] = pd.DataFrame(rows)

    # Table 6: date, time_block, deviation_mw, payable, receivable, etc.
    rows = []
    for d in dates:
        for tb in TIME_BLOCKS:
            dev = round(R.normal(0, 15), 2)
            rows.append({
                'date': d, 'time_block': tb,
                'deviation_mw': dev,
                'payable': round(max(0, dev * 3500 / 4), 2),
                'receivable': round(max(0, -dev * 3500 / 4), 2),
                'penalty': round(max(0, dev) ** 1.5 * 10, 2) if abs(dev) > 20 else 0,
                'net_amount': round(-dev * 3500 / 4, 2),
                'ui_charge': round(abs(dev) * 50 / 4, 2)})
    tables['deviation_settlement'] = pd.DataFrame(rows)
    return tables

DATA = generate_data()

# ═══════════════════════════════════════════════════════════════
# DYNAMIC COLUMN DETECTION
# ═══════════════════════════════════════════════════════════════
def detect_date_col(df):
    for c in df.columns:
        if 'date' in c.lower(): return c
    for c in df.columns:
        if df[c].dtype == 'object' and df[c].str.match(r'\d{4}-\d{2}-\d{2}').any(): return c
    return df.columns[0]

def detect_time_col(df):
    for c in df.columns:
        if 'time' in c.lower() or 'block' in c.lower(): return c
    for c in df.columns:
        if df[c].dtype == 'object' and df[c].str.match(r'\d{2}:\d{2}').any(): return c
    return None

def build_info(tables):
    info = {}
    for n, df in tables.items():
        dc = detect_date_col(df)
        tc = detect_time_col(df)
        num = df.select_dtypes(include=[np.number]).columns.tolist()
        cat = [c for c in df.columns if c not in num]
        info[n] = {
            'columns': df.columns.tolist(),
            'numeric': num,
            'categorical': cat,
            'date_col': dc,
            'time_col': tc,
            'rows': len(df),
            'date_range': f"{df[dc].min()} → {df[dc].max()}" if dc else 'N/A'
        }
    return info

INFO = build_info(DATA)

# ═══════════════════════════════════════════════════════════════
# DATA PROCESSING — Fully dynamic, no hardcoded columns
# ═══════════════════════════════════════════════════════════════
def _pandas_agg(name):
    return {'sum':'sum','avg':'mean','min':'min','max':'max','count':'count'}.get(name,'sum')

def apply_time_level(df, level, date_col, time_col):
    """Adds '_x' column based on time granularity."""
    if level == '15min':
        if time_col and date_col:
            df['_x'] = df[date_col].str[5:] + ' ' + df[time_col]
        elif time_col:
            df['_x'] = df[time_col]
        else:
            df['_x'] = df[date_col]
    elif level == 'hourly':
        hr = df[time_col].str[:2] if time_col else '00'
        df['_x'] = df[date_col].str[5:] + ' ' + hr + ':00'
    elif level == 'daily':
        df['_x'] = df[date_col]
    elif level == 'weekly':
        dt = pd.to_datetime(df[date_col])
        df['_x'] = dt.dt.strftime('%Y-W%U')
    elif level == 'monthly':
        df['_x'] = df[date_col].str[:7]
    elif level == 'yearly':
        df['_x'] = df[date_col].str[:4]
    return df

def weighted_agg(df, group_keys, y_cols, weight_col):
    """Compute weighted average per group."""
    wdf = df.copy()
    for yc in y_cols:
        wdf[f'__{yc}_w'] = wdf[yc] * wdf[weight_col]
    agg_dict = {}
    for yc in y_cols:
        agg_dict[f'__{yc}_wsum'] = (f'__{yc}_w', 'sum')
    agg_dict['__wsum'] = (weight_col, 'sum')
    res = wdf.groupby(group_keys, sort=False).agg(**agg_dict).reset_index()
    for yc in y_cols:
        res[yc] = np.where(res['__wsum'] != 0,
                           res[f'__{yc}_wsum'] / res['__wsum'], 0)
        res[yc] = res[yc].round(2)
    return res

def get_grouped(df, cfg):
    """Returns (result_df, labels_list, is_time_sorted)."""
    src = cfg.get('data_source', '')
    if src not in DATA: return pd.DataFrame(), [], False
    df = DATA[src].copy()
    di = INFO[src]
    dc, tc = di['date_col'], di['time_col']

    # Date filter
    if dc and cfg.get('date_start'): df = df[df[dc] >= cfg['date_start']]
    if dc and cfg.get('date_end'):   df = df[df[dc] <= cfg['date_end']]
    if df.empty: return pd.DataFrame(), [], False

    x_axis = cfg.get('x_axis', 'daily')
    y_cols = cfg.get('y_axis', [])
    agg = cfg.get('aggregation', 'sum')
    group_by = cfg.get('group_by', '')
    weight_col = cfg.get('weight_column', '')
    if not y_cols: return pd.DataFrame(), [], False

    # Build _x column
    time_sorted = x_axis in TIME_LEVELS
    if time_sorted:
        df = apply_time_level(df, x_axis, dc, tc)
    else:
        df['_x'] = df[x_axis].astype(str)
        time_sorted = False

    # Group keys
    gk = ['_x']
    if group_by and group_by in df.columns and group_by != '_x':
        gk.append(group_by)

    # Aggregate
    if agg == 'weighted_avg' and weight_col and weight_col in df.columns:
        res = weighted_agg(df, gk, y_cols, weight_col)
    else:
        af = _pandas_agg(agg)
        res = df.groupby(gk, sort=False)[y_cols].agg(af).reset_index()
        for yc in y_cols:
            res[yc] = res[yc].round(2)

    labels = res['_x'].unique().tolist()
    if time_sorted:
        try: labels = sorted(labels)
        except: pass
    return res, labels, time_sorted

def process_chart(cfg):
    res, labels, _ = get_grouped(pd.DataFrame(), cfg)
    if res.empty: return {'labels': [], 'datasets': []}
    y_cols = cfg.get('y_axis', [])
    group_by = cfg.get('group_by', '')
    is_bar = cfg.get('_chart_type') == 'bar'
    datasets = []

    def val_at(group, label, col):
        row = group[group['_x'] == label]
        return float(row[col].values[0]) if len(row) > 0 else 0.0

    if group_by and group_by in res.columns:
        for i, (name, g) in enumerate(res.groupby(group_by)):
            data = [val_at(g, l, y_cols[0]) for l in labels]
            ds = {'label': str(name), 'data': data,
                  'borderColor': COLORS[i % len(COLORS)]}
            if is_bar:
                ds['backgroundColor'] = COLORS[i % len(COLORS)] + '99'
            else:
                ds['backgroundColor'] = COLORS_A[i % len(COLORS_A)]
                ds['fill'] = False
                ds['tension'] = 0.35
                ds['pointRadius'] = 2 if len(labels) < 100 else 0
            datasets.append(ds)
    else:
        for i, yc in enumerate(y_cols):
            data = [val_at(res, l, yc) for l in labels]
            ds = {'label': yc, 'data': data,
                  'borderColor': COLORS[i % len(COLORS)]}
            if is_bar:
                ds['backgroundColor'] = COLORS[i % len(COLORS)] + '99'
            else:
                ds['backgroundColor'] = COLORS_A[i % len(COLORS_A)]
                ds['fill'] = i == 0
                ds['tension'] = 0.35
                ds['pointRadius'] = 2 if len(labels) < 100 else 0
            datasets.append(ds)
    return {'labels': labels, 'datasets': datasets}

def process_pie(cfg):
    res, labels, _ = get_grouped(pd.DataFrame(), cfg)
    if res.empty or '_x' not in res.columns: return {'labels':[],'data':[],'colors':[]}
    y_cols = cfg.get('y_axis', [])
    if not y_cols: return {'labels':[],'data':[],'colors':[]}
    data = res[y_cols[0]].tolist()
    return {'labels': res['_x'].tolist(), 'data': data,
            'colors': [COLORS[i % len(COLORS)] for i in range(len(res))]}

def process_kpi(cfg):
    _, _, _ = get_grouped(pd.DataFrame(), cfg)  # just for validation
    src = cfg.get('data_source', '')
    if src not in DATA: return {'value':'N/A','label':'','change':None,'agg':''}
    df = DATA[src].copy()
    dc = INFO[src]['date_col']
    if dc and cfg.get('date_start'): df = df[df[dc] >= cfg['date_start']]
    if dc and cfg.get('date_end'):   df = df[df[dc] <= cfg['date_end']]
    if df.empty: return {'value':'N/A','label':'','change':None,'agg':''}
    col = cfg['y_axis'][0] if cfg.get('y_axis') else None
    if not col: return {'value':'N/A','label':'','change':None,'agg':''}
    agg = cfg.get('aggregation', 'sum')
    wt = cfg.get('weight_column', '')

    if agg == 'weighted_avg' and wt and wt in df.columns:
        val = float((df[col] * df[wt]).sum() / df[wt].sum()) if df[wt].sum() != 0 else 0
    else:
        val = float(df[col].agg(_pandas_agg(agg)))

    # Change: first half vs second half by date
    chg = None
    if dc and len(df[dc].unique()) >= 2:
        ds = sorted(df[dc].unique())
        mid = len(ds) // 2
        d1, d2 = df[df[dc] <= ds[mid-1]], df[df[dc] > ds[mid-1]]
        if agg == 'weighted_avg' and wt and wt in df.columns:
            v1 = float((d1[col]*d1[wt]).sum()/d1[wt].sum()) if d1[wt].sum() else 0
            v2 = float((d2[col]*d2[wt]).sum()/d2[wt].sum()) if d2[wt].sum() else 0
        else:
            af = _pandas_agg(agg)
            v1 = float(d1[col].agg(af)) if len(d1) else 0
            v2 = float(d2[col].agg(af)) if len(d2) else 0
        chg = round((v2 - v1) / abs(v1) * 100, 1) if v1 != 0 else None

    fmt = (f"{val/1e6:.2f}M" if abs(val) >= 1e6 else
           f"{val/1e3:.1f}K" if abs(val) >= 1e3 else
           f"{val:,.2f}" if abs(val) < 100 else f"{val:,.1f}")
    return {'value': fmt, 'label': col.replace('_',' ').title(),
            'change': chg, 'agg': AGG_LABELS.get(agg, agg)}

def process_table(cfg):
    res, _, _ = get_grouped(pd.DataFrame(), cfg)
    if res.empty: return {'columns': [], 'rows': []}
    # Remove internal columns
    display = res.drop(columns=[c for c in res.columns if c.startswith('__')], errors='ignore')
    cols = [{'name': c, 'label': c.replace('_',' ').title(),
             'field': c, 'sortable': True} for c in display.columns]
    rows = []
    for _, r in display.iterrows():
        row = {}
        for k, v in r.items():
            if isinstance(v, (np.integer,)): row[k] = int(v)
            elif isinstance(v, (np.floating,)): row[k] = round(float(v), 2)
            else: row[k] = str(v)
        rows.append(row)
    return {'columns': cols, 'rows': rows[:500]}

# ═══════════════════════════════════════════════════════════════
# DASHBOARD MANAGER
# ═══════════════════════════════════════════════════════════════
class DashMgr:
    def __init__(self):
        self.dashboards: Dict = {}
        self._load()

    def _load(self):
        s = app.storage.general.get('nb_dash2', '{}')
        self.dashboards = json.loads(s) if isinstance(s, str) else s

    def _save(self):
        app.storage.general['nb_dash2'] = self.dashboards

    def create(self, name, desc=''):
        did = uuid.uuid4().hex[:8]
        self.dashboards[did] = {'id': did, 'name': name, 'description': desc,
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat(), 'widgets': []}
        self._save(); return did

    def delete(self, did):
        self.dashboards.pop(did, None); self._save()

    def get(self, did): return self.dashboards.get(did)

    def list_all(self):
        return sorted(self.dashboards.values(),
                      key=lambda x: x.get('updated_at', ''), reverse=True)

    def add_widget(self, did, w):
        w['id'] = uuid.uuid4().hex[:8]
        self.dashboards[did]['widgets'].append(w)
        self.dashboards[did]['updated_at'] = datetime.now().isoformat()
        self._save(); return w['id']

    def update_widget(self, did, wid, cfg):
        for i, w in enumerate(self.dashboards[did]['widgets']):
            if w['id'] == wid:
                self.dashboards[did]['widgets'][i].update(cfg); break
        self.dashboards[did]['updated_at'] = datetime.now().isoformat()
        self._save()

    def remove_widget(self, did, wid):
        self.dashboards[did]['widgets'] = [
            w for w in self.dashboards[did]['widgets'] if w['id'] != wid]
        self.dashboards[did]['updated_at'] = datetime.now().isoformat()
        self._save()

    def export_json(self, did):
        return json.dumps(self.dashboards.get(did, {}), indent=2)

    def import_json(self, js):
        d = json.loads(js)
        d['id'] = uuid.uuid4().hex[:8]
        d['name'] += ' (Imported)'
        d['created_at'] = datetime.now().isoformat()
        d['updated_at'] = datetime.now().isoformat()
        for w in d.get('widgets', []): w['id'] = uuid.uuid4().hex[:8]
        self.dashboards[d['id']] = d; self._save(); return d['id']

MGR = DashMgr()

# ═══════════════════════════════════════════════════════════════
# SAMPLE DASHBOARD
# ═══════════════════════════════════════════════════════════════
def create_sample():
    did = MGR.create('Power Operations Overview',
                     'Sample dashboard — all widget types with various aggregations')
    widgets = [
        {'type': 'line_chart', 'title': 'Plant A — MW (Daily Avg)',
         'config': {'data_source': 'plant_a', 'x_axis': 'daily',
                    'y_axis': ['mw'], 'aggregation': 'avg',
                    'group_by': '', 'weight_column': '',
                    'date_start': '', 'date_end': ''}, 'width': 6},
        {'type': 'line_chart', 'title': 'All Plants MW (Hourly Sum)',
         'config': {'data_source': 'plant_a', 'x_axis': 'hourly',
                    'y_axis': ['mw'], 'aggregation': 'sum',
                    'group_by': '', 'weight_column': '',
                    'date_start': '2025-01-06', 'date_end': '2025-01-12'}, 'width': 6},
        {'type': 'bar_chart', 'title': 'Monthly Revenue (All Plants)',
         'config': {'data_source': 'plant_a', 'x_axis': 'monthly',
                    'y_axis': ['total'], 'aggregation': 'sum',
                    'group_by': '', 'weight_column': '',
                    'date_start': '', 'date_end': ''}, 'width': 6},
        {'type': 'bar_chart', 'title': 'Scheduled vs Actual MW (Daily)',
         'config': {'data_source': 'scheduling', 'x_axis': 'daily',
                    'y_axis': ['scheduled_mw', 'actual_mw'], 'aggregation': 'avg',
                    'group_by': '', 'weight_column': '',
                    'date_start': '', 'date_end': ''}, 'width': 6},
        {'type': 'kpi_card', 'title': 'Total Revenue (Plant A)',
         'config': {'data_source': 'plant_a', 'x_axis': 'daily',
                    'y_axis': ['total'], 'aggregation': 'sum',
                    'group_by': '', 'weight_column': '',
                    'date_start': '', 'date_end': ''}, 'width': 3},
        {'type': 'kpi_card', 'title': 'Avg MW (Plant B)',
         'config': {'data_source': 'plant_b', 'x_axis': 'daily',
                    'y_axis': ['mw'], 'aggregation': 'avg',
                    'group_by': '', 'weight_column': '',
                    'date_start': '', 'date_end': ''}, 'width': 3},
        {'type': 'kpi_card', 'title': 'Wtd Avg Rate (Plant C)',
         'config': {'data_source': 'plant_c', 'x_axis': 'daily',
                    'y_axis': ['rate_rs_mwh'], 'aggregation': 'weighted_avg',
                    'group_by': '', 'weight_column': 'mw',
                    'date_start': '', 'date_end': ''}, 'width': 3},
        {'type': 'kpi_card', 'title': 'Max Deviation MW',
         'config': {'data_source': 'deviation_settlement', 'x_axis': 'daily',
                    'y_axis': ['deviation_mw'], 'aggregation': 'max',
                    'group_by': '', 'weight_column': '',
                    'date_start': '', 'date_end': ''}, 'width': 3},
        {'type': 'pie_chart', 'title': 'Payable vs Receivable Split',
         'config': {'data_source': 'deviation_settlement', 'x_axis': 'daily',
                    'y_axis': ['net_amount'], 'aggregation': 'sum',
                    'group_by': '', 'weight_column': '',
                    'date_start': '', 'date_end': ''}, 'width': 4},
        {'type': 'doughnut_chart', 'title': 'Yearly Total by Plant',
         'config': {'data_source': 'plant_a', 'x_axis': 'yearly',
                    'y_axis': ['total'], 'aggregation': 'sum',
                    'group_by': '', 'weight_column': '',
                    'date_start': '', 'date_end': ''}, 'width': 4},
        {'type': 'summary_table', 'title': 'Daily Settlement Summary',
         'config': {'data_source': 'deviation_settlement', 'x_axis': 'daily',
                    'y_axis': ['deviation_mw', 'payable', 'receivable',
                               'penalty', 'net_amount', 'ui_charge'],
                    'aggregation': 'sum', 'group_by': '',
                    'weight_column': '', 'date_start': '', 'date_end': ''}, 'width': 12},
        {'type': 'line_chart', 'title': 'Weekly

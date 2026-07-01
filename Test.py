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
        {'type': 'line_chart', 'title': 'Weekly MW Trend (Weighted Avg Rate)',
         'config': {'data_source': 'plant_b', 'x_axis': 'weekly',
                    'y_axis': ['rate_rs_mwh'], 'aggregation': 'weighted_avg',
                    'group_by': '', 'weight_column': 'mw',
                    'date_start': '', 'date_end': ''}, 'width': 12},
    ]
    for w in widgets:
        MGR.add_widget(did, w)
    return did

# ═══════════════════════════════════════════════════════════════
# NAV BAR
# ═══════════════════════════════════════════════════════════════
def nav_bar(title='Dashboard Designer'):
    with ui.header().classes('bg-slate-900 text-white px-4 py-2 flex items-center gap-3 shadow-lg z-50'):
        ui.button(icon='dashboard', on_click=lambda: ui.navigate.to('/')).props('flat color=white round')
        ui.label(title).classes('text-lg font-bold')
        ui.space()
        ui.label(f'{len(DATA)} sources · {sum(i["rows"] for i in INFO.values()):,} rows').classes('text-xs opacity-60')

# ═══════════════════════════════════════════════════════════════
# WIDGET RENDERER
# ═══════════════════════════════════════════════════════════════
def render_widget(widget, editable=False, on_edit=None, on_delete=None):
    wtype = widget.get('type', 'line_chart')
    cfg = dict(widget.get('config', {}))
    title = widget.get('title', 'Untitled')
    width = widget.get('width', 6)
    accent = WIDGET_TYPES.get(wtype, {}).get('color', '#666')

    with ui.card().classes(
            f'col-span-{width} shadow-md hover:shadow-xl transition-shadow relative overflow-hidden'
        ).style(f'border-top: 3px solid {accent}'):

        # Header
        with ui.row().classes('w-full items-center justify-between px-1 pt-1'):
            ui.label(title).classes('text-sm font-semibold text-gray-700 truncate')
            if editable:
                with ui.row().classes('gap-0'):
                    ui.button(icon='edit', size='xs',
                              on_click=lambda: on_edit(widget) if on_edit else None
                              ).props('flat dense color=grey-7 round')
                    ui.button(icon='delete_outline', size='xs',
                              on_click=lambda: on_delete(widget['id']) if on_delete else None
                              ).props('flat dense color=red-7 round')

        # Chart / Card / Table
        if wtype in ('line_chart', 'bar_chart'):
            cfg['_chart_type'] = 'bar' if wtype == 'bar_chart' else 'line'
            data = process_chart(cfg)
            cfg.pop('_chart_type', None)
            if data['labels']:
                ct = 'bar' if wtype == 'bar_chart' else 'line'
                ui.chart({
                    'type': ct,
                    'data': {'labels': data['labels'], 'datasets': data['datasets']},
                    'options': {
                        'responsive': True, 'maintainAspectRatio': False,
                        'interaction': {'mode': 'index', 'intersect': False},
                        'plugins': {'legend': {'position': 'top',
                                               'labels': {'font': {'size': 10}, 'boxWidth': 12}}},
                        'scales': {
                            'x': {'ticks': {'maxRotation': 45, 'font': {'size': 9}, 'maxTicksLimit': 24},
                                  'grid': {'display': False}},
                            'y': {'beginAtZero': True, 'ticks': {'font': {'size': 9}}}}
                    }
                }).classes('w-full').style('height:280px')
            else:
                ui.label('⚠ No data — configure data source and columns').classes('text-gray-400 text-sm p-10 text-center')

        elif wtype in ('pie_chart', 'doughnut_chart'):
            data = process_pie(cfg)
            if data['labels']:
                ct = 'doughnut' if wtype == 'doughnut_chart' else 'pie'
                ui.chart({
                    'type': ct,
                    'data': {'labels': data['labels'],
                             'datasets': [{'data': data['data'],
                                           'backgroundColor': data['colors']}]},
                    'options': {
                        'responsive': True, 'maintainAspectRatio': False,
                        'plugins': {'legend': {'position': 'right',
                                               'labels': {'font': {'size': 10}, 'boxWidth': 12}}}
                    }
                }).classes('w-full').style('height:280px')
            else:
                ui.label('⚠ No data — select metrics and grouping').classes('text-gray-400 text-sm p-10 text-center')

        elif wtype == 'kpi_card':
            data = process_kpi(cfg)
            with ui.column().classes('items-center justify-center py-6 px-4 w-full'):
                ui.label(data['agg']).classes('text-xs text-gray-400 uppercase tracking-widest')
                ui.label(data['value']).classes(f'text-4xl font-bold').style(f'color:{accent}')
                ui.label(data['label']).classes('text-sm text-gray-500 mt-1')
                if data['change'] is not None:
                    arrow = '▲' if data['change'] >= 0 else '▼'
                    clr = 'text-green-500' if data['change'] >= 0 else 'text-red-500'
                    ui.label(f'{arrow} {abs(data["change"])}% vs prior half').classes(f'text-xs {clr} mt-1')

        elif wtype == 'summary_table':
            data = process_table(cfg)
            if data['rows']:
                ui.table(columns=data['columns'], rows=data['rows']
                         ).classes('w-full').props('dense flat bordered')
            else:
                ui.label('⚠ No data — select metrics and time level').classes('text-gray-400 text-sm p-10 text-center')

# ═══════════════════════════════════════════════════════════════
# WIDGET CONFIG DIALOG
# ═══════════════════════════════════════════════════════════════
def widget_config_dialog(on_save, existing_widget=None):
    is_edit = existing_widget is not None
    wtype = existing_widget['type'] if is_edit else 'line_chart'
    cfg = dict(existing_widget.get('config', {})) if is_edit else {}
    title_val = existing_widget.get('title', '') if is_edit else ''
    width_val = existing_widget.get('width', 6) if is_edit else 6

    with ui.dialog().props('maximized transition-slide-up') as dialog:
        with ui.card().classes('w-full max-w-3xl mx-auto my-4'):
            ui.label(f'{"Edit" if is_edit else "Add"} Widget').classes('text-xl font-bold mb-4')

            with ui.row().classes('w-full gap-4 mb-4'):
                title_inp = ui.input('Widget Title', value=title_val).classes('flex-1')
                if not is_edit:
                    type_sel = ui.select(
                        'Widget Type',
                        options={k: v['label'] for k, v in WIDGET_TYPES.items()},
                        value=wtype).classes('w-52')

            cfg_area = ui.column().classes('w-full gap-3')
            state = {'type': wtype, 'source': cfg.get('data_source', '')}
            cstate = {
                'x_axis': cfg.get('x_axis', 'daily'),
                'y_axis': cfg.get('y_axis', []),
                'aggregation': cfg.get('aggregation', 'sum'),
                'group_by': cfg.get('group_by', ''),
                'weight_column': cfg.get('weight_column', ''),
                'date_start': cfg.get('date_start', ''),
                'date_end': cfg.get('date_end', ''),
                'width': width_val,
            }

            def build_form():
                cfg_area.clear()
                with cfg_area:
                    # Data source
                    src_opts = {}
                    for n, i in INFO.items():
                        src_opts[n] = f"{n.replace('_',' ').title()}  ({i['rows']:,} rows, {', '.join(i['numeric'][:3])}…)"
                    ui.select('Data Source', options=src_opts,
                              value=state['source'],
                              on_change=lambda e: (state.update({'source': e.value}), build_form())
                              ).classes('w-full').props('outlined dense')

                    if not state['source']:
                        ui.label('↑ Select a data source to configure the widget').classes('text-gray-400 py-4')
                        return

                    di = INFO[state['source']]
                    wt = state['type']
                    is_pie = wt in ('pie_chart', 'doughnut_chart')
                    is_kpi = wt == 'kpi_card'
                    is_table = wt == 'summary_table'

                    # ── X-Axis / Time Level ──
                    if not is_pie:
                        x_opts = dict(TIME_LEVELS)
                        # Add categorical columns as x-axis options
                        for c in di['categorical']:
                            if c != di['date_col'] and c != di['time_col']:
                                x_opts[c] = f"Column: {c.replace('_',' ').title()}"
                        ui.select('X-Axis / Time Level', options=x_opts,
                                  value=cstate['x_axis']).classes('w-full').props('outlined dense'
                                  ).bind_value_to(cstate, 'x_axis')

                    # ── Y-Axis / Metrics ──
                    num_opts = {c: f"{c.replace('_',' ').title()}  (numeric)" for c in di['numeric']}
                    if is_kpi:
                        ui.select('Metric', options=num_opts,
                                  value=cstate['y_axis'][0] if cstate.get('y_axis') else '',
                                  on_change=lambda e: cstate.update({'y_axis': [e.value]})
                                  ).classes('w-full').props('outlined dense')
                    else:
                        ui.select('Metrics (Y-Axis)', options=num_opts,
                                  value=cstate.get('y_axis', []),
                                  multiple=True).classes('w-full').props('outlined dense use-chips'
                                  ).bind_value_to(cstate, 'y_axis')

                    # ── Aggregation ──
                    with ui.row().classes('w-full gap-4'):
                        ui.select('Aggregation', options=AGG_LABELS,
                                  value=cstate['aggregation']).classes('flex-1').props('outlined dense'
                                  ).bind_value_to(cstate, 'aggregation')

                        # Weight column (only for weighted_avg)
                        weight_row = ui.row().classes('flex-1')
                        if cstate.get('aggregation') == 'weighted_avg':
                            with weight_row:
                                w_opts = {c: c.replace('_',' ').title() for c in di['numeric']}
                                ui.select('Weight Column', options=w_opts,
                                          value=cstate.get('weight_column', ''),
                                          ).classes('w-full').props('outlined dense'
                                          ).bind_value_to(cstate, 'weight_column')

                    # ── Group By ──
                    if not is_kpi:
                        if is_pie:
                            gb_label = 'Group By (slices)'
                            gb_opts = {c: c.replace('_',' ').title() for c in di['categorical']
                                       if c != di.get('date_col') and c != di.get('time_col')}
                            gb_opts['__time_level__'] = '(Use X-Axis time level as slices)'
                            ui.select(gb_label, options=gb_opts,
                                      value=cstate.get('group_by', '')
                                      ).classes('w-full').props('outlined dense'
                                      ).bind_value_to(cstate, 'group_by')
                        else:
                            gb_opts = {c: c.replace('_',' ').title() for c in di['categorical']
                                       if c != di.get('date_col') and c != di.get('time_col')}
                            ui.select('Group By (series/lines — optional)', options=gb_opts,
                                      value=cstate.get('group_by', ''), clearable=True
                                      ).classes('w-full').props('outlined dense clearable'
                                      ).bind_value_to(cstate, 'group_by')

                    # ── Date Range ──
                    with ui.row().classes('w-full gap-4'):
                        ui.input('Date From', value=cstate.get('date_start', ''),
                                 placeholder='e.g. 2025-01-06').classes('flex-1').props('outlined dense'
                                 ).bind_value_to(cstate, 'date_start')
                        ui.input('Date To', value=cstate.get('date_end', ''),
                                 placeholder='e.g. 2025-02-15').classes('flex-1').props('outlined dense'
                                 ).bind_value_to(cstate, 'date_end')

                    # ── Width ──
                    ui.select('Widget Width', options=WIDTH_OPTS,
                              value=cstate.get('width', 6)).classes('w-48').props('outlined dense'
                              ).bind_value_to(cstate, 'width')

                    # ── Source Info ──
                    with ui.expansion('📋 Data Source Info', icon='info').classes('w-full'):
                        ui.label(f"Columns ({len(di['columns'])}): "
                                 f"{', '.join(di['columns'])}").classes('text-xs text-gray-500 break-all')
                        with ui.row().classes('gap-4 mt-1'):
                            ui.label(f"Numeric ({len(di['numeric'])}): "
                                     f"{', '.join(di['numeric'])}").classes('text-xs text-blue-600')
                        ui.label(f"Date column: {di['date_col']}  |  "
                                 f"Time column: {di['time_col']}  |  "
                                 f"Range: {di['date_range']}").classes('text-xs text-gray-500 mt-1')

            def on_agg_change(e):
                cstate['aggregation'] = e.value
                build_form()

            # Bind aggregation change to rebuild form (for weight column visibility)
            # We handle this via the build_form reading cstate['aggregation']

            def on_type_change(e):
                state['type'] = e.value
                build_form()

            if not is_edit:
                type_sel.on_change(on_type_change)

            # Override the bind for aggregation to also rebuild
            orig_build = build_form

            def make_agg_handler():
                def handler(e):
                    cstate['aggregation'] = e.value
                    build_form()
                return handler

            build_form()
            # Patch: find the aggregation select and add a rebuild handler
            # (the bind_value_to already updates cstate, but we need to rebuild for weight col)
            # We'll use a wrapper approach instead — add on_change after build
            def patch_agg():
                # After build_form, find the aggregation select in cfg_area
                # Simpler: just make the save button check aggregation
                pass

            # Buttons
            with ui.row().classes('w-full justify-end gap-2 mt-6 pt-4 border-t'):
                ui.button('Cancel', on_click=dialog.close).props('flat color=grey')
                ui.button('Save Widget', icon='check', on_click=lambda: save()
                          ).props('color=primary unelevated')

            def save():
                t = title_inp.value.strip()
                if not t: ui.notify('Enter a widget title', 'warning'); return
                ya = cstate.get('y_axis', [])
                if isinstance(ya, list) and not ya: ui.notify('Select at least one metric', 'warning'); return
                if isinstance(ya, str) and not ya: ui.notify('Select a metric', 'warning'); return
                if isinstance(ya, str): ya = [ya]
                final_cfg = {
                    'data_source': state['source'],
                    'x_axis': cstate.get('x_axis', 'daily'),
                    'y_axis': ya,
                    'aggregation': cstate.get('aggregation', 'sum'),
                    'group_by': cstate.get('group_by', ''),
                    'weight_column': cstate.get('weight_column', ''),
                    'date_start': cstate.get('date_start', ''),
                    'date_end': cstate.get('date_end', ''),
                }
                result = {'type': state['type'], 'title': t,
                          'config': final_cfg,
                          'width': cstate.get('width', 6)}
                if is_edit: result['id'] = existing_widget['id']
                on_save(result)
                dialog.close()

        dialog.open()

# ═══════════════════════════════════════════════════════════════
# PAGE: DASHBOARD LIST
# ═══════════════════════════════════════════════════════════════
@ui.page('/')
def page_list():
    nav_bar()

    with ui.column().classes('max-w-6xl mx-auto w-full p-6 gap-6'):
        with ui.row().classes('w-full items-center justify-between flex-wrap gap-2'):
            ui.label('My Dashboards').classes('text-2xl font-bold text-gray-800')
            with ui.row().classes('gap-2'):
                ui.button('Load Sample', icon='auto_awesome',
                          on_click=lambda: ui.navigate.to(f'/designer/{create_sample()}')
                          ).props('outline color=indigo-9')
                ui.button('New Dashboard', icon='add', on_click=new_dlg.open
                          ).props('color=indigo-9 unelevated')
                ui.button('Import JSON', icon='upload_file', on_click=imp_dlg.open
                          ).props('outline color=indigo-9')

        grid = ui.row().classes('w-full grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4')

        def render_list():
            grid.clear()
            with grid:
                dashes = MGR.list_all()
                if not dashes:
                    with ui.card().classes('col-span-3 text-center py-20'):
                        ui.icon('dashboard_customize', size='72px').classes('text-gray-200')
                        ui.label('No dashboards yet').classes('text-gray-400 text-lg mt-4')
                        ui.label('Create one or load the sample to get started').classes('text-gray-300 text-sm')
                for d in dashes:
                    wc = len(d.get('widgets', []))
                    ut = d.get('updated_at', '')[:16].replace('T', ' ')
                    with ui.card().classes('w-full hover:shadow-lg transition-shadow cursor-pointer group'):
                        ui.column().classes('w-full p-1').on('click',
                            lambda did=d['id']: ui.navigate.to(f'/designer/{did}')):
                            ui.label(d['name']).classes(
                                'text-lg font-semibold text-gray-800 group-hover:text-indigo-700')
                            ui.label(d.get('description', 'No description')
                                     ).classes('text-sm text-gray-500 line-clamp-2')
                        with ui.row().classes('w-full items-center mt-2 px-1 gap-3 text-xs text-gray-400'):
                            ui.badge(f'{wc} widgets', color='indigo').props('outline size=sm')
                            ui.label(ut)
                        with ui.row().classes('w-full gap-1 mt-2 px-1 pb-1'):
                            ui.button('Edit', icon='edit', size='xs',
                                      on_click=lambda did=d['id']: ui.navigate.to(f'/designer/{did}')
                                      ).props('flat color=primary dense')
                            ui.button('View', icon='visibility', size='xs',
                                      on_click=lambda did=d['id']: ui.navigate.to(f'/view/{did}')
                                      ).props('flat color=teal dense')
                            ui.button('JSON', icon='download', size='xs',
                                      on_click=lambda did=d['id']: ui.download(
                                          MGR.export_json(did), f'dash_{did}.json', 'application/json')
                                      ).props('flat dense')
                            ui.space()
                            ui.button(icon='delete_outline', size='xs', color='red',
                                      on_click=lambda did=d['id']: del_dash(did)
                                      ).props('flat dense')

        render_list()

        # New dialog
        with ui.dialog() as new_dlg, ui.card().classes('w-96'):
            ui.label('New Dashboard').classes('text-lg font-bold mb-4')
            n_name = ui.input('Name').props('outlined')
            n_desc = ui.input('Description').props('outlined')
            with ui.row().classes('justify-end gap-2 mt-4'):
                ui.button('Cancel', on_click=new_dlg.close).props('flat')
                ui.button('Create', icon='add', on_click=lambda: do_create()).props('color=primary')
            def do_create():
                if not n_name.value: ui.notify('Enter a name', 'warning'); return
                new_dlg.close()
                ui.navigate.to(f"/designer/{MGR.create(n_name.value, n_desc.value)}")

        # Import dialog
        with ui.dialog() as imp_dlg, ui.card().classes('w-[600px]'):
            ui.label('Import Dashboard JSON').classes('text-lg font-bold mb-4')
            imp_txt = ui.textarea().classes('w-full').props('rows=14 outlined monospace')
            with ui.row().classes('justify-end gap-2 mt-4'):
                ui.button('Cancel', on_click=imp_dlg.close).props('flat')
                ui.button('Import', icon='upload', on_click=lambda: do_import()).props('color=primary')
            def do_import():
                try:
                    did = MGR.import_json(imp_txt.value)
                    imp_dlg.close(); render_list(); ui.notify('Imported!', 'positive')
                except Exception as ex:
                    ui.notify(f'Invalid JSON: {ex}', 'negative')

        def del_dash(did):
            with ui.dialog() as dlg, ui.card().classes('w-72'):
                ui.label('Delete this dashboard?').classes('font-bold')
                ui.label('This cannot be undone.').classes('text-gray-500 text-sm')
                with ui.row().classes('justify-end gap-2 mt-4'):
                    ui.button('Cancel', on_click=dlg.close).props('flat')
                    ui.button('Delete', icon='delete', color='red',
                              on_click=lambda: (MGR.delete(did), dlg.close(), render_list(),
                                                 ui.notify('Deleted', 'info')))
            dlg.open()

# ═══════════════════════════════════════════════════════════════
# PAGE: DESIGNER
# ═══════════════════════════════════════════════════════════════
@ui.page('/designer/{dashboard_id}')
def page_designer(dashboard_id: str):
    dash = MGR.get(dashboard_id)
    if not dash:
        ui.label('Dashboard not found').classes('text-red-500 text-2xl p-16'); return

    nav_bar(f"✏️ {dash['name']}")

    with ui.column().classes('max-w-7xl mx-auto w-full p-4 gap-4'):
        # Toolbar
        with ui.row().classes('w-full items-center gap-1 flex-wrap'):
            ui.button('← Back', icon='arrow_back',
                      on_click=lambda: ui.navigate.to('/')).props('flat round')
            ui.separator().props('vertical')
            for wtype, wt in WIDGET_TYPES.items():
                ui.button(wt['label'], icon=wt['icon'],
                          on_click=lambda t=wtype: add_w(t)
                          ).props('flat dense no-caps').style(
                              f'border-left: 3px solid {wt["color"]}; font-size: 12px')
            ui.separator().props('vertical')
            ui.button('👁 Preview', icon='visibility',
                      on_click=lambda: ui.navigate.to(f'/view/{dashboard_id}')
                      ).props('flat color=teal dense')
            ui.button('⬇ JSON', icon='download',
                      on_click=lambda: ui.download(
                          MGR.export_json(dashboard_id),
                          f'{dash["name"]}.json', 'application/json')
                      ).props('flat dense')

        grid = ui.row().classes('w-full grid grid-cols-12 gap-4')

        def render_widgets():
            grid.clear()
            with grid:
                ws = dash.get('widgets', [])
                if not ws:
                    with ui.card().classes('col-span-12 text-center py-20'):
                        ui.icon('widgets', size='80px').classes('text-gray-200')
                        ui.label('No widgets yet').classes('text-gray-400 text-xl mt-4')
                        ui.label('Click a widget type in the toolbar above to add one'
                                 ).classes('text-gray-300 text-sm')
                for w in ws:
                    render_widget(w, editable=True, on_edit=edit_w, on_delete=del_w)

        render_widgets()

        def add_w(wtype):
            def on_save(wd):
                MGR.add_widget(dashboard_id, wd)
                render_widgets(); ui.notify(f"Added: {wd['title']}", 'positive')
            widget_config_dialog(on_save)

        def edit_w(widget):
            def on_save(wd):
                MGR.update_widget(dashboard_id, widget['id'], wd)
                render_widgets(); ui.notify(f"Updated: {wd['title']}", 'positive')
            widget_config_dialog(on_save, existing_widget=widget)

        def del_w(wid):
            def confirm():
                MGR.remove_widget(dashboard_id, wid)
                render_widgets(); ui.notify('Widget removed', 'info')
            with ui.dialog() as dlg, ui.card().classes('w-72'):
                ui.label('Remove this widget?').classes('font-bold')
                with ui.row().classes('justify-end gap-2 mt-4'):
                    ui.button('Cancel', on_click=dlg.close).props('flat')
                    ui.button('Remove', icon='delete', color='red', on_click=confirm)
            dlg.open()

# ═══════════════════════════════════════════════════════════════
# PAGE: VIEWER (CLIENT MODE)
# ═══════════════════════════════════════════════════════════════
@ui.page('/view/{dashboard_id}')
def page_viewer(dashboard_id: str):
    dash = MGR.get(dashboard_id)
    if not dash:
        ui.label('Dashboard not found').classes('text-red-500 text-2xl p-16'); return

    with ui.header().classes('bg-slate-900 text-white px-4 py-2 flex items-center gap-3 shadow-lg z-50'):
        ui.button(icon='arrow_back', on_click=lambda: ui.navigate.to('/')
                  ).props('flat color=white round')
        ui.label(f"📊 {dash['name']}").classes('text-lg font-bold flex-1')
        ui.label(dash.get('description', '')).classes('text-sm opacity-50 hidden md:block')
        ui.space()
        ui.button('Edit', icon='edit',
                  on_click=lambda: ui.navigate.to(f'/designer/{dashboard_id}')
                  ).props('flat color=amber round')
        ui.button(icon='refresh', on_click=lambda: render()
                  ).props('flat color=white round')

    with ui.column().classes('max-w-7xl mx-auto w-full p-6 gap-4'):
        kpi_row = ui.row().classes('w-full grid grid-cols-12 gap-4')
        chart_grid = ui.row().classes('w-full grid grid-cols-12 gap-4')
        info_row = ui.row().classes('w-full text-xs text-gray-400 mt-2')

        def render():
            kpi_row.clear(); chart_grid.clear(); info_row.clear()
            with kpi_row:
                for w in dash.get('widgets', []):
                    if w.get('type') == 'kpi_card':
                        render_widget(w)
            with chart_grid:
                for w in dash.get('widgets', []):
                    if w.get('type') != 'kpi_card':
                        render_widget(w)
            with info_row:
                wc = len(dash.get('widgets', []))
                ut = dash.get('updated_at', '')[:16].replace('T', ' ')
                ui.label(f'{wc} widgets · Last updated: {ut} · Auto-refreshes every 60s')

        render()
        ui.timer(60.0, render)

# ═══════════════════════════════════════════════════════════════
# PAGE: DATA EXPLORER
# ═══════════════════════════════════════════════════════════════
@ui.page('/data_explorer')
def page_data():
    nav_bar('Data Explorer')
    with ui.column().classes('max-w-7xl mx-auto w-full p-6 gap-4'):
        src_sel = ui.select(
            'Select Table',
            options={n: f"{n.replace('_',' ').title()}  ({INFO[n]['rows']:,} rows)"
                     for n in DATA},
            value=list(DATA.keys())[0]).classes('w-80').props('outlined')
        info_area = ui.column().classes('w-full')
        tbl_area = ui.column().classes('w-full')

        def show():
            src = src_sel.value
            di = INFO[src]
            df = DATA[src].head(300)
            info_area.clear(); tbl_area.clear()
            with info_area:
                with ui.row().classes('gap-3 flex-wrap mb-2'):
                    ui.badge(f"{di['rows']:,} rows", color='blue').props('outline')
                    ui.badge(f"{len(di['numeric'])} numeric", color='green').props('outline')
                    ui.badge(f"{len(di['categorical'])} text", color='orange').props('outline')
                    ui.label(f"Date col: {di['date_col']}  ·  Time col: {di['time_col']}  ·  "
                             f"Range: {di['date_range']}").classes('text-xs text-gray-500')
                ui.label(f"All columns: {', '.join(di['columns'])}"
                         ).classes('text-xs text-gray-400')
            with tbl_area:
                cols = [{'name': c, 'label': c.replace('_',' ').title(),
                         'field': c, 'sortable': True} for c in df.columns]
                rows = []
                for _, r in df.iterrows():
                    row = {}
                    for k, v in r.items():
                        if isinstance(v, (np.integer,)): row[k] = int(v)
                        elif isinstance(v, (np.floating,)): row[k] = round(float(v), 2)
                        else: row[k] = str(v)
                    rows.append(row)
                ui.table(columns=cols, rows=rows).classes('w-full'
                         ).props('dense flat bordered virtual-scroll virtual-scroll-item-size=48')

        src_sel.on_change(lambda: show())
        show()

# ═══════════════════════════════════════════════════════════════
# LAUNCH
# ═══════════════════════════════════════════════════════════════
ui.run(title='Dashboard Designer', port=8080, reload=False, storage_secret='nb-dash-v2-secret')

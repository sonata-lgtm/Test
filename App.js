const { createApp, ref, reactive, computed, onMounted, nextTick } = Vue;
const API = "";

const app = createApp({
  setup() {
    const tables = ref([]);
    const schema = ref({});
    const activeLeftTab = ref("data");
    const activeRightTab = ref("properties");
    const selectedWidgetId = ref(null);
    const widgets = ref([]);
    const datasources = ref([]);
    const globalParams = reactive({}); // Holds values for filter widgets
    const dashboardName = ref("Untitled Dashboard");
    const dashboardId = ref(null);
    const previewMode = ref(false);
    const toast = ref(null);
    const showModelerModal = ref(false);
    const showLoadModal = ref(false);
    const showSaveModal = ref(false);
    
    const modelTables = ref([]);
    const modelJoins = ref([]);
    const draggingCol = ref(null);

    const palette = [
      { type:"kpi",     name:"KPI Card",    icon:"#" },
      { type:"chart",   name:"Smart Chart", icon:"📊" },
      { type:"report",  name:"Report Grid", icon:"🗒" },
      { type:"text",    name:"Text",        icon:"T"  },
      { type:"filter_date", name:"Date Filter", icon:"📅" },
      { type:"filter_dropdown", name:"Dropdown Filter", icon:"⬇" },
    ];

    function showToast(msg, type="info") {
      toast.value = { msg, type };
      setTimeout(()=> toast.value = null, 2500);
    }
    function uid(){ return "w_" + Math.random().toString(36).slice(2,9); }

    async function api(path, opts={}) {
      const res = await fetch(API + path, { headers: {"Content-Type":"application/json"}, ...opts });
      if (!res.ok) {
        const err = await res.json().catch(()=>({detail:res.statusText}));
        throw new Error(err.detail || "Request failed");
      }
      return res.json();
    }

    async function init() {
      const [tbls, sch] = await Promise.all([api("/api/tables"), api("/api/schema/all")]);
      tables.value = tbls.tables;
      schema.value = sch.schema;
    }

    /* ============ POWER PIVOT MODELER ============ */
    function openModeler() {
      modelTables.value = [];
      modelJoins.value = [];
      showModelerModal.value = true;
    }

    function addTableToModeler(t) {
      if (modelTables.value.find(mt => mt.name === t.name)) return;
      const cols = schema.value[t.name] || [];
      modelTables.value.push({
        name: t.name,
        x: 20 + (modelTables.value.length * 220),
        y: 20,
        columns: cols
      });
    }

    function onColDragStart(ev, tableName, colName) {
      draggingCol.value = { table: tableName, col: colName };
      ev.dataTransfer.setData("text/plain", colName);
    }

    function onColDrop(ev, targetTable, targetCol) {
      ev.preventDefault();
      if (!draggingCol.value) return;
      const src = draggingCol.value;
      if (src.table !== targetTable.name) {
        // Check if join already exists
        const exists = modelJoins.value.find(j => 
          j.leftTable === src.table && j.leftCol === src.col && 
          j.rightTable === targetTable.name && j.rightCol === targetCol
        );
        if (!exists) {
          modelJoins.value.push({
            leftTable: src.table, leftCol: src.col,
            rightTable: targetTable.name, rightCol: targetCol,
            type: "LEFT"
          });
          showToast(`Join created: ${src.table}.${src.col} = ${targetTable.name}.${targetCol}`);
        }
      }
      draggingCol.value = null;
    }

    function removeModelTable(t) {
      modelTables.value = modelTables.value.filter(mt => mt.name !== t.name);
      modelJoins.value = modelJoins.value.filter(j => j.leftTable !== t.name && j.rightTable !== t.name);
    }

    let modelDragState = null;
    function startModelMove(ev, table) {
      modelDragState = { name: table.name, startX: ev.clientX, startY: ev.clientY, origX: table.x, origY: table.y };
      document.addEventListener("mousemove", onModelMove);
      document.addEventListener("mouseup", endModelMove);
    }
    function onModelMove(ev) {
      if (!modelDragState) return;
      const t = modelTables.value.find(mt => mt.name === modelDragState.name);
      if (t) {
        t.x = modelDragState.origX + (ev.clientX - modelDragState.startX);
        t.y = modelDragState.origY + (ev.clientY - modelDragState.startY);
      }
    }
    function endModelMove() {
      modelDragState = null;
      document.removeEventListener("mousemove", onModelMove);
      document.removeEventListener("mouseup", endModelMove);
    }

    function saveDatasourceFromModeler() {
      const allFields = [];
      modelTables.value.forEach(mt => {
        mt.columns.forEach(c => {
          allFields.push({ label: `${mt.name}.${c.name}`, value: `${mt.name}.${c.name}`, type: c.type });
        });
      });

      const newDs = reactive({
        id: "ds_" + Math.random().toString(36).slice(2,8),
        name: "Dataset " + (datasources.value.length + 1),
        tables: modelTables.value.map(mt => ({ name: mt.name, alias: mt.name })),
        joins: modelJoins.value.map(j => ({...j})),
        fields: [],
        aggregations: [],
        calculatedColumns: [], // For business logic/arithmetic
        sql: ""
      });
      
      datasources.value.push(newDs);
      showModelerModal.value = false;
      showToast("Dataset created. Configure fields & logic in left panel.");
    }

    function deleteDatasource(id) {
      datasources.value = datasources.value.filter(d => d.id !== id);
    }

    function toggleField(ds, f) {
      const idx = ds.fields.findIndex(x => x.value === f.value);
      if (idx >= 0) ds.fields.splice(idx, 1);
      else ds.fields.push({...f});
    }

    function addAggregation(ds) {
      ds.aggregations.push({ func: "SUM", field: "" });
    }

    function addCalculatedColumn(ds) {
      ds.calculatedColumns.push({ alias: "calc_" + (ds.calculatedColumns.length+1), formula: "" });
    }

    function buildSQL(ds) {
      if (!ds.tables.length) return "-- No tables";
      let fromClause = `${ds.tables[0].name}`;
      
      // Support multiple joins on multiple columns
      ds.joins.forEach(j => {
        fromClause += ` ${j.type} JOIN ${j.rightTable} ON ${j.leftTable}.${j.leftCol} = ${j.rightTable}.${j.rightCol}`;
      });

      const selectParts = [];
      
      // Standard Fields
      ds.fields.forEach(f => selectParts.push(f.value));
      
      // Aggregations
      ds.aggregations.forEach(a => {
        if (a.field) selectParts.push(`${a.func}(${a.field}) AS ${a.func.toLowerCase()}_${a.field.split('.')[1] || 'val'}`);
      });
      
      // Calculated Columns (Business Logic / Arithmetic)
      ds.calculatedColumns.forEach(c => {
        if (c.formula) selectParts.push(`(${c.formula}) AS ${c.alias}`);
      });

      const select = selectParts.length ? selectParts.join(", ") : "*";
      
      // Parameterized WHERE Clause
      let where = "";
      const conditions = [];
      Object.keys(globalParams).forEach(paramKey => {
        const p = globalParams[paramKey];
        if (p.value && p.sqlClause) {
          // Basic SQL injection prevention for parameters
          const val = typeof p.value === 'string' ? `'${p.value.replace(/'/g, "''")}'` : p.value;
          conditions.push(p.sqlClause.replace(/\{\{val\}\}/g, val));
        }
      });
      if (conditions.length) where = " WHERE " + conditions.join(" AND ");

      let groupBy = "";
      if (ds.aggregations.length > 0 || ds.calculatedColumns.length > 0) {
        const groupFields = ds.fields.map(f => f.value);
        if (groupFields.length) groupBy = " GROUP BY " + groupFields.join(", ");
      }
      
      return `SELECT ${select} FROM ${fromClause}${where}${groupBy} LIMIT 500`;
    }

    function generateSQL(ds) {
      ds.sql = buildSQL(ds);
      showToast("SQL Generated");
    }

    /* ============ WIDGETS & CANVAS ============ */
    function addWidget(type) {
      const defaults = {
        kpi:   { w:240, h:120, title:"New KPI", config:{metric:"", format:"number"} },
        chart: { w:450, h:320, title:"Smart Chart", config:{chartType:"bar", xAxis:"", yAxis:""} },
        report:{ w:600, h:350, title:"Report", config:{columns:[]} },
        text:  { w:300, h:60,  title:"", config:{text:"Title",fontSize:20,align:"left"} },
        filter_date: { w:250, h:80, title:"Date Filter", config:{paramKey:"date_filter", sqlClause:"t.date >= {{val}}"} },
        filter_dropdown: { w:250, h:80, title:"Dropdown Filter", config:{paramKey:"dropdown_filter", sqlClause:"t.category = {{val}}", optionsQuery:"SELECT DISTINCT category FROM products"} },
      };
      const def = defaults[type];
      const w = reactive({
        id: uid(), type, x:20, y:20 + widgets.value.length*10,
        ...def, datasourceId: null, data: null, loading:false
      });
      widgets.value.push(w);
      selectedWidgetId.value = w.id;
      
      // Initialize global param
      if (type === 'filter_date') {
        globalParams[w.config.paramKey] = { value: "", sqlClause: w.config.sqlClause };
      } else if (type === 'filter_dropdown') {
        globalParams[w.config.paramKey] = { value: "", sqlClause: w.config.sqlClause };
        fetchDropdownOptions(w);
      }
    }

    async function fetchDropdownOptions(widget) {
      try {
        const r = await api("/api/query", { method:"POST", body: JSON.stringify({ sql: widget.config.optionsQuery, limit: 100 }) });
        widget.config.options = r.data.map(row => Object.values(row)[0]);
      } catch(e) { showToast(e.message, "error"); }
    }

    function selectWidget(id){ selectedWidgetId.value = id; }
    function deleteWidget(id){
      widgets.value = widgets.value.filter(w => w.id !== id);
      if (selectedWidgetId.value === id) selectedWidgetId.value = null;
    }

    const selectedWidget = computed(()=> widgets.value.find(w => w.id === selectedWidgetId.value));

    function onPaletteDragStart(ev, type) { ev.dataTransfer.setData("component-type", type); }
    function onCanvasDrop(ev) {
      ev.preventDefault();
      const type = ev.dataTransfer.getData("component-type");
      if (type) {
        addWidget(type);
        const rect = ev.currentTarget.getBoundingClientRect();
        const w = widgets.value[widgets.value.length-1];
        w.x = ev.clientX - rect.left - 20;
        w.y = ev.clientY - rect.top - 10;
      }
    }
    function onCanvasDragOver(ev){ ev.preventDefault(); }

    let dragState = null;
    function startMove(ev, widget) {
      if (previewMode.value) return;
      if (ev.target.classList.contains("widget-close") || ev.target.tagName === 'SELECT' || ev.target.tagName === 'INPUT') return;
      selectWidget(widget.id);
      dragState = { mode:"move", id:widget.id, startX:ev.clientX, startY:ev.clientY, origX:widget.x, origY:widget.y };
      document.addEventListener("mousemove", onDragMove);
      document.addEventListener("mouseup", onDragEnd);
    }
    function startResize(ev, widget) {
      if (previewMode.value) return;
      ev.stopPropagation();
      dragState = { mode:"resize", id:widget.id, startX:ev.clientX, startY:ev.clientY, origW:widget.w, origH:widget.h };
      document.addEventListener("mousemove", onDragMove);
      document.addEventListener("mouseup", onDragEnd);
    }
    function onDragMove(ev) {
      if (!dragState) return;
      const w = widgets.value.find(x => x.id === dragState.id);
      if (!w) return;
      const dx = ev.clientX - dragState.startX;
      const dy = ev.clientY - dragState.startY;
      if (dragState.mode === "move") {
        w.x = Math.max(0, dragState.origX + dx);
        w.y = Math.max(0, dragState.origY + dy);
      } else {
        w.w = Math.max(120, dragState.origW + dx);
        w.h = Math.max(80, dragState.origH + dy);
        if (w.type === 'chart' && w.instance) w.instance.resize();
        if (w.type === 'report' && w.gridApi) w.gridApi.sizeColumnsToFit();
      }
    }
    function onDragEnd() {
      dragState = null;
      document.removeEventListener("mousemove", onDragMove);
      document.removeEventListener("mouseup", onDragEnd);
    }

    /* ============ EXECUTION & RENDERING ============ */
    function applyFiltersAndRunAll() {
      widgets.value.forEach(w => {
        if (w.datasourceId) runWidget(w);
      });
      showToast("Dashboard refreshed with filters");
    }

    async function runWidget(widget) {
      if (!widget.datasourceId) return;
      const ds = datasources.value.find(d => d.id === widget.datasourceId);
      if (!ds) return;
      
      // Regenerate SQL to include latest filter values
      ds.sql = buildSQL(ds);
      
      widget.loading = true;
      try {
        const r = await api("/api/query", { method:"POST", body: JSON.stringify({ sql: ds.sql, limit: 500 }) });
        widget.data = r;
        widget.error = null;
        await nextTick();
        renderWidget(widget);
      } catch(e) {
        widget.error = e.message;
        showToast(e.message, "error");
      } finally {
        widget.loading = false;
      }
    }

    function renderWidget(widget) {
      if (widget.type === "chart") renderChart(widget);
      if (widget.type === "report") renderGrid(widget);
      if (widget.type === "kpi") renderKPI(widget);
    }

    function renderKPI(widget) {
      if (!widget.data || !widget.data.data.length) return;
      const row = widget.data.data[0];
      const firstVal = Object.values(row)[0];
      widget.value = typeof firstVal === 'number' ? firstVal.toLocaleString() : firstVal;
    }

    function renderChart(widget) {
      const el = document.getElementById("chart-" + widget.id);
      if (!el || !widget.data) return;
      if (widget.instance) widget.instance.dispose();
      widget.instance = echarts.init(el, 'dark');
      
      const cols = widget.data.columns;
      const rows = widget.data.data;
      const xKey = widget.config.xAxis || cols[0];
      const yKey = widget.config.yAxis || cols[1];
      
      const option = {
        backgroundColor: 'transparent',
        tooltip: { trigger: 'axis' },
        grid: { left: '3%', right: '4%', bottom: '3%', containLabel: true },
        xAxis: { type: 'category', data: rows.map(r => r[xKey]), axisLine: { lineStyle: { color: '#8b949e' } } },
        yAxis: { type: 'value', axisLine: { lineStyle: { color: '#8b949e' } } },
        series: [{ data: rows.map(r => r[yKey]), type: widget.config.chartType || 'bar', smooth: true, itemStyle: { color: '#5b9dff' } }]
      };
      widget.instance.setOption(option);
    }

    function renderGrid(widget) {
      const el = document.getElementById("grid-" + widget.id);
      if (!el || !widget.data) return;
      
      // If user configured specific columns, use them, else show all
      let colDefs;
      if (widget.config.columns && widget.config.columns.length > 0) {
        colDefs = widget.config.columns.map(c => ({ headerName: c.alias, field: c.alias, sortable: true, filter: true, resizable: true, flex: 1 }));
      } else {
        colDefs = widget.data.columns.map(c => ({ headerName: c, field: c, sortable: true, filter: true, resizable: true, flex: 1 }));
      }
      
      const gridOptions = {
        columnDefs: colDefs,
        rowData: widget.data.data,
        defaultColDef: { sortable: true, filter: true, resizable: true },
        pagination: true,
        paginationPageSize: 10
      };
      
      if (widget.gridApi) widget.gridApi.destroyGrid();
      widget.gridApi = agGrid.createGrid(el, gridOptions);
    }

    function exportGridToExcel(widget) {
      if (!widget.data) return;
      const ws = XLSX.utils.json_to_sheet(widget.data.data);
      const wb = XLSX.utils.book_new();
      XLSX.utils.book_append_sheet(wb, ws, "Report");
      XLSX.writeFile(wb, widget.title + ".xlsx");
    }

    /* ============ SAVE / LOAD ============ */
    function serializeDesign() {
      return {
        id: dashboardId.value,
        name: dashboardName.value,
        datasources: JSON.parse(JSON.stringify(datasources.value)),
        widgets: widgets.value.map(w => ({
          id:w.id, type:w.type, x:w.x, y:w.y, w:w.w, h:w.h,
          title:w.title, datasourceId:w.datasourceId, config:w.config
        })),
        version: "3.0"
      };
    }

    async function saveDashboard() {
      const design = serializeDesign();
      const r = await api("/api/dashboards", { method:"POST", body: JSON.stringify(design) });
      dashboardId.value = r.id;
      showToast("Dashboard saved ✓");
      showSaveModal.value = false;
    }

    async function loadDashboard(id) {
      const r = await api(`/api/dashboards/${id}`);
      dashboardId.value = r.id;
      dashboardName.value = r.name;
      datasources.value = r.design.datasources || [];
      widgets.value = (r.design.widgets||[]).map(w => reactive({ ...w, data:null, loading:false }));
      
      // Re-init global params and dropdown options
      widgets.value.forEach(w => {
        if (w.type === 'filter_date' || w.type === 'filter_dropdown') {
          globalParams[w.config.paramKey] = { value: "", sqlClause: w.config.sqlClause };
          if (w.type === 'filter_dropdown') fetchDropdownOptions(w);
        }
      });
      
      showLoadModal.value = false;
      showToast("Loaded: " + r.name);
      await nextTick();
      applyFiltersAndRunAll();
    }

    function exportJSON() {
      const design = serializeDesign();
      const blob = new Blob([JSON.stringify(design,null,2)], {type:"application/json"});
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = dashboardName.value + ".json"; a.click();
      URL.revokeObjectURL(url);
    }

    onMounted(async ()=>{
      try { await init(); } 
      catch(e) { showToast("Init error: "+e.message, "error"); }
    });

    // Watch for changes in filter widgets to update global params
    watch(widgets, (newWidgets) => {
      newWidgets.forEach(w => {
        if (w.type === 'filter_date' || w.type === 'filter_dropdown') {
          if (!globalParams[w.config.paramKey]) {
            globalParams[w.config.paramKey] = { value: "", sqlClause: w.config.sqlClause };
          }
          globalParams[w.config.paramKey].sqlClause = w.config.sqlClause;
        }
      });
    }, { deep: true });

    return {
      // State
      tables, schema, activeLeftTab, activeRightTab, selectedWidgetId, selectedWidget,
      widgets, datasources, globalParams, dashboardName, dashboardId, previewMode, toast,
      showModelerModal, showLoadModal, showSaveModal, modelTables, modelJoins, palette,
      // Modeler
      openModeler, addTableToModeler, onColDragStart, onColDrop, removeModelTable,
      startModelMove, saveDatasourceFromModeler, deleteDatasource,
      toggleField, addAggregation, addCalculatedColumn, generateSQL,
      // Widgets
      addWidget, selectWidget, deleteWidget, onPaletteDragStart, onCanvasDrop, onCanvasDragOver,
      startMove, startResize, runWidget, applyFiltersAndRunAll, exportGridToExcel, fetchDropdownOptions,
      // Save/Load
      saveDashboard, loadDashboard, exportJSON
    };
  },

  template: `
  <div class="app">
    <div class="topbar">
      <div class="brand">
        <div class="logo">📊</div>
        <span>Super Dashboard Designer</span>
      </div>
      <input v-model="dashboardName" style="width:240px;" placeholder="Dashboard name" />
      <button class="btn" @click="showLoadModal=true">Load</button>
      <button class="btn btn-primary" @click="showSaveModal=true">Save</button>
      <button class="btn" @click="exportJSON">Export JSON</button>
      <div class="spacer"></div>
      <button class="btn btn-primary" @click="applyFiltersAndRunAll">▶ Apply Filters & Run</button>
      <button class="btn" :class="{'btn-primary':previewMode}" @click="previewMode=!previewMode">
        {{ previewMode ? '✎ Edit' : '👁 Preview' }}
      </button>
    </div>

    <div class="main">
      <!-- LEFT SIDEBAR -->
      <div class="sidebar">
        <div class="sidebar-tabs">
          <button class="sidebar-tab" :class="{active:activeLeftTab==='data'}" @click="activeLeftTab='data'">Data & Model</button>
          <button class="sidebar-tab" :class="{active:activeLeftTab==='components'}" @click="activeLeftTab='components'">Components</button>
        </div>
        <div class="sidebar-content">
          
          <!-- DATA & MODEL TAB -->
          <div v-if="activeLeftTab==='data'">
            <div class="section">
              <button class="btn btn-primary" style="width:100%;" @click="openModeler">⚡ Open Pivot Modeler</button>
            </div>

            <div v-for="ds in datasources" :key="ds.id" class="section" style="background:var(--panel-2); padding:10px; border-radius:8px; margin-bottom:10px;">
              <div style="display:flex; justify-content:space-between; margin-bottom:8px;">
                <strong>{{ ds.name }}</strong>
                <button class="btn btn-sm btn-danger" @click="deleteDatasource(ds.id)">✕</button>
              </div>
              
              <label class="prop-label">Standard Fields</label>
              <div style="margin-bottom:6px;">
                <div v-for="f in ds.fields" :key="f.value" class="col-item" style="background:var(--panel);">
                  <span>{{ f.label }}</span>
                  <button class="btn btn-sm" @click="toggleField(ds, f)">✕</button>
                </div>
              </div>

              <label class="prop-label">Aggregations</label>
              <div v-for="(a,i) in ds.aggregations" :key="i" class="prop-row" style="margin-bottom:4px;">
                <select v-model="a.func" style="width:40%;">
                  <option>SUM</option><option>AVG</option><option>MIN</option><option>MAX</option><option>COUNT</option>
                </select>
                <select v-model="a.field" style="width:60%;">
                  <option value="">--field--</option>
                  <option v-for="t in ds.tables" :key="t.name" :value="t.name + '.some_col'">{{ t.name }} cols...</option>
                </select>
              </div>
              <button class="btn btn-sm" @click="addAggregation(ds)" style="margin-top:4px;">+ Aggregation</button>

              <label class="prop-label" style="margin-top:10px;">Calculated Columns (Arithmetic/Business Logic)</label>
              <div v-for="(c,i) in ds.calculatedColumns" :key="'calc'+i" class="logic-row">
                <input v-model="c.alias" placeholder="Alias (e.g. Profit)" />
                <input v-model="c.formula" placeholder="Formula (e.g. SUM(sales) - SUM(cost))" />
              </div>
              <button class="btn btn-sm" @click="addCalculatedColumn(ds)" style="margin-top:4px;">+ Calculated Column</button>

              <button class="btn btn-primary btn-sm" style="width:100%; margin-top:8px;" @click="generateSQL(ds)">Generate SQL</button>
              <div style="font-size:10px; color:var(--accent-2); margin-top:4px; word-break:break-all;">{{ ds.sql }}</div>
            </div>

            <div class="section">
              <div class="section-title">Database Tables</div>
              <div v-for="t in tables" :key="t.name" class="table-item" @click="addTableToModeler(t)">
                <div>
                  <div class="name">{{ t.name }}</div>
                  <div style="font-size:11px; color:var(--muted);">{{ t.rows }} rows</div>
                </div>
                <span style="color:var(--accent);">+ Add to Model</span>
              </div>
            </div>
          </div>

          <!-- COMPONENTS TAB -->
          <div v-if="activeLeftTab==='components'">
            <div class="section">
              <div class="section-title">Drag to Canvas</div>
              <div class="palette-grid">
                <div v-for="p in palette" :key="p.type" class="palette-item"
                     draggable="true" @dragstart="onPaletteDragStart($event, p.type)"
                     @click="addWidget(p.type)">
                  <span class="icon">{{ p.icon }}</span>
                  {{ p.name }}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- CANVAS -->
      <div class="canvas-area">
        <div class="canvas-toolbar">
          <strong>Canvas</strong>
          <span style="color:var(--muted);font-size:12px;">
            {{ widgets.length }} widgets · {{ datasources.length }} datasets
          </span>
        </div>
        <div class="canvas-wrap">
          <div class="canvas" @drop="onCanvasDrop" @dragover="onCanvasDragOver">
            <div v-if="widgets.length===0" class="empty" style="padding-top:120px;">
              <div style="font-size:40px;">🎨</div>
              <div>Drag components here to design your dashboard</div>
            </div>

            <div v-for="w in widgets" :key="w.id" class="widget"
                 :class="{selected: selectedWidgetId===w.id}"
                 :style="{left:w.x+'px', top:w.y+'px', width:w.w+'px', height:w.h+'px'}"
                 @mousedown="startMove($event, w)" @click="selectWidget(w.id)">

              <div class="widget-header" v-if="w.type!=='text'">
                <span>{{ w.title || '(untitled)' }}</span>
                <span v-if="w.loading" style="color:var(--accent);">⏳</span>
                <div>
                  <button v-if="w.type==='report' && w.data" class="widget-close" @click.stop="exportGridToExcel(w)" title="Export to Excel">⬇</button>
                  <button v-if="!previewMode" class="widget-close" @click.stop="deleteWidget(w.id)">✕</button>
                </div>
              </div>

              <div class="widget-body">
                <!-- KPI -->
                <div v-if="w.type==='kpi'" class="kpi-card">
                  <div class="kpi-value">{{ w.value ?? '—' }}</div>
                  <div class="kpi-label">{{ w.title }}</div>
                </div>
                
                <!-- ECharts -->
                <div v-else-if="w.type==='chart'" style="position:relative;width:100%;height:100%;">
                  <div :id="'chart-'+w.id" style="width:100%;height:100%;"></div>
                  <div v-if="!w.data" class="empty">Configure & Run</div>
                </div>
                
                <!-- AG Grid (Report) -->
                <div v-else-if="w.type==='report'" style="width:100%;height:100%;">
                  <div :id="'grid-'+w.id" class="ag-theme-quartz-dark" style="width:100%;height:100%;"></div>
                </div>

                <!-- Date Filter -->
                <div v-else-if="w.type==='filter_date'" style="display:flex;flex-direction:column;justify-content:center;height:100%;">
                  <label style="font-size:11px;color:var(--muted);margin-bottom:4px;">{{ w.title }}</label>
                  <input type="date" v-model="globalParams[w.config.paramKey].value" @change="applyFiltersAndRunAll()" />
                </div>

                <!-- Dropdown Filter -->
                <div v-else-if="w.type==='filter_dropdown'" style="display:flex;flex-direction:column;justify-content:center;height:100%;">
                  <label style="font-size:11px;color:var(--muted);margin-bottom:4px;">{{ w.title }}</label>
                  <select v-model="globalParams[w.config.paramKey].value" @change="applyFiltersAndRunAll()">
                    <option value="">All</option>
                    <option v-for="opt in w.config.options" :key="opt" :value="opt">{{ opt }}</option>
                  </select>
                </div>
                
                <!-- Text -->
                <div v-else-if="w.type==='text'" class="text-widget"
                     :style="{fontSize:(w.config.fontSize||20)+'px', textAlign:w.config.align||'left', fontWeight:600}">
                  {{ w.config.text }}
                </div>
              </div>
              <div v-if="!previewMode" class="widget-resize" @mousedown="startResize($event, w)"></div>
            </div>
          </div>
        </div>
      </div>

      <!-- RIGHT SIDEBAR (Properties) -->
      <div class="sidebar right">
        <div class="sidebar-tabs">
          <button class="sidebar-tab" :class="{active:activeRightTab==='properties'}" @click="activeRightTab='properties'">Properties</button>
          <button class="sidebar-tab" :class="{active:activeRightTab==='json'}" @click="activeRightTab='json'">Design JSON</button>
        </div>
        <div class="sidebar-content">
          <div v-if="activeRightTab==='properties'">
            <div v-if="!selectedWidget" class="empty">
              <div style="font-size:40px;">⚙</div>
              <div>Select a widget to edit properties</div>
            </div>
            <div v-else>
              <div class="prop-group">
                <label class="prop-label">Title</label>
                <input v-model="selectedWidget.title" />
              </div>
              <div class="prop-row">
                <div><label class="prop-label">Width</label><input type="number" v-model.number="selectedWidget.w" /></div>
                <div><label class="prop-label">Height</label><input type="number" v-model.number="selectedWidget.h" /></div>
              </div>

              <div v-if="['kpi','chart','report'].includes(selectedWidget.type)">
                <div class="prop-group">
                  <label class="prop-label">Dataset</label>
                  <select v-model="selectedWidget.datasourceId">
                    <option :value="null">— none —</option>
                    <option v-for="ds in datasources" :key="ds.id" :value="ds.id">{{ ds.name }}</option>
                  </select>
                </div>
              </div>

              <!-- Filter Config -->
              <div v-if="selectedWidget.type==='filter_date' || selectedWidget.type==='filter_dropdown'">
                <div class="prop-group">
                  <label class="prop-label">Parameter Key</label>
                  <input v-model="selectedWidget.config.paramKey" @change="globalParams[selectedWidget.config.paramKey] = { value: '', sqlClause: selectedWidget.config.sqlClause }" />
                </div>
                <div class="prop-group">
                  <label class="prop-label">SQL Clause (use {{ '{{val}}' }} for parameter)</label>
                  <input v-model="selectedWidget.config.sqlClause" @change="globalParams[selectedWidget.config.paramKey].sqlClause = selectedWidget.config.sqlClause" />
                  <small style="color:var(--muted);">e.g., orders.order_date >= {{ '{{val}}' }}</small>
                </div>
                <div v-if="selectedWidget.type==='filter_dropdown'" class="prop-group">
                  <label class="prop-label">Options Query (SQL)</label>
                  <textarea v-model="selectedWidget.config.optionsQuery" rows="2"></textarea>
                  <button class="btn btn-sm" @click="fetchDropdownOptions(selectedWidget)">Load Options</button>
                </div>
              </div>

              <!-- Chart Config -->
              <div v-if="selectedWidget.type==='chart'">
                <div class="prop-group">
                  <label class="prop-label">Chart Type</label>
                  <select v-model="selectedWidget.config.chartType">
                    <option value="bar">Bar</option>
                    <option value="line">Line (Trend)</option>
                    <option value="pie">Pie</option>
                  </select>
                </div>
                <div class="prop-group">
                  <label class="prop-label">X-Axis Field</label>
                  <input v-model="selectedWidget.config.xAxis" />
                </div>
                <div class="prop-group">
                  <label class="prop-label">Y-Axis Field</label>
                  <input v-model="selectedWidget.config.yAxis" />
                </div>
              </div>

              <!-- Report Config -->
              <div v-if="selectedWidget.type==='report'">
                <div class="prop-group">
                  <label class="prop-label">Report Columns (Leave empty to show all)</label>
                  <div v-for="(col,i) in selectedWidget.config.columns" :key="i" class="prop-row">
                    <input v-model="col.alias" placeholder="Display Name" />
                    <input v-model="col.field" placeholder="SQL Field Name" />
                  </div>
                  <button class="btn btn-sm" @click="selectedWidget.config.columns.push({alias:'', field:''})">+ Add Column</button>
                </div>
              </div>

              <button v-if="['kpi','chart','report'].includes(selectedWidget.type)" class="btn btn-primary" style="width:100%;margin-top:10px;" @click="runWidget(selectedWidget)">▶ Run Query</button>
            </div>
          </div>

          <div v-if="activeRightTab==='json'">
            <div class="section-title">
              <span>Live Design JSON</span>
              <button class="btn btn-sm" @click="exportJSON">⬇ Download</button>
            </div>
            <pre style="font-size:10px;background:#0a0e14;padding:8px;border-radius:4px;overflow:auto;color:var(--accent-2);max-height:80vh;">{{ JSON.stringify({name:dashboardName,datasources,widgets:widgets.map(w=>({id:w.id,type:w.type,x:w.x,y:w.y,w:w.w,h:w.h,title:w.title,datasourceId:w.datasourceId,config:w.config}))},null,2) }}</pre>
          </div>
        </div>
      </div>
    </div>

    <!-- POWER PIVOT MODELER MODAL -->
    <div v-if="showModelerModal" class="modal-overlay">
      <div class="modal">
        <div class="modal-title">⚡ Power Pivot Modeler (Drag Columns to Join)</div>
        <div style="flex:1; position:relative; overflow:auto; background:var(--bg); border:1px solid var(--border); border-radius:6px;">
          <div v-if="modelTables.length===0" class="empty" style="padding-top:40px;">
            Click tables from the list on the left to add them here. Then drag a column from one table and drop it onto a column in another table to create a join. (Supports multiple joins)
          </div>
          
          <div v-for="t in modelTables" :key="t.name" class="model-table" 
               :style="{left: t.x + 'px', top: t.y + 'px'}">
            <div class="model-table-header" @mousedown="startModelMove($event, t)">
              {{ t.name }}
              <button style="float:right; background:rgba(0,0,0,0.2); padding:0 4px;" @click="removeModelTable(t)">✕</button>
            </div>
            <div class="model-table-cols">
              <div v-for="c in t.columns" :key="c.name" class="model-col"
                   draggable="true"
                   @dragstart="onColDragStart($event, t.name, c.name)"
                   @dragover.prevent
                   @drop="onColDrop($event, t, c.name)">
                <span>{{ c.name }}</span>
                <span style="color:var(--muted);">{{ c.type }}</span>
              </div>
            </div>
          </div>
        </div>
        
        <div class="modal-actions">
          <button class="btn" @click="showModelerModal=false">Cancel</button>
          <button class="btn btn-primary" @click="saveDatasourceFromModeler">Create Dataset</button>
        </div>
      </div>
    </div>

    <!-- LOAD MODAL -->
    <div v-if="showLoadModal" class="modal-overlay" @click.self="showLoadModal=false">
      <div class="modal" style="height:auto;">
        <div class="modal-title">Load Dashboard</div>
        <button class="btn btn-primary" @click="loadDashboard('temp-id')">Load Sample</button>
      </div>
    </div>

    <!-- SAVE MODAL -->
    <div v-if="showSaveModal" class="modal-overlay" @click.self="showSaveModal=false">
      <div class="modal" style="height:auto;">
        <div class="modal-title">Save Dashboard</div>
        <input v-model="dashboardName" style="margin-bottom:10px;" />
        <div class="modal-actions">
          <button class="btn" @click="showSaveModal=false">Cancel</button>
          <button class="btn btn-primary" @click="saveDashboard">Save</button>
        </div>
      </div>
    </div>

    <div v-if="toast" class="toast">{{ toast.msg }}</div>
  </div>
  `
});

app.mount("#app");

"""
Super Dashboard & Report Designer API
"""
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any, List
from pathlib import Path
import sqlite3
import json
import uuid
import re
from datetime import datetime

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DB_PATH = "../system_data.db"
DESIGNS_PATH = "designs.json"
STATIC_DIR = Path(__file__).parent / "static"

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def load_designs() -> List[dict]:
    if not Path(DESIGNS_PATH).exists(): return []
    with open(DESIGNS_PATH, "r", encoding="utf-8") as f: return json.load(f)

def save_designs(designs: List[dict]):
    with open(DESIGNS_PATH, "w", encoding="utf-8") as f:
        json.dump(designs, f, indent=2)

class QueryRequest(BaseModel):
    sql: str
    limit: int = 1000

class DashboardSave(BaseModel):
    id: Optional[str] = None
    name: str
    design: Dict[str, Any]

@app.get("/api/tables")
def list_tables():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [r[0] for r in c.fetchall()]
    result = []
    for t in tables:
        c.execute(f"SELECT COUNT(*) FROM {t}")
        result.append({"name": t, "rows": c.fetchone()[0]})
    conn.close()
    return {"tables": result}

@app.get("/api/schema/all")
def get_all_schema():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
    tables = [r[0] for r in c.fetchall()]
    result = {}
    for t in tables:
        c.execute(f"PRAGMA table_info({t})")
        result[t] = [{"name": r[1], "type": r[2]} for r in c.fetchall()]
    conn.close()
    return {"schema": result}

@app.post("/api/query")
async def execute_query(req: QueryRequest):
    sql = req.sql.strip()
    if not re.match(r"^\s*(SELECT|WITH)", sql, re.IGNORECASE):
        raise HTTPException(400, "Only SELECT/WITH queries are allowed")
    if re.search(r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|ATTACH|DETACH)\b", sql, re.IGNORECASE):
        raise HTTPException(400, "DML/DDL statements are not allowed")
    
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute(sql)
        rows = c.fetchall()
        columns = [d[0] for d in c.description] if c.description else []
        data = [dict(r) for r in rows[:req.limit]]
        return {"columns": columns, "data": data, "rowCount": len(rows)}
    except Exception as e:
        raise HTTPException(400, f"SQL Error: {str(e)}")
    finally:
        conn.close()

@app.get("/api/dashboards")
def list_dashboards():
    return {"dashboards": [{"id": d["id"], "name": d["name"], "updated": d.get("updated","")} for d in load_designs()]}

@app.post("/api/dashboards")
def save_dashboard(payload: DashboardSave):
    designs = load_designs()
    now = datetime.now().isoformat()
    if not payload.id:
        payload.id = str(uuid.uuid4())
        designs.append({"id": payload.id, "name": payload.name, "design": payload.design, "updated": now})
    else:
        for i, d in enumerate(designs):
            if d["id"] == payload.id:
                designs[i] = {"id": payload.id, "name": payload.name, "design": payload.design, "updated": now}
                break
    save_designs(designs)
    return {"id": payload.id, "status": "saved"}

if STATIC_DIR.exists():
    app.mount("/app", StaticFiles(directory=str(STATIC_DIR), html=True), name="app")

@app.get("/")
def root():
    index = STATIC_DIR / "index.html"
    if index.exists(): return FileResponse(str(index))
    return {"message": "Frontend not built."}

if __name__ == "__main__":
    import uvicorn
    if not Path(DB_PATH).exists():
        print(f"Warning: {DB_PATH} does not exist.")
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

from fastapi import FastAPI, HTTPException, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
import sqlite3
import glob
import os
import sys

base = sys.argv[1]
app = FastAPI(title="SQLite Browser")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
async def read_index():
    return FileResponse("index.html")

@app.get("/api/databases")
async def get_databases():
    try:
        db_files = glob.glob("**/*.db", root_dir=base, recursive=True) + glob.glob("**/*.sqlite", root_dir=base, recursive=True)
        return {"databases": [f for f in db_files if os.path.isfile(os.path.join(base, f))]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/tables")
async def get_tables(db_path: str = Form(...)):
    db_path = os.path.join(base, db_path)
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="Database file not found")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = [table[0] for table in cursor.fetchall()]
        table_info = []
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
            row_count = cursor.fetchone()[0]
            table_info.append({"name": table, "row_count": row_count})
        conn.close()
        return {"tables": table_info}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/records")
async def get_records(
    db_path: str = Form(...),
    table_name: str = Form(...),
    where_clause: str = Form("")
):
    db_path = os.path.join(base, db_path)
    if not os.path.exists(db_path):
        raise HTTPException(status_code=404, detail="Database file not found")
    
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Basic validation to prevent SQL injection
        if not table_name.replace('_', '').isalnum():
            raise HTTPException(status_code=400, detail="Invalid table name")
        
        query = f"SELECT * FROM {table_name}"
        params = []
        
        if where_clause.strip():
            query += f" {where_clause}"
        
        cursor.execute(query)
        records = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
        conn.close()
        
        return {
            "columns": columns,
            "records": records,
            "count": len(records)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
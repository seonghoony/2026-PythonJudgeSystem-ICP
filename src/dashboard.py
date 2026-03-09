import argparse
import os
import sys
import yaml
import logging
from pathlib import Path
from dotenv import load_dotenv

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

# Fix Import Path
sys.path.append(str(Path(__file__).parent.parent))

from src.infrastructure import database as db

# Load Env
load_dotenv()

app = FastAPI(title="PythonJudgeSystem Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "web" / "templates"))

def load_monitor_config():
    path = Path("config/monitor.yaml")
    if not path.exists():
        return {"refresh_interval": 60, "lectures": [], "blacklist": [], "whitelist": []}
    with open(path) as f:
        return yaml.safe_load(f)

@app.get("/{lecture_id}", response_class=HTMLResponse)
async def dashboard_view(request: Request, lecture_id: int):
    # Get stats for this lecture
    stats = db.get_assignment_stats(lecture_id)
    
    # We might want to enrich this with assignment details (like name) from the db
    # db.get_assignment_stats returns { assignment_id: { 'uniq_students': X, ... } }
    # To get assignment names, we should fetch them explicitly or join them.
    # Fortunately, we can query assignments list directly.
    
    assignments = []
    
    sql = "SELECT id, name, last_fetched_at FROM assignments WHERE lecture_id = %s AND (week_start IS NULL OR week_start <= NOW()) ORDER BY id ASC"
    rows = db.execute_query(sql, (lecture_id,), fetch=True)
    
    for row in (rows or []):
        aid = row['id']
        aname = row['name']
        
        astat = stats.get(aid, {})
        uniq = astat.get('uniq_students', 0)
        subs = astat.get('num_submissions', 0)
        acs = astat.get('num_correct', 0)
        last_fetch = astat.get('last_fetched_at', row.get('last_fetched_at', '-'))
        if last_fetch and len(str(last_fetch)) > 19:
            last_fetch = str(last_fetch)[:19]
            
        assignments.append({
            "id": aid,
            "name": aname,
            "last_fetch": last_fetch,
            "uniq_students": uniq,
            "submissions": subs,
            "ac_count": acs
        })
        
    # Get the lecture name
    sql_lecture = "SELECT name FROM lectures WHERE id = %s"
    lecture_rows = db.execute_query(sql_lecture, (lecture_id,), fetch=True)
    lecture_name = lecture_rows[0]['name'] if lecture_rows else f"Lecture {lecture_id}"
        
    return templates.TemplateResponse(
        request=request, name="dashboard.html", context={"lecture_id": lecture_id, "lecture_name": lecture_name, "assignments": assignments}
    )

@app.get("/{lecture_id}/{assignment_id}", response_class=HTMLResponse)
async def cdf_view(request: Request, lecture_id: int, assignment_id: int):
    # We need the assignment details
    sql = "SELECT name FROM assignments WHERE id = %s"
    rows = db.execute_query(sql, (assignment_id,), fetch=True)
    assignment_name = rows[0]['name'] if rows else f"Assignment {assignment_id}"
    
    # Fetch CDF Data
    cdf_data = db.get_cdf_data(assignment_id)
    
    # Process for the graph
    import json
    dates = []
    counts = []
    cum_count = 0
    for row in cdf_data:
        dt = row.get('first_submission')
        if not dt:
            continue
        cum_count += 1
        dates.append(str(dt))
        counts.append(cum_count)
    
    return templates.TemplateResponse(
        request=request, name="cdf.html", context={
            "lecture_id": lecture_id, 
            "assignment_id": assignment_id,
            "assignment_name": assignment_name,
            "dates": json.dumps(dates),
            "counts": json.dumps(counts)
        }
    )

def main():
    parser = argparse.ArgumentParser(description="Start the Dashboard Dashboard")
    parser.add_argument("--port", type=int, default=8000, help="Port to run the dashboard on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind the dashboard to")
    
    args = parser.parse_args()
    
    uvicorn.run("src.dashboard:app", host=args.host, port=args.port, reload=True)

if __name__ == "__main__":
    main()

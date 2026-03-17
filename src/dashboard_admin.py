import argparse
import sys
import json
import os
import secrets
import yaml
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

import uvicorn
from fastapi import FastAPI, Request, Response, HTTPException, Depends, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials

sys.path.append(str(Path(__file__).parent.parent))

from src.infrastructure import database as db

load_dotenv()

app = FastAPI(title="PythonJudgeSystem Admin Dashboard")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "web" / "templates"))

def parse_json(value):
    if not value: return None
    try:
        return json.loads(value)
    except:
        return None
templates.env.filters["parse_json"] = parse_json

security = HTTPBasic()

def verify_credentials(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = os.environ.get("SNOWBOARD_USER", "")
    correct_password = os.environ.get("SNOWBOARD_PASSWORD", "")
    
    if not correct_username or not correct_password:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Admin Dashboard Auth (SNOWBOARD_USER / SNOWBOARD_PASSWORD) is not configured.",
        )
        
    is_correct_username = secrets.compare_digest(
        credentials.username.encode("utf8"), correct_username.encode("utf8")
    )
    is_correct_password = secrets.compare_digest(
        credentials.password.encode("utf8"), correct_password.encode("utf8")
    )
    
    if is_correct_username and is_correct_password:
        return credentials.username
        
    if db.verify_ta_account(credentials.username, credentials.password):
        return credentials.username
        
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect username or password",
        headers={"WWW-Authenticate": "Basic"},
    )

def log_dashboard_access(request: Request, username: str = Depends(verify_credentials)):
    """Log access for all users (Admin and TAs) to the dashboard."""
    # Don't log static assets or API polling closely, focus on main views
    path = request.url.path
    db.log_ta_access(username, path)
    return username

def verify_lecture_access(request: Request, lecture_id: int, username: str = Depends(log_dashboard_access)):
    """Verify that the user has access to the specified lecture, logging access first."""
    admin_username = os.environ.get("SNOWBOARD_USER", "")
    if username == admin_username:
        return username  # Admin has full access
        
    if not db.check_ta_lecture_access(username, lecture_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"TA {username} is not authorized to view lecture {lecture_id}",
        )
    return username

@app.get("/admin/roaster/{lecture_id}", response_class=HTMLResponse)
async def admin_lecture_view(request: Request, lecture_id: int, _: str = Depends(verify_lecture_access)):
    sql_lecture = "SELECT name FROM lectures WHERE id = %s"
    lecture_rows = db.execute_query(sql_lecture, (lecture_id,), fetch=True)
    lecture_name = lecture_rows[0]['name'] if lecture_rows else f"Lecture {lecture_id}"
    
    students = db.get_admin_lecture_students(lecture_id)
    
    assignments = []
    if students and students[0].get('assignments'):
        assignments = [{'id': a['assignment_id'], 'name': a['name']} for a in students[0]['assignments']]
        
    return templates.TemplateResponse(
        request=request, name="admin_lecture.html", context={
            "lecture_id": lecture_id,
            "lecture_name": lecture_name,
            "students": students,
            "assignments": assignments
        }
    )

@app.get("/admin/roaster/{lecture_id}/{student_id}", response_class=HTMLResponse)
async def admin_student_summary_view(request: Request, lecture_id: int, student_id: str, _: str = Depends(verify_lecture_access)):
    student_info = db.get_student_info(student_id)
    student_name = student_info['name'] if student_info else student_id
    student_dept = student_info['department'] if student_info and 'department' in student_info else "Unknown Department"
    
    sql_lecture = "SELECT name FROM lectures WHERE id = %s"
    lecture_rows = db.execute_query(sql_lecture, (lecture_id,), fetch=True)
    lecture_name = lecture_rows[0]['name'] if lecture_rows else f"Lecture {lecture_id}"
    
    all_submissions = db.get_student_all_submissions(student_id, lecture_id)
    
    grouped_assignments = {}
    for sub in all_submissions:
        aid = sub['assignment_id']
        if aid not in grouped_assignments:
            grouped_assignments[aid] = {
                'assignment_id': aid,
                'assignment_name': sub['assignment_name'],
                'submissions': []
            }
        grouped_assignments[aid]['submissions'].append(sub)
        
    return templates.TemplateResponse(
        request=request, name="admin_student_summary.html", context={
            "lecture_id": lecture_id,
            "lecture_name": lecture_name,
            "student_id": student_id,
            "student_name": student_name,
            "student_dept": student_dept,
            "grouped_assignments": list(grouped_assignments.values()),
        }
    )

@app.get("/admin/roaster/{lecture_id}/{student_id}/{assignment_id}", response_class=HTMLResponse)
async def admin_student_view(request: Request, lecture_id: int, student_id: str, assignment_id: int, _: str = Depends(verify_lecture_access)):
    student_info = db.get_student_info(student_id)
    student_name = student_info['name'] if student_info else student_id
    student_dept = student_info['department'] if student_info and 'department' in student_info else "Unknown Department"
    
    sql_assign = "SELECT name FROM assignments WHERE id = %s"
    assign_rows = db.execute_query(sql_assign, (assignment_id,), fetch=True)
    assignment_name = assign_rows[0]['name'] if assign_rows else f"Assignment {assignment_id}"
    
    history_raw = db.get_student_submission_history(assignment_id, student_id)
    history = []
    for sub in history_raw:
        failure = None
        if sub.get('failure_details'):
            try:
                failure = json.loads(sub['failure_details'])
            except:
                pass
                
        history.append({
            'verdict': sub['verdict'],
            'score': sub['score'],
            'max_score': sub['max_score'],
            'submitted_at': str(sub['submitted_at']),
            'code': sub['code'],
            'failure': failure
        })
    
    return templates.TemplateResponse(
        request=request, name="admin_student.html", context={
            "lecture_id": lecture_id,
            "student_id": student_id,
            "student_name": student_name,
            "student_dept": student_dept,
            "assignment_id": assignment_id,
            "assignment_name": assignment_name,
            "history": history
        }
    )

@app.get("/admin/feed", response_class=HTMLResponse)
async def admin_feed_view(request: Request, username: str = Depends(log_dashboard_access)):
    admin_username = os.environ.get("SNOWBOARD_USER", "")
    allowed_lecture_ids = None
    if username != admin_username:
        allowed_lecture_ids = db.get_ta_accessible_lectures(username)

    feed = db.get_global_feed(limit=50, allowed_lecture_ids=allowed_lecture_ids)
    
    sql_lectures = "SELECT id, name FROM lectures ORDER BY id ASC"
    lectures = db.execute_query(sql_lectures, fetch=True) or []
    
    if allowed_lecture_ids is not None:
        lectures = [l for l in lectures if l['id'] in allowed_lecture_ids]
    
    monitor_config_path = Path("config/monitor.yaml")
    conf = {}
    if monitor_config_path.exists():
        try:
             with open(monitor_config_path) as f:
                 conf = yaml.safe_load(f) or {}
        except Exception:
             pass
             
    monitored_lectures = conf.get("lectures", [l['id'] for l in lectures])
    
    if allowed_lecture_ids is not None:
        monitored_lectures = [l for l in monitored_lectures if l in allowed_lecture_ids]
        
    blacklist = set(conf.get("blacklist", []))
    
    monitor_table = []
    
    for lec_id in monitored_lectures:
        stats = db.get_assignment_stats(lec_id)
        sql_a = "SELECT id, name FROM assignments WHERE lecture_id = %s ORDER BY id DESC"
        assigns_rows = db.execute_query(sql_a, (lec_id,), fetch=True) or []
        
        count = 0
        for a in assigns_rows:
            if count >= 8: break
            aid = a['id']
            if aid in blacklist:
                continue
                
            astat = stats.get(aid, {})
            last_fetch = str(astat.get('last_fetched_at', '-'))
            if last_fetch and len(last_fetch) > 19:
                last_fetch = last_fetch[:19]
            
            monitor_table.append({
                'lecture_id': lec_id,
                'aid': aid,
                'name': a['name'],
                'last_fetched_at': last_fetch,
                'uniq_students': astat.get('uniq_students', 0),
                'num_submissions': astat.get('num_submissions', 0),
                'num_correct': astat.get('num_correct', 0),
            })
            count += 1
            
    monitor_table.sort(key=lambda x: (x['lecture_id'], x['aid']))
    
    return templates.TemplateResponse(
        request=request, name="admin_feed.html", context={
            "feed": feed,
            "lectures": lectures,
            "monitor_table": monitor_table
        }
    )

@app.get("/admin/api/feed")
async def api_feed_data(username: str = Depends(verify_credentials)):
    """Returns JSON data for AJAX polling to prevent UI flicker."""
    admin_username = os.environ.get("SNOWBOARD_USER", "")
    allowed_lecture_ids = None
    if username != admin_username:
        allowed_lecture_ids = db.get_ta_accessible_lectures(username)

    feed = db.get_global_feed(limit=50, allowed_lecture_ids=allowed_lecture_ids)
    
    monitor_config_path = Path("config/monitor.yaml")
    conf = {}
    if monitor_config_path.exists():
        try:
             with open(monitor_config_path) as f:
                 conf = yaml.safe_load(f) or {}
        except Exception:
             pass
             
    sql_lectures = "SELECT id FROM lectures"
    all_lecs = db.execute_query(sql_lectures, fetch=True) or []
    monitored_lectures = conf.get("lectures", [l['id'] for l in all_lecs])
    
    if allowed_lecture_ids is not None:
        monitored_lectures = [l for l in monitored_lectures if l in allowed_lecture_ids]
        
    blacklist = set(conf.get("blacklist", []))
    
    monitor_table = []
    
    for lec_id in monitored_lectures:
        stats = db.get_assignment_stats(lec_id)
        sql_a = "SELECT id, name FROM assignments WHERE lecture_id = %s ORDER BY id DESC"
        assigns_rows = db.execute_query(sql_a, (lec_id,), fetch=True) or []
        
        count = 0
        for a in assigns_rows:
            if count >= 8: break
            aid = a['id']
            if aid in blacklist:
                continue
                
            astat = stats.get(aid, {})
            last_fetch = str(astat.get('last_fetched_at', '-'))
            if last_fetch and len(last_fetch) > 19:
                last_fetch = last_fetch[:19]
            
            monitor_table.append({
                'lecture_id': lec_id,
                'aid': aid,
                'name': a['name'],
                'last_fetched_at': last_fetch,
                'uniq_students': astat.get('uniq_students', 0),
                'num_submissions': astat.get('num_submissions', 0),
                'num_correct': astat.get('num_correct', 0),
            })
            count += 1
            
    monitor_table.sort(key=lambda x: (x['lecture_id'], x['aid']))
    
    for item in feed:
       if item.get('verdict') and item.get('verdict') != 'AC' and item.get('failure_details'):
           try:
               item['failure_parsed'] = json.loads(item['failure_details'])
           except:
               item['failure_parsed'] = None
       else:
           item['failure_parsed'] = None
           
       if item.get('submitted_at'):
           item['submitted_at_short'] = str(item['submitted_at'])[:16]
           
    return {
        "feed": feed,
        "monitor_table": monitor_table
    }

@app.get("/admin/photo/{student_id}")
async def get_photo(student_id: str, _: str = Depends(verify_credentials)):
    photo_bytes = db.get_student_photo(student_id)
    if not photo_bytes:
        transparent_gif = b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
        return Response(content=transparent_gif, media_type="image/gif")
    
    return Response(content=photo_bytes, media_type="image/jpeg")

def main():
    parser = argparse.ArgumentParser(description="Start the Admin Dashboard")
    parser.add_argument("--port", type=int, default=8001, help="Port to run the admin dashboard on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind the dashboard to")
    
    args = parser.parse_args()
    
    uvicorn.run("src.dashboard_admin:app", host=args.host, port=args.port, reload=True)

if __name__ == "__main__":
    main()

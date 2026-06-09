import argparse
import binascii
import sys
import json
import os
import secrets
import yaml
from base64 import b64decode
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

import uvicorn
from fastapi import FastAPI, Request, Response, HTTPException, Depends, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.security.utils import get_authorization_scheme_param
from pydantic import BaseModel


class HTTPBasicUTF8(HTTPBasic):
    """HTTPBasic variant that decodes Authorization header as UTF-8 (RFC 7617).

    Why: the stock fastapi.security.HTTPBasic decodes as ASCII, so non-ASCII
    usernames (e.g. Korean TA names) always fail with 401.
    """

    async def __call__(self, request: Request):  # type: ignore[override]
        authorization = request.headers.get("Authorization")
        scheme, param = get_authorization_scheme_param(authorization)
        if not authorization or scheme.lower() != "basic":
            if self.auto_error:
                raise self.make_not_authenticated_error()
            return None
        try:
            data = b64decode(param).decode("utf-8")
        except (ValueError, UnicodeDecodeError, binascii.Error) as e:
            raise self.make_not_authenticated_error() from e
        username, separator, password = data.partition(":")
        if not separator:
            raise self.make_not_authenticated_error()
        return HTTPBasicCredentials(username=username, password=password)

    def make_not_authenticated_error(self) -> HTTPException:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": 'Basic realm="{}", charset="UTF-8"'.format(self.realm or "")},
        )

sys.path.append(str(Path(__file__).parent.parent))

from src.infrastructure import database as db

load_dotenv()

app = FastAPI(title="PythonJudgeSystem Admin Dashboard")
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "web" / "static")), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "web" / "templates"))

def parse_json(value):
    if not value: return None
    try:
        return json.loads(value)
    except:
        return None
templates.env.filters["parse_json"] = parse_json

security = HTTPBasicUTF8()

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
    path = request.url.path
    db.log_ta_access(username, path)
    return username

def _is_part_time_user(username: str) -> bool:
    # 아르바이트(시험감독)는 한글 실명 아이디로 생성되며, 정식 조교/관리자는 ASCII 아이디를 쓴다.
    # 따라서 non-ASCII 여부만으로 part-time 여부를 판정한다.
    return not username.isascii()

def _forbid_part_time(username: str):
    if _is_part_time_user(username):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="입퇴실 체크 기능만 이용 가능합니다.",
        )

def require_full_access(username: str = Depends(log_dashboard_access)):
    _forbid_part_time(username)
    return username

def require_full_access_api(username: str = Depends(verify_credentials)):
    _forbid_part_time(username)
    return username

def verify_lecture_access(request: Request, lecture_id: int, username: str = Depends(log_dashboard_access)):
    """Verify that the user has access to the specified lecture, logging access first."""
    _forbid_part_time(username)

    admin_username = os.environ.get("SNOWBOARD_USER", "")
    if username == admin_username:
        return username

    if not db.check_ta_lecture_access(username, lecture_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"TA {username} is not authorized to view lecture {lecture_id}",
        )
    return username

@app.get("/", response_class=HTMLResponse)
async def admin_index(request: Request, username: str = Depends(log_dashboard_access)):
    """모든 운영 기능으로 가는 진입점 랜딩 페이지."""
    admin_username = os.environ.get("SNOWBOARD_USER", "")
    is_admin = (username == admin_username)
    is_part_time = _is_part_time_user(username)

    sql_lectures = "SELECT id, name FROM lectures ORDER BY id ASC"
    lectures = db.execute_query(sql_lectures, fetch=True) or []
    if not is_admin:
        allowed = db.get_ta_accessible_lectures(username)
        lectures = [l for l in lectures if l['id'] in allowed]

    monitor_config_path = Path("config/monitor.yaml")
    exam_conf = {}
    if monitor_config_path.exists():
        try:
            with open(monitor_config_path) as f:
                exam_conf = (yaml.safe_load(f) or {}).get("exam") or {}
        except Exception:
            exam_conf = {}

    # 시험 문항이 실제로 선언된 분반만 시험 대시보드 링크로 노출.
    exam_lectures = [
        {
            "id": l["id"],
            "name": l["name"],
            "title": (exam_conf.get(l["id"]) or {}).get("title") or "시험",
        }
        for l in lectures
        if (exam_conf.get(l['id']) or {}).get("problems")
    ]

    return templates.TemplateResponse(
        request=request, name="admin_index.html", context={
            "username": username,
            "is_admin": is_admin,
            "is_part_time": is_part_time,
            "lectures": lectures,
            "exam_lectures": exam_lectures,
        }
    )

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
async def admin_feed_view(request: Request, username: str = Depends(require_full_access)):
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
async def api_feed_data(username: str = Depends(require_full_access_api)):
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

def _mask_student_id(sid: str) -> str:
    """학번 앞 2자 + 중간 x 마스킹 + 뒤 2자. 5자 미만이면 전체 마스킹."""
    if not sid:
        return "xxxx"
    s = str(sid)
    if len(s) < 5:
        return "x" * len(s)
    return s[:2] + "x" * (len(s) - 4) + s[-2:]

def _load_exam_config(lecture_id: int):
    """config/monitor.yaml 의 exam.<lecture_id> 섹션을 읽고
    문항 메타(assignments 테이블의 name)를 합쳐 반환."""
    monitor_config_path = Path("config/monitor.yaml")
    conf = {}
    if monitor_config_path.exists():
        try:
            with open(monitor_config_path) as f:
                conf = yaml.safe_load(f) or {}
        except Exception:
            conf = {}

    exam = (conf.get("exam") or {}).get(lecture_id) or {}
    problem_ids = list(exam.get("problems") or [])
    window_start = exam.get("window_start")
    window_end = exam.get("window_end")
    class_size = exam.get("class_size")
    exam_type = exam.get("exam_type") or "midterm"
    title = exam.get("title") or "중간고사"

    problem_meta = []
    for idx, aid in enumerate(problem_ids):
        rows = db.execute_query(
            "SELECT name FROM assignments WHERE id = %s", (aid,), fetch=True
        ) or []
        name = rows[0]["name"] if rows else f"Assignment {aid}"
        # "Midterm 001 P1—…" 형태에서 em dash 이후만 추출해 간결화
        if "—" in name:
            display_name = name.split("—", 1)[1].strip()
        else:
            display_name = name
        problem_meta.append({
            "assignment_id": aid,
            "label": f"실기 {idx + 1}",
            "name": display_name,
        })

    return {
        "window_start": str(window_start) if window_start else None,
        "window_end": str(window_end) if window_end else None,
        "class_size": int(class_size) if class_size else None,
        "exam_type": exam_type,
        "title": title,
        "problems": problem_meta,
    }

@app.get("/admin/exam/{lecture_id}", response_class=HTMLResponse)
async def admin_exam_view(request: Request, lecture_id: int, _: str = Depends(verify_lecture_access)):
    """실시간 중간고사 대시보드 (HTML 셸). 실제 숫자·차트·최근 5건은
    브라우저가 1초마다 /admin/api/exam/{lecture_id} 를 폴링하여 채운다."""
    sql_lecture = "SELECT name FROM lectures WHERE id = %s"
    lecture_rows = db.execute_query(sql_lecture, (lecture_id,), fetch=True)
    lecture_name = lecture_rows[0]["name"] if lecture_rows else f"Lecture {lecture_id}"

    cfg = _load_exam_config(lecture_id)
    # 초기 셸 렌더링 용도: 시험장 섹션 틀을 서버가 먼저 심어두고,
    # 실시간 숫자 3개(응시/출석/퇴실)는 JS 폴링으로 갱신.
    room_stats = db.get_exam_room_attendance_stats(lecture_id, cfg["exam_type"])

    return templates.TemplateResponse(
        request=request, name="admin_exam.html", context={
            "lecture_id": lecture_id,
            "lecture_name": lecture_name,
            "exam_title": cfg["title"],
            "window_start": cfg["window_start"],
            "window_end": cfg["window_end"],
            "class_size": cfg["class_size"],
            "problems": cfg["problems"],
            "rooms": room_stats,
        }
    )

@app.get("/admin/api/exam/{lecture_id}")
async def api_exam_data(lecture_id: int, _: str = Depends(verify_lecture_access)):
    """대시보드용 JSON. 학생 실정보(student_id, name, 부서, 코드 등)는
    서버 경계에서 제거되며 마스킹된 anon_id 만 브라우저로 전달된다."""
    cfg = _load_exam_config(lecture_id)
    problems_out = []

    for meta in cfg["problems"]:
        aid = meta["assignment_id"]
        stats = db.get_exam_problem_stats(aid)

        recent_raw = db.get_exam_recent_submissions(aid, limit=5)
        recent = [{
            "anon_id": _mask_student_id(r["student_id"]),
            "verdict": r.get("verdict"),
            "submitted_at": str(r["submitted_at"]) if r.get("submitted_at") else None,
        } for r in recent_raw]

        # 서버에서 누적 카운트까지 계산해 ECharts 에 바로 바인딩 가능한 (ts, n) 쌍으로 내려줌.
        cdf_rows = db.get_exam_first_ac_cdf(aid)
        cdf = []
        for i, row in enumerate(cdf_rows, start=1):
            ts = row.get("first_ac")
            if ts is None:
                continue
            cdf.append([str(ts), i])

        problems_out.append({
            "assignment_id": aid,
            "label": meta["label"],
            "name": meta["name"],
            "submitters": stats["submitters"],
            "total_submissions": stats["total_submissions"],
            "ac_count": stats["ac_count"],
            "correct_rate": stats["correct_rate"],
            "last_fetched_at": stats["last_fetched_at"],
            "cdf": cdf,
            "recent": recent,
        })

    return {
        "server_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window_start": cfg["window_start"],
        "window_end": cfg["window_end"],
        "problems": problems_out,
        "rooms": db.get_exam_room_attendance_stats(lecture_id, cfg["exam_type"]),
    }

@app.get("/admin/photo/{student_id}")
async def get_photo(student_id: str, _: str = Depends(verify_credentials)):
    photo_bytes = db.get_student_photo(student_id)
    if not photo_bytes:
        transparent_gif = b'GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\x00\x00\x00!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;'
        return Response(content=transparent_gif, media_type="image/gif")
    
    return Response(content=photo_bytes, media_type="image/jpeg")

class AttendanceRequest(BaseModel):
    student_id: str
    lecture_id: int
    exam_type: str

@app.get("/admin/attendance", response_class=HTMLResponse)
async def admin_attendance_view(request: Request, username: str = Depends(log_dashboard_access)):
    sql_lectures = "SELECT id, name FROM lectures ORDER BY id ASC"
    lectures = db.execute_query(sql_lectures, fetch=True) or []

    admin_username = os.environ.get("SNOWBOARD_USER", "")
    if username != admin_username:
        allowed_lecture_ids = db.get_ta_accessible_lectures(username)
        lectures = [l for l in lectures if l['id'] in allowed_lecture_ids]

    # 기본 선택값: 접근 가능한 분반 중 '가장 최근에 시작하는 시험'(monitor.yaml exam.window_start
    # 기준)을 기본 과목·시험종류로. 새 시험을 config 에 올리면 출석부 기본값이 자동으로 전환된다.
    default_lecture_id = lectures[0]["id"] if lectures else None
    default_exam_type = "midterm"
    monitor_config_path = Path("config/monitor.yaml")
    if monitor_config_path.exists():
        try:
            with open(monitor_config_path) as f:
                exam_conf = (yaml.safe_load(f) or {}).get("exam") or {}
        except Exception:
            exam_conf = {}
        accessible_ids = {l["id"] for l in lectures}
        candidates = [
            (str(c.get("window_start") or ""), lid, (c.get("exam_type") or "midterm"))
            for lid, c in exam_conf.items()
            if lid in accessible_ids
        ]
        if candidates:
            candidates.sort(reverse=True)  # 가장 최근 window_start 우선
            _, default_lecture_id, default_exam_type = candidates[0]

    return templates.TemplateResponse(
        request=request, name="admin_attendance.html", context={
            "lectures": lectures,
            "default_lecture_id": default_lecture_id,
            "default_exam_type": default_exam_type,
        }
    )

@app.get("/admin/api/attendance/search")
async def api_attendance_search(q: str, lecture_id: int, _: str = Depends(verify_credentials)):
    if not q or len(q) < 2:
        return []
    students = db.search_students_by_id(q, lecture_id)
    return students

def _enforce_room_match(username: str, student_id: str, lecture_id: int, exam_type: str):
    """Raise 403 unless the caller's exam_room_staff assignment matches the student's room.

    Applies uniformly — admin has no bypass. The admin account must be registered
    in exam_room_staff for whichever room(s) they proctor.
    """
    student_room = db.get_student_exam_room(student_id, lecture_id, exam_type)
    if not student_room:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="이 학생은 시험장 배정이 없습니다.",
        )
    user_rooms = db.get_user_exam_rooms(username, lecture_id, exam_type)
    if student_room not in user_rooms:
        allowed = ", ".join(user_rooms) if user_rooms else "없음"
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"이 학생은 [{student_room}] 응시자입니다. 담당 시험장: {allowed}",
        )

@app.get("/admin/api/attendance/student_info")
async def api_attendance_student_info(student_id: str, lecture_id: int, exam_type: str, username: str = Depends(verify_credentials)):
    student = db.get_student_for_exam(student_id, lecture_id)
    if not student:
        raise HTTPException(status_code=404, detail="Student not found in this lecture")

    attendance = db.get_student_attendance(student_id, lecture_id, exam_type)
    student_room = db.get_student_exam_room(student_id, lecture_id, exam_type)
    user_rooms = db.get_user_exam_rooms(username, lecture_id, exam_type)
    can_mark = student_room is not None and student_room in user_rooms
    return {
        "student": student,
        "attendance": attendance,
        "student_room": student_room,
        "user_rooms": user_rooms,
        "can_mark": can_mark,
    }

@app.post("/admin/api/attendance/check_in")
async def api_attendance_check_in(req: AttendanceRequest, username: str = Depends(verify_credentials)):
    _enforce_room_match(username, req.student_id, req.lecture_id, req.exam_type)
    success = db.log_exam_check_in(req.student_id, req.lecture_id, req.exam_type, username)
    if not success:
        return JSONResponse(status_code=400, content={"message": "이미 처리됨 (Already checked in)"})
    return {"message": "Success"}

@app.post("/admin/api/attendance/check_out")
async def api_attendance_check_out(req: AttendanceRequest, username: str = Depends(verify_credentials)):
    _enforce_room_match(username, req.student_id, req.lecture_id, req.exam_type)
    success = db.log_exam_check_out(req.student_id, req.lecture_id, req.exam_type, username)
    if not success:
        return JSONResponse(status_code=400, content={"message": "이미 처리됨 (Already checked out or not checked in)"})
    return {"message": "Success"}

def main():
    parser = argparse.ArgumentParser(description="Start the Admin Dashboard")
    parser.add_argument("--port", type=int, default=8001, help="Port to run the admin dashboard on")
    parser.add_argument("--host", type=str, default="0.0.0.0", help="Host to bind the dashboard to")
    
    args = parser.parse_args()
    
    uvicorn.run("src.dashboard_admin:app", host=args.host, port=args.port, reload=True)

if __name__ == "__main__":
    main()

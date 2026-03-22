import argparse
import os
import sys
import yaml
import logging
import time
import tempfile
import pandas as pd
import signal
from pathlib import Path
from typing import Optional, List, Dict
from dotenv import load_dotenv

import concurrent.futures
import os
GLOBAL_EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, (os.cpu_count() or 4) - 2))
GRADING_QUEUE = set()

load_dotenv()

sys.path.append(str(Path(__file__).parent.parent))

from src.models.schema import AssignmentConfig, EvaluationResult
from src.core.sandbox import DockerSandbox
from src.core.standard_judge import StandardJudge
from src.core.special_judge import SpecialJudge

from src.infrastructure.snowboard import SnowBoard
from src.infrastructure import database as db
from src.infrastructure.telegram import push

from src.utils.file_validator import validate_submission

from rich.logging import RichHandler

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s',
    handlers=[RichHandler(rich_tracebacks=True, show_path=False, show_time=False)]
)
logger = logging.getLogger(__name__)

def load_config(assignment_id: str) -> AssignmentConfig:
    config_path = Path(f"assignments/{assignment_id}/assignment.yaml")
    if not config_path.exists():
        raise FileNotFoundError(f"Config for {assignment_id} not found.")
        
    with open(config_path) as f:
        data = yaml.safe_load(f)
        
    return AssignmentConfig(**data)

def load_monitor_config() -> Dict:
    path = Path("config/monitor.yaml")
    if not path.exists():
        return {"refresh_interval": 60, "lectures": [], "blacklist": [], "whitelist": []}
    with open(path) as f:
        return yaml.safe_load(f)


def run_evaluate(
    assignment_id: str, 
    submission_path: Path, 
    student_id: str = "test_student",
    build: bool = False
):
    logger.info(f"Loading config for {assignment_id}...")
    config = load_config(assignment_id)
    
    if build:
        logger.info("Building Docker image...")
        DockerSandbox.build_image(config)
    
    assignment_dir = Path(f"assignments/{assignment_id}").absolute()
    
    if config.type == "standard":
        engine = StandardJudge(config)
    elif config.type == "special":
        engine = SpecialJudge(config)
    else:
        raise ValueError(f"Unknown assignment type: {config.type}")
        
    logger.info(f"Evaluating {submission_path.name}...")
    result = engine.evaluate(submission_path, assignment_dir, student_info={"student_id": student_id})
    return result

def run_fetch(lecture_id: int, assignment_id: Optional[str] = None, filter_status: str = 'submitted', force: bool = False, sb: Optional[SnowBoard] = None):
    """Crawl assignments/submissions -> DB."""
    if sb is None:
        sb = SnowBoard()
    
    logger.debug(f"Fetching assignments for lecture {lecture_id}...")
    df_assign = sb.list_assignments(str(lecture_id))
    if df_assign.empty:
        logger.warning("No assignments found.")
        return {}, {}

    db.ensure_lecture(lecture_id, f"Lecture {lecture_id}")
    
    assignments = {}
    now = pd.Timestamp.now()
    for _, row in df_assign.iterrows():
        aid = row['id_assignment']
        aname = row['과제']
        week_start = row.get('week_start') if pd.notna(row.get('week_start')) else None
        week_end = row.get('week_end') if pd.notna(row.get('week_end')) else None
        
        if not aid: continue
         
        # Filter by assignment if requested
        if assignment_id and str(assignment_id) != str(aid):
            continue
        
        # Skip outdated assignments (past due + 5 min grace period), unless force=True
        if not force and '종료 일시' in row.index and row['종료 일시'] != '-':
            try:
                due = pd.to_datetime(row['종료 일시'])
                if due + pd.Timedelta(minutes=5) < now:
                    continue
            except Exception:
                pass
        
        assignments[int(aid)] = {'title': aname, 'week_start': week_start, 'week_end': week_end}
        db.ensure_assignment(int(aid), lecture_id, aname, week_start, week_end)

    fresh_urls = {}  
    lock_urls = {}  
    for aid, item in assignments.items():
        logger.debug(f"Fetching submissions for {item.get('title')} ({aid}) [Filter: {filter_status}]...")
        
        df = sb.list_submissions(aid, filter_status=filter_status)
        db.ensure_assignment(int(aid), lecture_id, item.get('title'), item.get('week_start'), item.get('week_end'))
        
        if df.empty:
            logger.debug("  No submissions found.")
            db.update_assignment_fetch_time(int(aid))
            continue

        total = len(df)
        logger.debug(f"Found {total} submissions.")
        count = 0
        
        for _, row in df.iterrows():
            sid = str(row['학번'])
            sname = row.get('이름', 'Unknown')
            
            db.ensure_student(sid, sname, lecture_id)
            
            href = row.get('첨부파일href')
            grade_url = row.get('성적버튼href')
            lock_url = row.get('제출변경방지href')
            ts = row.get('최근 제출일', '')
            max_score_val = float(row.get('max_score', 100.0))
            
            if grade_url:
                fresh_urls[sid] = grade_url
            if lock_url:
                lock_urls[sid] = lock_url

            is_duplicate = False
            if filter_status != 'requiregrading' and not force:
                last_ts = db.get_last_submission_time(int(aid), sid)
                if last_ts:
                    try:
                        ts_dt = pd.to_datetime(ts)
                        last_ts_dt = pd.to_datetime(last_ts)
                        if ts_dt == last_ts_dt:
                            is_duplicate = True
                    except:
                        if str(last_ts) == str(ts):
                            is_duplicate = True
            
            if is_duplicate:
                continue

            try:
                if not href:
                    logger.info(f"  {sname} ({sid}): No attachment — recording as score 0.")
                    fetched_at = time.strftime('%Y-%m-%d %H:%M:%S')
                    empty_md5 = db.record_file(b"")
                    attempt_count = db.get_submission_count(int(aid), sid) + 1
                    comment = f"{attempt_count}번째 시도. 오답입니다. 제출물에 파일이 첨부되지 않았습니다."
                    db.record_submission(
                        assignment_id=int(aid),
                        student_id=sid,
                        file_md5=empty_md5,
                        submitted_at=ts,
                        fetched_at=fetched_at,
                        score=0.0,
                        verdict="WA",
                        comment=comment,
                        is_force=force,
                        max_score=max_score_val
                    )
                    if grade_url:
                        try:
                            sb.submit_score(grade_url, 0.0, comment)
                        except Exception as e:
                            logger.error(f"  Upload Error: {e}")
                            push(f"Snowboard 업로드 오류: {e}")
                    count += 1
                    continue

                content = sb.fetch_submission(href)
                md5 = db.record_file(content)
                fetched_at = time.strftime('%Y-%m-%d %H:%M:%S')
                
                fname = row.get('첨부파일명', '')
                is_valid, validation_error = validate_submission(content, fname)
                
                if not is_valid:
                    logger.info(f"  {sname} ({sid}): Validation Failed ({validation_error})")
                    attempt_count = db.get_submission_count(int(aid), sid) + 1
                    comment = f"{attempt_count}번째 시도. {validation_error}"
                    
                    db.record_submission(
                        assignment_id=int(aid),
                        student_id=sid,
                        file_md5=md5,
                        submitted_at=ts,
                        fetched_at=fetched_at,
                        score=0.0,
                        verdict="WA",
                        comment=comment,
                        is_force=force,
                        max_score=max_score_val
                    )
                    if grade_url:
                        try:
                            sb.submit_score(grade_url, 0.0, comment)
                        except Exception as e:
                            logger.error(f"  Upload Error: {e}")
                            push(f"Snowboard 업로드 오류: {e}")
                    count += 1
                    continue
                
                db.record_submission(
                    assignment_id=int(aid),
                    student_id=sid,
                    file_md5=md5,
                    submitted_at=ts,
                    fetched_at=fetched_at,
                    is_force=force,
                    max_score=max_score_val
                )
                count += 1
                if count % 10 == 0:
                     logger.info(f"  Processed {count}/{total}")
                     
            except Exception as e:
                logger.exception(f"Failed to process {sname}")
        
        if count > 0:
            logger.info(f"Fetched {count} new submissions.")
        
        db.update_assignment_fetch_time(int(aid))
    
    db.update_lecture_fetch_time(lecture_id)
    
    return fresh_urls, lock_urls

def run_grade(assignment_id: str, dry_run: bool = False, force: bool = False, url_map: Dict[str, str] = None, lock_map: Dict[str, str] = None, sb: Optional[SnowBoard] = None):
    """Grade ungraded submissions from DB. If force=True, grade ALL."""
    
    if not dry_run and sb is None:
        try:
            sb = SnowBoard()
        except Exception as e:
            logger.warning(f"Snowboard login failed in grade step: {e}. Uploads might fail.")

    try:
        config = load_config(assignment_id)
    except FileNotFoundError:
        logger.error(f"Config for {assignment_id} not found. Skipping grading.")
        return

    assignment_dir = Path(f"assignments/{assignment_id}").absolute()
    
    DockerSandbox.build_image(config)
    
    if config.type == "standard":
        engine = StandardJudge(config)
    elif config.type == "special":
        engine = SpecialJudge(config)
    else:
        logger.error(f"Unknown type: {config.type}")
        return

    submissions = db.get_ungraded_submissions(int(assignment_id), limit=100, force=force)
    
    if not submissions:
        return

    total = len(submissions)
    logger.info(f"Found {total} submissions to grade for {assignment_id} (Force: {force}).")
    
    count = 0
    def process_submission(i, sub):
            sid = sub['id'] # submission ID from DB
            student_id = sub['student_id'] # student ID
            md5 = sub['file_md5']
    
            # Override Grade URL if fresh
            grade_url = None
            if url_map and student_id in url_map:
                grade_url = url_map[student_id]
    
            # Get Max Score from sub (default 100.0)
            max_score = float(sub.get('max_score', 100.0))
    
            student_info = db.get_student_info(student_id)
            if student_info and student_info.get('name') and student_info.get('department'):
                student_str = f"{student_id} ({student_info['name']}, {student_info['department']})"
            elif student_info and student_info.get('name'):
                student_str = f"{student_id} ({student_info['name']})"
            else:
                student_str = str(student_id)
    
            logger.info(f"Grading #{i+1}/{total} (Submission ID: {sid}, Student ID: {student_str}, Max: {max_score})...")
    
            try:
                content = db.get_file_content(md5)
    
                is_valid, validation_error = validate_submission(content)
                if not is_valid:
                    attempt_count = db.get_submission_count(int(assignment_id), student_id)
                    comment = f"{attempt_count}번째 시도. {validation_error}"
                    logger.info(f"  -> WA (Validation Failed: {validation_error}) {'[DRY RUN]' if dry_run else ''}")
                    if not dry_run:
                        db.update_submission_result(sid, 0.0, "WA", comment)
    
                        # Upload to Snowboard
                        if sb and grade_url:
                            try:
                                sb.submit_score(grade_url, 0.0, comment)
                            except Exception as e:
                                logger.error(f"  Upload Error: {e}")
                                push(f"Snowboard 업로드 오류: {e}")
                    return

                with tempfile.NamedTemporaryFile(suffix=".py", delete=True) as tmp:
                    tmp.write(content)
                    tmp.flush()
    
                    student_name = db.get_student_name(student_id) or ""
    
                    result = engine.evaluate(
                        Path(tmp.name), 
                        assignment_dir, 
                        student_info={"student_id": student_id, "student_name": student_name}
                    )
    
                    current_verdict = "AC"
                    if result.system_error:
                        current_verdict = "SYS"
                    else:
                        is_pass = True
                        for res in result.results:
                            if not res.is_correct:
                                is_pass = False
                                msg = res.message or ""
                                if "Time Limit" in msg:
                                    current_verdict = "TLE"
                                    break
                                elif "Memory Limit" in msg:
                                    current_verdict = "MLE"
                                    break
                                elif "Error" in msg or "Exception" in msg: 
                                    current_verdict = "RTE" 
                                    break
                                else:
                                    current_verdict = "WA"
                        if is_pass:
                            current_verdict = "AC"
    
                    attempt_count = db.get_submission_count(int(assignment_id), student_id)
                    failure_details_json = None
    
                    if result.total_score == 1.0:
                        comment = "정답입니다!"
                    else:
                        comment = f"{attempt_count}번째 시도, 오답입니다."
    
                        # Append exception/error details if available
                        for res in result.results:
                             if not res.is_correct:
                                 # Capture first failure details for DB (Admin Debug)
                                 if not failure_details_json:
                                     import json
                                     details = {
                                         "test_case_id": res.test_case_id,
                                         "input": getattr(res, "input_data", None),
                                         "actual_output": res.stdout,
                                         "expected_output": getattr(res, "expected_output", None), # From StandardJudge
                                         "traceback": res.stderr,
                                         "message": res.message
                                     }
                                     failure_details_json = json.dumps(details, ensure_ascii=False)
    
                                 # Comment Logic (Student Feedback)
                                 # Prioritize stderr for Python tracebacks (StandardJudge)
                                 # Only log if stderr is present (indicates Runtime Error / Exception)
                                 # We ignore 'message' here (e.g. "Wrong Answer", "Time Limit Exceeded") 
    
                                 raw_error = (res.stderr or "").strip()
    
                                 if raw_error:
                                     from src.utils.sanitizer import sanitize_traceback
                                     clean_trace = sanitize_traceback(raw_error)
    
                                     # Limit length to avoid massive comments
                                     if len(clean_trace) > 1000:
                                         clean_trace = clean_trace[:1000] + "\n... (Truncated)"
    
                                     if clean_trace:
                                         if "EOFError: EOF when reading a line" in clean_trace:
                                             comment += "\n\nEOFError는 입력(input)이 더 이상 없을 때 발생합니다.\n불필요한 input() 호출이 과도하게 많거나, 반복문 종료 조건이 잘못되어 입력을 계속 기다리고 있지 않은지 확인해주세요!"
                                             
                                         comment += f"\nError Logs:\n<pre>\n{clean_trace}\n</pre>\n"
                                         break 
    
                    if result.system_error:
                        from src.utils.sanitizer import sanitize_system_error
                        san_msg, should_alert = sanitize_system_error(result.system_error)
                        comment += san_msg
                        if should_alert:
                            push(f"🚨 System Error (Assign {assignment_id}, Sub {sid}):\n{result.system_error}")
    
                    logger.info(f"  -> {current_verdict} (Score: {result.total_score:.2f}, Attempt: {attempt_count}) {'[DRY RUN]' if dry_run else ''}")
    
                    if not dry_run:
                        # Update DB (Store Actual Score: ratio * max_score)
                        final_score_points = result.total_score * max_score
    
                        db.update_submission_result(
                            sid, 
                            final_score_points, 
                            current_verdict, 
                            comment,
                            failure_details=failure_details_json
                        )
    
                        if sb and grade_url:
                            upload_score = result.total_score * max_score
                            logger.info(f"  Uploading score to Snowboard (Raw: {result.total_score} * Max: {max_score} = {upload_score})...")
                            try:
                                ok = sb.submit_score(grade_url, upload_score, comment)
                                if ok:
                                    logger.info("  Upload Success.")
                                    # Lock submission if student achieved max score (skip for professor)
                                    if upload_score >= max_score and student_id != os.environ.get("SNOWBOARD_USER", ""):
                                        lock_url = (lock_map or {}).get(student_id)
                                        if lock_url:
                                            try:
                                                sb.lock_submission(lock_url)
                                                logger.info(f"  Submission locked (max score achieved).")
                                            except Exception as e:
                                                logger.error(f"  Lock Error: {e}")
                                else:
                                    logger.error("  Upload Failed (Snowboard returned false).")
                                    push(f"Snowboard 업로드 실패: {student_id}")
                            except Exception as e:
                                logger.error(f"  Upload Error: {e}")
                                push(f"Snowboard 업로드 오류: {student_id} - {e}")
                        elif not grade_url:
                            logger.warning("  No grade_url found for submission. Cannot upload.")
    
            except Exception as e:
                logger.error(f"Grading failed for {sid}: {e}")
                push(f"채점 오류 발생! Submission {sid}: {e}")
                if not dry_run:
                    db.update_submission_result(sid, 0.0, "SYS", f"Judge Error: {e}")
            finally:
                GRADING_QUEUE.discard(sid)

    for i, sub in enumerate(submissions):
        sid = sub['id']
        if sid in GRADING_QUEUE:
            continue
        GRADING_QUEUE.add(sid)
        GLOBAL_EXECUTOR.submit(process_submission, i, sub)

# --- Command Handlers ---

def run_loop_body(lecture_id: int, assignment_id: int, dry_run: bool, force: bool, sb: Optional[SnowBoard] = None):
    """Core logic for one iteration of monitor/oneshot."""
    try:
        # Determine filter: force -> 'submitted', else 'requiregrading'
        filter_target = 'submitted' if force else 'requiregrading'
        
        logger.debug(f"Processing Assignment {assignment_id} (Lecture {lecture_id}) [Force={force}]")
            
        fresh_urls, lock_urls = run_fetch(lecture_id, assignment_id, filter_status=filter_target, force=force, sb=sb)
        
        # 2. Grade & Upload
        run_grade(str(assignment_id), dry_run=dry_run, force=force, url_map=fresh_urls, lock_map=lock_urls, sb=sb)
    except Exception as e:
        logger.error(f"Error processing assignment {assignment_id}: {e}")

def cmd_evaluate(args):
    try:
        submission_path = Path(args.submission).absolute()
        result = run_evaluate(args.assignment, submission_path, args.student, args.build)
        
        # Output
        print("\n" + "="*40)
        print(f"Total Score: {result.total_score}")
        if result.system_error:
            print(f"SYSTEM ERROR: {result.system_error}")
        print("-" * 40)
        for cf in result.results:
            status = "PASS" if cf.is_correct else "FAIL"
            print(f"Case {cf.test_case_id}: {status}")
            if not cf.is_correct:
                print(f"  Message: {cf.message}")
            if cf.stderr:
                from src.utils.sanitizer import sanitize_traceback
                cl_st = sanitize_traceback(cf.stderr)
                if cl_st:
                    print(f"  Traceback:\n{cl_st}")
        print("="*40 + "\n")
    except Exception as e:
        logger.exception(e)
        sys.exit(1)

def cmd_oneshot(args):
    from rich.status import Status
    """Single run mode."""
    logger.info(f"Starting Oneshot (Dry-Run: {args.dry_run}, Force: {args.force})")
    
    # Unified SnowBoard Instance
    sb = None
    if not args.dry_run:
        try:
            sb = SnowBoard()
        except Exception as e:
             logger.error(f"Failed to login to Snowboard: {e}")
             if not args.dry_run:
                 return # Cannot run without login if not dry run (strictly speaking fetching needs login too)
    else:
        # Check if dry-run still needs fetching? Yes. fetch needs SnowBoard.
        # So dry-run primarily means "Don't Upload" and "Don't Save DB"?
        # Actually fetch needs login anyway.
        try:
             sb = SnowBoard()
        except:
             pass
             
    # Actually, run_fetch WILL initialize its own SB if we pass None.
    # But for oneshot we want unified.
    
    with Status(f"[bold green]Running oneshot for Assign {args.assignment} / Lec {args.lecture}...", spinner="dots"):
        run_loop_body(int(args.lecture), int(args.assignment), args.dry_run, args.force, sb=sb)

def generate_table(monitor_state: dict) -> 'Table':
    from rich.table import Table
    table = Table(title="Monitor Dashboard", expand=True)
    table.add_column("Active", justify="center", style="cyan")
    table.add_column("Lecture")
    table.add_column("Assgn ID", style="magenta")
    table.add_column("Name")
    table.add_column("Last Fetch")
    table.add_column("Uniq Students", justify="right")
    table.add_column("Submissions", justify="right")
    table.add_column("AC Count", justify="right", style="green")

    active_aid = monitor_state.get("active_assignment")
    stats = monitor_state.get("stats", {})
    now_str = monitor_state.get("last_check", "")
    
    table.caption = f"Last Check: {now_str}" if now_str else ""

    for row in monitor_state.get("assignments", []):
        aid = row["aid"]
        lecture_id = row["lecture_id"]
        aname = row["name"]
        
        is_active = "*" if active_aid == aid else ""
        
        astat = stats.get(aid, {})
        uniq = str(astat.get('uniq_students', 0))
        subs = str(astat.get('num_submissions', 0))
        acs = str(astat.get('num_correct', 0))
        last_fetch = astat.get('last_fetched_at', '-')
        
        if last_fetch and len(str(last_fetch)) > 19:
            last_fetch = str(last_fetch)[:19]
            
        table.add_row(is_active, str(lecture_id), str(aid), aname, last_fetch, uniq, subs, acs)
        
    return table

def cmd_status(args):
    from rich.live import Live
    from datetime import datetime
    """Status display mode (read-only monitor loop)."""
    # Disable most logging to avoid drawing warnings over the rich UI
    logging.getLogger().setLevel(logging.ERROR)
    
    monitor_state = {
        "active_assignment": None,
        "assignments": [],
        "stats": {},
        "last_check": ""
    }
    
    with Live(generate_table(monitor_state), refresh_per_second=1) as live:
        while True:
            try:
                conf = load_monitor_config()
                interval = 2 # Fixed fast interval
                lectures = conf.get("lectures", [])
                
                if args.lecture:
                    lectures = [int(args.lecture)]
                
                commited_whitelist = conf.get("whitelist", [])
                raw_blacklist = conf.get("blacklist", [])
                blacklist = [int(x) for x in raw_blacklist]
                whitelist = [int(x) for x in commited_whitelist] if commited_whitelist else []

                now = pd.Timestamp.now()
                now_str = datetime.now().strftime("%H:%M:%S")
                monitor_state["last_check"] = now_str
                
                monitor_state["assignments"] = []
                
                for lecture_id in lectures:
                    try:
                        sql = "SELECT id, name, week_start, week_end FROM assignments WHERE lecture_id = %s"
                        assigns = db.execute_query(sql, (lecture_id,), fetch=True) or []
                        
                        lec_stats = db.get_assignment_stats(lecture_id)
                        monitor_state["stats"].update(lec_stats)
                        
                        for row in assigns:
                            aid = row.get('id')
                            if not aid: continue
                            aid_int = int(aid)
                            aname = row.get('name', 'Unknown')
                            
                            should_process = False
                            week_start_str = row.get('week_start')
                            week_started = True
                            if week_start_str and str(week_start_str) not in ('', 'None', 'NaT'):
                                try:
                                    ws_dt = pd.to_datetime(week_start_str)
                                    if ws_dt > now:
                                        week_started = False
                                except Exception:
                                    pass
                            
                            if args.assignment:
                                if str(args.assignment) == str(aid):
                                    should_process = True
                                else:
                                    continue
                            elif args.lecture: 
                                 if not week_started:
                                     continue
                                 end_date_str = row.get('week_end')
                                 if end_date_str:
                                     try:
                                         end_dt = pd.to_datetime(end_date_str).replace(hour=23, minute=59, second=59)
                                         if end_dt + pd.Timedelta(minutes=5) > now:
                                             should_process = True
                                     except:
                                         pass
                                 else:
                                     should_process = True
                            else:
                                if aid_int in blacklist:
                                    continue
                                if not week_started:
                                    continue
                                if aid_int in whitelist:
                                    should_process = True
                                else:
                                    end_date_str = row.get('week_end')
                                    if end_date_str:
                                        try:
                                            end_dt = pd.to_datetime(end_date_str).replace(hour=23, minute=59, second=59)
                                            if end_dt + pd.Timedelta(minutes=5) > now:
                                                should_process = True
                                        except:
                                            pass
                                    else:
                                        should_process = True
                            
                            if should_process:
                                monitor_state["assignments"].append({
                                    "aid": aid_int,
                                    "lecture_id": lecture_id,
                                    "name": aname
                                })
                                 
                    except Exception as e:
                        pass

                live.update(generate_table(monitor_state))
                time.sleep(interval)
                
            except KeyboardInterrupt:
                break
            except Exception as e:
                time.sleep(2)

def cmd_monitor(args):
    from rich.live import Live
    from datetime import datetime
    """Infinite loop mode."""
    is_daemon = getattr(args, 'daemon', False)
    logger.info(f"Starting Monitor (Dry-Run: {args.dry_run}, Force: {args.force}, Daemon: {is_daemon})")
    push("채점기 시작했습니다.")
    monitor_state = {
        "active_assignment": None,
        "assignments": [],
        "stats": {},
        "last_check": ""
    }
    
    class DummyLive:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, exc_type, exc_val, exc_tb): pass
        def update(self, *args, **kwargs): pass

    if is_daemon:
        live_ctx = DummyLive()
    else:
        live_ctx = Live(generate_table(monitor_state), refresh_per_second=1)
    
    with live_ctx as live:
        while True:
            try:
                # Reload Config
                conf = load_monitor_config()
                interval = conf.get("refresh_interval", 60)
                lectures = conf.get("lectures", [])
                
                # Explicit overrides
                if args.lecture:
                    lectures = [int(args.lecture)] # Just check this lecture
                
                commited_whitelist = conf.get("whitelist", [])
                raw_blacklist = conf.get("blacklist", [])
                blacklist = [int(x) for x in raw_blacklist]
                whitelist = [int(x) for x in commited_whitelist] if commited_whitelist else []

                # Unified SnowBoard for this iteration
                sb = SnowBoard()
                now = pd.Timestamp.now()
                now_str = datetime.now().strftime("%H:%M:%S")
                monitor_state["last_check"] = now_str
                
                monitor_state["assignments"] = []
                
                for lecture_id in lectures:
                    logger.debug(f"Checking Lecture {lecture_id}...")
                    try:
                        df = sb.list_assignments(str(lecture_id))
                        if df.empty:
                            continue
                            
                        lec_stats = db.get_assignment_stats(lecture_id)
                        monitor_state["stats"].update(lec_stats)
                        
                        for _, row in df.iterrows():
                            aid = row.get('id_assignment')
                            if not aid: continue
                            aid_int = int(aid)
                            aname = row.get('과제', 'Unknown')
                            
                            should_process = False
                            
                            week_start_str = row.get('week_start')
                            week_started = True
                            if week_start_str and str(week_start_str) not in ('', 'None', 'NaT'):
                                try:
                                    ws_dt = pd.to_datetime(week_start_str)
                                    if ws_dt > now:
                                        week_started = False
                                except Exception:
                                    pass
                            
                            if args.assignment:
                                if str(args.assignment) == str(aid):
                                    should_process = True
                                else:
                                    continue
                            
                            elif args.lecture: 
                                 if not week_started:
                                     continue
                                 end_date_str = row.get('종료 일시', '-')
                                 if end_date_str and end_date_str != '-':
                                     try:
                                         end_dt = pd.to_datetime(end_date_str)
                                         if end_dt + pd.Timedelta(minutes=5) > now:
                                             should_process = True
                                     except:
                                         pass
                                 else:
                                     should_process = True
                            else:
                                if aid_int in blacklist:
                                    continue
                                
                                if not week_started:
                                    continue
                                    
                                if aid_int in whitelist:
                                    should_process = True
                                else:
                                    end_date_str = row.get('종료 일시', '-')
                                    if end_date_str and end_date_str != '-':
                                        try:
                                            end_dt = pd.to_datetime(end_date_str)
                                            if end_dt + pd.Timedelta(minutes=5) > now:
                                                should_process = True
                                        except:
                                            pass
                            
                            if should_process:
                                monitor_state["assignments"].append({
                                    "aid": aid_int,
                                    "lecture_id": lecture_id,
                                    "name": aname
                                })
                                 
                    except Exception as e:
                        logger.error(f"Error checking lecture {lecture_id}: {e}")

                live.update(generate_table(monitor_state))
                
                for task in monitor_state["assignments"]:
                    monitor_state["active_assignment"] = task["aid"]
                    live.update(generate_table(monitor_state))
                    
                    run_loop_body(task["lecture_id"], task["aid"], args.dry_run, args.force, sb=sb)
                    
                    new_stats = db.get_assignment_stats(task["lecture_id"])
                    monitor_state["stats"].update(new_stats)
                    live.update(generate_table(monitor_state))

                monitor_state["active_assignment"] = None
                monitor_state["last_check"] = f"Sleeping until { (now + pd.Timedelta(seconds=interval)).strftime('%H:%M:%S') }"
                live.update(generate_table(monitor_state))
                
                logger.debug(f"Sleeping {interval}s...")
                time.sleep(interval)
                
            except KeyboardInterrupt:
                logger.info("Halting.")
                push("채점기 종료되었습니다.")
                break
            except Exception as e:
                logger.error(f"Monitor Loop Error: {e}")
                push(f"모니터 루프 에러 발생! {e}")
                time.sleep(10)

def main():
    def handle_sigterm(signum, frame):
        raise KeyboardInterrupt()
    
    signal.signal(signal.SIGTERM, handle_sigterm)

    parser = argparse.ArgumentParser(description="Python Judge System CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # eval
    p_eval = subparsers.add_parser("eval", help="Evaluate local file")
    p_eval.add_argument("--assignment", required=True)
    p_eval.add_argument("--submission", required=True)
    p_eval.add_argument("--student", default="test_student")
    p_eval.add_argument("--build", action="store_true")
    p_eval.set_defaults(func=cmd_evaluate)
    
    # oneshot
    p_one = subparsers.add_parser("oneshot", help="Single run fetch & grade")
    p_one.add_argument("--lecture", required=True)
    p_one.add_argument("--assignment", required=True)
    p_one.add_argument("--dry-run", action="store_true", help="Do not update DB")
    p_one.add_argument("--force", action="store_true", help="Force active, fetch all history")
    p_one.set_defaults(func=cmd_oneshot)
    
    # monitor
    p_mon = subparsers.add_parser("monitor", help="Continuous monitoring loop")
    p_mon.add_argument("--lecture", help="Override Lecture ID")
    p_mon.add_argument("--assignment", help="Override Assignment ID")
    p_mon.add_argument("--dry-run", action="store_true", help="Do not update DB scores")
    p_mon.add_argument("--force", action="store_true", help="Force active, fetch all history")
    p_mon.add_argument("--daemon", action="store_true", help="Run in daemon mode without Rich UI")
    p_mon.set_defaults(func=cmd_monitor)
    
    # status
    p_stat = subparsers.add_parser("status", help="Read-only status dashboard")
    p_stat.add_argument("--lecture", help="Override Lecture ID")
    p_stat.add_argument("--assignment", help="Override Assignment ID")
    p_stat.set_defaults(func=cmd_status)
    
    args = parser.parse_args()
    args.func(args)
    GLOBAL_EXECUTOR.shutdown(wait=True)

if __name__ == "__main__":
    main()

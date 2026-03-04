import argparse
import sys
import yaml
import logging
import time
import tempfile
import pandas as pd
from pathlib import Path
from typing import Optional, List, Dict
from dotenv import load_dotenv

# Load Env
load_dotenv()

# Fix Import Path (Allow running as `python src/main.py`)
sys.path.append(str(Path(__file__).parent.parent))

# Core & Models
from src.models.schema import AssignmentConfig, EvaluationResult
from src.core.sandbox import DockerSandbox
from src.core.standard_judge import StandardJudge
from src.core.special_judge import SpecialJudge

# Infrastructure
from src.infrastructure.snowboard import SnowBoard
from src.infrastructure import database as db
from src.infrastructure.telegram import push

# Utils
from src.utils.file_validator import validate_submission

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
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

# --- Reusable Logic ---

def run_evaluate(
    assignment_id: str, 
    submission_path: Path, 
    student_id: str = "test_student",
    build: bool = False
):
    """Run single file evaluation (No DB)."""
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
    
    logger.info(f"Fetching assignments for lecture {lecture_id}...")
    df_assign = sb.list_assignments(str(lecture_id))
    if df_assign.empty:
        logger.warning("No assignments found.")
        return

    db.ensure_lecture(lecture_id, f"Lecture {lecture_id}")
    
    assignments = {}
    for _, row in df_assign.iterrows():
        aid = row['id_assignment']
        aname = row['과제']
        
        if not aid: continue
         
        # Filter by assignment if requested
        if assignment_id and str(assignment_id) != str(aid):
            continue
        
        assignments[int(aid)] = {'title': aname}
        db.ensure_assignment(int(aid), lecture_id, aname)

    fresh_urls = {} # Transient storage for grade_urls  
    lock_urls = {}  # Transient storage for 제출변경방지href (submission lock)
    for aid, item in assignments.items():
        logger.info(f"Fetching submissions for {item.get('title')} ({aid}) [Filter: {filter_status}]...")
        
        # 1. List Submissions
        df = sb.list_submissions(aid, filter_status=filter_status)
        db.ensure_assignment(int(aid), lecture_id, item.get('title'))
        
        if df.empty:
            logger.info("  No submissions found.")
            continue

        total = len(df)
        logger.info(f"Found {total} submissions.")
        count = 0
        
        for _, row in df.iterrows():
            sid = str(row['학번'])
            sname = row.get('이름', 'Unknown')
            
            db.ensure_student(sid, sname, lecture_id)
            
            href = row.get('첨부파일href') # File download link
            grade_url = row.get('성적버튼href') # Grading page link
            lock_url = row.get('제출변경방지href') # Submission lock link
            ts = row.get('최근 제출일', '') # Timestamp string
            max_score_val = float(row.get('max_score', 100.0))
            
            # Store transient URLs for immediate grading
            if grade_url:
                fresh_urls[sid] = grade_url
            if lock_url:
                lock_urls[sid] = lock_url

            # Deduplication Logic
            # 1. If filter_status == 'requiregrading', ALWAYS fetch (Trust Snowboard)
            # 2. If force=True, ALWAYS fetch (We want to regrade/refresh)
            # 3. Otherwise, check timestamp to avoid redundant fetch
            
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
                # logger.debug(f"Skipping {sname} ({sid}): Unchanged")
                continue

            try:
                # Check for missing attachment
                if not href:
                    logger.info(f"  {sname} ({sid}): No attachment — recording as score 0.")
                    fetched_at = time.strftime('%Y-%m-%d %H:%M:%S')
                    # Record empty submission
                    empty_md5 = db.record_file(b"")
                    db.record_submission(
                        assignment_id=int(aid),
                        student_id=sid,
                        file_md5=empty_md5,
                        submitted_at=ts,
                        fetched_at=fetched_at,
                        score=0.0,
                        verdict="WA",
                        comment="오답입니다. 제출물에 파일이 첨부되지 않았습니다.",
                        is_force=force,
                        max_score=max_score_val
                    )
                    # Upload score 0 to Snowboard
                    if grade_url:
                        try:
                            sb.submit_score(grade_url, 0.0, "오답입니다. 제출물에 파일이 첨부되지 않았습니다.")
                        except Exception as e:
                            logger.error(f"  Upload Error: {e}")
                    count += 1
                    continue

                # Just fetch.
                content = sb.fetch_submission(href)
                md5 = db.record_file(content)
                
                fetched_at = time.strftime('%Y-%m-%d %H:%M:%S')
                
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
    return fresh_urls, lock_urls

def run_grade(assignment_id: str, dry_run: bool = False, force: bool = False, url_map: Dict[str, str] = None, lock_map: Dict[str, str] = None, sb: Optional[SnowBoard] = None):
    """Grade ungraded submissions from DB. If force=True, grade ALL."""
    
    # Instantiate Snowboard only if we might upload (and wasn't passed)
    if not dry_run and sb is None:
        try:
            sb = SnowBoard()
        except Exception as e:
            logger.warning(f"Snowboard login failed in grade step: {e}. Uploads might fail.")

    # 1. Config & Engine
    try:
        config = load_config(assignment_id)
    except FileNotFoundError:
        logger.error(f"Config for {assignment_id} not found. Skipping grading.")
        return

    assignment_dir = Path(f"assignments/{assignment_id}").absolute()
    
    # Ensure Image
    DockerSandbox.build_image(config)
    
    if config.type == "standard":
        engine = StandardJudge(config)
    elif config.type == "special":
        engine = SpecialJudge(config)
    else:
        logger.error(f"Unknown type: {config.type}")
        return

    # 2. Fetch Ungraded (or All if force)
    submissions = db.get_ungraded_submissions(int(assignment_id), limit=100, force=force)
    
    if not submissions:
        return

    total = len(submissions)
    logger.info(f"Found {total} submissions to grade for {assignment_id} (Force: {force}).")
    
    count = 0
    for i, sub in enumerate(submissions):
        sid = sub['id'] # submission ID from DB
        student_id = sub['student_id'] # student ID
        md5 = sub['file_md5']
        
        # Override Grade URL if fresh
        grade_url = None # Legacy, usually None now
        if url_map and student_id in url_map:
            grade_url = url_map[student_id]
            
        # Get Max Score from sub (default 100.0)
        max_score = float(sub.get('max_score', 100.0))

        logger.info(f"Grading #{i+1}/{total} (Submission ID: {sid}, Student ID: {student_id}, Max: {max_score})...")
        
        try:
            content = db.get_file_content(md5)
            
            # --- Inline File Validation (pre-evaluation) ---
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
                continue
            
            with tempfile.NamedTemporaryFile(suffix=".py", delete=True) as tmp:
                tmp.write(content)
                tmp.flush()
                
                # Look up student name for Docker env
                student_name = db.get_student_name(student_id) or ""
                
                result = engine.evaluate(
                    Path(tmp.name), 
                    assignment_dir, 
                    student_info={"student_id": student_id, "student_name": student_name}
                )
                
                # Verdict Logic
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
                
                # Calculate Comment & Failure Details
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
                             # as per user request to show logs only for exceptions.
                             raw_error = (res.stderr or "").strip()
                             
                             if raw_error:
                                 from src.utils.sanitizer import sanitize_traceback
                                 clean_trace = sanitize_traceback(raw_error)
                                 
                                 # Limit length to avoid massive comments
                                 if len(clean_trace) > 1000:
                                     clean_trace = clean_trace[:1000] + "\n... (Truncated)"
                                     
                                 if clean_trace:
                                     comment += f"\nError Logs:\n<pre>\n{clean_trace}\n</pre>\n"
                                     break 
                
                if result.system_error:
                    comment += f" (System Error: {result.system_error})"
                
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
                    
                    # Upload to Snowboard
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
                        except Exception as e:
                            logger.error(f"  Upload Error: {e}")
                    elif not grade_url:
                        logger.warning("  No grade_url found for submission. Cannot upload.")
                    
        except Exception as e:
            logger.error(f"Grading failed for {sid}: {e}")
            push(f"채점 오류 발생! Submission {sid}: {e}")
            if not dry_run:
                db.update_submission_result(sid, 0.0, "SYS", f"Judge Error: {e}")

# --- Command Handlers ---

def run_loop_body(lecture_id: int, assignment_id: int, dry_run: bool, force: bool, sb: Optional[SnowBoard] = None):
    """Core logic for one iteration of monitor/oneshot."""
    try:
        # Determine filter: force -> 'submitted', else 'requiregrading'
        filter_target = 'submitted' if force else 'requiregrading'
        
        logger.info(f"Processing Assignment {assignment_id} (Lecture {lecture_id}) [Force={force}]")
            
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
        print("="*40 + "\n")
    except Exception as e:
        logger.exception(e)
        sys.exit(1)

def cmd_oneshot(args):
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
    
    run_loop_body(int(args.lecture), int(args.assignment), args.dry_run, args.force, sb=sb)

def cmd_monitor(args):
    """Infinite loop mode."""
    logger.info(f"Starting Monitor (Dry-Run: {args.dry_run}, Force: {args.force})")
    push("채점기 시작했습니다.")
    
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
            
            for lecture_id in lectures:
                logger.debug(f"Checking Lecture {lecture_id}...")
                try:
                    df = sb.list_assignments(str(lecture_id))
                    if df.empty:
                        continue
                    
                    for _, row in df.iterrows():
                        aid = row.get('id_assignment')
                        if not aid: continue
                        aid_int = int(aid)
                        
                        # Decision Logic
                        should_process = False
                        
                        # 1. CLI Override (Manual Selection)
                        if args.assignment:
                            if str(args.assignment) == str(aid):
                                should_process = True
                            else:
                                continue # Skip unrelated assignments if filtering by ID
                        
                        elif args.lecture: 
                             # Check date for safety?
                             end_date_str = row.get('종료 일시', '-')
                             if end_date_str and end_date_str != '-':
                                 try:
                                     end_dt = pd.to_datetime(end_date_str)
                                     if end_dt > now:
                                         should_process = True
                                 except:
                                     pass
                             else:
                                 should_process = True # No end date
                             
                        else:
                            # Standard Monitor (No overrides)
                            if aid_int in blacklist:
                                continue
                                
                            if aid_int in whitelist:
                                should_process = True
                            else:
                                # Date check
                                end_date_str = row.get('종료 일시', '-')
                                if end_date_str and end_date_str != '-':
                                    try:
                                        end_dt = pd.to_datetime(end_date_str)
                                        if end_dt > now:
                                            should_process = True
                                    except:
                                        pass
                        
                        if should_process:
                             # Pass unified SB
                             run_loop_body(lecture_id, int(aid), args.dry_run, args.force, sb=sb)
                             
                except Exception as e:
                    logger.error(f"Error checking lecture {lecture_id}: {e}")

            logger.info(f"Sleeping {interval}s...")
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
    p_mon.set_defaults(func=cmd_monitor)
    
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()

import os
import pymysql
import logging
from pymysql import cursors
from pathlib import Path
from src.utils.hash import calculate_md5
from typing import Optional, Union, Tuple, Dict, Any, List

logger = logging.getLogger(__name__)

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "PythonJudgeSystem")
DB_PORT = int(os.environ.get("DB_PORT", 3306))

def get_connection():
    return pymysql.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        port=DB_PORT,
        cursorclass=cursors.DictCursor,
        autocommit=False
    )

def execute_query(sql: str, args: tuple = (), commit: bool = False, fetch: bool = False, trycount: int = 3):
    last_error = None
    for i in range(trycount):
        conn = None
        try:
            conn = get_connection()
            with conn.cursor() as cursor:
                cursor.execute(sql, args)
                if commit:
                    conn.commit()
                
                if fetch:
                    return cursor.fetchall()
                return cursor.rowcount
                
        except pymysql.err.OperationalError as e:
            last_error = e
            logger.warning(f"Database error {e}, retrying...")
            continue
        finally:
            if conn:
                conn.close()
    
    if last_error:
        raise last_error

# --- Entity Management ---

def ensure_lecture(lecture_id: int, name: str):
    """Upsert lecture."""
    sql = """
    INSERT IGNORE INTO lectures (id, name) VALUES (%s, %s)
    """
    execute_query(sql, (lecture_id, name), commit=True)

def update_lecture_fetch_time(lecture_id: int):
    """Update last_fetched_at to NOW() for the given lecture."""
    sql = "UPDATE lectures SET last_fetched_at = NOW() WHERE id = %s"
    execute_query(sql, (lecture_id,), commit=True)

def get_lecture_fetch_time(lecture_id: int):
    """Get last_fetched_at for the given lecture. Returns datetime or None."""
    sql = "SELECT last_fetched_at FROM lectures WHERE id = %s"
    rows = execute_query(sql, (lecture_id,))
    if rows and rows[0]['last_fetched_at']:
        return rows[0]['last_fetched_at']
    return None

def ensure_student(student_id: str, name: str, lecture_id: int):
    """Upsert student and enrollment."""
    sql_student = """
    INSERT INTO students (student_id, name) VALUES (%s, %s)
    ON DUPLICATE KEY UPDATE name = VALUES(name)
    """
    execute_query(sql_student, (student_id, name), commit=True)

    sql_enroll = """
    INSERT IGNORE INTO enrollments (lecture_id, student_id) VALUES (%s, %s)
    """
    execute_query(sql_enroll, (lecture_id, student_id), commit=True)

def ensure_assignment(assignment_id: int, lecture_id: int, name: str, week_start: Optional[str] = None, week_end: Optional[str] = None):
    """Upsert assignment."""
    sql = """
    INSERT INTO assignments (id, lecture_id, name, week_start, week_end) 
    VALUES (%s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE 
        name = VALUES(name), 
        lecture_id = VALUES(lecture_id),
        week_start = COALESCE(VALUES(week_start), week_start),
        week_end = COALESCE(VALUES(week_end), week_end)
    """
    execute_query(sql, (assignment_id, lecture_id, name, week_start, week_end), commit=True)

def update_assignment_fetch_time(assignment_id: int):
    """Update last_fetched_at to NOW() for the given assignment."""
    sql = "UPDATE assignments SET last_fetched_at = NOW() WHERE id = %s"
    execute_query(sql, (assignment_id,), commit=True)

def get_assignment_fetch_time(assignment_id: int):
    """Get last_fetched_at for the given assignment. Returns datetime or None."""
    sql = "SELECT last_fetched_at FROM assignments WHERE id = %s"
    rows = execute_query(sql, (assignment_id,))
    if rows and rows[0]['last_fetched_at']:
        return rows[0]['last_fetched_at']
    return None

def record_file(file: Union[bytes, str, Path]) -> str:
    """Store file content and return MD5."""
    if isinstance(file, Path):
        file = file.read_bytes()
    elif isinstance(file, str):
        file = file.encode()
    
    md5hash = calculate_md5(file)
    sql = 'INSERT IGNORE INTO files(md5, content) VALUES(%s, %s)'
    execute_query(sql, (md5hash, file), commit=True)
    return md5hash

def record_submission(
        assignment_id: int,
        student_id: str,
        file_md5: str,
        submitted_at: str,
        fetched_at: str,
        score: Optional[float] = None,
        verdict: Optional[str] = None,
        comment: Optional[str] = None,
        is_latest: bool = True,
        is_force: bool = False,
        max_score: float = 100.0
):
    """Record a submission."""
    if is_latest:
        sql_update = """
        UPDATE submissions
        SET is_latest = 0
        WHERE assignment_id = %s AND student_id = %s
        """
        execute_query(sql_update, (assignment_id, student_id), commit=True)

    sql = """
    INSERT INTO submissions (
        assignment_id, student_id, file_md5, submitted_at, fetched_at, 
        score, verdict, comment, is_latest, is_force, max_score
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """
    rows = execute_query(sql, (
        assignment_id, student_id, file_md5, submitted_at, fetched_at,
        score, verdict, comment, is_latest, is_force, max_score
    ), commit=True)
    
    return rows

def get_submission_count(assignment_id: int, student_id: str) -> int:
    sql = """
    SELECT COUNT(*) as count FROM submissions 
    WHERE assignment_id = %s AND student_id = %s
    """
    rows = execute_query(sql, (assignment_id, student_id), fetch=True)
    return rows[0]['count'] if rows else 0

def get_last_submission_time(assignment_id: int, student_id: str) -> Optional[str]:
    """Get the submitted_at timestamp of the latest submission."""
    sql = """
    SELECT submitted_at FROM submissions 
    WHERE assignment_id = %s AND student_id = %s
    ORDER BY submitted_at DESC LIMIT 1
    """
    rows = execute_query(sql, (assignment_id, student_id), fetch=True)
    return rows[0]['submitted_at'] if rows else None

def get_code_similarity(md5_a: str, md5_b: str) -> Tuple[float, float]:
    if md5_a > md5_b:
        md5_a, md5_b = md5_b, md5_a

    sql = """SELECT similarity, similarity_normalized
             FROM code_similarity
             WHERE md5_a = %s AND md5_b = %s"""

    rows = execute_query(sql, (md5_a, md5_b), fetch=True)
    if not rows:
        return -1.0, -1.0

    return rows[0]['similarity'], rows[0]['similarity_normalized']

def record_code_similarity(
        md5_a: str, md5_b: str,
        similarity: float, similarity_normalized: float):
    if md5_a > md5_b:
        md5_a, md5_b = md5_b, md5_a
        
    sql = """
    INSERT IGNORE INTO code_similarity (md5_a, md5_b, similarity, similarity_normalized) 
    VALUES (%s, %s, %s, %s)
    """
    return execute_query(sql, (md5_a, md5_b, similarity, similarity_normalized), commit=True)

def get_student_name(student_id: str) -> Optional[str]:
    """Look up student name by ID."""
    sql = "SELECT name FROM students WHERE student_id = %s"
    rows = execute_query(sql, (student_id,), fetch=True)
    return rows[0]['name'] if rows else None

def get_student_info(student_id: str) -> Optional[dict]:
    """Look up student name and department by ID."""
    sql = "SELECT name, department FROM students WHERE student_id = %s"
    rows = execute_query(sql, (student_id,), fetch=True)
    return rows[0] if rows else None

# --- Grading Support ---

def get_ungraded_submissions(assignment_id: int, limit: int = 100, force: bool = False) -> List[Dict]:
    """
    Fetch submissions to grade. 
    Strict Rule: Only fetch ACTIVE submissions (is_latest=1).
    If force=True, fetch even if already scored.
    """
    if force:
        sql = """
        SELECT * FROM submissions 
        WHERE assignment_id = %s AND is_latest = 1
        ORDER BY submitted_at ASC
        LIMIT %s
        """
    else:
        sql = """
        SELECT * FROM submissions 
        WHERE assignment_id = %s AND score IS NULL AND is_latest = 1
        ORDER BY submitted_at ASC
        LIMIT %s
        """
    return execute_query(sql, (assignment_id, limit), fetch=True)

def get_assignment_name(assignment_id: int) -> Optional[str]:
    rows = execute_query("SELECT name FROM assignments WHERE id = %s", (assignment_id,), fetch=True)
    return rows[0]['name'] if rows else None

def get_file_content(md5: str) -> bytes:
    """Fetch file content by MD5."""
    sql = "SELECT content FROM files WHERE md5 = %s"
    rows = execute_query(sql, (md5,), fetch=True)
    if rows:
        return rows[0]['content']
    raise FileNotFoundError(f"File content for {md5} not found in DB.")

def update_submission_result(
    submission_id: int, 
    score: float, 
    verdict: str, 
    comment: str,
    failure_details: Optional[str] = None
):
    """Update submission with grading result."""
    sql = """
    UPDATE submissions 
    SET score = %s, verdict = %s, comment = %s, failure_details = %s
    WHERE id = %s
    """
    execute_query(sql, (score, verdict, comment, failure_details, submission_id), commit=True)

def get_assignment_stats(lecture_id: int) -> dict:
    """
    Returns a dict mapping assignment_id to stats for the dashboard:
    {
      assignment_id: {
        'uniq_students': int,
        'num_submissions': int,
        'num_correct': int,
        'last_fetched_at': str
      }
    }
    """
    sql = """
    SELECT 
        s.assignment_id,
        COUNT(DISTINCT CASE WHEN s.is_latest = 1 THEN s.student_id END) as uniq_students,
        COUNT(s.id) as num_submissions,
        SUM(CASE WHEN s.is_latest = 1 AND s.verdict = 'AC' THEN 1 ELSE 0 END) as num_correct,
        MAX(a.last_fetched_at) as last_fetched_at
    FROM submissions s
    JOIN assignments a ON s.assignment_id = a.id
    WHERE a.lecture_id = %s
    GROUP BY s.assignment_id
    """
    rows = execute_query(sql, (lecture_id,), fetch=True)

    # 제출이 0건인 assignment도 결과에 포함시키기 위한 보조 쿼리.
    sql_all = "SELECT id, last_fetched_at FROM assignments WHERE lecture_id = %s"
    all_assigns = execute_query(sql_all, (lecture_id,), fetch=True)
    
    stats = {}
    for a in all_assigns:
        aid = a['id']
        stats[aid] = {
            'uniq_students': 0,
            'num_submissions': 0,
            'num_correct': 0,
            'last_fetched_at': str(a.get('last_fetched_at') or '')
        }
        
    for r in rows:
        aid = r['assignment_id']
        stats[aid] = {
            'uniq_students': r['uniq_students'],
            'num_submissions': r['num_submissions'],
            'num_correct': int(r['num_correct'] or 0),
            'last_fetched_at': str(r.get('last_fetched_at') or stats[aid]['last_fetched_at'])
        }
        
    return stats

def get_cdf_data(assignment_id: int) -> list[dict]:
    """
    Returns data for the CDF graph (Unique students submitted over time).
    Finds the earliest submission time for each student, and returns a chronologically sorted list.
    """
    sql = """
    SELECT
        student_id,
        MIN(submitted_at) as first_submission
    FROM submissions
    WHERE assignment_id = %s
    GROUP BY student_id
    ORDER BY first_submission ASC
    """
    rows = execute_query(sql, (assignment_id,), fetch=True)
    return rows

# --- Midterm Exam Live Dashboard ---

def get_exam_problem_stats(assignment_id: int) -> dict:
    """
    단일 문항에 대한 실시간 통계. uniq_students는 `is_latest=1` 기준,
    ac_count도 `is_latest=1 AND verdict='AC'` 기준이라 재제출로 AC를 깨면
    정답자 수가 감소할 수 있다.
    last_fetched_at 은 assignments 테이블의 값(크롤러의 가장 최근 fetch 시점).
    """
    sql = """
    SELECT
        COUNT(DISTINCT CASE WHEN s.is_latest = 1 THEN s.student_id END) AS submitters,
        COUNT(s.id) AS total_submissions,
        SUM(CASE WHEN s.is_latest = 1 AND s.verdict = 'AC' THEN 1 ELSE 0 END) AS ac_count,
        a.last_fetched_at AS last_fetched_at
    FROM assignments a
    LEFT JOIN submissions s ON s.assignment_id = a.id
    WHERE a.id = %s
    GROUP BY a.id
    """
    rows = execute_query(sql, (assignment_id,), fetch=True)
    if not rows:
        return {"submitters": 0, "total_submissions": 0, "ac_count": 0,
                "correct_rate": 0.0, "last_fetched_at": None}
    r = rows[0]
    submitters = int(r["submitters"] or 0)
    total = int(r["total_submissions"] or 0)
    ac = int(r["ac_count"] or 0)
    rate = (ac / submitters) if submitters else 0.0
    return {
        "submitters": submitters,
        "total_submissions": total,
        "ac_count": ac,
        "correct_rate": rate,
        "last_fetched_at": str(r["last_fetched_at"]) if r.get("last_fetched_at") else None,
    }

def get_exam_recent_submissions(assignment_id: int, limit: int = 5) -> list[dict]:
    """
    최근 제출 N건. is_latest 무관 — 재제출 직전 시도도 몇 초간 보이도록.
    student_id를 그대로 돌려주므로 라우터에서 반드시 익명화할 것.
    """
    sql = """
    SELECT student_id, verdict, score, max_score, submitted_at
    FROM submissions
    WHERE assignment_id = %s
    ORDER BY submitted_at DESC, id DESC
    LIMIT %s
    """
    return execute_query(sql, (assignment_id, limit), fetch=True) or []

def get_exam_first_ac_cdf(assignment_id: int) -> list[dict]:
    """
    학생별 '최초 AC' 시각을 오름차순으로 반환. student_id는 집계 목적으로만
    조회되며 호출측에서 즉시 버리고 (timestamp, cumulative_count) 형태로 쓴다.
    """
    sql = """
    SELECT MIN(submitted_at) AS first_ac
    FROM submissions
    WHERE assignment_id = %s AND verdict = 'AC'
    GROUP BY student_id
    ORDER BY first_ac ASC
    """
    return execute_query(sql, (assignment_id,), fetch=True) or []

# --- Admin Dashboard Support ---

def get_admin_lecture_students(lecture_id: int) -> List[Dict]:
    """
    Fetch all students in a lecture, along with their assignment summary.
    Summary is a list of dicts: {'assignment_id': int, 'name': str, 'verdict': str, 'attempts': int}
    """
    sql_students = """
    SELECT s.student_id, s.name, s.department
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    WHERE e.lecture_id = %s
    ORDER BY s.student_id
    """
    students = execute_query(sql_students, (lecture_id,), fetch=True) or []

    sql_assignments = """
    SELECT id as assignment_id, name
    FROM assignments
    WHERE lecture_id = %s
    ORDER BY id ASC
    """
    assignments = execute_query(sql_assignments, (lecture_id,), fetch=True) or []

    sql_submissions = """
    SELECT s.student_id, s.assignment_id, s.verdict, 
           (SELECT COUNT(*) FROM submissions sub 
            WHERE sub.assignment_id = s.assignment_id AND sub.student_id = s.student_id) as attempts
    FROM submissions s
    JOIN assignments a ON s.assignment_id = a.id
    WHERE a.lecture_id = %s AND s.is_latest = 1
    """
    subs = execute_query(sql_submissions, (lecture_id,), fetch=True) or []

    sub_map = {}
    for sub in subs:
        if sub['student_id'] not in sub_map:
            sub_map[sub['student_id']] = {}
        sub_map[sub['student_id']][sub['assignment_id']] = sub

    for student in students:
        sid = student['student_id']
        student['assignments'] = []
        for a in assignments:
            aid = a['assignment_id']
            student_sub = sub_map.get(sid, {}).get(aid, None)
            
            if student_sub:
                student['assignments'].append({
                    'assignment_id': aid,
                    'name': a['name'],
                    'verdict': student_sub['verdict'],
                    'attempts': student_sub['attempts']
                })
            else:
                 student['assignments'].append({
                    'assignment_id': aid,
                    'name': a['name'],
                    'verdict': None,
                    'attempts': 0
                })
                
    return students

def get_student_submission_history(assignment_id: int, student_id: str) -> List[Dict]:
    """
    Fetch all attempts for a specific student and assignment, including code and output details.
    """
    sql = """
    SELECT s.id, s.submitted_at, s.score, s.max_score, s.verdict, s.failure_details, f.content as code
    FROM submissions s
    JOIN files f ON s.file_md5 = f.md5
    WHERE s.assignment_id = %s AND s.student_id = %s
    ORDER BY s.submitted_at DESC
    """
    rows = execute_query(sql, (assignment_id, student_id), fetch=True) or []

    for r in rows:
        if isinstance(r.get('code'), (bytes, bytearray)):
            try:
                r['code'] = r['code'].decode('utf-8')
            except UnicodeDecodeError:
                try:
                    r['code'] = r['code'].decode('euc-kr')
                except Exception:
                    r['code'] = "<Binary Data or Decode Error>"
        elif r.get('code') is not None:
            r['code'] = str(r['code'])

        if isinstance(r.get('failure_details'), (bytes, bytearray)):
            r['failure_details'] = r['failure_details'].decode('utf-8')

    return rows

def get_student_photo(student_id: str) -> Optional[bytes]:
    """Fetch the raw MEDIUMBLOB photo for a student."""
    sql = "SELECT photo FROM students WHERE student_id = %s"
    rows = execute_query(sql, (student_id,), fetch=True)
    if rows and rows[0]['photo']:
        return rows[0]['photo']
    return None

def get_student_all_submissions(student_id: str, lecture_id: int) -> List[Dict]:
    """
    Fetch all attempts for a specific student across all assignments in a lecture.
    Used for the Student Summary page.
    """
    sql = """
    SELECT 
        s.id, s.assignment_id, a.name as assignment_name, s.submitted_at, 
        s.score, s.max_score, s.verdict, s.failure_details, f.content as code
    FROM submissions s
    JOIN assignments a ON s.assignment_id = a.id
    JOIN files f ON s.file_md5 = f.md5
    WHERE s.student_id = %s AND a.lecture_id = %s
    ORDER BY s.assignment_id DESC, s.submitted_at DESC
    """
    rows = execute_query(sql, (student_id, lecture_id), fetch=True) or []

    for r in rows:
        if isinstance(r.get('code'), (bytes, bytearray)):
            try:
                r['code'] = r['code'].decode('utf-8')
            except UnicodeDecodeError:
                try:
                    r['code'] = r['code'].decode('euc-kr')
                except Exception:
                    r['code'] = "<Binary Data or Decode Error>"
        elif r.get('code') is not None:
            r['code'] = str(r['code'])
            
        if isinstance(r.get('failure_details'), (bytes, bytearray)):
            r['failure_details'] = r['failure_details'].decode('utf-8')
            
    return rows

def get_ta_accessible_lectures(username: str) -> List[int]:
    """Get the list of lecture IDs that a specific TA is authorized to view."""
    sql = "SELECT lecture_id FROM ta_lecture_access WHERE username = %s"
    rows = execute_query(sql, (username,), fetch=True) or []
    return [row['lecture_id'] for row in rows]

def check_ta_lecture_access(username: str, lecture_id: int) -> bool:
    """Check if a specific TA is authorized to view a specific lecture."""
    sql = "SELECT 1 FROM ta_lecture_access WHERE username = %s AND lecture_id = %s"
    rows = execute_query(sql, (username, lecture_id), fetch=True)
    return bool(rows)

def get_global_feed(limit: int = 50, allowed_lecture_ids: Optional[List[int]] = None) -> List[Dict]:
    """Fetch latest submissions across all assignments globally. Optionally restricted to specific lectures."""
    sql = """
    SELECT 
        s.id, s.student_id, st.name as student_name, st.department,
        a.name as assignment_name, a.lecture_id, l.name as lecture_name, s.assignment_id,
        s.submitted_at, s.score, s.max_score, s.verdict, s.failure_details
    FROM submissions s
    JOIN students st ON s.student_id = st.student_id
    JOIN assignments a ON s.assignment_id = a.id
    JOIN lectures l ON a.lecture_id = l.id
    """
    params = []
    
    if allowed_lecture_ids is not None:
        if len(allowed_lecture_ids) == 0:
            return []
        placeholders = ', '.join(['%s'] * len(allowed_lecture_ids))
        sql += f" WHERE a.lecture_id IN ({placeholders})"
        params.extend(allowed_lecture_ids)

    sql += " ORDER BY s.submitted_at DESC LIMIT %s"
    params.append(limit)

    return execute_query(sql, tuple(params), fetch=True) or []

# --- TA Feature Support ---

def verify_ta_account(username: str, password_plain: str) -> bool:
    """Verify TA account credentials."""
    sql = "SELECT 1 FROM ta_accounts WHERE username = %s AND password_plain = %s"
    rows = execute_query(sql, (username, password_plain), fetch=True)
    return bool(rows)

def log_ta_access(username: str, path: str):
    """Log TA dashboard access."""
    sql = "INSERT INTO ta_access (username, path) VALUES (%s, %s)"
    execute_query(sql, (username, path), commit=True)

# --- Exam Attendance Support ---

def search_students_by_id(prefix: str, lecture_id: int) -> List[Dict]:
    """Search for students in a lecture by ID prefix."""
    sql = """
    SELECT s.student_id, s.name, s.department, s.phone_number
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    WHERE e.lecture_id = %s AND s.student_id LIKE %s
    ORDER BY s.student_id ASC LIMIT 10
    """
    return execute_query(sql, (lecture_id, f"{prefix}%"), fetch=True) or []

def get_student_for_exam(student_id: str, lecture_id: int) -> Optional[Dict]:
    """Get student details for an exam check."""
    sql = """
    SELECT s.student_id, s.name, s.department, s.phone_number
    FROM students s
    JOIN enrollments e ON s.student_id = e.student_id
    WHERE e.lecture_id = %s AND s.student_id = %s
    """
    rows = execute_query(sql, (lecture_id, student_id), fetch=True)
    return rows[0] if rows else None

def get_student_attendance(student_id: str, lecture_id: int, exam_type: str) -> Optional[Dict]:
    """Get check-in/out times for a student."""
    sql = """
    SELECT check_in_time, check_out_time, check_in_by, check_out_by
    FROM exam_attendance
    WHERE student_id = %s AND lecture_id = %s AND exam_type = %s
    """
    rows = execute_query(sql, (student_id, lecture_id, exam_type), fetch=True)
    return rows[0] if rows else None

def log_exam_check_in(student_id: str, lecture_id: int, exam_type: str, username: str) -> bool:
    """Check in a student. Returns False if already checked in."""
    attendance = get_student_attendance(student_id, lecture_id, exam_type)
    if attendance and attendance['check_in_time']:
        return False
        
    sql = """
    INSERT INTO exam_attendance (student_id, lecture_id, exam_type, check_in_time, check_in_by)
    VALUES (%s, %s, %s, NOW(), %s)
    ON DUPLICATE KEY UPDATE 
        check_in_time = COALESCE(check_in_time, NOW()),
        check_in_by = COALESCE(check_in_by, %s)
    """
    execute_query(sql, (student_id, lecture_id, exam_type, username, username), commit=True)
    return True

def log_exam_check_out(student_id: str, lecture_id: int, exam_type: str, username: str) -> bool:
    """Check out a student. Returns False if already checked out."""
    attendance = get_student_attendance(student_id, lecture_id, exam_type)
    if attendance and attendance['check_out_time']:
        return False
        
    sql = """
    UPDATE exam_attendance
    SET check_out_time = NOW(), check_out_by = %s
    WHERE student_id = %s AND lecture_id = %s AND exam_type = %s
    """
    rowcount = execute_query(sql, (username, student_id, lecture_id, exam_type), commit=True)
    return rowcount > 0

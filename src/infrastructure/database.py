import os
import pymysql
import logging
from pymysql import cursors
from pathlib import Path
from src.utils.hash import calculate_md5
from typing import Optional, Union, Tuple, Dict, Any, List

# Configure logging
logger = logging.getLogger(__name__)

# Constants (Defaults can be overridden by env)
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
    INSERT INTO lectures (id, name) VALUES (%s, %s)
    ON DUPLICATE KEY UPDATE name = VALUES(name)
    """
    execute_query(sql, (lecture_id, name), commit=True)

def ensure_student(student_id: str, name: str, lecture_id: int):
    """Upsert student and enrollment."""
    # 1. Ensure Student
    sql_student = """
    INSERT INTO students (student_id, name) VALUES (%s, %s)
    ON DUPLICATE KEY UPDATE name = VALUES(name)
    """
    execute_query(sql_student, (student_id, name), commit=True)
    
    # 2. Ensure Enrollment
    sql_enroll = """
    INSERT IGNORE INTO enrollments (lecture_id, student_id) VALUES (%s, %s)
    """
    execute_query(sql_enroll, (lecture_id, student_id), commit=True)

def ensure_assignment(assignment_id: int, lecture_id: int, name: str):
    """Upsert assignment."""
    sql = """
    INSERT INTO assignments (id, lecture_id, name) VALUES (%s, %s, %s)
    ON DUPLICATE KEY UPDATE name = VALUES(name), lecture_id = VALUES(lecture_id)
    """
    execute_query(sql, (assignment_id, lecture_id, name), commit=True)

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
    # 1. If this is marked as latest, unset previous latest for this student/assignment
    if is_latest:
        sql_update = """
        UPDATE submissions 
        SET is_latest = 0 
        WHERE assignment_id = %s AND student_id = %s
        """
        execute_query(sql_update, (assignment_id, student_id), commit=True)
    
    # 2. Insert new submission
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

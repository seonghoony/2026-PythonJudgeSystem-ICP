import os
import pymysql
import logging
from typing import List

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv()

DB_HOST = os.environ.get("DB_HOST", "localhost")
DB_USER = os.environ.get("DB_USER", "root")
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_NAME = os.environ.get("DB_NAME", "PythonJudgeSystem")

DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS lectures (
        id BIGINT PRIMARY KEY COMMENT 'Snowboard Lecture ID',
        name VARCHAR(255) NOT NULL,
        last_fetched_at DATETIME DEFAULT NULL COMMENT 'Last time submissions were fetched'
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS students (
        student_id VARCHAR(50) PRIMARY KEY COMMENT 'University Student ID',
        name VARCHAR(100) NOT NULL,
        department VARCHAR(100) DEFAULT NULL COMMENT 'Manually updated',
        photo MEDIUMBLOB DEFAULT NULL COMMENT 'Student Photo'
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS enrollments (
        lecture_id BIGINT NOT NULL,
        student_id VARCHAR(50) NOT NULL,
        PRIMARY KEY (lecture_id, student_id),
        FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE,
        FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS assignments (
        id BIGINT PRIMARY KEY COMMENT 'Snowboard Assignment ID',
        lecture_id BIGINT NOT NULL,
        name VARCHAR(255) NOT NULL,
        week_start DATE DEFAULT NULL,
        week_end DATE DEFAULT NULL,
        last_fetched_at DATETIME DEFAULT NULL COMMENT 'Last time submissions were fetched for this assignment',
        FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS files (
        md5 CHAR(32) PRIMARY KEY,
        content MEDIUMBLOB NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS submissions (
        id INT AUTO_INCREMENT PRIMARY KEY,
        assignment_id BIGINT NOT NULL,
        student_id VARCHAR(50) NOT NULL,
        file_md5 CHAR(32) NOT NULL,

        submitted_at DATETIME NOT NULL COMMENT 'Submission time from Snowboard',
        fetched_at DATETIME NOT NULL COMMENT 'When we crawled it',

        is_latest BOOLEAN DEFAULT TRUE COMMENT 'Helper to find latest submission per student/assign',

        max_score FLOAT DEFAULT 100.0,
        score FLOAT DEFAULT NULL,
        verdict VARCHAR(50) DEFAULT NULL,
        comment TEXT DEFAULT NULL,
        failure_details TEXT DEFAULT NULL,
        is_force BOOLEAN DEFAULT FALSE,

        FOREIGN KEY (assignment_id) REFERENCES assignments(id) ON DELETE CASCADE,
        FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE,
        FOREIGN KEY (file_md5) REFERENCES files(md5)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS code_similarity (
        md5_a CHAR(32) NOT NULL,
        md5_b CHAR(32) NOT NULL,
        similarity FLOAT NOT NULL,
        similarity_normalized FLOAT NOT NULL,
        PRIMARY KEY (md5_a, md5_b),
        FOREIGN KEY (md5_a) REFERENCES files(md5) ON DELETE CASCADE,
        FOREIGN KEY (md5_b) REFERENCES files(md5) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS ta_accounts (
        username VARCHAR(50) PRIMARY KEY,
        name VARCHAR(100) DEFAULT NULL,
        password_plain VARCHAR(255) DEFAULT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS ta_access (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(50) NOT NULL,
        accessed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        path VARCHAR(255) NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS ta_lecture_access (
        username VARCHAR(50) NOT NULL,
        lecture_id BIGINT NOT NULL,
        PRIMARY KEY (username, lecture_id),
        FOREIGN KEY (username) REFERENCES ta_accounts(username) ON DELETE CASCADE,
        FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS exam_rooms (
        lecture_id BIGINT NOT NULL,
        exam_type VARCHAR(20) NOT NULL,
        student_id VARCHAR(50) NOT NULL,
        room VARCHAR(50) NOT NULL,
        PRIMARY KEY (lecture_id, exam_type, student_id),
        FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS exam_room_staff (
        lecture_id BIGINT NOT NULL,
        exam_type VARCHAR(20) NOT NULL,
        username VARCHAR(50) NOT NULL,
        room VARCHAR(50) NOT NULL,
        PRIMARY KEY (lecture_id, exam_type, username, room),
        FOREIGN KEY (lecture_id) REFERENCES lectures(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """,

    """
    CREATE TABLE IF NOT EXISTS mole_prefills (
        assignment_id BIGINT NOT NULL,
        student_id VARCHAR(50) NOT NULL,
        prefilled_at DATETIME NOT NULL,
        PRIMARY KEY (assignment_id, student_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
    """
]

def init_db():
    logger.info(f"Initializing Database: {DB_NAME}")
    
    conn = pymysql.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASSWORD
    )
    
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME} CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
            logger.info(f"Database {DB_NAME} ensured.")
            
            cursor.execute(f"USE {DB_NAME}")
            
            for ddl in DDL_STATEMENTS:
                try:
                    cursor.execute(ddl)
                except Exception as e:
                    logger.error(f"Failed to execute DDL:\n{ddl}\nError: {e}")
                    raise e
            
            logger.info("All tables created successfully.")
            
            conn.commit()
    finally:
        conn.close()

if __name__ == "__main__":
    init_db()

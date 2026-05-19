import hashlib
import hmac
import os
import re

MOLE_SALT = os.environ["MOLE_SALT"]

_HEX64 = re.compile(r"[0-9a-f]{64}")


def _hmac(label: str, data: str) -> str:
    msg = f"{label}:{data}".encode()
    return hmac.new(MOLE_SALT.encode(), msg, hashlib.sha256).hexdigest()


def personal_token_for(student_id: str) -> str:
    return _hmac("personal", str(student_id))[:32]


def answer_token(personal: str) -> str:
    return _hmac("answer", personal)


def expected_answer_for(student_id: str) -> str:
    return answer_token(personal_token_for(student_id))


def verify_answer(student_id: str, submitted_text: str) -> bool:
    expected = expected_answer_for(student_id).lower()
    for cand in _HEX64.findall(submitted_text.lower()):
        if hmac.compare_digest(cand, expected):
            return True
    return False

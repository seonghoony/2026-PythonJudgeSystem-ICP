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


_WS = re.compile(r"\s+")


def verify_answer(student_id: str, submitted_text: str) -> bool:
    expected = expected_answer_for(student_id).lower()
    # 학생이 정답 토큰을 복붙하며 사이에 공백·줄바꿈을 끼워 넣어도 인식되도록
    # 1차: 원문에서 64-hex 패턴 매칭, 2차: 공백 모두 제거한 텍스트에서 재시도.
    haystack = submitted_text.lower()
    for cand in _HEX64.findall(haystack):
        if hmac.compare_digest(cand, expected):
            return True
    compact = _WS.sub("", haystack)
    for cand in _HEX64.findall(compact):
        if hmac.compare_digest(cand, expected):
            return True
    return False

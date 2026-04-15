from pathlib import Path
from typing import Tuple, Optional


def validate_submission(content: bytes, filename: str = "") -> Tuple[bool, Optional[str]]:
    """
    Validates submission content before evaluation.
    Returns (is_valid, error_comment).
    If is_valid is False, error_comment contains Korean feedback for the student.
    """

    if len(content) == 0:
        return False, "오답입니다. 파일이 비어 있습니다."

    if filename:
        ext = Path(filename).suffix.lower()
        if ext and ext != ".py":
            return False, "오답입니다. 파이썬 스크립트 확장자(.py)가 아닙니다."

    stripped = content.lstrip()
    stripped_end = content.rstrip()
    if (stripped.startswith(b"{") and
            stripped_end.endswith(b"}") and
            b'"cells"' in content):
        return False, (
            "오답입니다. 비록 확장자는 .py 이지만, 실질적인 파일의 내용은 "
            ".ipynb (Jupyter Notebook) 형식입니다. "
            "코드 셀에 작성한 파이썬 코드를 갈무리하여 파이썬 스크립트로 제출하세요."
        )

    if b'\x00' in content[:1024]:
        return False, "오답입니다. 파이썬 문법으로 작성된 텍스트 파일(.py)이 아닌 이미지 등의 파일이 제출되었습니다."

    return True, None

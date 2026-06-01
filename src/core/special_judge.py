import json
import tempfile
import shutil
from pathlib import Path
from typing import List, Optional

from src.core.engine import JudgeEngine
from src.core.sandbox import DockerSandbox
from src.models.schema import EvaluationResult, TestCaseResult


def _looks_like_results(value) -> bool:
    """결과 배열처럼 생겼는지: 비어있지 않고 첫 원소가 채점 결과 dict인지."""
    if not isinstance(value, list) or not value:
        return False
    head = value[0]
    return isinstance(head, dict) and (
        "is_correct" in head or "test_case_id" in head or "message" in head
    )


def _extract_result_array(text: str):
    """
    그래더 stdout(마커 사이 문자열)에서 결과 JSON 배열을 복구한다.

    계약상 그래더는 `print(json.dumps(results))`로 '결과 dict들의 리스트'만 출력해야 하지만,
    학생이 __init__/__str__/메서드 안에서 stdout으로 print하면 JSON 앞뒤로 잡음이 섞여
    통짜 json.loads가 깨진다(학생 실수지만 그래더가 낸 결과 자체는 멀쩡하다).
    모든 '[' 위치에서 raw_decode를 시도해 '결과처럼 생긴' 마지막 리스트를 복구한다.
    복구 불가 시 None.
    """
    if not text or not text.strip():
        return None

    # 1) 정상 경로: 마커 사이가 통째로 결과 리스트.
    try:
        value = json.loads(text)
        if isinstance(value, list):
            return value
    except ValueError:
        pass

    # 2) 오염된 경로: '[' 마다 raw_decode. '결과처럼 생긴' 마지막 리스트를 우선 채택하고,
    #    없으면 마지막으로 디코드된 리스트를 쓴다. 그래더의 최종 출력이 가장 뒤에 있으므로 '마지막'.
    decoder = json.JSONDecoder()
    last_list = None
    last_resultish = None
    i = text.find('[')
    while i != -1:
        try:
            value, end = decoder.raw_decode(text, i)
        except ValueError:
            i = text.find('[', i + 1)
            continue
        if isinstance(value, list):
            last_list = value
            if _looks_like_results(value):
                last_resultish = value
        i = text.find('[', max(end, i + 1))

    return last_resultish if last_resultish is not None else last_list


class SpecialJudge(JudgeEngine):
    def evaluate(self, submission_path: Path, assignment_dir: Path, student_info: dict = None) -> EvaluationResult:
        eval_result = EvaluationResult(
            submission_id=submission_path.stem,
            assignment_id=self.config.id,
            student_id=student_info.get("student_id", "Unknown") if student_info else "Unknown",
            total_score=0.0,
            results=[]
        )
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            submission_dest = temp_path / "submission"
            submission_dest.mkdir()
            
            if submission_path.is_dir():
                shutil.copytree(submission_path, submission_dest, dirs_exist_ok=True)
            else:
                shutil.copy2(submission_path, submission_dest / "Target.py")
                
            try:
                raw_result = DockerSandbox.run(
                    self.config,
                    submission_dest,
                    assignment_dir,
                    mode="special",
                    env_vars=student_info or {}
                )
            except Exception as e:
                eval_result.system_error = str(e)
                return eval_result
            
            stdout = raw_result.get("stdout", "")
            stderr = raw_result.get("stderr", "")
            exit_code = raw_result.get("exit_code", 0)

            start_marker = "___JUDGE_RESULT_START___"
            end_marker = "___JUDGE_RESULT_END___"

            # 첫 START와 '마지막' END 사이를 취한다. 학생/그래더가 마커 문자열을 직접
            # 출력해 잡음에 섞여도 launcher가 감싼 진짜 구간을 안정적으로 끊어낸다.
            si = stdout.find(start_marker)
            ei = stdout.rfind(end_marker)

            if si != -1 and ei != -1 and ei > si:
                json_str = stdout[si + len(start_marker):ei]

                # 마커 사이에는 결과 JSON 배열만 있어야 하지만, 학생이 메서드/__str__ 안에서
                # stdout으로 print하면 잡음이 섞여 통짜 파싱이 깨진다(학생 실수). 이 경우에도
                # 그래더가 마지막에 낸 결과 배열을 복구해 정상 채점하고, 출제자에게 오탐 알림을
                # 보내지 않는다. 정말로 복구할 결과가 없을 때만 system_error로 승격한다.
                results_data = _extract_result_array(json_str)
                if results_data is None:
                    eval_result.system_error = (
                        "Failed to parse special judge results. "
                        f"Output tail: {json_str[-300:]!r}"
                    )
                    return eval_result

                for res in results_data:
                    tc = TestCaseResult(
                        test_case_id=res.get("test_case_id", "1"),
                        is_correct=res.get("is_correct", False),
                        stdout=res.get("stdout", ""),
                        stderr=res.get("stderr", ""),
                        exit_code=res.get("exit_code", 0),
                        message=res.get("message", "")
                    )
                    eval_result.results.append(tc)

                if eval_result.results:
                    correct_count = sum(1 for r in eval_result.results if r.is_correct)
                    total_count = len(eval_result.results)

                    policy = self.config.grading.policy
                    if policy == "partial":
                        eval_result.total_score = correct_count / total_count
                    else:
                        eval_result.total_score = 1.0 if correct_count == total_count else 0.0
                else:
                    eval_result.total_score = 0.0

                # launcher가 그래더 크래시를 마커 JSON으로 합성해 보내준 경우, system_error로 승격해
                # sanitize_system_error / Telegram 알림 경로가 동작하게 한다.
                for tc in eval_result.results:
                    if "Grader Crashed" in (tc.message or ""):
                        eval_result.system_error = (
                            f"{tc.message}. Stderr tail: {(tc.stderr or '')[-300:]!r}"
                        )
                        break
            else:
                # 그래더가 마커를 출력하기 전에 죽음. standard_judge와 동일한 분류.
                code_to_msg = {
                    124: "Time Limit Exceeded",
                    137: "Memory Limit Exceeded",
                }
                if exit_code in code_to_msg:
                    msg = code_to_msg[exit_code]
                    eval_result.system_error = None
                else:
                    msg = f"Grader Crashed (exit {exit_code})"
                    eval_result.system_error = (
                        f"{msg}. Stdout tail: {stdout[-300:]!r}\nStderr tail: {stderr[-300:]!r}"
                    )
                eval_result.results.append(TestCaseResult(
                    test_case_id="1",
                    is_correct=False,
                    exit_code=exit_code,
                    stdout=stdout[-2000:],
                    stderr=stderr[-2000:],
                    message=msg,
                ))

        return eval_result

import json
import tempfile
import shutil
from pathlib import Path
from typing import List, Optional

from src.core.engine import JudgeEngine
from src.core.sandbox import DockerSandbox
from src.models.schema import EvaluationResult, TestCaseResult

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

            if start_marker in stdout and end_marker in stdout:
                try:
                    json_str = stdout.split(start_marker)[1].split(end_marker)[0]
                    results_data = json.loads(json_str)
                except json.JSONDecodeError:
                    eval_result.system_error = "Failed to parse special judge results."
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

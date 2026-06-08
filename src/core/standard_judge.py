import json
import tempfile
import shutil
import importlib.util
import sys
from pathlib import Path
from typing import List, Optional

from src.core.engine import JudgeEngine
from src.core.sandbox import DockerSandbox
from src.models.schema import EvaluationResult, TestCaseResult

class StandardJudge(JudgeEngine):
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
                    mode="standard",
                    env_vars=student_info or {}
                )
            except Exception as e:
                eval_result.system_error = str(e)
                return eval_result

            stdout = raw_result.get("stdout", "")
            stderr = raw_result.get("stderr", "")
            exit_code = raw_result.get("exit_code", 0)

            results_json = []
            start_marker = "___JUDGE_RESULT_START___"
            end_marker = "___JUDGE_RESULT_END___"

            if start_marker in stdout and end_marker in stdout:
                try:
                    json_str = stdout.split(start_marker)[1].split(end_marker)[0]
                    results_json = json.loads(json_str)
                except json.JSONDecodeError:
                    eval_result.system_error = "Failed to parse judge results."
                    return eval_result
            else:
                # Launcher가 JSON 마커를 찍기 전에 죽었음. exit code로 카테고리 분류해서
                # per-testcase 결과를 합성해 main.py가 정상 verdict 매핑을 할 수 있게 한다.
                code_to_msg = {
                    124: "Time Limit Exceeded",
                    137: "Memory Limit Exceeded",
                }
                if exit_code in code_to_msg:
                    msg = code_to_msg[exit_code]
                    # 학생 사유이므로 system_error는 비워서 Telegram 알림이 울리지 않게 한다.
                    eval_result.system_error = None
                else:
                    msg = f"Launcher Crashed (exit {exit_code})"
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

            hook_func = self._load_hook(assignment_dir)
            custom_validator = self._load_validator(assignment_dir)

            for res_data in results_json:
                 tc_result = TestCaseResult(**res_data)

                 # 선택적 testcase 가중치: testcases/<id>/weight 파일이 있으면 사용(기본 1.0).
                 weight_file = assignment_dir / "testcases" / tc_result.test_case_id / "weight"
                 if weight_file.exists():
                     try:
                         tc_result.weight = float(weight_file.read_text().strip())
                     except (ValueError, OSError):
                         pass

                 if hook_func:
                     try:
                         # hook_func은 tc_result를 in-place로 수정한다.
                         hook_func(tc_result, assignment_dir)
                     except Exception as e:
                         tc_result.system_error = f"Hook Error: {e}"

                 if not tc_result.is_correct:
                     # testcases/<id>/output.txt 와 testcases/output_<id>.txt 두 가지 네이밍을 모두 지원.
                     output_file = assignment_dir / "testcases" / tc_result.test_case_id / "output.txt"
                     if not output_file.exists():
                         output_file = assignment_dir / "testcases" / f"output_{tc_result.test_case_id}.txt"

                     if output_file.exists():
                         try:
                             expected = output_file.read_text()
                             tc_result.expected_output = expected

                             if custom_validator and tc_result.exit_code == 0:
                                 try:
                                     if custom_validator(tc_result.stdout, expected):
                                         tc_result.is_correct = True
                                         tc_result.message = ""
                                 except Exception as e:
                                     tc_result.message += f" (Validator Error: {e})"
                         except Exception:
                             pass

                     input_file = assignment_dir / "testcases" / tc_result.test_case_id / "input.txt"
                     if not input_file.exists():
                         input_file = assignment_dir / "testcases" / f"input_{tc_result.test_case_id}.txt"

                     if input_file.exists():
                         try:
                             tc_result.input_data = input_file.read_text()
                         except Exception:
                             pass

                 eval_result.results.append(tc_result)

            # launcher 내부 예외(per-testcase setup 실패)는 학생 코드와 무관 — system_error로 승격해
            # main.py가 SYS verdict + Telegram 알림을 트리거하도록 한다.
            for tc in eval_result.results:
                if "System Error" in (tc.message or ""):
                    eval_result.system_error = (
                        f"Launcher Crashed: {tc.message}. Stderr tail: {(tc.stderr or '')[-300:]!r}"
                    )
                    break

            if eval_result.results:
                correct_count = sum(1 for r in eval_result.results if r.is_correct)
                total_count = len(eval_result.results)

                policy = self.config.grading.policy
                if policy == "partial":
                    # 가중치 부분점수: 통과 testcase weight 합 / 전체 weight 합.
                    # weight 기본 1.0 → 기존 (통과 수/전체 수)와 동일(하위호환).
                    total_w = sum(r.weight for r in eval_result.results)
                    correct_w = sum(r.weight for r in eval_result.results if r.is_correct)
                    eval_result.total_score = (correct_w / total_w) if total_w > 0 else 0.0
                else:
                    eval_result.total_score = 1.0 if correct_count == total_count else 0.0
            else:
                eval_result.total_score = 0.0

        return eval_result

    def _load_validator(self, assignment_dir: Path):
        """Loads the check(output, expected) function from run_after.py if exists."""
        run_after = assignment_dir / "run_after.py"
        if not run_after.exists():
            return None
            
        try:
            spec = importlib.util.spec_from_file_location("run_after", run_after)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "check"):
                return module.check
        except Exception:
            pass
        return None

    def _load_hook(self, assignment_dir: Path):
        """Loads the post_process(result, assignment_dir) function from hook.py if exists."""
        hook_path = assignment_dir / "hook.py"
        if not hook_path.exists():
            return None
            
        try:
            spec = importlib.util.spec_from_file_location("hook", hook_path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if hasattr(module, "post_process"):
                return module.post_process
        except Exception:
            pass
        return None

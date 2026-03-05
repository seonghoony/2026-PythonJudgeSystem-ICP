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
            
            try:
                start_marker = "___JUDGE_RESULT_START___"
                end_marker = "___JUDGE_RESULT_END___"
                
                if start_marker in stdout and end_marker in stdout:
                    json_str = stdout.split(start_marker)[1].split(end_marker)[0]
                    # Expected format: List[TestCaseResult] dicts
                    results_data = json.loads(json_str) 
                    
                    total_score = 0
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
                        
                    # Final Score Calculation
                    if eval_result.results:
                        correct_count = sum(1 for r in eval_result.results if r.is_correct)
                        total_count = len(eval_result.results)
                        
                        policy = self.config.grading.policy
                        if policy == "partial":
                            eval_result.total_score = correct_count / total_count
                        else:
                            # Default: all_or_nothing
                            eval_result.total_score = 1.0 if correct_count == total_count else 0.0
                    else:
                        eval_result.total_score = 0.0
                    
                else:
                    eval_result.system_error = f"Special Judge Output Format Error.\nStdout: {stdout[:500]}"
                    
            except json.JSONDecodeError:
                eval_result.system_error = "Failed to parse special judge results."
                
        return eval_result

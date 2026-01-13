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
        # Prepare result structure
        eval_result = EvaluationResult(
            submission_id=submission_path.stem, # Placeholder
            assignment_id=self.config.id,
            student_id=student_info.get("student_id", "Unknown") if student_info else "Unknown",
            total_score=0.0,
            results=[]
        )
        
        # 1. Prepare Submission in Temp Directory
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            submission_dest = temp_path / "submission"
            submission_dest.mkdir()
            
            if submission_path.is_dir():
                shutil.copytree(submission_path, submission_dest, dirs_exist_ok=True)
            else:
                shutil.copy2(submission_path, submission_dest / "Target.py")
                
            # 2. Run Sandbox
            # We assume DockerSandbox.run takes the directory containing submission files
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
            
            # 3. Parse Output
            # Launcher prints JSON between delimiters
            stdout = raw_result.get("stdout", "")
            stderr = raw_result.get("stderr", "") # Debugging purpose
            
            results_json = []
            try:
                start_marker = "___JUDGE_RESULT_START___"
                end_marker = "___JUDGE_RESULT_END___"
                
                if start_marker in stdout and end_marker in stdout:
                    json_str = stdout.split(start_marker)[1].split(end_marker)[0]
                    results_json = json.loads(json_str)
                else:
                    # Fallback if critical failure in launcher before printing JSON
                    if raw_result["exit_code"] == 124:
                         eval_result.system_error = "Execution Timed Out (Container Level)"
                    else:
                         eval_result.system_error = f"Malformed Output from Launcher.\nStdout: {stdout[:200]}...\nStderr: {stderr[:200]}..."
                    return eval_result
            except json.JSONDecodeError:
                eval_result.system_error = "Failed to parse judge results."
                return eval_result
                
            # 4. Post-Process & Validate
            
            # Load hooks (e.g. for transforming RTE -> Success)
            hook_func = self._load_hook(assignment_dir)
            
            # Load run_after.py if exists (Validator)
            custom_validator = self._load_validator(assignment_dir)
            
            # Load expected outputs to compare if validator exists
            # (Launcher did exact match, but validator might override 'False' to 'True')
            
            for res_data in results_json:
                 tc_result = TestCaseResult(**res_data)
                 
                 # Apply Hook if exists (Pre-computation modification)
                 if hook_func:
                     try:
                         # Hook modifies tc_result in-place
                         hook_func(tc_result, assignment_dir)
                     except Exception as e:
                         tc_result.system_error = f"Hook Error: {e}"
                 
                 if not tc_result.is_correct:
                     # Find expected output for debugging/validation
                     # Assuming testcase structure: testcases/{id}/output.txt
                     output_file = assignment_dir / "testcases" / tc_result.test_case_id / "output.txt"
                     # Also try input_{id}.txt / output_{id}.txt
                     if not output_file.exists():
                         output_file = assignment_dir / "testcases" / f"output_{tc_result.test_case_id}.txt"
                     
                     if output_file.exists():
                         try:
                             expected = output_file.read_text()
                             tc_result.expected_output = expected
                             
                             # Custom Validator Check
                             if custom_validator and tc_result.exit_code == 0:
                                 try:
                                     if custom_validator(tc_result.stdout, expected):
                                         tc_result.is_correct = True
                                         tc_result.message = "" # Clear "Wrong Answer"
                                 except Exception as e:
                                     tc_result.message += f" (Validator Error: {e})"
                         except Exception:
                             pass
                    
                     # Capture Input Data for Debugging
                     input_file = assignment_dir / "testcases" / tc_result.test_case_id / "input.txt"
                     if not input_file.exists():
                         input_file = assignment_dir / "testcases" / f"input_{tc_result.test_case_id}.txt"
                     
                     if input_file.exists():
                         try:
                             tc_result.input_data = input_file.read_text()
                         except Exception:
                             pass
                             
                 # Calculate score (Scaled 0.0 to 1.0)
                 # We will finalize total_score after loop
                 
                 eval_result.results.append(tc_result)
                 
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

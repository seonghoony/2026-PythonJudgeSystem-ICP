import os
import sys
import subprocess
import json
import glob
import traceback
from pathlib import Path

SUBMISSION_DIR = Path("/submission")
TARGET_FILE = Path("/Target.py")
ASSIGNMENT_DIR = Path("/assignment")

MODE = os.environ.get("JUDGE_MODE", "standard")
TIMEOUT = int(os.environ.get("JUDGE_TIMEOUT", "5"))

STUDENT_ID = os.environ.get("STUDENT_ID", "")
STUDENT_NAME = os.environ.get("STUDENT_NAME", "")

def find_entry_point():
    """Finds the python script to run."""
    if TARGET_FILE.exists():
        return TARGET_FILE

    if SUBMISSION_DIR.exists():
        candidates = ["main.py", "Target.py", "assignment.py"]
        for c in candidates:
            p = SUBMISSION_DIR / c
            if p.exists():
                return p
        # Fallback: any .py file if only one exists
        py_files = list(SUBMISSION_DIR.glob("*.py"))
        if len(py_files) == 1:
            return py_files[0]
            
    raise FileNotFoundError("Could not find a valid Python entry point (Target.py, main.py).")

def run_pre_script():
    """Executes run_before.py if it exists."""
    pre_script = ASSIGNMENT_DIR / "run_before.py"
    if pre_script.exists():
        try:
            print(f"[Launcher] Executing {pre_script}...", file=sys.stderr)
            exec(pre_script.read_text(), globals())
        except Exception as e:
            print(f"[Launcher] run_before.py failed: {e}", file=sys.stderr)
            traceback.print_exc()

def run_standard_judge(entry_point):
    """Iterates over test cases and runs submission as subprocess."""
    results = []
    
    testcases_dir = ASSIGNMENT_DIR / "testcases"
    if not testcases_dir.exists():
        print(f"[Launcher] Error: {testcases_dir} does not exist.", file=sys.stderr)
        return

    # 두 가지 testcase 레이아웃 지원: testcases/1/input.txt (디렉토리) 와 testcases/input_1.txt (평탄)
    cases_dirs = sorted([d for d in testcases_dir.iterdir() if d.is_dir()], key=lambda x: x.name)

    flat_inputs = list(testcases_dir.glob("input_*.txt"))
    flat_cases = []
    for f in flat_inputs:
        suffix = f.name[len("input_"):]
        out_f = testcases_dir / f"output_{suffix}"
        flat_cases.append({
            "id": suffix.replace(".txt", ""),
            "input": f,
            "output": out_f,
            "type": "flat"
        })
    flat_cases.sort(key=lambda x: x["id"])

    all_cases = []
    for d in cases_dirs:
        all_cases.append({
            "id": d.name,
            "input": d / "input.txt",
            "output": d / "output.txt",
            "type": "dir"
        })
    all_cases.extend(flat_cases)
    
    for case in all_cases:
        input_file = case["input"]
        output_file = case["output"]
        case_id = case["id"]
        res = {
            "test_case_id": case_id,
            "stdout": "",
            "stderr": "",
            "exit_code": 0,
            "is_correct": False,
            "message": ""
        }
        
        try:
            input_data = input_file.read_text()

            # run_before.py가 학생 코드와 같은 프로세스에서 실행되도록 wrapper로 묶어 한 번에 exec.
            pre_script = ASSIGNMENT_DIR / "run_before.py"
            actual_entry = entry_point

            if pre_script.exists():
                wrapper_content = f"""
import sys
from pathlib import Path

try:
    with open("{pre_script}", "r") as f:
        exec(f.read(), globals())
except Exception as e:
    print(f"Error in run_before.py: {{e}}", file=sys.stderr)
    sys.exit(1)

try:
    sys.path.insert(0, "{entry_point.parent}")
    with open("{entry_point}", "r") as f:
        code = compile(f.read(), "{entry_point}", 'exec')
        exec(code, globals())
except Exception as e:
    print(f"Runtime Error: {{e}}", file=sys.stderr)
    import traceback
    traceback.print_exc()
    sys.exit(1)
"""
                wrapper_path = entry_point.parent / "_wrapper_run_before.py"
                wrapper_path.write_text(wrapper_content)
                actual_entry = wrapper_path

            proc = subprocess.run(
                [sys.executable, str(actual_entry)],
                input=input_data,
                capture_output=True,
                text=True,
                timeout=TIMEOUT,
                cwd=str(entry_point.parent),
                env={**os.environ, "STUDENT_ID": STUDENT_ID, "STUDENT_NAME": STUDENT_NAME}
            )
            
            res["stdout"] = proc.stdout
            res["stderr"] = proc.stderr
            res["exit_code"] = proc.returncode
            
            if proc.returncode != 0:
                res["message"] = "Runtime Error"
                if "RecursionError" in proc.stderr:
                    res["message"] = "Recursion Error"
            else:
                # 호스트의 run_after.py가 별도 검증할 수 있지만, launcher는 우선 정확 일치로 판정한다.
                expected = output_file.read_text() if output_file.exists() else ""
                if proc.stdout.strip() == expected.strip():
                    res["is_correct"] = True
                else:
                    res["message"] = "Wrong Answer"

        except subprocess.TimeoutExpired:
            res["message"] = "Time Limit Exceeded"
            res["exit_code"] = 124
        except Exception as e:
            res["message"] = f"System Error: {str(e)}"
            res["exit_code"] = -1
            
        results.append(res)
        
    # 학생의 stdout과 섞이지 않도록 고유한 delimiter로 감싸서 호스트가 파싱한다.
    print("___JUDGE_RESULT_START___")
    print(json.dumps(results))
    print("___JUDGE_RESULT_END___")

def run_special_judge(entry_point):
    """Runs the grader script. 우선순위: /evaluator.py -> /grader.py -> /assignment/evaluator.py -> /assignment/grader.py"""
    candidates = [
        Path("/evaluator.py"),
        Path("/grader.py"),
        ASSIGNMENT_DIR / "evaluator.py",
        ASSIGNMENT_DIR / "grader.py"
    ]
    
    grader_script = None
    for c in candidates:
        if c.exists():
            grader_script = c
            break
            
    if not grader_script:
         print(f"[Launcher] Error: evaluator.py (or grader.py) not found.", file=sys.stderr)
         return
         
    # special judge의 grader가 학생 모듈을 import 할 수 있도록 PYTHONPATH에 submission 디렉토리를 주입.
    env = os.environ.copy()
    env["PYTHONPATH"] = str(entry_point.parent) + os.pathsep + env.get("PYTHONPATH", "")

    try:
        res = subprocess.run(
            [sys.executable, str(grader_script)],
            timeout=TIMEOUT,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(entry_point.parent)
        )

        print("___JUDGE_RESULT_START___")

        if res.returncode != 0:
            if not res.stdout.strip():
                 print(json.dumps([{"message": f"Grader Crashed (Exit {res.returncode})", "stderr": res.stderr, "is_correct": False, "exit_code": res.returncode}]))
            else:
                 print(res.stdout)
        else:
             print(res.stdout)
        print("___JUDGE_RESULT_END___")

        if res.stderr:
            print(f"[Launcher] Grader Stderr: {res.stderr}", file=sys.stderr)

    except subprocess.TimeoutExpired:
        print("___JUDGE_RESULT_START___")
        print(json.dumps([{"message": "Time Limit Exceeded", "is_correct": False}]))
        print("___JUDGE_RESULT_END___")

def main():
    try:
        entry = find_entry_point()
        run_pre_script()
        
        if MODE == "standard":
            run_standard_judge(entry)
        elif MODE == "special":
            run_special_judge(entry)
        else:
            print(f"[Launcher] Unknown mode: {MODE}", file=sys.stderr)
            
    except Exception as e:
        print(f"[Launcher] Fatal Error: {e}", file=sys.stderr)
        traceback.print_exc()

if __name__ == "__main__":
    main()

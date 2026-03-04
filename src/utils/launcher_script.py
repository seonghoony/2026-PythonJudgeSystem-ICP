import os
import sys
import subprocess
import json
import glob
import traceback
from pathlib import Path

# Paths
SUBMISSION_DIR = Path("/submission") # Mount point for student submission dir
TARGET_FILE = Path("/Target.py")     # Default single-file mount
ASSIGNMENT_DIR = Path("/assignment") # Mount point for assignment data

# Configuration (passed via Env Vars)
MODE = os.environ.get("JUDGE_MODE", "standard") # 'standard' or 'special'
TIMEOUT = int(os.environ.get("JUDGE_TIMEOUT", "5"))

# Student Info (passed via Env Vars from host)
STUDENT_ID = os.environ.get("STUDENT_ID", "")
STUDENT_NAME = os.environ.get("STUDENT_NAME", "")

def find_entry_point():
    """Finds the python script to run."""
    if TARGET_FILE.exists():
        return TARGET_FILE
    
    # Check submission dir
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
            # Execute in global scope? 
            # For special judge (import based), this matters. 
            # For standard judge (subprocess based), this only affects the launcher's env, 
            # checking if we need to propagate env vars.
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

    # Assuming numbered directories 1, 2, 3... or flat input_*.txt?
    # Support Directory-based (testcases/1/input.txt) AND Flat-file (testcases/input_1.txt)
    
    # 1. Directory based
    cases_dirs = sorted([d for d in testcases_dir.iterdir() if d.is_dir()], key=lambda x: x.name)
    
    # 2. Flat file based
    # Look for input_*.txt
    flat_inputs = list(testcases_dir.glob("input_*.txt"))
    # Map to virtual "case objects"
    flat_cases = []
    for f in flat_inputs:
        # Expected output: output_{suffix} matching input_{suffix}
        suffix = f.name[len("input_"):] # e.g. "1.txt"
        out_f = testcases_dir / f"output_{suffix}"
        flat_cases.append({
            "id": suffix.replace(".txt", ""), # e.g. "1"
            "input": f,
            "output": out_f,
            "type": "flat"
        })
    # Sort flat cases
    flat_cases.sort(key=lambda x: x["id"])

    # Combine (prioritizing dirs if needed, but usually one or the other)
    # Loop strategy:
    
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
            # Read input
            input_data = input_file.read_text()
            
            # Run Subprocess
            # Logic: If run_before.py exists, we must run it in the SAME process as the student code.
            # We create a temporary wrapper script to achieve this.
            pre_script = ASSIGNMENT_DIR / "run_before.py"
            actual_entry = entry_point
            
            if pre_script.exists():
                wrapper_content = f"""
import sys
from pathlib import Path

# 1. Execute run_before.py
try:
    with open("{pre_script}", "r") as f:
        exec(f.read(), globals())
except Exception as e:
    print(f"Error in run_before.py: {{e}}", file=sys.stderr)
    sys.exit(1)

# 2. Execute Student Script
# We set __name__ to __main__ to mimic direct execution
try:
    sys.path.insert(0, "{entry_point.parent}")
    with open("{entry_point}", "r") as f:
        code = compile(f.read(), "{entry_point}", 'exec')
        exec(code, globals())
except Exception as e:
    # Print runtime error to stderr so launcher catches it
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
                cwd=str(entry_point.parent), # Run in submission dir
                env={**os.environ, "STUDENT_ID": STUDENT_ID, "STUDENT_NAME": STUDENT_NAME}
            )
            
            res["stdout"] = proc.stdout
            res["stderr"] = proc.stderr
            res["exit_code"] = proc.returncode
            
            if proc.returncode != 0:
                res["message"] = "Runtime Error"
                # Check for common keywords in stderr
                if "RecursionError" in proc.stderr:
                    res["message"] = "Recursion Error"
            else:
                # Basic output comparison (Launcher does exact match default)
                # The HOST logic can override this with run_after.py, 
                # but the launcher needs to report stdout regardless.
                expected = output_file.read_text() if output_file.exists() else ""
                if proc.stdout.strip() == expected.strip():
                    res["is_correct"] = True
                else:
                    res["message"] = "Wrong Answer"

        except subprocess.TimeoutExpired:
            res["message"] = "Time Limit Exceeded"
            res["exit_code"] = 124 # Common timeout code
        except Exception as e:
            res["message"] = f"System Error: {str(e)}"
            res["exit_code"] = -1
            
        results.append(res)
        
    # Print results as JSON to stdout for host to parse
    # We use a robust delimiter to avoid student stdout interference if logic changes
    print("___JUDGE_RESULT_START___")
    print(json.dumps(results))
    print("___JUDGE_RESULT_END___")

def run_special_judge(entry_point):
    """Runs the grader script."""
    # Priority: /evaluator.py -> /grader.py -> /assignment/evaluator.py -> /assignment/grader.py
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
         
    # Special judge usually imports the student code.
    # We add submission dir to path so grader can import it
    # We must propagate this to the subprocess environment
    env = os.environ.copy()
    env["PYTHONPATH"] = str(entry_point.parent) + os.pathsep + env.get("PYTHONPATH", "")
    
    try:
        # We capture output to wrap it in markers
        res = subprocess.run(
            [sys.executable, str(grader_script)],
            timeout=TIMEOUT,
            capture_output=True,
            text=True,
            env=env,
            cwd=str(entry_point.parent) # Also set CWD for file ops
        )
        
        # Determine success/failure based on exit code or output
        # We wrap the stdout in markers for the host to parse
        print("___JUDGE_RESULT_START___")
        # If grader failed (nonzero), we might want to report RTE if stdout is empty
        if res.returncode != 0:
            if not res.stdout.strip():
                 # Generate a JSON error if script crashed without output
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

import os
import sys
import subprocess
import json
import glob
import tempfile
import traceback
from pathlib import Path

SUBMISSION_DIR = Path("/submission")
TARGET_FILE = Path("/Target.py")
ASSIGNMENT_DIR = Path("/assignment")

MODE = os.environ.get("JUDGE_MODE", "standard")
TIMEOUT = int(os.environ.get("JUDGE_TIMEOUT", "5"))

STUDENT_ID = os.environ.get("STUDENT_ID", "")
STUDENT_NAME = os.environ.get("STUDENT_NAME", "")

# 학생 stdout/stderr는 메모리 파이프가 아닌 tmpfile로 흘려보내고, 끝에서 첫 N바이트만 읽는다.
# 폭주(while True: print(...))로 launcher RSS가 부풀어 OOM(exit 137) 나는 사고를 막기 위함.
STUDENT_STREAM_CAP = 64 * 1024
GRADER_STREAM_CAP = 256 * 1024


def _run_with_caps(cmd, *, stdin_bytes, timeout, cap, **popen_kwargs):
    """
    Popen + tmpfile redirect로 stdout/stderr를 OS 레벨에서 흘려보내고, 종료 후 cap 바이트까지만 읽는다.
    반환: dict(stdout, stderr, exit_code, timed_out, truncated_stdout, truncated_stderr)
    """
    with tempfile.NamedTemporaryFile(dir="/tmp") as so, \
         tempfile.NamedTemporaryFile(dir="/tmp") as se:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=so,
            stderr=se,
            **popen_kwargs,
        )
        timed_out = False
        try:
            proc.communicate(input=stdin_bytes, timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            timed_out = True

        so.seek(0); se.seek(0)
        so_raw = so.read(cap + 1)
        se_raw = se.read(cap + 1)
        truncated_out = len(so_raw) > cap
        truncated_err = len(se_raw) > cap
        return {
            "stdout": so_raw[:cap].decode("utf-8", "replace"),
            "stderr": se_raw[:cap].decode("utf-8", "replace"),
            "exit_code": 124 if timed_out else proc.returncode,
            "timed_out": timed_out,
            "truncated_stdout": truncated_out,
            "truncated_stderr": truncated_err,
        }

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
                # wrapper는 학생 코드 import를 한 번 감싸 run_before.py와 globals를 공유시킨다.
                # 트레이스백은 표준 채점기와 동일하게 CPython 원본만 stderr로 흘려보낸다.
                wrapper_content = f"""
import sys
import traceback

try:
    with open("{pre_script}", "r") as f:
        exec(f.read(), globals())
except Exception:
    traceback.print_exc()
    sys.exit(1)

try:
    sys.path.insert(0, "{entry_point.parent}")
    with open("{entry_point}", "r") as f:
        code = compile(f.read(), "{entry_point}", 'exec')
        exec(code, globals())
except Exception:
    traceback.print_exc()
    sys.exit(1)
"""
                # wrapper는 /tmp에 둔다. /submission에 두면 학생 traceback에 wrapper 프레임이 섞이지만
                # /tmp에 두면 host의 sanitize_traceback("/submission/" 외 프레임 제거) 이 자동으로 정리해 준다.
                wrapper_path = Path("/tmp/_wrapper_run_before.py")
                wrapper_path.write_text(wrapper_content)
                actual_entry = wrapper_path

            run = _run_with_caps(
                [sys.executable, str(actual_entry)],
                stdin_bytes=input_data.encode("utf-8"),
                timeout=TIMEOUT,
                cap=STUDENT_STREAM_CAP,
                cwd=str(entry_point.parent),
                env={**os.environ, "STUDENT_ID": STUDENT_ID, "STUDENT_NAME": STUDENT_NAME}
            )

            res["stdout"] = run["stdout"]
            res["stderr"] = run["stderr"]
            res["exit_code"] = run["exit_code"]

            if run["timed_out"]:
                res["message"] = "Time Limit Exceeded"
            elif run["truncated_stdout"] or run["truncated_stderr"]:
                # 캡 초과 = 디버그 print 폭주 / 무한출력. exit code는 그대로 두되 verdict이 OLE로 분류된다.
                res["message"] = "Output Limit Exceeded"
            elif run["exit_code"] != 0:
                res["message"] = "Runtime Error"
                if "RecursionError" in run["stderr"]:
                    res["message"] = "Recursion Error"
            else:
                # 호스트의 run_after.py가 별도 검증할 수 있지만, launcher는 우선 정확 일치로 판정한다.
                expected = output_file.read_text() if output_file.exists() else ""
                if run["stdout"].strip() == expected.strip():
                    res["is_correct"] = True
                else:
                    res["message"] = "Wrong Answer"

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

    run = _run_with_caps(
        [sys.executable, str(grader_script)],
        stdin_bytes=None,
        timeout=TIMEOUT,
        cap=GRADER_STREAM_CAP,
        env=env,
        cwd=str(entry_point.parent),
    )

    print("___JUDGE_RESULT_START___")
    if run["timed_out"]:
        print(json.dumps([{
            "test_case_id": "1",
            "message": "Time Limit Exceeded",
            "is_correct": False,
            "exit_code": 124,
        }]))
    elif run["exit_code"] != 0 and not run["stdout"].strip():
        # 그래더가 마커 없이 죽었음 — host의 special_judge가 분류할 수 있도록 합성 마커 JSON 출력.
        print(json.dumps([{
            "test_case_id": "1",
            "message": f"Grader Crashed (exit {run['exit_code']})",
            "stderr": run["stderr"][-2000:],
            "is_correct": False,
            "exit_code": run["exit_code"],
        }]))
    else:
        # 그래더 stdout이 이미 마커 없이 JSON 결과만 들어 있다고 가정 (evaluator.py가 print(json.dumps(results))).
        print(run["stdout"])
    print("___JUDGE_RESULT_END___")

    if run["stderr"]:
        print(f"[Launcher] Grader Stderr: {run['stderr']}", file=sys.stderr)

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

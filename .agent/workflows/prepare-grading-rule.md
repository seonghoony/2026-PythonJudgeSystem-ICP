---
description: Prepare grading rules for a Snowboard assignment using AI analysis
---

# Prepare Grading Rule

This workflow takes a `lecture_id` and `assignment_id` and prepares the complete grading rule for it.
Two lectures may run concurrently, so `lecture_id` is required to identify the correct course context.

## Prerequisites
- The assignment must exist on Snowboard
- You need the `lecture_id` (numeric, identifies the course on Snowboard)
- You need the `assignment_id` (numeric, from the Snowboard URL)

## Steps

### 1. Locate and Analyze Instruction

First, check if the user has provided local instruction files (e.g., in `assignments/new_assignments/` like `.tex` files or test `.py` scripts). 
If local files exist, read them.

If no local instructions are provided, ask the user if you should crawl Snowboard for the instruction. If yes, run the crawler to download the assignment instruction:

```bash
cd /home/seonghoon/2026-PythonJudgeSystem-ICP && conda run -n PythonJudgeSystem python -c "
from dotenv import load_dotenv; load_dotenv()
from src.infrastructure.snowboard import SnowBoard
sb = SnowBoard()
path = sb.save_instruction('ASSIGNMENT_ID')
print(f'Saved to: {path}')
"
```

Read the local files or the downloaded instruction from `downloaded_instructions/ASSIGNMENT_ID.md`.

Analyze the instruction to determine:
- **Is it I/O based?** (Standard Judge) — Student reads input, prints output. Look for "입력", "출력", example I/O pairs.
- **Is it function/class based?** (Special Judge) — Student implements a function/class that is tested by unit tests. Look for "함수", "def", "class", return value requirements.
- **Does the student code need the student's info?** — Look for "학번", "이름", student-specific output (e.g., "Hello, [your name]").
- **Are there any edge cases?** — Boundary values, empty input, large input, special characters.
- **Are there any library restrictions?** — "금지", banned libraries.
- **Does output matching need to be fuzzy?** — Input prompts like "Enter number:" mixed with output.

### 3. Create Assignment Directory

Create `assignments/ASSIGNMENT_ID/` with:

```
assignments/ASSIGNMENT_ID/
├── assignment.yaml       # Required
├── testcases/            # Standard judge only
│   ├── 1/
│   │   ├── input.txt
│   │   └── output.txt
│   ├── 2/
│   │   ├── input.txt
│   │   └── output.txt
│   └── ...
├── run_after.py          # Optional: fuzzy output matching
├── run_before.py         # Optional: setup/import restrictions
└── evaluator.py          # Special judge only
```

### 4. Create `assignment.yaml`

```yaml
id: "ASSIGNMENT_ID"
name: "Assignment Name"
type: "standard"  # or "special"

resources:
  cpu_count: 1
  memory_limit: "128m"
  timeout: 5
  network_disabled: true

build:
  base_image: "condaforge/miniforge3"
  requirements: []  # Add if needed: numpy, pandas, etc.

grading:
  policy: "all_or_nothing"  # or "partial"
```

### 5a. Standard Judge — Create Test Cases

For each I/O example in the instruction, create a testcase directory:
- `testcases/N/input.txt` — exact stdin content
- `testcases/N/output.txt` — exact expected stdout content

**Guidelines:**
- Include ALL examples from the instruction
- Add extra edge cases beyond the examples (boundary values, zero, negative, large numbers)
- Strip trailing whitespace but keep trailing newline if present in expected output
- Typically create 5–10 test cases total

If the instruction contains `input()` prompts that prefix the output, create `run_after.py`:
```python
def check(output, expected):
    clean_out = output.strip()
    clean_exp = expected.strip()
    if clean_out == clean_exp:
        return True
    if clean_out.endswith(clean_exp):
        return True
    return False
```

### 5b. Special Judge — Create `evaluator.py`

For function-based assignments, create an `evaluator.py` that:
1. Imports student's `Target.py`
2. Runs unit tests against the student's function
3. Prints JSON result to stdout

**Template:**
```python
import sys
import json
import builtins
import os
from io import StringIO
import contextlib

# Optional: Import restrictions
# __original_import__ = builtins.__import__
# BANNED_LIBRARIES = ['itertools']
# def custom_import(name, *args, **kwargs):
#     if name in BANNED_LIBRARIES:
#         raise ImportError(f"'{name}' 라이브러리의 사용은 금지되었습니다.")
#     return __original_import__(name, *args, **kwargs)
# builtins.__import__ = custom_import

# Student info from environment
STUDENT_ID = os.environ.get("STUDENT_ID", "")
STUDENT_NAME = os.environ.get("STUDENT_NAME", "")

def main():
    results = []

    sys.path.append('/')
    sys.path.append('/submission')
    sys.path.append(os.getcwd())

    try:
        try:
            with contextlib.redirect_stdout(StringIO()):
                import Target
        except ImportError as e:
            print(json.dumps([{"message": f"Import Error: {e}", "is_correct": False, "exit_code": 1}]))
            return

        # Define test cases
        test_data = [
            # ("Test Name", input_args, expected_result),
        ]

        for i, (name, args, expected) in enumerate(test_data):
            res = {"test_case_id": str(i+1), "is_correct": False, "message": ""}
            try:
                result = Target.function_name(*args)
                if result == expected:
                    res["is_correct"] = True
                    res["message"] = "Pass"
                else:
                    res["message"] = f"{name}: Got {result}, Expected {expected}"
            except Exception as e:
                res["message"] = f"{name}: {e}"
            results.append(res)

    except Exception as e:
        results.append({"message": f"Fatal Error: {e}", "is_correct": False})

    print(json.dumps(results))

if __name__ == "__main__":
    main()
```

### 6. Write a Sample Solution

Create a known-correct solution file at `/tmp/solution_ASSIGNMENT_ID.py`.

### 7. Test the Rule

// turbo
Run the evaluator against the sample solution:

```bash
cd /home/seonghoon/2026-PythonJudgeSystem-ICP && conda run -n PythonJudgeSystem python src/main.py eval --assignment ASSIGNMENT_ID --submission /tmp/solution_ASSIGNMENT_ID.py --build
```

Verify:
- All test cases pass (Score: 1.0)
- No system errors
- If any test case fails, review and fix the rule

### 8. Test with an Intentionally Wrong Solution

Create a wrong solution at `/tmp/wrong_ASSIGNMENT_ID.py` and run:

```bash
cd /home/seonghoon/2026-PythonJudgeSystem-ICP && conda run -n PythonJudgeSystem python src/main.py eval --assignment ASSIGNMENT_ID --submission /tmp/wrong_ASSIGNMENT_ID.py
```

Verify:
- Score is 0.0 (or partial for partial policy)
- Error messages are meaningful

### 9. (Optional) End-to-End Test via Oneshot

If you want to test the full fetch-grade-upload cycle against a real assignment:

```bash
cd /home/seonghoon/2026-PythonJudgeSystem-ICP && conda run -n PythonJudgeSystem python src/main.py oneshot --lecture LECTURE_ID --assignment ASSIGNMENT_ID --dry-run
Replace both `LECTURE_ID` and `ASSIGNMENT_ID` with actual values.

### 10. Link Assignments for Concurrent Lectures

If the user asks to prepare the same rules for another lecture (e.g., creating soft links for `lecture_B`), follow this process:

1. Fetch the assignment list for the second lecture to get the new `ASSIGNMENT_ID`s:
```bash
cd /home/seonghoon/2026-PythonJudgeSystem-ICP && conda run -n PythonJudgeSystem python -c "
from dotenv import load_dotenv; load_dotenv()
from src.infrastructure.snowboard import SnowBoard
import urllib3; urllib3.disable_warnings()
sb = SnowBoard()
sb.s.timeout = 10 
try:
    df = sb.list_assignments('NEW_LECTURE_ID')
    print(df[['id_assignment', '과제', '종료 일시']].to_string())
except Exception as e:
    print('Failed:', e)
"
```
2. Match the exact assignments from the first lecture to the new `ASSIGNMENT_ID`s in the second lecture based on the title.
3. Create soft links in the `assignments/` folder (DO NOT copy the whole directory):
```bash
cd /home/seonghoon/2026-PythonJudgeSystem-ICP/assignments
ln -s ORIGINAL_ID NEW_ID
```
4. Verify the symlinks using `ls -l`.

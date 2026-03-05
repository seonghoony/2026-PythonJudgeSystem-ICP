# Python Judge System (2026)

A Docker-based Python assignment grading system with Snowboard (LMS) integration.

## Lecture ID

| ID | Section |
|---|---|
| 86345 | 001분반 |
| 86347 | 003분반 |

## Setup & Dependencies

This project uses a strict Conda environment policy.

### 1. Create Conda Environment
Use `requirements.txt` or the direct command below.

```bash
conda create -n PythonJudgeSystem -c conda-forge python=3.14 pydantic pyyaml pandas requests beautifulsoup4 docker-py lxml pymysql python-dotenv
conda activate PythonJudgeSystem
```

**Libraries:**
- `python=3.14`: Runtime
- `pydantic`: Data validation
- `pyyaml`: Configuration parsing
- `pandas`: Data handling
- `requests`, `beautifulsoup4`, `lxml`: Snowboard Crawler
- `docker-py`: Sandbox management
- `pymysql`: Database connectivity
- `python-dotenv`: Environment variable management

### 2. Configure Environment
Create a `.env` file in the project root:
```env
# Database
DB_HOST=localhost
DB_USER=root
DB_PASSWORD=your_password
DB_NAME=PythonJudgeSystem

# Snowboard Login
SNOWBOARD_USER=your_id
SNOWBOARD_PASSWORD=your_pw

# Telegram Notifications
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### 3. Initialize Database
```bash
python src/infrastructure/init_db.py
```

## CLI Usage (`src/main.py`)

The system is managed via a single CLI entrypoint.

### Monitor (Default Loop)
Continuously fetches new submissions (`requiregrading`), grades them, and uploads scores.

- **Daemon Mode** (Infinite Loop using Config):
  ```bash
  python src/main.py monitor
  ```
  *Reads settings from `config/monitor.yaml`.*

- **Manual Override**:
  ```bash
  python src/main.py monitor --lecture 86347 --assignment 1961959
  ```

- **Force Re-Evaluation** (Dangerous):
  ```bash
  python src/main.py monitor --lecture 86347 --assignment 1961959 --force
  ```
  *Loops while forcing full re-evaluation of ALL history (fetch `submitted` status). Use with caution or `oneshot` preferred.*

### Oneshot (Single Pass)
Perform a single fetch-grade-upload cycle and exit. Ideal for manual triggering or debugging.

- **Standard Run**:
  ```bash
  python src/main.py oneshot --lecture 86347 --assignment 1961959
  ```

- **Force Re-Run** (Updates History):
  ```bash
  python src/main.py oneshot --lecture 86347 --assignment 1961959 --force
  ```
  *Fetches entire submission history, creating a new "Forced" entry for every student, and re-grades the latest version.*

### Evaluate Local File
Debug a specific file locally.
```bash
python src/main.py eval --assignment 1961959 --submission /tmp/solution_1961959.py --build
```

## Configuration

- **Assignment Rules**: `assignments/<id>/assignment.yaml`
- **Monitor Settings**: `config/monitor.yaml`
  ```yaml
  refresh_interval: 5
  lectures:
    - 86345 # 001분반
    - 86347 # 003분반
  blacklist:
    - 1961998 # 1분반 개발환경 구축
    - 1961958 # 3분반 개발환경 구축
  ```

## Verdicts
The system assigns one of the following verdicts to each submission:

- **AC (Accepted)**: The submission produced correct output for all test cases.
- **WA (Wrong Answer)**: The submission produced incorrect output for at least one test case.
- **TLE (Time Limit Exceeded)**: The submission exceeded the execution time limit.
- **MLE (Memory Limit Exceeded)**: The submission exceeded the memory limit.
- **RTE (Runtime Error)**: The submission raised an unhandled exception (e.g., SyntaxError, ValueError).
- **SYS (System Error)**: An internal system error occurred during grading (e.g., Docker failure).

# Grading Rules Guideline

Assignments in this system are folder-based and support two modes: **Standard Judge** (I/O) and **Special Judge** (Custom Logic).

## 1. Directory Structure

Each assignment must have a dedicated folder in the `assignments/` directory named with its **Assignment ID**.

```
assignments/
└── [Assignment_ID]/          # e.g., 1802077
    ├── assignment.yaml       # Configuration file (Required)
    ├── testcases/            # Test Case Directory (Standard Mode)
    │   ├── 1/
    │   │   ├── input.txt
    │   │   └── output.txt
    │   ├── 2/
    │   │   ├── input.txt
    │   │   └── output.txt
    │   └── ...
    ├── run_after.py          # Custom Check Script (Optional, fuzzy output matching)
    └── evaluator.py          # Full Control Script (Special Mode Only)
```

## 2. Configuration (`assignment.yaml`)

Every assignment requires an `assignment.yaml` file defining metadata and resource limits.
You can also specify **3rd Party Conda Libraries** required for the assignment.

```yaml
id: "1808032"
name: "Assignment Name"
type: "standard"  # Options: "standard", "special"

resources:
  cpu_count: 1
  memory_limit: "128m"  # e.g., 128m, 512m, 1g
  timeout: 5            # Seconds
  network_disabled: true

build:
  base_image: "condaforge/miniforge3"  # Default environment
  requirements:  # List extra libraries here
    - numpy
    - pandas
    - scipy
```

## 3. Standard Judge (I/O Based)

Standard Judge runs the student's code against input files and compares the output with expected output files.

### 3.1. Test Cases
- Create numbered subdirectories in `testcases/` (e.g., `1/`, `2/`, `3/`).
- Each subdirectory contains `input.txt` and `output.txt`.
- The system automatically discovers and sorts test case directories.

### 3.2. Custom Output Checking (`run_after.py`)
By default, the system performs an **Exact Match** (strip whitespace).
To implement permissive or fuzzy matching (e.g., ignoring prompts like "Enter number:"), create a `run_after.py`.

**Template:**
```python
def check(output, expected):
    """
    Args:
        output (str): Student's stdout capture.
        expected (str): Content of output_X.txt.
    Returns:
        bool: True if pass, False if fail.
    """
    clean_out = output.strip()
    clean_exp = expected.strip()
    
    # Example: Check if expected answer appears at the END of output
    if clean_out.endswith(clean_exp):
        return True
    
    return clean_out == clean_exp
```

## 4. Special Judge (Interactive / Complex Logic)

Use **Special Judge** when:
- The assignment requires interactive input/output (e.g., a game loop).
- Validity depends on internal state or multiple valid outputs.
- You need to run unit tests (pytest) instead of I/O.

### 4.1. Configuration
Set `type: "special"` in `assignment.yaml`.

### 4.2. Evaluator Script (`evaluator.py`)
You must provide an `evaluator.py` which fully controls the grading process.
The system executes this script natively (inside the container).

**Requirements**:
- The script should import/execute the student's code (`Target.py`).
- It must print JSON-formatted result to stdout (or specific file descriptor) if custom reporting is needed, OR simply raise exceptions on failure.
- *(Note: Specific API for Special Judge is defined in `src/core/special_judge.py` - currently implemented as running a custom script that returns a Verdict).*

## 5. Telegram Notifications

The system sends Telegram alerts for monitor start/stop, loop errors, and grading failures. Configure via `.env`:
- `TELEGRAM_TOKEN`: Bot API token
- `TELEGRAM_CHAT_ID`: Target chat ID

Notifications fail silently — they never crash the grading system.

## 6. File Validation

Submissions are validated before evaluation:
- **Empty file** → Score 0, comment in Korean
- **Wrong extension** (not `.py`) → Score 0
- **Jupyter Notebook disguised as `.py`** → Score 0

# Python Judge System (2026)

A Docker-based Python assignment grading system with Snowboard (LMS) integration.

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

# Snowboard Login (Optional, for automatic login)
SNOWBOARD_USER=your_id
SNOWBOARD_PASSWORD=your_pw
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
  python src/main.py monitor --lecture 80794 --assignment 1808032
  ```

- **Force Re-Evaluation** (Dangerous):
  ```bash
  python src/main.py monitor --lecture 80794 --assignment 1808032 --force
  ```
  *Loops while forcing full re-evaluation of ALL history (fetch `submitted` status). Use with caution or `oneshot` preferred.*

### Oneshot (Single Pass)
Perform a single fetch-grade-upload cycle and exit. Ideal for manual triggering or debugging.

- **Standard Run**:
  ```bash
  python src/main.py oneshot --lecture 80794 --assignment 1808032
  ```

- **Force Re-Run** (Updates History):
  ```bash
  python src/main.py oneshot --lecture 80794 --assignment 1808032 --force
  ```
  *Fetches entire submission history, creating a new "Forced" entry for every student, and re-grades the latest version.*

### Evaluate Local File
Debug a specific file locally.
```bash
python src/main.py eval --assignment 1808032 --submission tests/sample.py
```

## Configuration

- **Assignment Rules**: `assignments/<id>/assignment.yaml`
- **Monitor Settings**: `config/monitor.yaml`
  ```yaml
  refresh_interval: 60
  lectures:
    - 80794
  blacklist:
    - 1234567
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
└── [Assignment_ID]/          # e.g., 1808032
    ├── assignment.yaml       # Configuration file (Required)
    ├── testcases/            # Test Case Directory (Standard Mode)
    │   ├── input_1.txt
    │   ├── output_1.txt
    │   ├── input_2.txt
    │   └── output_2.txt
    ├── run_after.py          # Custom Check Script (Optional for Standard, creating 'Semi-Special' behavior)
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
- Place pairs of files in the `testcases/` directory.
- Naming convention: `input_X.txt` and `output_X.txt` (where X is an index or name).
- The system automatically discovers matching pairs.

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


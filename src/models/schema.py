from typing import List, Optional, Literal, Dict, Any
from pydantic import BaseModel, Field

class Resources(BaseModel):
    cpu_count: int = 1
    memory_limit: str = "128m"
    timeout: int = 5
    network_disabled: bool = True

class BuildOptions(BaseModel):
    base_image: str = "condaforge/miniforge3"
    requirements: List[str] = Field(default_factory=list)

class Paths(BaseModel):
    student_code: str = "/Target.py"
    grader_code: str = "/grader.py"

class GradingConfig(BaseModel):
    policy: Literal["all_or_nothing", "partial"] = "all_or_nothing"

class AssignmentConfig(BaseModel):
    id: str
    name: str
    type: Literal["standard", "special", "token"]
    resources: Resources = Field(default_factory=Resources)
    build: BuildOptions = Field(default_factory=BuildOptions)
    paths: Paths = Field(default_factory=Paths)
    grading: GradingConfig = Field(default_factory=GradingConfig)

class TestCase(BaseModel):
    id: str
    input_path: str
    output_path: str
    points: float = 10.0

class TestCaseResult(BaseModel):
    test_case_id: str
    is_correct: bool
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    time_elapsed: float = 0.0
    memory_used: int = 0
    message: str = ""
    expected_output: Optional[str] = None
    input_data: Optional[str] = None

class EvaluationResult(BaseModel):
    submission_id: str
    assignment_id: str
    student_id: str
    total_score: float
    results: List[TestCaseResult]
    system_error: Optional[str] = None

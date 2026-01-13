from abc import ABC, abstractmethod
from pathlib import Path
from src.models.schema import AssignmentConfig, EvaluationResult

class JudgeEngine(ABC):
    def __init__(self, config: AssignmentConfig):
        self.config = config

    @abstractmethod
    def evaluate(self, submission_path: Path, assignment_dir: Path, student_info: dict = None) -> EvaluationResult:
        """
        Evaluates a submission.
        
        Args:
            submission_path: Path to the student's submission file or directory.
            assignment_dir: Path to the assignment directory (containing testcases, etc.)
            student_info: Optional dictionary containing 'student_id', 'name', etc.
            
        Returns:
            EvaluationResult: The result of the evaluation.
        """
        pass

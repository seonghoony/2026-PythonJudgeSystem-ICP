import subprocess
import sys
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List, Dict
from src.models.schema import AssignmentConfig, Resources

class DockerSandbox:
    @staticmethod
    def _run_cmd(cmd: str, timeout: int = 300) -> subprocess.CompletedProcess:
        return subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )

    @staticmethod
    def build_image(assignment_config: AssignmentConfig) -> str:
        """
        Builds the docker image for the assignment only if changed.
        Returns the image tag.
        """
        import hashlib
        import json
        
        image_tag = f"image_{assignment_config.id}"
        
        dockerfile_content = f"FROM {assignment_config.build.base_image}\n"
        dockerfile_content += "RUN mamba install -y python=3.14\n"
        
        if assignment_config.build.requirements:
            reqs = " ".join(assignment_config.build.requirements)
            dockerfile_content += f"RUN pip install --no-cache-dir {reqs}\n"
        
        dockerfile_content += "WORKDIR /app\n"
        
        build_hash = hashlib.md5(dockerfile_content.encode()).hexdigest()
        
        check_cmd = f"docker inspect {image_tag}"
        res = DockerSandbox._run_cmd(check_cmd, timeout=10)
        
        needs_build = True
        if res.returncode == 0:
            try:
                data = json.loads(res.stdout)
                if data:
                    existing_labels = data[0].get('Config', {}).get('Labels', {})
                    if existing_labels and existing_labels.get('build_hash') == build_hash:
                        needs_build = False
            except Exception:
                pass
        
        if not needs_build:
            return image_tag

        print(f"[Sandbox] Building image {image_tag}...")
        
        with tempfile.TemporaryDirectory() as build_ctx:
            ctx_path = Path(build_ctx)
            (ctx_path / "Dockerfile").write_text(dockerfile_content)

            cmd = f"docker build -t {image_tag} --label build_hash={build_hash} {ctx_path}"
            
            res = DockerSandbox._run_cmd(cmd, timeout=600)
            
            if res.returncode != 0:
                raise RuntimeError(f"Docker build failed: {res.stderr}")
                
            print(f"[Sandbox] Image {image_tag} built successfully.")
            return image_tag

    @staticmethod
    def run(
        assignment_config: AssignmentConfig,
        submission_path: Path,
        assignment_dir: Path,
        mode: str = "standard",
        env_vars: Dict[str, str] = None
    ) -> Dict[str, str]:
        """
        Runs the container with the submission.
        Returns a dictionary with raw stdout/stderr/exit_code.
        """
        image_tag = f"image_{assignment_config.id}"
        resources = assignment_config.resources
        
        mount_cmd = ""
        if submission_path.is_dir():
            # /submission은 매 채점마다 호스트의 임시 디렉토리에서 새로 생성되어 마운트되므로,
            # 컨테이너 내부에서 학생 코드가 수정하더라도 다른 학생/실행에 영향이 없다.
            # rw로 마운트해야 run_before.py 메커니즘이 wrapper 파일을 쓸 수 있다.
            mount_cmd += f"-v {submission_path.absolute()}:/submission:rw "
        else:
            mount_cmd += f"-v {submission_path.absolute()}:/Target.py:ro "
            
        mount_cmd += f"-v {assignment_dir.absolute()}:/assignment:ro "

        launcher_path = Path("src/utils/launcher_script.py").absolute()
        mount_cmd += f"-v {launcher_path}:/launcher.py:ro "
        
        net_flag = "--net none" if resources.network_disabled else ""
        
        env_cmd = f"-e JUDGE_MODE={mode} -e JUDGE_TIMEOUT={resources.timeout} "
        if env_vars:
            for k, v in env_vars.items():
                env_cmd += f"-e {k}='{v}' "

        cmd = (
            f"docker run --rm "
            f"--cpus={resources.cpu_count} "
            f"--memory={resources.memory_limit} "
            f"{net_flag} "
            f"--tmpfs /tmp:rw,size=64m,mode=1777 "
            f"{env_cmd}"
            f"{mount_cmd} "
            f"{image_tag} "
            f"python3 /launcher.py"
        )
        
        try:
            res = DockerSandbox._run_cmd(cmd, timeout=resources.timeout + 5)
            return {
                "stdout": res.stdout,
                "stderr": res.stderr,
                "exit_code": res.returncode
            }
        except subprocess.TimeoutExpired:
            return {
                "stdout": "",
                "stderr": "Docker run timed out (Hard Limit).",
                "exit_code": 124
            }

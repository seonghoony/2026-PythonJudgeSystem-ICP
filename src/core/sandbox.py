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
        
        # 1. Generate Dockerfile Content
        dockerfile_content = f"FROM {assignment_config.build.base_image}\n"
        dockerfile_content += "RUN mamba install -y python=3.14\n"
        
        if assignment_config.build.requirements:
            reqs = " ".join(assignment_config.build.requirements)
            dockerfile_content += f"RUN pip install --no-cache-dir {reqs}\n"
        
        dockerfile_content += "WORKDIR /app\n"
        
        # 2. Compute Hash
        build_hash = hashlib.md5(dockerfile_content.encode()).hexdigest()
        
        # 3. Check Existing Image
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
                pass # JSON parse error or other, force build
        
        if not needs_build:
            # print(f"[Sandbox] Using cached image {image_tag}.")
            return image_tag

        # 4. Build
        print(f"[Sandbox] Building image {image_tag}...")
        
        with tempfile.TemporaryDirectory() as build_ctx:
            ctx_path = Path(build_ctx)
            (ctx_path / "Dockerfile").write_text(dockerfile_content)
            
            # Add label to build command
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
        
        # Prepare mounts
        # 1. Submission: mapped to /submission (directory) or /Target.py (file)
        # Architecture says: "Mount Student Submission -> Read-Only"
        # Since we use launcher, we mount submission to /submission dir or /Target.py
        # Current implementation assumes auto-detection in launcher.
        
        mount_cmd = ""
        if submission_path.is_dir():
            mount_cmd += f"-v {submission_path.absolute()}:/submission:ro "
        else:
            mount_cmd += f"-v {submission_path.absolute()}:/Target.py:ro "
            
        # 2. Assignment Data: /assignment
        mount_cmd += f"-v {assignment_dir.absolute()}:/assignment:ro "
        
        # 3. Launcher Script
        launcher_path = Path("src/utils/launcher_script.py").absolute()
        mount_cmd += f"-v {launcher_path}:/launcher.py:ro " 
        
        # Network
        net_flag = "--net none" if resources.network_disabled else ""
        
        # Environment Variables
        env_cmd = f"-e JUDGE_MODE={mode} -e JUDGE_TIMEOUT={resources.timeout} "
        if env_vars:
            for k, v in env_vars.items():
                # Simple sanitization to prevent injection if value has spaces/quotes
                # Ideally use shlex.quote but here we assume safe alphanum or use docker API
                # For shell=True, quotes are risky. 
                # Better: only allow specific set or trust caller.
                # Let's wrap value in quotes.
                env_cmd += f"-e {k}='{v}' "

        # Docker Command
        # We run the launcher
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
            if res.returncode != 0:
                pass # Caller handles partial results or we allow nonzero exit (e.g. RTE)
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

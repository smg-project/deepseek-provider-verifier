"""Inspect wheel/sdist and run the installed four-request example off checkout."""

import argparse
import json
import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import zipfile
from pathlib import Path


def execute(args, cwd, env):
    return subprocess.run(
        args, cwd=cwd, env=env, check=True, text=True, capture_output=True, timeout=120
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    (wheel,) = (root / "dist").glob("*.whl")
    (sdist,) = (root / "dist").glob("*.tar.gz")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for folder in ["profiles", "cases", "schemas", "configs", "docs/calibration"]:
            for path in (root / folder).rglob("*"):
                if path.is_file():
                    assert (
                        "deepseek_provider_verifier/" + str(path.relative_to(root))
                        in names
                    ), path
        assert "deepseek_provider_verifier/synthetic_fixture.py" in names
    with tarfile.open(sdist) as archive:
        names = {name.split("/", 1)[-1] for name in archive.getnames()}
        for file in [
            "uv.lock",
            "pyproject.toml",
            "README.md",
            "LICENSE",
            "CONTRIBUTING.md",
            "docs/reproducibility.md",
            "scripts/check_release.py",
            ".github/workflows/test.yml",
        ]:
            assert file in names, file
        for folder in [
            "profiles",
            "cases",
            "schemas",
            "configs",
            "tests",
            "docs/calibration",
        ]:
            for path in (root / folder).rglob("*"):
                if path.is_file() and "__pycache__" not in path.parts:
                    assert str(path.relative_to(root)) in names, path
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "PYTHONPATH",
            "VIRTUAL_ENV",
            "DEEPSEEK_API_KEY",
            "CANDIDATE_API_KEY",
        }
    }
    with tempfile.TemporaryDirectory(prefix="dpv-wheel-") as directory:
        outside = Path(directory)
        execute(
            [args.uv, "venv", "--python", sys.executable, str(outside / "venv")],
            outside,
            env,
        )
        python = str(outside / "venv/bin/python")
        dpv = str(outside / "venv/bin/dpv")
        locked = execute(
            [
                args.uv,
                "export",
                "--locked",
                "--no-dev",
                "--no-emit-project",
                "--format",
                "requirements-txt",
            ],
            root,
            env,
        )
        requirements = outside / "requirements.txt"
        requirements.write_text(locked.stdout)
        execute(
            [
                args.uv,
                "pip",
                "install",
                "--python",
                python,
                "-r",
                str(requirements),
                str(wheel),
            ],
            outside,
            env,
        )
        execute([dpv, "--help"], outside, env)
        resource = execute(
            [
                python,
                "-c",
                'from importlib.resources import files; print(files("deepseek_provider_verifier").joinpath("configs", "offline-fixture.example.toml").read_text(), end="")',
            ],
            outside,
            env,
        )
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        (outside / "fixture.toml").write_text(
            resource.stdout.replace(":8765/", f":{port}/")
        )
        server = subprocess.Popen(
            [
                python,
                "-m",
                "deepseek_provider_verifier.synthetic_fixture",
                "--port",
                str(port),
                "--max-requests",
                "4",
            ],
            cwd=outside,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            for _ in range(100):
                if server.poll() is not None:
                    raise RuntimeError("Installed fixture stopped before readiness")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                raise RuntimeError("Installed fixture readiness timed out")
            execute([dpv, "plan", "--config", "fixture.toml"], outside, env)
            execute(
                [
                    dpv,
                    "run",
                    "--config",
                    "fixture.toml",
                    "--endpoint",
                    "fixture",
                    "--out",
                    "evidence",
                ],
                outside,
                env,
            )
            server.wait(timeout=10)
            assert server.returncode == 0
            result = json.loads((outside / "evidence/summary.json").read_text())
            assert result["complete"] and result["exit_code"] == 0
            assert result["counts"]["PASS"] == 4
            assert sum(result["counts"].values()) == 4
            assert result["budget_usage"] == {"fixture": 4}
        finally:
            if server.poll() is None:
                server.terminate()
                server.wait(timeout=10)
            if server.stdout:
                server.stdout.close()
    print(
        "Wheel/sdist contents verified; fresh installed CLI outside checkout: 4/4 synthetic trials PASS, 4 requests."
    )


if __name__ == "__main__":
    main()

"""Inspect distributions and verify installed legacy/depth suites off checkout."""

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


def run_fixture(
    python, dpv, outside, env, config_text, evidence, profile=None, command="run"
):
    profile_args = ["--profile", str(profile)] if profile else []
    ready = outside / f"{evidence}-ready.json"
    ready.unlink(missing_ok=True)
    server = subprocess.Popen(
        [
            python,
            "-m",
            "deepseek_provider_verifier.synthetic_fixture",
            "--port",
            "0",
            "--ready-file",
            str(ready),
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
            if not ready.exists():
                time.sleep(0.05)
                continue
            port = json.loads(ready.read_text())["port"]
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            raise RuntimeError("Installed fixture readiness timed out")
        (outside / "fixture.toml").write_text(
            config_text.replace(":8765/", f":{port}/")
        )
        execute([dpv, "plan", "--config", "fixture.toml", *profile_args], outside, env)
        execute(
            [
                dpv,
                command,
                "--config",
                "fixture.toml",
                "--endpoint",
                "fixture",
                "--out",
                evidence,
                *profile_args,
            ],
            outside,
            env,
        )
        server.wait(timeout=10)
        assert server.returncode == 0
        result = json.loads((outside / evidence / "summary.json").read_text())
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", default="uv")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    (wheel,) = (root / "dist").glob("*.whl")
    (sdist,) = (root / "dist").glob("*.tar.gz")
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for folder in [
            "profiles",
            "policies",
            "cases",
            "schemas",
            "configs",
            "docs/calibration",
        ]:
            for path in (root / folder).rglob("*"):
                if path.is_file():
                    assert (
                        "deepseek_provider_verifier/" + str(path.relative_to(root))
                        in names
                    ), path
        assert "deepseek_provider_verifier/synthetic_fixture.py" in names
        assert "deepseek_provider_verifier/docs/reliability-depth.md" in names
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
            "policies",
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
        compatibility = execute(
            [
                python,
                "-c",
                'from importlib.resources import files; print(files("deepseek_provider_verifier").joinpath("configs", "self-hosted.example.toml").read_text(), end="")',
            ],
            outside,
            env,
        ).stdout
        for policy in ("self-hosted", "official-parity"):
            config = outside / f"{policy}.toml"
            config.write_text(
                compatibility.replace("deepseek-self-hosted-", f"deepseek-{policy}-")
            )
            planned = execute([dpv, "plan", "--config", str(config)], outside, env)
            value = json.loads(planned.stdout)["manifest"]
            assert len(value["cases"]) == 99 and value["request_ceiling"] == 123
            assert value["profile_snapshot"]["id"] == f"deepseek-{policy}-2026-09-22-v1"
        resource = execute(
            [
                python,
                "-c",
                'from importlib.resources import files; print(files("deepseek_provider_verifier").joinpath("configs", "offline-fixture.example.toml").read_text(), end="")',
            ],
            outside,
            env,
        )
        run_fixture(python, dpv, outside, env, resource.stdout, "evidence")
        run_fixture(
            python,
            dpv,
            outside,
            env,
            resource.stdout,
            "acceptance-evidence",
            command="verify",
        )
        accepted = json.loads(
            (outside / "acceptance-evidence/acceptance.json").read_text()
        )
        assert accepted["verdict"] == "PASS" and accepted["exit_code"] == 0
        execute(
            [dpv, "assess", "acceptance-evidence", "--out", "assessment"], outside, env
        )
        from xml.etree import ElementTree as ET

        for name in ["acceptance.json", "acceptance.md", "acceptance.junit.xml"]:
            assert (outside / "assessment" / name).read_bytes() == (
                outside / "acceptance-evidence" / name
            ).read_bytes()
        junit = ET.parse(outside / "assessment/acceptance.junit.xml").getroot()
        assert junit.attrib["failures"] == "0" and junit.attrib["errors"] == "0"
        verification = execute(
            [
                python,
                "-c",
                'from importlib.resources import files; print(files("deepseek_provider_verifier").joinpath("configs", "self-hosted-verify.example.toml").read_text(), end="")',
            ],
            outside,
            env,
        ).stdout
        (outside / "verification.toml").write_text(verification)
        planned = execute(
            [
                dpv,
                "plan",
                "--config",
                "verification.toml",
                "--policy",
                "official-compatible-v1",
            ],
            outside,
            env,
        )
        assert json.loads(planned.stdout)["acceptance"]["request_ceiling"] == 210
        inventory = [
            ("repeatability", "repeatability", 200, 250),
            ("repeatability-expanded", "repeatability-expanded", 800, 1000),
            ("workflows-small", "workflows", 32, 160),
            ("workflows-full", "workflows", 56, 384),
            ("schemas", "schemas", 128, 128),
            ("sizes-small", "sizes", 24, 48),
            ("sizes-large", "sizes-large", 44, 92),
        ]
        depth_config = None
        depth_profile = None
        for suite, config_name, trial_count, request_count in inventory:
            text = execute(
                [
                    python,
                    "-c",
                    f'from importlib.resources import files; print(files("deepseek_provider_verifier").joinpath("configs", "depth-{config_name}.example.toml").read_text(), end="")',
                ],
                outside,
                env,
            ).stdout
            if suite == "workflows-full":
                text = text.replace(
                    'suite = "workflows-small"', 'suite = "workflows-full"'
                )
            config = outside / f"depth-{suite}.toml"
            config.write_text(text)
            planned = json.loads(
                execute([dpv, "plan", "--config", str(config)], outside, env).stdout
            )
            m = planned["manifest"]
            assert (len(m["cases"]), m["request_ceiling"]) == (
                trial_count,
                request_count,
            )
            assert m["budgets"]["retries"] == 0 and m["budgets"]["concurrency"] == 1
            assert (
                planned["resource_summary"]["aggregate_request_byte_ceiling"]
                == request_count * 8388608
            )
            if suite == "repeatability":
                depth_config, depth_profile = text, m["profile_snapshot"]
        depth_profile["presets"] = {
            "installed-smoke": {
                **depth_profile["presets"]["repeatability"],
                "case_ids": ["R01"],
                "max_requests_per_protocol": 2,
                "max_requests_per_endpoint": 4,
                "max_total_requests": 8,
            }
        }
        custom = outside / "depth-profile.json"
        custom.write_text(json.dumps(depth_profile))
        text = (
            depth_config.replace('suite = "repeatability"', 'suite = "installed-smoke"')
            .replace("repetitions = 5", "repetitions = 2")
            .replace("[endpoints.candidate]", "[endpoints.fixture]")
            .replace("http://localhost:30000/v1", "http://127.0.0.1:8765/v1")
            .replace("your-served-deepseek-model", "synthetic-fixture-model")
        )
        run_fixture(python, dpv, outside, env, text, "depth-evidence", custom)
        analysis = json.loads((outside / "depth-evidence/reliability.json").read_text())
        assert len(analysis["groups"]) == 2
        assert {g["protocol"] for g in analysis["groups"]} == {"chat", "responses"}
        assert sum(g["http_attempts"] for g in analysis["groups"]) == 4
        assert all(
            g["counts"]["PASS"] == 2 and g["distinct_prompts"] == 1
            for g in analysis["groups"]
        )
        regenerated = execute(
            [dpv, "report", "depth-evidence", "--format", "markdown"], outside, env
        ).stdout
        assert regenerated == (outside / "depth-evidence/summary.md").read_text()
    print(
        "Wheel/sdist verified; seven depth plans; installed legacy 4/4, acceptance 4/4, and depth 4/4 synthetic trials PASS, twelve local requests total."
    )


if __name__ == "__main__":
    main()

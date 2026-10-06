from __future__ import annotations

import json
import shlex
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DOCKER_IMAGE = "finance-saas-preflight:local"
RENDER_DOCKER_IMAGE = "finance-saas-render-preflight:local"
EXIT_SUCCESS = 0
EXIT_FAILURE = 1
EXIT_INTERRUPTED = 130
SECRET_EXCLUDED_FILES = (
    r"(^|/)(\.git|\.venv|node_modules|dist)(/|$)|(^|/)\.env$"
)
SECRET_EXCLUDED_LINES = r"username[:]password[@]host"


class PreflightError(RuntimeError):
    """Represents a check failure that is safe to show in terminal output."""


@dataclass(frozen=True)
class SecretFinding:
    filename: str
    line_number: int | None
    secret_type: str


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    runner: Callable[[], None]


def print_command(command: tuple[str, ...]) -> None:
    print(f"$ {shlex.join(command)}", flush=True)


def run_command(*, command: tuple[str, ...]) -> None:
    print_command(command)

    try:
        subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            check=True,
        )
    except FileNotFoundError as error:
        executable = command[0]
        raise PreflightError(
            f"Required executable was not found: {executable}",
        ) from error
    except subprocess.CalledProcessError as error:
        raise PreflightError(
            f"Command exited with status {error.returncode}.",
        ) from error


def check_python_compilation() -> None:
    run_command(
        command=(
            sys.executable,
            "-m",
            "compileall",
            "-q",
            "app",
        ),
    )


def check_frontend_build() -> None:
    run_command(
        command=(
            "npm",
            "--prefix",
            "frontend",
            "run",
            "build",
        ),
    )


def check_local_docker_build() -> None:
    run_command(
        command=(
            "docker",
            "build",
            "--file",
            "Dockerfile",
            "--tag",
            LOCAL_DOCKER_IMAGE,
            ".",
        ),
    )


def check_render_docker_build() -> None:
    run_command(
        command=(
            "docker",
            "build",
            "--file",
            "Dockerfile.render",
            "--tag",
            RENDER_DOCKER_IMAGE,
            ".",
        ),
    )


def parse_secret_findings(payload: object) -> list[SecretFinding]:
    if not isinstance(payload, dict):
        raise PreflightError("detect-secrets returned a non-object JSON payload.")

    raw_results: object = payload.get("results")
    if not isinstance(raw_results, dict):
        raise PreflightError("detect-secrets output does not contain a results object.")

    findings: list[SecretFinding] = []
    for raw_filename, raw_file_findings in raw_results.items():
        is_valid_file_entry = isinstance(raw_filename, str) and isinstance(
            raw_file_findings,
            list,
        )
        if not is_valid_file_entry:
            raise PreflightError("detect-secrets returned an invalid result entry.")

        for raw_finding in raw_file_findings:
            if not isinstance(raw_finding, dict):
                raise PreflightError("detect-secrets returned an invalid finding.")

            raw_secret_type: object = raw_finding.get("type")
            raw_line_number: object = raw_finding.get("line_number")
            is_valid_line_number = (
                raw_line_number is None
                or isinstance(raw_line_number, int)
                and not isinstance(raw_line_number, bool)
            )
            if not isinstance(raw_secret_type, str) or not is_valid_line_number:
                raise PreflightError("detect-secrets returned incomplete finding metadata.")

            findings.append(
                SecretFinding(
                    filename=raw_filename,
                    line_number=raw_line_number,
                    secret_type=raw_secret_type,
                ),
            )

    return findings


def check_secrets() -> None:
    command = (
        sys.executable,
        "-m",
        "detect_secrets",
        "scan",
        "--all-files",
        "--exclude-files",
        SECRET_EXCLUDED_FILES,
        "--exclude-lines",
        SECRET_EXCLUDED_LINES,
    )
    print_command(command)

    try:
        scan = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError as error:
        raise PreflightError(
            f"Required executable was not found: {command[0]}",
        ) from error

    if scan.stderr:
        print(scan.stderr, file=sys.stderr, end="")

    if scan.returncode != 0:
        if scan.stdout:
            print(scan.stdout, end="")
        raise PreflightError(
            f"detect-secrets exited with status {scan.returncode}.",
        )

    try:
        payload: object = json.loads(scan.stdout)
    except json.JSONDecodeError as error:
        raise PreflightError("detect-secrets returned invalid JSON output.") from error

    findings = parse_secret_findings(payload)
    if not findings:
        return

    print("Potential secrets detected:", file=sys.stderr)
    for finding in findings:
        location = finding.filename
        if finding.line_number is not None:
            location = f"{location}:{finding.line_number}"
        print(
            f"  - {location} ({finding.secret_type})",
            file=sys.stderr,
        )

    raise PreflightError(
        f"Found {len(findings)} potential secret(s). Secret values were not printed.",
    )


def build_checks() -> tuple[PreflightCheck, ...]:
    return (
        PreflightCheck(
            name="Python backend compilation",
            runner=check_python_compilation,
        ),
        PreflightCheck(
            name="React production build",
            runner=check_frontend_build,
        ),
        PreflightCheck(
            name="Local Dockerfile build",
            runner=check_local_docker_build,
        ),
        PreflightCheck(
            name="Render Dockerfile build",
            runner=check_render_docker_build,
        ),
        PreflightCheck(
            name="Secret scan",
            runner=check_secrets,
        ),
    )


def main() -> int:
    checks = build_checks()
    total_checks = len(checks)
    preflight_started_at = time.monotonic()

    print(f"Running {total_checks} preflight checks from {PROJECT_ROOT}", flush=True)

    try:
        for check_number, check in enumerate(checks, start=1):
            print(
                f"\n[{check_number}/{total_checks}] {check.name}",
                flush=True,
            )
            check_started_at = time.monotonic()

            try:
                check.runner()
            except PreflightError as error:
                print(f"[FAIL] {check.name}", file=sys.stderr)
                print(f"Reason: {error}", file=sys.stderr)
                print("Preflight stopped after the first failure.", file=sys.stderr)
                return EXIT_FAILURE

            elapsed_seconds = time.monotonic() - check_started_at
            print(f"[PASS] {check.name} ({elapsed_seconds:.1f}s)", flush=True)
    except KeyboardInterrupt:
        print("\n[FAIL] Preflight interrupted by the user.", file=sys.stderr)
        return EXIT_INTERRUPTED

    total_seconds = time.monotonic() - preflight_started_at
    print(
        f"\nAll {total_checks} preflight checks passed ({total_seconds:.1f}s).",
        flush=True,
    )
    return EXIT_SUCCESS


if __name__ == "__main__":
    raise SystemExit(main())

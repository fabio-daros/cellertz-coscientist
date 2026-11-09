"""Local subprocess execution runtime.

Never uses a shell string. argv is always a token list.
On POSIX, each execution receives its own process session so a timeout
can terminate the complete execution process group.
"""

from __future__ import annotations

import os
import signal
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from cellertz.runtime.base import ExecutionRequest, ExecutionResult

RUNTIME_NAME = "local"
RUNTIME_VERSION = "local_execution_runtime_v2"
TERMINATION_GRACE_SECONDS = 2.0


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _validate_argv(argv: tuple[str, ...]) -> str | None:
    if not argv:
        return "ExecutionRequest.argv must not be empty."

    if any(not isinstance(part, str) or not part for part in argv):
        return "ExecutionRequest.argv must contain non-empty strings."

    if len(argv) == 1 and " " in argv[0]:
        return (
            "ExecutionRequest.argv must be structured tokens, "
            "not a shell string."
        )

    return None


def _terminate_process_group(
    process: subprocess.Popen[str],
    *,
    force: bool,
) -> None:
    if process.poll() is not None:
        return

    if os.name == "posix":
        sig = signal.SIGKILL if force else signal.SIGTERM

        try:
            os.killpg(process.pid, sig)
            return
        except ProcessLookupError:
            return

    try:
        if force:
            process.kill()
        else:
            process.terminate()
    except ProcessLookupError:
        pass


class LocalExecutionRuntime:
    """Run structured argv locally. No scientific interpretation."""

    name = RUNTIME_NAME
    version = RUNTIME_VERSION

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        started_at = _now()

        invalid = _validate_argv(request.argv)

        if invalid is not None:
            return ExecutionResult(
                status="failed",
                return_code=None,
                stdout="",
                stderr="",
                runtime_name=self.name,
                runtime_version=self.version,
                started_at=started_at,
                completed_at=_now(),
                error=invalid,
            )

        cwd = None if request.cwd is None else Path(request.cwd)

        env = None

        if request.env is not None:
            env = os.environ.copy()
            env.update(request.env)

        try:
            process = subprocess.Popen(
                list(request.argv),
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                start_new_session=(os.name == "posix"),
            )
        except OSError as error:
            return ExecutionResult(
                status="failed",
                return_code=None,
                stdout="",
                stderr="",
                runtime_name=self.name,
                runtime_version=self.version,
                started_at=started_at,
                completed_at=_now(),
                error=f"Process failed to start: {error}",
            )

        try:
            stdout, stderr = process.communicate(
                timeout=request.timeout_seconds
            )

        except subprocess.TimeoutExpired:
            _terminate_process_group(
                process,
                force=False,
            )

            try:
                stdout, stderr = process.communicate(
                    timeout=TERMINATION_GRACE_SECONDS
                )

            except subprocess.TimeoutExpired:
                _terminate_process_group(
                    process,
                    force=True,
                )

                stdout, stderr = process.communicate()

            return ExecutionResult(
                status="failed",
                return_code=None,
                stdout=stdout or "",
                stderr=stderr or "",
                runtime_name=self.name,
                runtime_version=self.version,
                started_at=started_at,
                completed_at=_now(),
                error="Process timed out.",
            )

        stdout = stdout or ""
        stderr = stderr or ""

        if process.returncode != 0:
            detail = (stderr or stdout).strip()

            message = (
                "Process failed with return code "
                f"{process.returncode}."
            )

            if detail:
                message = f"{message} {detail}"

            return ExecutionResult(
                status="failed",
                return_code=process.returncode,
                stdout=stdout,
                stderr=stderr,
                runtime_name=self.name,
                runtime_version=self.version,
                started_at=started_at,
                completed_at=_now(),
                error=message,
            )

        return ExecutionResult(
            status="complete",
            return_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            runtime_name=self.name,
            runtime_version=self.version,
            started_at=started_at,
            completed_at=_now(),
        )

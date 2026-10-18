"""Isolated code execution in a hardened, warm Docker container.

One long-lived container (no network, read-only rootfs, non-root, capped memory/cpu/pids) is started on
first use; every run is a fresh `python -I` process inside it, killed by `timeout` from within the
container so a runaway process can't outlive its budget.

Runs are serialised, and before each one every leftover process is killed and /tmp is wiped, so one run (or one
interview) can't leave anything behind for the next: no stashed hidden-test inputs, no background snooper.
The harness output is untrusted; see harness.py.
"""

import asyncio
import json
import logging
import secrets
from pathlib import Path

from ..config import get_settings

log = logging.getLogger(__name__)

CONTAINER = "interviewbud-sandbox"
HARNESS = (Path(__file__).parent / "harness.py").read_text()
SANDBOX_VERSION = "2"
# PID 1: reaps every orphaned or killed process (so they can't pile up against --pids-limit). Signals sent from
# inside the container can't kill a namespace's init, so the cleanup below can't take the container down.
REAPER = (
    "import os, time\n"
    "while True:\n"
    "    try:\n"
    "        os.wait()\n"
    "    except ChildProcessError:\n"
    "        time.sleep(0.5)\n"
)
_lock = asyncio.Lock()  # container lifecycle
_run_lock = asyncio.Lock()  # one run at a time
# As the sandbox user: SIGKILL every process except PID 1 (signals from inside a pid namespace can't kill its init)
# and this shell, then empty the tmpfs.
_CLEAN = "kill -9 -1 2>/dev/null; find /tmp -mindepth 1 -delete 2>/dev/null; true"


class SandboxError(RuntimeError):
    pass


async def _docker(*args: str, stdin: bytes | None = None, timeout: float = 30) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "docker",
        *args,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except TimeoutError:
        proc.kill()
        raise SandboxError("docker command timed out")
    return proc.returncode or 0, out.decode(errors="replace"), err.decode(errors="replace")


async def ensure_container() -> None:
    async with _lock:
        # Running *and* the current layout (reaper as PID 1); older containers get replaced.
        code, out, _ = await _docker("inspect", "-f", '{{.State.Running}} {{index .Config.Labels "ib.sandbox"}}',
                                     CONTAINER)
        if code == 0 and out.split() == ["true", SANDBOX_VERSION]:
            return
        await _docker("rm", "-f", CONTAINER)
        s = get_settings()
        code, _, err = await _docker(
            "run", "-d", "--name", CONTAINER,
            "--network", "none",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            "--memory", s.sandbox_memory, "--memory-swap", s.sandbox_memory,
            "--cpus", "1",
            "--pids-limit", "64",
            "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--user", "65534:65534",
            "--label", "app=interviewbud",
            "--label", f"ib.sandbox={SANDBOX_VERSION}",
            s.sandbox_python_image,
            "python", "-I", "-c", REAPER,
            timeout=120,
        )
        if code != 0:
            raise SandboxError(f"could not start sandbox: {err.strip()}")


async def execute(payload: dict, timeout_s: float | None = None) -> dict:
    """Run the harness with `payload`; returns the harness JSON (compile_error/results/value).
    A run whose output contains more than one result block is reported with tampered=True."""
    await ensure_container()
    limit = timeout_s or get_settings().sandbox_timeout_s
    marker = f"__IB_RESULT_{secrets.token_hex(12)}__"
    script = HARNESS + f"\nmain(json.loads({json.dumps(json.dumps({**payload, 'marker': marker}))}))\n"
    async with _run_lock:
        await _docker("exec", "--user", "65534:65534", CONTAINER, "sh", "-c", _CLEAN, timeout=15)
        code, out, err = await _docker(
            "exec", "-i", CONTAINER,
            "timeout", "-s", "KILL", str(int(limit)),
            "python", "-I", "-",
            stdin=script.encode(),
            timeout=limit + 15,
        )
    blocks = out.count(marker)
    if blocks == 0:
        if code in (137, 124):
            return {"compile_error": None, "results": [], "value": None, "killed": True,
                    "error": f"Execution killed after {limit:.0f}s (time or memory limit)"}
        raise SandboxError(f"sandbox failed (exit {code}): {(err or out)[-800:]}")
    if blocks > 1:
        log.warning("sandbox output had %d result blocks: tampering", blocks)
        return {"compile_error": None, "results": [], "value": None, "tampered": True,
                "error": "The program wrote extra result blocks to standard output, so this run can't be trusted."}
    return json.loads(out.split(marker, 1)[1].strip().splitlines()[0])

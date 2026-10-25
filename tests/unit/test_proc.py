"""V1.2 runner hardening (PLAN Stonic-4): input closed, output capped, no secret-named
environment, and on a timeout or a cancelled task the WHOLE tree ends — while a command
that finishes normally keeps what it started. Real (harmless) Python processes."""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import psutil
import pytest

from core.skills.proc import clean_env, run_contained

PY = sys.executable


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def _wait_pid(path: Path, seconds: float = 10) -> int:
    end = time.time() + seconds
    while time.time() < end:
        if path.exists() and path.read_text().strip():
            return int(path.read_text())
        time.sleep(0.1)
    raise AssertionError("the grandchild never started")


def test_timeout_ends_a_grandchild_whose_parent_already_left(tmp_path: Path) -> None:
    """A → B → C, B exits at once: C is no longer in A's tree. psutil alone misses it;
    the job object does not."""
    pid_file = tmp_path / "c.pid"
    b = (f"import subprocess,sys; p=subprocess.Popen([sys.executable,'-c',"
         f"'import time; time.sleep(60)']); open(r'{pid_file}','w').write(str(p.pid))")
    a = (f"import subprocess,sys,time; subprocess.run([sys.executable,'-c',{b!r}]); "
         f"time.sleep(60)")
    t0 = time.time()
    done = asyncio.run(run_contained([PY, "-c", a], cwd=str(tmp_path), timeout=4))
    grandchild = _wait_pid(pid_file)
    assert done.timed_out and time.time() - t0 < 20
    time.sleep(0.5)
    if sys.platform == "win32":
        assert not _alive(grandchild)
    else:                                            # no job objects: best effort only
        psutil.Process(grandchild).kill()


def test_a_cancelled_task_ends_its_process(tmp_path: Path) -> None:
    pid_file = tmp_path / "p.pid"
    code = (f"import os,time; open(r'{pid_file}','w').write(str(os.getpid())); "
            f"time.sleep(60)")

    async def go() -> None:
        job = asyncio.create_task(run_contained([PY, "-c", code], cwd=str(tmp_path),
                                                timeout=60))
        while not pid_file.exists() or not pid_file.read_text():
            await asyncio.sleep(0.05)
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
    asyncio.run(go())
    time.sleep(0.5)
    assert not _alive(int(pid_file.read_text()))


def test_output_is_capped_without_stalling(tmp_path: Path) -> None:
    code = "import sys; sys.stdout.write('x' * 3_000_000); sys.stdout.flush()"
    done = asyncio.run(run_contained([PY, "-c", code], cwd=str(tmp_path), timeout=30,
                                     max_output=1000))
    assert done.exit_code == 0 and done.truncated and done.stdout == b"x" * 1000


def test_input_is_closed(tmp_path: Path) -> None:
    code = "import sys; print(repr(sys.stdin.read()))"
    done = asyncio.run(run_contained([PY, "-c", code], cwd=str(tmp_path), timeout=10))
    assert done.stdout.strip() == b"''"              # never waits for a keyboard


def test_secret_named_environment_is_not_inherited(monkeypatch: pytest.MonkeyPatch,
                                                   tmp_path: Path) -> None:
    monkeypatch.setenv("NOVA_TEST_API_KEY", "abc")
    monkeypatch.setenv("SOME_TOKEN", "abc")
    monkeypatch.setenv("NOVA_TEST_PLAIN", "ok")
    env = clean_env()
    assert "NOVA_TEST_API_KEY" not in env and "SOME_TOKEN" not in env
    code = ("import os; print(os.environ.get('NOVA_TEST_API_KEY'), "
            "os.environ.get('NOVA_TEST_PLAIN'))")
    done = asyncio.run(run_contained([PY, "-c", code], cwd=str(tmp_path), timeout=10))
    assert done.stdout.split() == [b"None", b"ok"]


def test_a_finished_command_keeps_what_it_started(tmp_path: Path) -> None:
    """«Start-Process notepad» must leave Notepad open — only a timeout or a cancel
    ends the tree."""
    pid_file = tmp_path / "c.pid"
    code = (f"import subprocess,sys; p=subprocess.Popen([sys.executable,'-c',"
            f"'import time; time.sleep(30)']); open(r'{pid_file}','w').write(str(p.pid))")
    done = asyncio.run(run_contained([PY, "-c", code], cwd=str(tmp_path), timeout=20))
    child = _wait_pid(pid_file)
    try:
        assert done.exit_code == 0 and _alive(child)
    finally:
        psutil.Process(child).kill()


def test_env_var_left_alone_for_the_caller() -> None:
    os.environ.setdefault("PATH", "")
    assert "PATH" in clean_env() and clean_env({"X_EXTRA": "1"})["X_EXTRA"] == "1"

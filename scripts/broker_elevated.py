"""The ELEVATED half of scripts/broker_install.py (Phase 22). Standard library only.

Started by broker_install.py through UAC ("runas") with a plan file it prepared:

    pythonw.exe -I broker_elevated.py install|uninstall <plan.json>

install:   stop the old broker → copy a protected Python (+ pywin32 + nova_broker)
           into the install folder → write policy.json → register the scheduled task
           (at logon, highest privileges, restart on failure) → start it.
uninstall: stop + delete the task, remove the install folder.
Writes <plan dir>/result.json with what happened.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from xml.sax.saxutils import escape

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SKIP_PY = {"tcl", "include", "libs", "Scripts", "__pycache__", "test", "idlelib", "tkinter",
           "turtledemo", "ensurepip", "site-packages"}


def system32() -> Path:
    import ctypes
    buf = ctypes.create_unicode_buffer(260)
    ctypes.windll.kernel32.GetSystemDirectoryW(buf, 260)
    return Path(buf.value)


def run(argv: list[str]) -> tuple[int, str]:
    if argv[0] == "schtasks":                      # never a PATH lookup when elevated
        argv = [str(system32() / "schtasks.exe"), *argv[1:]]
    r = subprocess.run(argv, capture_output=True, creationflags=NO_WINDOW,  # noqa: S603
                       check=False)
    return r.returncode, (r.stdout + r.stderr).decode("mbcs", "replace")


def stop_task(task: str) -> None:
    run(["schtasks", "/end", "/tn", task])
    # give the broker a moment to exit; pythonw processes started from the install go too
    time.sleep(1.5)


def task_xml(user_sid: str, exe: Path, workdir: Path) -> str:
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Nova AI Privileged/Admin Broker: allowlisted admin actions,
    each approved by the owner.</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{user_sid}</UserId></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>{user_sid}</UserId>
      <LogonType>InteractiveToken</LogonType><RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(str(exe))}</Command><Arguments>-I -m nova_broker</Arguments>
      <WorkingDirectory>{escape(str(workdir))}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def find_winget() -> str:
    """The real winget.exe inside the (admin-only) WindowsApps package folder — never
    the user-writable alias in %LOCALAPPDATA%."""
    base = Path(r"C:\Program Files\WindowsApps")
    try:
        found = sorted(base.glob("Microsoft.DesktopAppInstaller_*_x64__8wekyb3d8bbwe/winget.exe"))
    except OSError:
        return ""
    for exe in reversed(found):
        rc, out = run([str(exe), "--version"])
        if rc == 0 and out.strip().startswith("v"):
            return str(exe)
    return ""


def install(plan: dict, out: dict) -> None:
    dest = Path(plan["install_dir"])
    stop_task(plan["task"])
    py_dest = dest / "python"
    if py_dest.exists():
        shutil.rmtree(py_dest)
    src = Path(plan["base_python"])
    shutil.copytree(src, py_dest, ignore=lambda d, names: [n for n in names if n in SKIP_PY])
    site = py_dest / "Lib" / "site-packages"
    site.mkdir(parents=True, exist_ok=True)
    for item in plan["site_items"]:
        p = Path(item)
        if p.is_dir():
            shutil.copytree(p, site / p.name, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(p, site / p.name)
    shutil.copytree(plan["broker_package"], site / "nova_broker",
                    ignore=shutil.ignore_patterns("__pycache__"))
    policy = dict(plan["policy"])
    policy["winget"] = find_winget() if plan["packages"] else ""
    for sub in ("state", "logs"):
        (dest / sub).mkdir(parents=True, exist_ok=True)
    (dest / "policy.json").write_text(json.dumps(policy, indent=2), encoding="utf-8")
    out["winget"] = policy["winget"]
    xml = dest / "task.xml"
    xml.write_text(task_xml(plan["user_sid"], py_dest / "pythonw.exe", dest), encoding="utf-16")
    rc, text = run(["schtasks", "/create", "/tn", plan["task"], "/xml", str(xml), "/f"])
    out["task_create"] = [rc, text.strip()]
    if rc != 0:
        raise RuntimeError(f"schtasks /create failed: {text.strip()}")
    rc, text = run(["schtasks", "/run", "/tn", plan["task"]])
    out["task_run"] = [rc, text.strip()]


def uninstall(plan: dict, out: dict) -> None:
    stop_task(plan["task"])
    rc, text = run(["schtasks", "/delete", "/tn", plan["task"], "/f"])
    out["task_delete"] = [rc, text.strip()]
    dest = Path(plan["install_dir"])
    if dest.exists() and dest.name == "broker" and "Nova AI" in str(dest):
        shutil.rmtree(dest, ignore_errors=True)
    out["removed"] = not dest.exists()


def main() -> int:
    mode, plan_path = sys.argv[1], Path(sys.argv[2])
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    out: dict = {"mode": mode, "ok": False}
    try:
        (install if mode == "install" else uninstall)(plan, out)
        out["ok"] = True
    except Exception as e:  # noqa: BLE001 - reported back to the non-elevated side
        out["error"] = f"{type(e).__name__}: {e}"
        out["trace"] = traceback.format_exc()[-2000:]
    (plan_path.parent / "result.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())

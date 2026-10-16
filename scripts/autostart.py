"""Start Nova automatically after login (plan §32 reboot recovery, Phase 23).

    uv run python scripts/autostart.py --install   # owner-approved 2026-10-16
    uv run python scripts/autostart.py --status
    uv run python scripts/autostart.py --remove

Registers a NORMAL-user Task Scheduler task "\\Nova AI\\Nova" (no administrator rights,
no UAC): 30 s after the owner logs in it starts the supervisor hidden (pythonw), which
starts core + workers. Restarted by Windows if it fails. Standard library only.
"""

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

APP = Path(__file__).resolve().parents[1]
TASK = r"\Nova AI\Nova"
SCHTASKS = r"C:\Windows\System32\schtasks.exe"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(*args: str) -> tuple[int, str]:
    r = subprocess.run([SCHTASKS, *args], capture_output=True, creationflags=NO_WINDOW,  # noqa: S603
                       check=False, stdin=subprocess.DEVNULL)
    return r.returncode, (r.stdout + r.stderr).decode("mbcs", "replace").strip()


def base_pythonw() -> Path:
    exe = Path(sys.base_prefix) / "pythonw.exe"
    if not exe.is_file():
        raise SystemExit(f"pythonw.exe not found next to {sys.base_prefix}")
    return exe


def user_id() -> str:
    import getpass
    import os
    return f"{os.environ.get('USERDOMAIN', '')}\\{getpass.getuser()}".lstrip("\\")


def task_xml() -> str:
    exe = base_pythonw()
    script = APP / "workers" / "supervisor" / "run.py"
    user = escape(user_id())
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Nova AI: starts the supervisor (core + workers) after login.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{user}</UserId><Delay>PT30S</Delay></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><UserId>{user}</UserId>
      <LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>5</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>{escape(str(exe))}</Command>
      <Arguments>-I -S "{escape(str(script))}" --quiet</Arguments>
      <WorkingDirectory>{escape(str(APP))}</WorkingDirectory></Exec>
  </Actions>
</Task>
"""


def install() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        xml = Path(tmp) / "nova-task.xml"
        xml.write_text(task_xml(), encoding="utf-16")
        rc, out = run("/create", "/tn", TASK, "/xml", str(xml), "/f")
    print(("✓ " if rc == 0 else "✗ ") + out)
    return rc


def status() -> int:
    rc, out = run("/query", "/tn", TASK, "/fo", "LIST", "/v")
    if rc != 0:
        print("✗ not installed")
        return 1
    keep = ("TaskName", "Status", "Last Run Time", "Last Result", "Task To Run", "Run As User",
            "Logon Mode", "Next Run Time")
    print("\n".join(line for line in out.splitlines() if line.split(":")[0].strip() in keep))
    return 0


def remove() -> int:
    rc, out = run("/delete", "/tn", TASK, "/f")
    print(("✓ " if rc == 0 else "✗ ") + out)
    return rc


def main() -> int:
    if sys.platform != "win32":
        print("Windows only")
        return 1
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--install", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--remove", action="store_true")
    a = ap.parse_args()
    return install() if a.install else status() if a.status else remove()


if __name__ == "__main__":
    sys.exit(main())

"""Install / check / remove the Privileged/Admin Broker (Phase 22, docs/BROKER.md).

    uv run python scripts/broker_install.py            # install or update (UAC prompt)
    uv run python scripts/broker_install.py --check    # verify, no changes
    uv run python scripts/broker_install.py --uninstall

Only the copy step runs elevated (scripts/broker_elevated.py, after the owner
clicks Yes on the UAC prompt). Everything it installs lives in
C:\\Program Files\\Nova AI\\broker, which normal user programs cannot change.
"""

import argparse
import asyncio
import json
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.broker.client import BrokerClient, BrokerError, BrokerUnavailable  # noqa: E402
from core.broker.policy import build_policy  # noqa: E402
from core.config import load_config  # noqa: E402
from core.ipc.token import TokenStore  # noqa: E402

APP = Path(__file__).resolve().parents[1]
PYWIN32 = ("win32", "win32com", "win32comext", "pythonwin", "pywin32_system32",
           "pywin32.pth", "pywin32.version.txt")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
SCHTASKS = r"C:\Windows\System32\schtasks.exe"
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"


def known_folders() -> dict[str, str]:
    import win32api
    from win32com.shell import shell, shellcon  # type: ignore[import-untyped]
    return {"%SystemRoot%": win32api.GetWindowsDirectory(),
            "%ProgramFiles%": shell.SHGetKnownFolderPath(shellcon.FOLDERID_ProgramFiles)}


def user_sid() -> str:
    import win32api
    import win32security
    tok = win32security.OpenProcessToken(win32api.GetCurrentProcess(),
                                         win32security.TOKEN_QUERY)
    return str(win32security.ConvertSidToStringSid(
        win32security.GetTokenInformation(tok, win32security.TokenUser)[0]))


def run_elevated(mode: str, plan: dict, work: Path) -> dict:
    import win32con
    import win32event
    import win32process
    from win32com.shell import shell  # type: ignore[import-untyped]
    from win32com.shell import shellcon as sc
    work.mkdir(parents=True, exist_ok=True)
    (work / "result.json").unlink(missing_ok=True)
    plan_file = work / "plan.json"
    plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    pyw = Path(sys.base_prefix) / "pythonw.exe"
    script = APP / "scripts" / "broker_elevated.py"
    print("… Windows will ask for permission (UAC) — click Yes on the PC")
    info = shell.ShellExecuteEx(fMask=sc.SEE_MASK_NOCLOSEPROCESS, lpVerb="runas",
                                lpFile=str(pyw),
                                lpParameters=f'-I "{script}" {mode} "{plan_file}"',
                                nShow=win32con.SW_HIDE)
    win32event.WaitForSingleObject(info["hProcess"], 15 * 60 * 1000)
    win32process.GetExitCodeProcess(info["hProcess"])
    result = work / "result.json"
    if not result.is_file():
        return {"ok": False, "error": "the elevated step did not finish (UAC declined?)"}
    data: dict = json.loads(result.read_text(encoding="utf-8"))
    return data


def plan_for(cfg) -> dict:  # type: ignore[no-untyped-def]
    b = cfg.workers.workers.privileged_broker
    site = Path(sysconfig.get_paths()["purelib"])
    install_dir = Path(b.install_dir)
    return {
        "install_dir": str(install_dir),
        "task": b.scheduled_task,
        "user_sid": user_sid(),
        "base_python": str(Path(sys.base_prefix).resolve()),
        "site_items": [str(site / n) for n in PYWIN32 if (site / n).exists()],
        "broker_package": str(APP / "workers" / "privileged-broker" / "nova_broker"),
        "packages": b.packages,
        "policy": build_policy(cfg, install_dir=install_dir, folders=known_folders()),
    }


def check(cfg) -> bool:  # type: ignore[no-untyped-def]
    b = cfg.workers.workers.privileged_broker
    client = BrokerClient(cfg, TokenStore(cfg.path("secrets_dir")))
    ok = True

    def line(good: bool, text: str) -> None:
        nonlocal ok
        ok &= good
        print(("✓ " if good else "✗ ") + text)

    install = Path(b.install_dir)
    line(client.installed, f"installed at {install}")
    query = [SCHTASKS, "/query", "/tn", b.scheduled_task, "/fo", "LIST", "/v"]
    r = subprocess.run(query, capture_output=True, creationflags=NO_WINDOW,  # noqa: S603
                       check=False)
    text = r.stdout.decode("mbcs", "replace")
    line(r.returncode == 0, f"scheduled task {b.scheduled_task}")
    if r.returncode == 0:
        line("pythonw.exe" in text and str(install).lower() in text.lower(),
             "the task runs the protected copy")
    # user-level code must not be able to change the install or the task
    probe = install / "python" / f"nova-write-probe-{int(time.time())}.tmp"
    try:
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        line(False, "install folder is WRITABLE by the normal user")
    except OSError:
        line(True, "install folder is read-only for the normal user")
    # Re-register the UNCHANGED definition as the normal user: Windows must refuse, so
    # nobody can point this highest-privilege task at another program. (Toggling
    # enabled/disabled is allowed for the owner — that is no escalation.)
    path, _, name = b.scheduled_task.rpartition("\\")
    ps = (f"$t = Get-ScheduledTask -TaskPath '{path}\\' -TaskName '{name}'; "
          "try { Set-ScheduledTask -InputObject $t -ErrorAction Stop | Out-Null; 'ALLOWED' } "
          "catch { 'DENIED' }")
    r = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-Command", ps],  # noqa: S603
                       capture_output=True, creationflags=NO_WINDOW, check=False,
                       stdin=subprocess.DEVNULL, timeout=60)
    line(r.stdout.decode("mbcs", "replace").strip() == "DENIED",
         "the task's program cannot be changed without administrator rights")
    st = asyncio.run(client.ensure_running())
    line(st is not None, "broker answers on the pipe")
    if st is not None:
        line(bool(st.get("elevated")), "broker runs as administrator")
        line(st.get("protocol_version") == cfg.workers.protocol_version, "protocol version")
        print(f"  policy: {st.get('policy')} · lockdown: {st.get('lockdown')}")
        for bad, name in ((("whoami",), "a shell command"),):
            try:
                asyncio.run(client.call("run_shell", {"cmd": bad[0]}))
                line(False, f"{name} was accepted")
            except BrokerError as e:
                line(e.code == "ACTION_NOT_ALLOWED", f"{name} is refused ({e.code})")
        try:
            asyncio.run(client.execute("service", {"name": "Spooler", "op": "restart"},
                                       "service Spooler", 1))
            line(False, "an unapproved restart was executed")
        except BrokerError as e:
            line(e.code == "APPROVAL_REQUIRED", f"an unapproved admin action is refused "
                                                f"({e.code})")
        except BrokerUnavailable as e:
            line(False, str(e))
    return ok


def main() -> int:
    if sys.platform != "win32":
        print("the broker is Windows-only")
        return 1
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true")
    g.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    work = cfg.path("data_dir") / "broker-install"
    if args.check:
        return 0 if check(cfg) else 1
    plan = plan_for(cfg)
    if args.uninstall:
        res = run_elevated("uninstall", plan, work)
        print(json.dumps(res, indent=2))
        return 0 if res.get("ok") else 1
    print(f"Installing the broker into {plan['install_dir']}")
    print(f"  services: {plan['policy']['services']}")
    print(f"  tasks:    {sorted(plan['policy']['tasks'])}")
    res = run_elevated("install", plan, work)
    if not res.get("ok"):
        print("✗ install failed:", res.get("error"))
        if res.get("trace"):
            print(res["trace"])
        return 1
    print(f"✓ installed · winget: {res.get('winget') or 'not found (packages off)'}")
    time.sleep(2)
    return 0 if check(cfg) else 1


if __name__ == "__main__":
    sys.exit(main())

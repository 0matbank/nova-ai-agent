"""The broker's fixed operations. Each one validates its own parameters (exact keys,
allowlists, ranges) and runs a fixed program by absolute path with an argument
list — never a shell, never a command line built from free text.

op → permission action the owner must have approved (None = read-only / safe):
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nova_broker.policy import Policy

RULE_PREFIX = "Nova AI - "
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,59}$")
_SERVICE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+_-]{1,99}$")
_PORT = re.compile(r"^(\d{1,5})(-(\d{1,5}))?$")
MAX_OUT = 3000


class BadParams(ValueError):
    pass


class NotAllowed(ValueError):
    pass


@dataclass(frozen=True)
class Spec:
    keys: dict[str, type]                         # exact parameter keys and types
    action: Callable[[dict[str, Any]], str | None]


SPECS: dict[str, Spec] = {
    "status": Spec({}, lambda p: None),
    "service": Spec({"name": str, "op": str},
                    lambda p: None if p["op"] == "query" else "admin.service_control"),
    "firewall": Spec({"op": str, "name": str, "direction": str, "rule_action": str,
                      "protocol": str, "port": str},
                     lambda p: None if p["op"] == "list" else "admin.firewall"),
    "package": Spec({"op": str, "id": str},
                    lambda p: {"install": "admin.package_install",
                               "uninstall": "admin.package_uninstall"}.get(p["op"], "?")),
    "power": Spec({"op": str, "delay_seconds": int},
                  lambda p: {"restart": "system.restart", "shutdown": "system.shutdown"}
                  .get(p["op"])),
    "task": Spec({"name": str}, lambda p: "admin.task"),
}


def required_action(op: str, params: dict[str, Any]) -> str | None:
    """The permission action an op needs approved (None = no approval needed)."""
    return SPECS[op].action(params)


def validate(op: str, params: dict[str, Any]) -> dict[str, Any]:
    spec = SPECS.get(op)
    if spec is None:
        raise NotAllowed(op)
    if set(params) != set(spec.keys):
        raise BadParams(f"expected exactly {sorted(spec.keys)}")
    for k, t in spec.keys.items():
        v = params[k]
        if type(v) is not t:                      # bool is not int here
            raise BadParams(f"{k} must be {t.__name__}")
    if op == "service":
        if params["op"] not in ("query", "start", "stop", "restart"):
            raise BadParams("op must be query/start/stop/restart")
        if not _SERVICE.match(params["name"]):
            raise BadParams("invalid service name")
    elif op == "firewall":
        if params["op"] not in ("list", "add", "remove"):
            raise BadParams("op must be list/add/remove")
        if params["op"] != "list" and not _NAME.match(params["name"]):
            raise BadParams("rule name: letters, digits, space . _ - (max 60)")
        if params["op"] == "add":
            if params["direction"] not in ("in", "out"):
                raise BadParams("direction must be in/out")
            if params["rule_action"] not in ("block", "allow"):
                raise BadParams("rule_action must be block/allow")
            if params["protocol"] not in ("TCP", "UDP"):
                raise BadParams("protocol must be TCP/UDP")
            m = _PORT.match(params["port"])
            if m is None:
                raise BadParams("port must be a number or a range like 8000-8100")
            lo, hi = int(m.group(1)), int(m.group(3) or m.group(1))
            if not (1 <= lo <= hi <= 65535):
                raise BadParams("port out of range")
    elif op == "package":
        if params["op"] not in ("install", "uninstall"):
            raise BadParams("op must be install/uninstall")
        if not _PACKAGE.match(params["id"]):
            raise BadParams("invalid winget package id")
    elif op == "power":
        if params["op"] not in ("restart", "shutdown", "cancel"):
            raise BadParams("op must be restart/shutdown/cancel")
        if params["op"] != "cancel" and not 60 <= params["delay_seconds"] <= 3600:
            raise BadParams("delay_seconds must be 60..3600 (time to cancel)")
    return params


# ------------------------------------------------------------------ backend

@dataclass
class Backend:
    """Everything that touches Windows. Tests replace it with a fake."""
    system32: Path = field(default_factory=lambda: Path(r"C:\Windows\System32"))

    def __post_init__(self) -> None:
        if sys.platform == "win32":
            import win32api
            self.system32 = Path(win32api.GetSystemDirectory())

    def run(self, argv: list[str], timeout: int = 120) -> tuple[int, str]:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            r = subprocess.run(argv, capture_output=True, timeout=timeout,  # noqa: S603
                               creationflags=flags, stdin=subprocess.DEVNULL, check=False)
        except subprocess.TimeoutExpired:
            return -1, "timed out"
        except OSError as e:
            return -2, f"could not start {Path(argv[0]).name}: {e}"
        out = (r.stdout or b"") + (r.stderr or b"")
        for enc in ("utf-8", "mbcs"):
            try:
                return r.returncode, out.decode(enc)[-MAX_OUT:]
            except (UnicodeDecodeError, LookupError):
                continue
        return r.returncode, out.decode("latin-1")[-MAX_OUT:]

    def sys_exe(self, name: str) -> str:
        return str(self.system32 / name)

    def service_state(self, name: str) -> str:
        import win32service
        import win32serviceutil
        states = {win32service.SERVICE_STOPPED: "stopped",
                  win32service.SERVICE_START_PENDING: "starting",
                  win32service.SERVICE_STOP_PENDING: "stopping",
                  win32service.SERVICE_RUNNING: "running",
                  win32service.SERVICE_PAUSED: "paused"}
        status = win32serviceutil.QueryServiceStatus(name)
        return states.get(status[1], f"state {status[1]}")

    def service_control(self, name: str, op: str) -> None:
        import win32serviceutil
        if op == "start":
            win32serviceutil.StartService(name)
        elif op == "stop":
            win32serviceutil.StopService(name)
        else:
            win32serviceutil.RestartService(name, waitSeconds=60)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


# ---------------------------------------------------------------- operations

def _done(summary: str, **evidence: Any) -> dict[str, Any]:
    return {"summary": summary, "evidence": evidence}


class Failed(RuntimeError):
    pass


def op_service(p: dict[str, Any], policy: Policy, be: Backend) -> dict[str, Any]:
    if p["op"] == "query":
        return _done(f"{p['name']}: {be.service_state(p['name'])}", state=be.service_state(
            p["name"]))
    name = policy.services.get(p["name"].lower())
    if name is None:
        raise NotAllowed(f"service {p['name']} is not in the broker's allowlist "
                         f"({', '.join(sorted(policy.services.values())) or 'empty'})")
    be.service_control(name, p["op"])
    want = "stopped" if p["op"] == "stop" else "running"
    state = be.service_state(name)
    for _ in range(60):
        if state == want:
            break
        be.sleep(0.5)
        state = be.service_state(name)
    if state != want:
        raise Failed(f"{name} is {state} after {p['op']}")
    return _done(f"{name} {p['op']} done — now {state}", service=name, state=state)


_LIST_PS = ("$ErrorActionPreference='Stop'; "
            "Get-NetFirewallRule -DisplayName 'Nova AI - *' -ErrorAction SilentlyContinue | "
            "ForEach-Object { $f = $_ | Get-NetFirewallPortFilter; "
            "'{0}|{1}|{2}|{3}|{4}|{5}' -f $_.DisplayName,$_.Direction,$_.Action,$_.Enabled,"
            "$f.Protocol,($f.LocalPort -join ',') }")


def firewall_rules(be: Backend) -> list[dict[str, str]]:
    ps = be.sys_exe(r"WindowsPowerShell\v1.0\powershell.exe")
    rc, out = be.run([ps, "-NoProfile", "-NonInteractive", "-Command", _LIST_PS], 60)
    if rc != 0:
        raise Failed(f"listing firewall rules failed: {out.strip()[:300]}")
    rules = []
    for line in out.splitlines():
        parts = line.strip().split("|")
        if len(parts) == 6 and parts[0].startswith(RULE_PREFIX):
            rules.append(dict(zip(("name", "direction", "action", "enabled", "protocol",
                                   "port"), parts, strict=True)))
    return rules


def op_firewall(p: dict[str, Any], policy: Policy, be: Backend) -> dict[str, Any]:
    if p["op"] == "list":
        rules = firewall_rules(be)
        return _done(f"{len(rules)} Nova firewall rule(s)", rules=rules)
    full = RULE_PREFIX + p["name"]
    netsh = be.sys_exe("netsh.exe")
    exists = any(r["name"] == full for r in firewall_rules(be))
    if p["op"] == "add":
        if exists:
            raise Failed(f"a rule named '{full}' already exists")
        argv = [netsh, "advfirewall", "firewall", "add", "rule", f"name={full}",
                f"dir={p['direction']}", f"action={p['rule_action']}",
                f"protocol={p['protocol']}",
                f"{'localport' if p['direction'] == 'in' else 'remoteport'}={p['port']}",
                "enable=yes"]
    else:
        if not exists:
            raise Failed(f"no Nova rule named '{full}'")
        argv = [netsh, "advfirewall", "firewall", "delete", "rule", f"name={full}"]
    rc, out = be.run(argv, 60)
    if rc != 0:
        raise Failed(f"netsh failed ({rc}): {out.strip()[:300]}")
    present = any(r["name"] == full for r in firewall_rules(be))
    if present != (p["op"] == "add"):
        raise Failed(f"rule '{full}' {'missing' if p['op'] == 'add' else 'still there'} "
                     "after the change")
    return _done(f"firewall rule '{full}' {'added' if p['op'] == 'add' else 'removed'}",
                 rule=full, present=present)


def op_package(p: dict[str, Any], policy: Policy, be: Backend) -> dict[str, Any]:
    if not policy.winget:
        raise NotAllowed("package install is off (winget was not found at install time)")
    common = ["--id", p["id"], "--exact", "--accept-source-agreements",
              "--disable-interactivity"]
    if p["op"] == "install":
        argv = [policy.winget, "install", *common, "--source", "winget", "--silent",
                "--accept-package-agreements"]
    else:
        argv = [policy.winget, "uninstall", *common, "--silent"]
    rc, out = be.run(argv, 1800)
    _, listed = be.run([policy.winget, "list", *common], 120)
    present = p["id"].lower() in listed.lower()
    if present != (p["op"] == "install"):
        raise Failed(f"winget {p['op']} {p['id']} did not take effect (exit {rc}): "
                     f"{out.strip()[-400:]}")
    return _done(f"{p['id']} {'installed' if present else 'uninstalled'} (winget exit {rc})",
                 package=p["id"], installed=present)


def op_power(p: dict[str, Any], policy: Policy, be: Backend) -> dict[str, Any]:
    shutdown = be.sys_exe("shutdown.exe")
    if p["op"] == "cancel":
        rc, out = be.run([shutdown, "/a"], 30)
        if rc == 1116:                                   # ERROR_NO_SHUTDOWN_IN_PROGRESS
            return _done("nothing was scheduled", cancelled=False)
        if rc != 0:
            raise Failed(f"cancel failed ({rc}): {out.strip()[:200]}")
        return _done("scheduled restart/shutdown cancelled", cancelled=True)
    flag = "/r" if p["op"] == "restart" else "/s"
    delay = p["delay_seconds"]
    msg = (f"Nova AI: owner-approved {p['op']} in {delay // 60} min. "
           "Save your work. (Cancel from the phone: 'restart cancel')")
    rc, out = be.run([shutdown, flag, "/t", str(delay), "/c", msg, "/d", "p:0:0"], 30)
    if rc != 0:
        raise Failed(f"shutdown.exe failed ({rc}): {out.strip()[:200]}")
    return _done(f"{p['op']} scheduled in {delay} s (can be cancelled)", scheduled=p["op"],
                 delay_seconds=delay)


def op_task(p: dict[str, Any], policy: Policy, be: Backend) -> dict[str, Any]:
    argv = policy.tasks.get(p["name"])
    if argv is None:
        raise NotAllowed(f"task {p['name']} is not in the broker's allowlist "
                         f"({', '.join(sorted(policy.tasks)) or 'empty'})")
    rc, out = be.run(list(argv), 1800)
    if rc != 0:
        raise Failed(f"{p['name']} failed (exit {rc}): {out.strip()[-400:]}")
    return _done(f"{p['name']} done", task=p["name"], exit_code=rc, output=out.strip()[-800:])


HANDLERS: dict[str, Callable[[dict[str, Any], Policy, Backend], dict[str, Any]]] = {
    "service": op_service, "firewall": op_firewall, "package": op_package,
    "power": op_power, "task": op_task,
}

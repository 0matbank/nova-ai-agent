"""An in-process Privileged Broker for tests: the real nova_broker request handling
(validation, approval check, lockdown, ops) with a fake Windows backend — no pipe,
no elevation, nothing on the PC changes."""

from __future__ import annotations

import json
import secrets
import uuid
from pathlib import Path
from typing import Any

from nova_broker.ops import Backend
from nova_broker.policy import Policy
from nova_broker.server import Broker

from core.broker.client import BrokerError

SERVICES = ["Spooler", "W32Time"]
TASKS = {"flush_dns": [r"C:\Windows\System32\ipconfig.exe", "/flushdns"]}


class FakeBackend(Backend):
    def __init__(self) -> None:
        self.system32 = Path(r"C:\Windows\System32")
        self.calls: list[list[str]] = []
        self.services: dict[str, str] = {"Spooler": "running", "W32Time": "running"}
        self.controls: list[tuple[str, str]] = []
        self.rules: list[dict[str, str]] = []
        self.packages: set[str] = set()

    def run(self, argv: list[str], timeout: int = 120) -> tuple[int, str]:
        self.calls.append(list(argv))
        exe = Path(argv[0]).name.lower()
        if exe == "powershell.exe":                       # firewall listing
            return 0, "\n".join(f"{r['name']}|Inbound|Block|True|TCP|{r['port']}"
                                for r in self.rules)
        if exe == "netsh.exe":
            kv = dict(a.split("=", 1) for a in argv if "=" in a)
            if "add" in argv:
                self.rules.append({"name": kv["name"], "port": kv.get("localport", "")})
            else:
                self.rules = [r for r in self.rules if r["name"] != kv["name"]]
            return 0, "Ok."
        if exe == "winget.exe":
            pid = argv[argv.index("--id") + 1]
            if argv[1] == "install":
                self.packages.add(pid)
            elif argv[1] == "uninstall":
                self.packages.discard(pid)
            elif argv[1] == "list":
                return (0, pid) if pid in self.packages else (1, "No installed package")
            return 0, "Successfully"
        if exe == "shutdown.exe":
            return (1116, "no shutdown") if argv[1] == "/a" else (0, "")
        return 0, "done"

    def service_state(self, name: str) -> str:
        return self.services.get(name, "stopped")

    def service_control(self, name: str, op: str) -> None:
        self.controls.append((name, op))
        self.services[name] = "stopped" if op == "stop" else "running"

    def sleep(self, seconds: float) -> None:
        pass


def make_broker(tmp: Path, db_path: Path, elevated: bool = True,
                **policy: Any) -> tuple[Broker, FakeBackend, str]:
    token = secrets.token_urlsafe(32)
    (tmp / "broker-secrets").mkdir(parents=True, exist_ok=True)
    token_file = tmp / "broker-secrets" / "internal_rpc.token"
    token_file.write_text(token, encoding="utf-8")
    pol = Policy.from_dict({
        "pipe_name": rf"\\.\pipe\nova-test-{uuid.uuid4().hex[:12]}", "protocol_version": 1,
        "token_file": str(token_file), "db_path": str(db_path),
        "state_dir": str(tmp / "broker-state"), "log_dir": str(tmp / "broker-logs"),
        "services": SERVICES, "tasks": TASKS, "winget": r"C:\fake\winget.exe", **policy})
    be = FakeBackend()
    return Broker(pol, be, elevated=elevated), be, token


class InProcessClient:
    """Same surface as core.broker.client.BrokerClient, answered by Broker.handle."""

    def __init__(self, broker: Broker, token: str) -> None:
        self.broker = broker
        self.token = token
        self.enabled = True
        self.installed = True

    def raw(self, action: str, params: dict[str, Any] | None = None, target: str = "",
            task_id: int | str = "system", **over: Any) -> dict[str, Any]:
        msg = {"protocol_version": 1, "request_id": uuid.uuid4().hex, "task_id": str(task_id),
               "token": self.token, "action": action, "target": target,
               "params": params or {}, **over}
        return self.broker.handle(json.dumps(msg).encode())

    async def call(self, action: str, params: dict[str, Any] | None = None, *,
                   target: str = "", task_id: int | str = "system",
                   connect_ms: int = 0) -> dict[str, Any]:
        reply = self.raw(action, params, target, task_id)
        if not reply.get("ok"):
            raise BrokerError(str(reply.get("error")), str(reply.get("detail", "")))
        result: dict[str, Any] = reply["result"]
        return result

    async def execute(self, op: str, params: dict[str, Any], target: str,
                      task_id: int | str) -> dict[str, Any]:
        return await self.call(op, params, target=target, task_id=task_id)

    async def status(self) -> dict[str, Any]:
        return await self.call("status")

    async def lockdown(self, on: bool) -> None:
        await self.call("lockdown", {"on": on})

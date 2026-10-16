# admin skill (Phase 22)

Approved administrator-level work, executed by the **Privileged/Admin Broker** — a
separate elevated process installed once into `C:\Program Files\Nova AI\broker`
(docs/BROKER.md). The skill itself never elevates anything; it sends the exact
approved parameters to the broker over a restricted named pipe. The broker validates
them again, checks the owner's approval in the database on its own (single use,
max 15 min old) and then runs a fixed program — never a shell.

| Tool | Action (level) | What |
|---|---|---|
| `status` | admin.status (GREEN) | broker running / elevated / lockdown / allowlist |
| `service` | admin.status (query) · admin.service_control (RED) | start / stop / restart an allowlisted service |
| `firewall` | admin.status (list) · admin.firewall (RED) | add / remove a `Nova AI - <name>` rule (port or range, TCP/UDP, in/out, block/allow) |
| `package` | admin.package_install / _uninstall (RED) | winget, exact id, source `winget`; the approval shows name + publisher |
| `power` | system.restart / system.shutdown (RED) · admin.power_cancel (GREEN) | restart / shutdown after 1–60 min, cancellable |
| `task` | admin.task (RED) | fixed commands from workers.yaml `privileged_broker.tasks` |

The allowlist (services, tasks, winget on/off) lives in config/workers.yaml and is
copied into the protected install — changing it requires reinstalling (UAC).

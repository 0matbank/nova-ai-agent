# windows skill

Read-only PC information (all GREEN). Works while the PC is locked.

| Tool | Action | Output |
|---|---|---|
| `status` | pc.status | CPU %, RAM, disk free, GPU util/VRAM/temperature, uptime |
| `processes` | process.list | top processes by memory or CPU, optional name filter |
| `system_info` | pc.status | OS version, machine name, user, CPU count, uptime |

Opening/closing apps is the `app-control` skill (runs through the Desktop Worker).

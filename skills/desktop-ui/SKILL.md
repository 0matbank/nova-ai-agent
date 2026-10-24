# desktop-ui skill

See and operate desktop windows through **Windows UI Automation**, in the
plan's priority order (§17): UIA control patterns → keyboard → raw coordinates
(last resort). Runs in the Desktop Worker; PC locked → task waits in
`WAITING_DESKTOP` and resumes after unlock.

| Tool | Action (level) | Notes |
|---|---|---|
| `windows` | ui.read 🟢 | top-level windows, owning process, which one is in front |
| `observe` | ui.read 🟢 | plan §71: one window's controls numbered 1…n with their boxes, a window screenshot + a numbered-marks copy, and `sparse` (tree tells little → vision needed) — any app, **untrusted** |
| `act` | ui.click 🟡 / ui.type 🔵 / ui.replace_text 🟡 / ui.focus 🔵 | plan §72: act on element N of an observation (click, set_text, focus, toggle, select, expand, collapse, scroll_into_view) — re-checked right before the input (same window, process, runtime id, place) or refused as `stale_target`; result says delivered vs. the app changed |
| `inspect` | ui.read 🟢 | control tree (name, type, automation id) |
| `read_value` | ui.read 🟢 | current text/value of a control — **untrusted** |
| `focus` | ui.focus 🔵 | bring a window to front; verified |
| `click` | ui.click 🟡 | UIA Invoke/Toggle/Select/Expand; danger-word names need approval |
| `type` | ui.type 🔵 / ui.replace_text 🟡 | **appends**; `replace=true` backs up old content first |
| `keys` | ui.keys 🟡 | `ctrl+s`, `enter`… ; Alt+F4 / Ctrl+W / Delete need approval |
| `click_xy` | ui.click_xy 🔴 | raw coordinates, last resort |

Anything aimed at a terminal/PowerShell/cmd window or the Run dialog becomes
**🔴 ui.shell_input**, so the PowerShell classifier can never be bypassed by
typing a command into a console.

Targets: `window` = title (exact, else unique substring) or process name
(`notepad`); controls by `name`, `automation_id` and/or `control_type`
(e.g. `EditControl`, `ButtonControl`, `DocumentControl`).

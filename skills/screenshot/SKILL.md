# screenshot skill

`capture` (GREEN `screenshot`) grabs the desktop inside the user session via
the Desktop Worker. `monitor=0` = all monitors in one image, `1..n` = one monitor.

- Full-resolution PNG is kept locally in `workspace\screenshots\` (never uploaded
  anywhere except to the owner on request).
- A JPEG preview (default 1280 px wide) is returned for Telegram.
- PC locked → the task waits in `WAITING_DESKTOP` and resumes after unlock.
- A near-uniform image is reported as `blank` (e.g. secure desktop / UAC).

Telegram: `/screenshot` sends the preview straight to the owner.

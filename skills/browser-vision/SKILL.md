# browser-vision skill

Plan §15 Layer 3: when the page's structure (accessibility snapshot) doesn't
expose the target — a canvas, an unlabeled icon, a custom widget — Nova takes a
screenshot, a vision model finds the target, and it acts on that point.

| Tool | Action (level) | Notes |
|---|---|---|
| `click_xy` | browser.click_xy 🟡 | only in an `engine="mcp"` session; viewport 1280×720 |

- **Vision model:** local `qwen3-vl:8b` first (screenshots stay on this PC);
  Gemini only as fallback (owner choice 2026-09-25). The router picks.
- **Safety check:** the element at the point is identified in the page
  (`elementFromPoint`, nearest clickable ancestor). Automatic only when it is
  identified and has no danger word (buy/pay/send/delete/confirm…); otherwise
  the owner approves. A vision result never approves a dangerous action (§57).
- After the click the page address is checked again; a jump to a blocked
  address is undone.
- Verify: the page must change (address/title/content) — otherwise the step
  counts as failed.

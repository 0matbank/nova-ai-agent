"""A stand-in for the Claude Code CLI in tests: `python fake_claude.py <args>`.
Behaviour comes from FAKE_CLAUDE_MODE; FAKE_CLAUDE_LOG (optional) receives the
argv, stdin and whether any API-key variable reached the process."""

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
mode = os.environ.get("FAKE_CLAUDE_MODE", "ok")
args = sys.argv[1:]

if args[:2] == ["auth", "status"]:
    if mode == "signedout":
        print("Not logged in. Run claude and use /login.")
        sys.exit(1)
    print("Logged in (Claude subscription)")
    sys.exit(0)

if "-p" in args:
    stdin = sys.stdin.read()
    if os.environ.get("FAKE_CLAUDE_LOG"):
        with open(os.environ["FAKE_CLAUDE_LOG"], "w", encoding="utf-8") as f:
            json.dump({"argv": args, "stdin": stdin,
                       "api_key_seen": "ANTHROPIC_API_KEY" in os.environ}, f)
    base = {"type": "result", "session_id": "sess-7", "num_turns": 2, "total_cost_usd": 0.0,
            "usage": {"input_tokens": 500, "output_tokens": 40}, "permission_denials": []}
    if mode == "limit":
        print(json.dumps({**base, "subtype": "error", "is_error": True,
                          "result": "You've hit your session limit · resets in 2 hours"}))
    elif mode == "auth":
        print(json.dumps({**base, "subtype": "error", "is_error": True,
                          "result": "Invalid API key · Please run /login"}))
    elif mode == "crash":
        print("fatal: something broke", file=sys.stderr)
        sys.exit(2)
    else:
        print(json.dumps({**base, "subtype": "success", "is_error": False,
                          "result": "Fixed average() for empty lists."}))
    sys.exit(0)

sys.exit(2)

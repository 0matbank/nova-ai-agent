"""A stand-in for the Codex CLI in tests: `python fake_codex.py <codex args>`.
Behaviour comes from the FAKE_CODEX_MODE environment variable."""

import json
import os
import sys

mode = os.environ.get("FAKE_CODEX_MODE", "ok")
args = sys.argv[1:]

if args[:2] == ["login", "status"]:
    print({"chatgpt": "Logged in using ChatGPT", "apikey": "Logged in using an API key",
           "none": "Not logged in"}.get(mode, "Logged in using ChatGPT"))
    sys.exit(0 if mode != "none" else 1)

if args and args[0] == "exec":
    events = [{"type": "thread.started", "thread_id": "thread-123"}, {"type": "turn.started"}]
    if mode == "limit":
        events.append({"type": "turn.failed", "error": {
            "message": "You've hit your usage limit. Try again in 2 hours 5 minutes."}})
    elif mode == "auth":
        events.append({"type": "error", "message": "401 Unauthorized: please log in again"})
    else:
        events += [
            {"type": "item.completed", "item": {"id": "1", "type": "agent_message",
                                                "text": "Looking at the code."}},
            {"type": "item.completed", "item": {"id": "2", "type": "command_execution",
                                                "command": "python -m unittest",
                                                "aggregated_output": "OK", "exit_code": 0}},
            {"type": "item.completed", "item": {"id": "3", "type": "file_change", "changes": [
                {"path": "stats.py", "kind": "update"}]}},
            {"type": "item.completed", "item": {"id": "4", "type": "agent_message",
                                                "text": "Fixed average() and median()."}},
            {"type": "turn.completed", "usage": {"input_tokens": 1200, "output_tokens": 80}}]
    for e in events:
        print(json.dumps(e))
    print(json.dumps({"argv": args}), file=sys.stderr)
    sys.exit(0)

sys.exit(2)

"""A stand-in for the Antigravity CLI in tests: `python fake_agy.py <agy args>`.
Behaviour comes from FAKE_AGY_MODE; FAKE_AGY_LOG (optional) receives the
argv, working directory and stdin of the call as JSON."""

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # like the real Go binary
mode = os.environ.get("FAKE_AGY_MODE", "ok")
args = sys.argv[1:]

if args[:1] == ["models"]:
    print("Fetching available models...")
    if mode == "signedout":
        print("Error: Please sign in to view available models. Launch the CLI without "
              "arguments to sign in.", file=sys.stderr)
        sys.exit(1)
    print("gemini-3.8-pro-high\ngemini-3.8-flash-high")
    sys.exit(0)

if "--print" in args:
    stdin = sys.stdin.read()
    if os.environ.get("FAKE_AGY_LOG"):
        with open(os.environ["FAKE_AGY_LOG"], "w", encoding="utf-8") as f:
            json.dump({"argv": args, "cwd": os.getcwd(), "stdin": stdin}, f)
    print("ERROR: logging before google.Init: I0926 installer.go:27] noise", file=sys.stderr)

    def result(status: str, response: str = "", error: str = "") -> dict:
        out = {"type": "result", "conversation_id": "conv-42", "status": status,
               "response": response, "duration_seconds": 3.2, "num_turns": 1,
               "usage": {"input_tokens": 900, "output_tokens": 60, "thinking_tokens": 10,
                         "cache_read_tokens": 0, "total_tokens": 970}}
        if error:
            out["error"] = error
        return out

    events: list[dict] = [{"type": "init", "conversation_id": "conv-42", "cwd": os.getcwd(),
                           "tools": ["write_to_file", "run_command"],
                           "permission_mode": "default", "model": "gemini-3.8-pro-high"}]
    if mode == "limit":
        events.append(result("ERROR", error="Quota exceeded: RESOURCE_EXHAUSTED. "
                                            "Try again in 45 minutes."))
    elif mode == "auth":
        print("Authentication required. Please visit the URL to log in:\n"
              "  https://accounts.google.com/o/oauth2/auth?client_id=x", file=sys.stderr)
        events.append(result("ERROR", error="authentication failed or timed out"))
        for e in events:
            print(json.dumps(e))
        sys.exit(1)
    elif mode == "crash":
        print("panic: something broke", file=sys.stderr)
        sys.exit(3)
    elif mode == "deltas":                     # no response in the envelope
        events += [{"type": "step_update", "step_index": 0, "state": "ACTIVE",
                    "step_type": "response", "text_delta": "উত্তর "},
                   {"type": "step_update", "step_index": 0, "state": "DONE",
                    "step_type": "response", "text_delta": "এখানে।"},
                   result("SUCCESS")]
    else:
        events += [
            {"type": "step_update", "step_index": 0, "state": "DONE", "step_type": "tool",
             "tool_name": "run_command",
             "tool_info": {"parameters": {"command": "python -m unittest"}, "output": "OK"}},
            {"type": "step_update", "step_index": 1, "state": "DONE", "step_type": "tool",
             "tool_name": "write_to_file",
             "tool_info": {"parameters": {"path": "stats.py"}}},
            result("SUCCESS", "Fixed average() for empty lists.")]
    for e in events:
        print(json.dumps(e, ensure_ascii=False))
    sys.exit(0)

sys.exit(2)

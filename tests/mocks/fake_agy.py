"""A stand-in for the Antigravity CLI in tests: `python fake_agy.py <agy args>`.
Event shapes copy the real agy 1.2.11 (`{"event": kind, kind: {...}}`).
Behaviour comes from FAKE_AGY_MODE; FAKE_AGY_LOG (optional) receives the
argv, working directory and stdin line of the call as JSON."""

import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # like the real Go binary
mode = os.environ.get("FAKE_AGY_MODE", "ok")
args = sys.argv[1:]
CONV = "conv-42"

if args[:1] == ["models"]:
    print("Fetching available models...")
    if mode == "signedout":
        print("Error: Please sign in to view available models. Launch the CLI without "
              "arguments to sign in.")
        sys.exit(1)
    print("gemini-3.8-flash-high\tGemini 3.8 Flash (High)")
    sys.exit(0)


def emit(kind: str, body: dict) -> None:
    top = {"event": kind, "conversation_id": CONV} if kind == "init" else {"event": kind}
    print(json.dumps({**top, kind: body}, ensure_ascii=False), flush=True)


def step(i: int, **body: object) -> None:
    emit("step_update", {"conversation_id": CONV, "step_index": i, **body})


def result(status: str, response: str = "", error: str = "", denied: list | None = None) -> None:
    body: dict = {"conversation_id": CONV, "status": status, "response": response,
                  "duration_seconds": 3.2, "num_turns": 1,
                  "usage": {"input_tokens": 900, "output_tokens": 60, "thinking_tokens": 10,
                            "cache_read_tokens": 0, "total_tokens": 970}}
    if error:
        body["error"] = error
    if denied:
        body["denied_actions"] = denied
    emit("result", body)


if any(a.startswith("--print") for a in args):
    # the real CLI reads one NDJSON message per turn and cancels tools on EOF
    line = sys.stdin.readline()
    if os.environ.get("FAKE_AGY_LOG"):
        with open(os.environ["FAKE_AGY_LOG"], "w", encoding="utf-8") as f:
            json.dump({"argv": args, "cwd": os.getcwd(), "stdin": line}, f)
    print("ERROR: logging before google.Init: I0926 installer.go:27] noise", file=sys.stderr)
    if mode == "crash":
        print("panic: something broke", file=sys.stderr)
        sys.exit(3)
    if mode == "auth":
        print("Authentication required. Please visit the URL to log in:\n"
              "  https://accounts.google.com/o/oauth2/auth?client_id=x", file=sys.stderr)
        print(json.dumps({"conversation_id": "", "status": "ERROR", "response": "",
                          "error": "authentication failed or timed out", "usage": {}}))
        sys.exit(1)
    emit("init", {"cwd": os.getcwd(), "tools": ["view_file", "replace_file_content",
                                                "run_command"],
                  "permission_mode": "request-review"})
    step(0, state="DONE", step_type="user_input")
    if mode == "limit":
        result("ERROR", error="Quota exceeded: RESOURCE_EXHAUSTED. Try again in 45 minutes.")
    elif mode == "text":
        step(1, state="ACTIVE", step_type="agent_response", text_delta="উত্তর ")
        step(1, state="DONE", step_type="agent_response", text_delta="এখানে।")
        result("SUCCESS", "উত্তর এখানে।\n")
    elif mode == "deltas":                     # no response in the envelope
        step(1, state="ACTIVE", step_type="agent_response", text_delta="উত্তর ")
        step(1, state="DONE", step_type="agent_response", text_delta="এখানে।")
        result("SUCCESS")
    elif mode == "nudge":                      # stops on a refused command, then carries on
        step(1, state="ERROR", step_type="tool", tool_name="run_command",
             tool_info={"name": "run_command", "parameters": {"CommandLine": "dir"},
                        "error": {"type": "TOOL_ERROR", "message": "user denied permission"}})
        result("SUCCESS", "", denied=[{"action": "command", "display_name": "RunCommand"}])
        again = sys.stdin.readline()
        if os.environ.get("FAKE_AGY_LOG"):
            with open(os.environ["FAKE_AGY_LOG"] + ".2", "w", encoding="utf-8") as f:
                f.write(again)
        if again:
            step(2, state="DONE", step_type="tool", tool_name="write_to_file",
                 tool_info={"name": "write_to_file", "parameters": {"TargetFile": "a.py"}})
            result("SUCCESS", "Done — see [a.py](file:///D:/p/a.py#L1-L2).")
    elif mode == "denied":
        step(1, state="ERROR", step_type="tool", tool_name="run_command",
             tool_info={"name": "run_command", "parameters": {"CommandLine": "dir"},
                        "error": {"type": "TOOL_ERROR", "message": "user denied permission"}})
        result("SUCCESS", "", denied=[{"action": "command", "display_name": "RunCommand"}])
    else:
        target = os.path.join(os.getcwd(), "stats.py")
        step(1, state="DONE", step_type="tool", tool_name="view_file",
             tool_info={"name": "view_file", "parameters": {"AbsolutePath": target},
                        "output": "3 lines, 45 bytes"})
        step(2, state="DONE", step_type="tool", tool_name="replace_file_content",
             tool_info={"name": "replace_file_content", "parameters": {"TargetFile": target}})
        step(3, state="ERROR", step_type="tool", tool_name="run_command",
             tool_info={"name": "run_command", "parameters": {"CommandLine": "python -m unittest"},
                        "error": {"type": "TOOL_ERROR", "message": "user denied permission"}})
        result("SUCCESS", "Fixed average() for empty lists.",
               denied=[{"action": "command", "display_name": "RunCommand"}])
    sys.exit(0)

sys.exit(2)

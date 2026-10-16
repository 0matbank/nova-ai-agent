"""Join several videos / audios one after another into one new file."""

from __future__ import annotations

import tempfile
from pathlib import Path

from pydantic import Field

from core.skills.api import Params, Tool, ToolEnv, ToolResult
from core.skills.mediaops import input_file, output_file, produce


class P(Params):
    inputs: list[str] = Field(min_length=2, max_length=50)
    output: str | None = None


def precheck(p: P, env: ToolEnv) -> None:
    srcs = [input_file(env, i) for i in p.inputs]
    output_file(env, srcs[0], p.output, "merged", srcs[0].suffix)


async def run(p: P, env: ToolEnv) -> ToolResult:
    srcs = [input_file(env, i) for i in p.inputs]
    out = output_file(env, srcs[0], p.output, "merged", srcs[0].suffix)
    with tempfile.TemporaryDirectory(prefix="nova-merge-") as tmp:
        listing = Path(tmp) / "list.txt"
        listing.write_text("".join("file '" + str(s).replace("'", "'\\''") + "'\n"
                                   for s in srcs), encoding="utf-8")
        return await produce(srcs[0], out, ["-f", "concat", "-safe", "0", "-i",
                                            str(listing), str(out)], f"merged {len(srcs)} files")


TOOL = Tool(name="merge", params=P, run=run, action="file.create",
            target=lambda p: " + ".join(p.inputs)[:300],
            summary=lambda p: f"Merge {len(p.inputs)} files", precheck=precheck)

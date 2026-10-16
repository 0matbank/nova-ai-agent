"""The neutral edit plan (Creative pipeline step 3) — what to make, independent of
any editing program. The Creative Director (AI) writes it as JSON; Nova validates it
against the footage facts before anything is rendered, and keeps it next to the
result together with an OpenTimelineIO timeline, so the edit stays editable.

OpenTimelineIO feasibility (owner requirement 2026-10-16): the plan maps onto OTIO
cleanly for cuts, clip order, timing, gaps and simple fades (`Transition.1`); burned
captions become markers; loudness / music / reframe are kept as metadata under
"nova". Program-specific effects are NOT in OTIO — they stay in the plan's own
fields, so nothing is lost. The file is written directly in OTIO's JSON schema
(Timeline.1 / Track.1 / Clip.2 / ExternalReference.1 / RationalTime.1) — no extra
dependency; the official `opentimelineio` package can read it."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.creative.analyze import FootageReport

ASPECTS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080),
           "4:5": (1080, 1350)}
MAX_TOTAL_SECONDS = 1800


class _M(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Clip(_M):
    source: int = Field(ge=0)              # index into sources
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    note: str = ""

    @property
    def length(self) -> float:
        return self.end - self.start


class Caption(_M):
    start: float = Field(ge=0)             # on the OUTPUT timeline
    end: float = Field(gt=0)
    text: str = Field(min_length=1, max_length=200)


class AudioPlan(_M):
    normalize: bool = True                 # EBU R128 -16 LUFS
    mute_original: bool = False
    music: str | None = None               # a music file path
    music_volume: float = Field(default=0.25, ge=0, le=1)


class LookPlan(_M):
    aspect: Literal["16:9", "9:16", "1:1", "4:5"] | None = None
    fit: Literal["crop", "pad"] = "crop"   # crop = fill the frame, pad = keep all
    fade_in: float = Field(default=0, ge=0, le=5)
    fade_out: float = Field(default=0, ge=0, le=5)
    transitions: Literal["cut", "dip"] = "cut"   # dip = short fade through black
    transition_seconds: float = Field(default=0.4, ge=0.1, le=2)


class ExportPlan(_M):
    format: Literal["mp4", "mov", "webm"] = "mp4"
    quality: Literal["high", "medium", "small"] = "high"


class EditPlan(_M):
    title: str = Field(default="Nova edit", max_length=120)
    sources: list[str] = Field(min_length=1, max_length=20)
    clips: list[Clip] = Field(min_length=1, max_length=200)
    captions: list[Caption] = []
    audio: AudioPlan = AudioPlan()
    look: LookPlan = LookPlan()
    export: ExportPlan = ExportPlan()
    reasoning: str = Field(default="", max_length=1500)    # the director's why

    @model_validator(mode="after")
    def _consistent(self) -> EditPlan:
        for i, c in enumerate(self.clips):
            if c.source >= len(self.sources):
                raise ValueError(f"clip {i}: source {c.source} does not exist")
            if c.end <= c.start:
                raise ValueError(f"clip {i}: end {c.end} must be after start {c.start}")
        total = self.total_seconds()
        if total > MAX_TOTAL_SECONDS:
            raise ValueError(f"the edit is {total:.0f}s — longer than {MAX_TOTAL_SECONDS}s")
        for i, cap in enumerate(self.captions):
            if cap.end <= cap.start or cap.start >= total:
                raise ValueError(f"caption {i} is outside the {total:.1f}s edit")
        return self

    def total_seconds(self) -> float:
        return round(sum(c.length for c in self.clips), 3)

    def needed_capabilities(self) -> set[str]:
        caps = {"trim", "export"}
        if len(self.clips) > 1:
            caps |= {"cut", "reorder"}
        if self.captions:
            caps.add("captions")
        if self.look.aspect:
            caps.add("aspect_reframe")
        if self.look.transitions != "cut" or self.look.fade_in or self.look.fade_out:
            caps.add("transitions")
        if self.audio.music:
            caps |= {"music", "audio_mix"}
        if self.audio.normalize:
            caps.add("audio_mix")
        return caps


def check_against(plan: EditPlan, reports: list[FootageReport]) -> list[str]:
    """Problems with the plan given the real footage (empty = fine)."""
    problems = []
    if len(reports) != len(plan.sources):
        problems.append("sources and analysed files differ")
        return problems
    for i, c in enumerate(plan.clips):
        dur = reports[c.source].duration
        if c.end > dur + 0.05:
            problems.append(f"clip {i} ends at {c.end}s but {Path(reports[c.source].path).name}"
                            f" is only {dur:.2f}s long")
    if plan.audio.mute_original is False and not any(r.has_audio for r in reports) \
            and not plan.audio.music:
        plan.audio.normalize = False              # nothing to normalize — not an error
    return problems


def to_otio(plan: EditPlan, fps: float = 25.0, output: str | None = None) -> dict[str, Any]:
    """The plan as an OpenTimelineIO timeline (JSON schema, see module doc)."""
    def rt(seconds: float) -> dict[str, Any]:
        return {"OTIO_SCHEMA": "RationalTime.1", "rate": fps, "value": round(seconds * fps, 3)}

    def tr(start: float, length: float) -> dict[str, Any]:
        return {"OTIO_SCHEMA": "TimeRange.1", "start_time": rt(start), "duration": rt(length)}

    video: list[dict[str, Any]] = []
    for i, c in enumerate(plan.clips):
        if i and plan.look.transitions == "dip":
            half = plan.look.transition_seconds / 2
            video.append({"OTIO_SCHEMA": "Transition.1", "name": "dip to black",
                          "transition_type": "SMPTE_Dissolve", "in_offset": rt(half),
                          "out_offset": rt(half), "metadata": {}})
        src = plan.sources[c.source]
        video.append({
            "OTIO_SCHEMA": "Clip.2", "name": f"{Path(src).name} {c.start:g}-{c.end:g}",
            "source_range": tr(c.start, c.length), "metadata": {"nova": {"note": c.note}},
            "media_references": {"DEFAULT_MEDIA": {
                "OTIO_SCHEMA": "ExternalReference.1",
                "target_url": Path(src).resolve().as_uri() if Path(src).is_absolute()
                else src, "available_range": None, "metadata": {}}},
            "active_media_reference_key": "DEFAULT_MEDIA", "effects": [], "markers": [],
            "enabled": True})
    markers = [{"OTIO_SCHEMA": "Marker.2", "name": cap.text, "color": "YELLOW",
                "marked_range": tr(cap.start, cap.end - cap.start), "comment": "caption",
                "metadata": {}} for cap in plan.captions]
    track: dict[str, Any] = {
        "OTIO_SCHEMA": "Track.1", "name": "V1", "kind": "Video", "children": video,
        "source_range": None, "effects": [], "markers": markers, "enabled": True,
        "metadata": {}}
    return {"OTIO_SCHEMA": "Timeline.1", "name": plan.title,
            "global_start_time": None,
            "metadata": {"nova": {"plan": plan.model_dump(mode="json"), "rendered_to": output}},
            "tracks": {"OTIO_SCHEMA": "Stack.1", "name": "tracks", "children": [track],
                       "source_range": None, "effects": [], "markers": [], "enabled": True,
                       "metadata": {}}}

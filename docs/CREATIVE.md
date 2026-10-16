# Universal AI Creative Automation — foundation (Phase 20)

Owner requirement 2026-10-16. Built INTO Nova (no separate assistant): the same
Telegram / voice intake, intent router, agents, provider router, permissions, memory,
verifier and checkpoints.

## Pipeline

```text
request (বাংলা / English / Banglish)
→ Intent: "creative" (edit / reel / shorts / highlight + footage)      core/router/intent.py
→ Creative Director agent (AI via the provider router — none hard-coded) agents/creative_director
→ Footage analysis — ffmpeg facts, no AI tokens                         core/creative/analyze.py
    duration · size · fps · shot changes · silences · black stretches · loudness
→ Structured EditPlan (JSON) — validated against the footage,
  one repair round on errors                                            core/creative/plan.py
→ Software selection — only adapters whose needed capabilities are
  `tested`; otherwise an honest "not supported yet"                     core/creative/registry.py
→ Render — NEW file, sources untouched (non-destructive)                core/creative/render.py
→ QC on the real output — length, frame size, unplanned black,
  audio present, no clipping                                            core/creative/qc.py
→ Preview frame to Telegram → verifier: ✅ PASS only with the QC proofs
→ Kept next to the video: <name>.plan.json + <name>.otio (editable)
```

Style feedback ("amar edit style mone rakho: cut druto, caption boro") is stored as a
standing preference (memory "creative") and given to every later plan.

## What works today (tested)

FFmpeg engine: cuts and reordering of several clips (from one or more sources),
trims, dip-to-black transitions, fade in / out, burned captions (Bangla font: Nirmala
UI), reframing to 9:16 / 1:1 / 4:5 / 16:9 (crop or pad), loudness normalize
(-16 LUFS), background music under the edit (looped, volume), mute, export mp4 / mov /
webm at high / medium / small quality. Plus the media skill's single operations
(trim, cut, crop, resize, compress, convert, merge, audio extract / normalize,
subtitle burn, thumbnail, frames, info).

## OpenTimelineIO feasibility

Decision: write OTIO's JSON schema directly (`Timeline.1`, `Stack.1`, `Track.1`,
`Clip.2` with `ExternalReference.1`, `Transition.1`, `Marker.2`, `RationalTime.1`) —
no new dependency now; the official `opentimelineio` package (Apache-2.0) and editors
with OTIO import (DaVinci Resolve, Kdenlive, Premiere via its OTIO plugin) can read it.

| Plan field | In OTIO | Loss? |
|---|---|---|
| clips, order, in / out | Clip.2 source_range | none |
| dip transitions | Transition.1 (SMPTE_Dissolve) | the "via black" look is metadata |
| captions | Marker.2 on the track | rendering style stays in the plan |
| reframe, fades, loudness, music | `metadata.nova.plan` | not standard OTIO — kept, not lost |
| program-specific effects (later) | adapter's own project file | OTIO is not the master for those |

So OTIO is the neutral timeline; each adapter keeps its own native project (.prproj,
.aep, .drp, .blend) as the master for effects it alone understands.

## Capability matrix (generated: `uv run python scripts/creative_matrix.py`)

| Software | On this PC | Automation | cut | trim | reorder | transitions | crossfade | captions | color | audio_mix | audio_cleanup | music | effects | motion_graphics | compositing | three_d | templates | aspect_reframe | export | editable_project | analysis |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| FFmpeg | ✔ 7.1 | cli | ✅ | ✅ | ✅ | ✅ | 🔜 | ✅ | 🟡 | ✅ | 🟡 | ✅ | 🟡 | ⛔ | 🟡 | ⛔ | ⛔ | ✅ | ✅ | 🟡 | ✅ |
| Adobe Premiere Pro | ✔ 26.0.0.72 | native_api, ui | 🔜 | 🔜 | 🔜 | 🔜 | · | 🔜 | 🔜 | 🔜 | · | · | 🔜 | · | · | · | · | · | 🔜 | 🔜 | · |
| Adobe After Effects | ✔ 25.4.0.86 | script, ui | · | · | · | · | · | · | · | · | · | · | 🔜 | 🔜 | 🔜 | · | 🔜 | · | 🔜 | · | · |
| Adobe Media Encoder | — | script, ui | · | · | · | · | · | · | · | · | · | · | · | · | · | · | · | · | 🔜 | · | · |
| DaVinci Resolve | — | script, ui | 🔜 | 🔜 | 🔜 | 🔜 | · | · | 🔜 | 🔜 | · | · | · | · | · | · | · | · | 🔜 | 🔜 | · |
| CapCut Desktop | ✔ 1.0.0.0 | ui | · | · | · | · | · | 🔜 | · | · | · | · | · | · | · | · | 🔜 | · | 🔜 | · | · |
| Wondershare Filmora | — | ui | · | · | · | 🔜 | · | · | · | · | · | · | · | · | · | · | 🔜 | · | 🔜 | · | · |
| VEGAS Pro | — | script, ui | 🔜 | 🔜 | · | 🔜 | · | · | · | · | · | · | · | · | · | · | · | · | 🔜 | · | · |
| Final Cut Pro | — | script | ⛔ | · | · | · | · | · | · | · | · | · | · | · | · | · | · | · | ⛔ | · | · |
| Blender | — | script, cli | · | · | · | · | · | · | · | · | · | · | · | 🔜 | 🔜 | 🔜 | · | · | 🔜 | · | · |
| Adobe Audition | — | ui | · | · | · | · | · | · | · | 🔜 | 🔜 | · | · | · | · | · | · | · | · | · | · |
| Audacity | ✔ 3.7.7.0 | script, ui | · | · | · | · | · | · | · | 🔜 | 🔜 | · | · | · | · | · | · | · | · | · | · |
| OBS Studio | ✔ 32.0.4.0 | native_api | · | · | · | · | · | · | · | · | · | · | · | · | · | · | · | · | 🔜 | · | · |
| Kdenlive | — | cli, ui | 🔜 | · | · | · | · | · | · | · | · | · | · | · | · | · | · | · | 🔜 | 🔜 | · |
| Shotcut | — | cli, ui | 🔜 | · | · | · | · | · | · | · | · | · | · | · | · | · | · | · | 🔜 | 🔜 | · |
| Adobe Photoshop | ✔ 26.9.0.15 | script, ui | · | · | · | · | · | · | · | · | · | · | 🔜 | · | · | · | 🔜 | · | · | · | · |
| GIMP | — | script | · | · | · | · | · | · | · | · | · | · | 🔜 | · | · | · | · | · | · | · | · |

✅ tested · 🟡 partial · ⛔ unsupported · 🔜 future · · not applicable


Only ✅ capabilities are ever executed. 🔜 = an adapter milestone below.

## Roadmap (independent milestones — they do not change Phases 21–25)

Order chosen by what is installed on this PC and by how automatable it is:

1. **FFmpeg engine extras** — true crossfades (xfade), LUT / basic color correction,
   speed ramps, picture-in-picture, logo / watermark. No new software.
2. **Adobe Premiere Pro 2026** (installed) — UXP plugin: build a sequence from the
   EditPlan, apply transitions / captions, export via the render queue; .prproj kept.
   Needs: the owner's Creative Cloud login (Nova never stores it) and enabling UXP
   developer mode / loading a local plugin — asked first.
3. **Adobe After Effects 2025** (installed) — ExtendScript + `aerender` for motion
   graphics / titles / templates (.aep / MOGRT).
4. **Photoshop 2025 / Audacity 3.7** (installed) — thumbnails & artwork (UXP /
   ExtendScript), audio cleanup (mod-script-pipe).
5. **OBS Studio 32** (installed) — obs-websocket: start / stop recording, scenes.
6. **CapCut** (installed) — no public API: desktop UI + vision only, approval-gated,
   lower reliability (marked partial until proven).
7. **DaVinci Resolve / Blender / Kdenlive / Shotcut** (not installed) — scripting /
   headless rendering adapters when the owner installs them.
8. **Final Cut Pro** — only with a future macOS worker.

Each milestone ships with: detection, capability flags flipped to ✅ only after tests
and a live drill, a fallback to FFmpeg where possible, and QC on the real render.

## Resource rule (16 GB RAM)

One heavy creative program at a time, never together with a heavy local model
(qwen3-vl): the Resource Manager's critical-RAM guard applies, and heavy adapters
will be scheduled one after another, not in parallel.

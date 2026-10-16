# media skill

Video / audio work with FFmpeg (plan §37) — deterministic, no LLM tokens. The
ffmpeg binary is pinned in the venv (`imageio-ffmpeg`), nothing is installed on the
system.

| Tool | Action (level) | What |
|---|---|---|
| `info` | file.read (GREEN) | duration, resolution, codecs, bitrate |
| `trim` | file.create (BLUE) | keep start → end (or a duration) |
| `cut` | file.create (BLUE) | remove start → end, keep the rest |
| `crop` / `resize` | file.create (BLUE) | crop a rectangle / scale (aspect kept) |
| `compress` | file.create (BLUE) | H.264, quality high / medium / small |
| `convert` | file.create (BLUE) | mp4 mkv mov webm avi mp3 wav m4a aac flac ogg gif |
| `merge` | file.create (BLUE) | join files one after another |
| `extract_audio` / `normalize_audio` | file.create (BLUE) | audio out / loudness to -16 LUFS |
| `subtitle_burn` | file.create (BLUE) | burn an .srt / .ass into the video |
| `thumbnail` / `frames` | file.create (BLUE) | one jpg / a jpg every N seconds |

Rules: the input is only read; the result is always a NEW file next to the input (or
where the owner says) — never the input, never an existing file; a failed run
leaves no half file. Every result is read back by ffmpeg before it counts as done.

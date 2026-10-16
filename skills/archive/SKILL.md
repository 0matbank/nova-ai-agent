# archive skill

| Tool | Action (level) | What |
|---|---|---|
| `list` | file.read (GREEN) | what is inside a .zip |
| `zip` | file.create (BLUE) | pack files / folders into a new .zip (verified with testzip) |
| `unzip` | file.create (BLUE) | unpack into a NEW folder |

Unzip refuses entries that would land outside the folder (zip-slip), more than
20 000 files, more than 4 GB, or a pack ratio over 200× (zip bomb). Nova's own
secrets / DB are never packed.

"""The PC's installed apps (V1.1), kept in data/app_catalog.json.

Sources, in order: the app-control skill's fixed apps (apps.json), a scan of the
Start Menu shortcuts (all users + this user) and the Store apps (Get-StartApps),
then config/app_aliases.yaml ties spoken names to them. The Desktop Worker opens
only apps in this file, by id — never a path sent by the core.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml

from core.apps.phonetic import normalize
from core.log import get_logger

_log = get_logger("core")

APP_DIR = Path(__file__).resolve().parents[2]
BUILTIN_FILE = APP_DIR / "skills" / "app-control" / "apps.json"
ALIASES_FILE = APP_DIR / "config" / "app_aliases.yaml"
CATALOG_VERSION = 1

# Start Menu entries that are not apps
_JUNK = re.compile(
    r"\b(uninstall|readme|read me|help|manual|manuals|documentation|release notes|website|"
    r"reset preferences|what is new|module docs|license|telemetry|odbc|skinned|"
    r"recovery ?drive|install additional tools|component services|iscsi|"
    r"security configuration|print management|memory diagnostics)\b", re.IGNORECASE)
_YEAR = re.compile(r"\s*\b(19|20)\d\d\b\s*")
_VENDOR = re.compile(r"^(adobe|microsoft|google|maxon|mozilla)\s+", re.IGNORECASE)


@dataclass(frozen=True)
class AppEntry:
    id: str
    name: str                       # display name
    kind: str                       # builtin | lnk | exe | uwp | missing
    launch: str = ""                # shortcut path / exe path / AUMID ("" = builtin)
    process: tuple[str, ...] = ()   # exe names that show it runs
    aliases: tuple[str, ...] = ()   # written + spoken names
    installed: bool = True

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["process"], d["aliases"] = list(self.process), list(self.aliases)
        return d

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> AppEntry:
        return cls(str(d["id"]), str(d["name"]), str(d["kind"]), str(d.get("launch", "")),
                   tuple(d.get("process") or ()), tuple(d.get("aliases") or ()),
                   bool(d.get("installed", True)))


@dataclass
class Catalog:
    apps: dict[str, AppEntry] = field(default_factory=dict)
    scanned_at: float = 0.0

    def installed(self) -> list[AppEntry]:
        return [a for a in self.apps.values() if a.installed]

    def get(self, app_id: str) -> AppEntry | None:
        return self.apps.get(app_id)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": CATALOG_VERSION, "scanned_at": self.scanned_at,
                                   "apps": [a.to_json() for a in self.apps.values()]},
                                  ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path) -> Catalog | None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if data.get("version") != CATALOG_VERSION:
            return None
        apps = [AppEntry.from_json(d) for d in data.get("apps", [])]
        return cls({a.id: a for a in apps}, float(data.get("scanned_at", 0)))


def slug(name: str) -> str:
    s = _VENDOR.sub("", _YEAR.sub(" ", name)).strip()
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "app"


def short_names(display: str) -> list[str]:
    """'Adobe Premiere Pro 2026' → ['Adobe Premiere Pro 2026', 'Adobe Premiere Pro',
    'Premiere Pro']."""
    out = [display]
    no_year = _YEAR.sub(" ", display).strip()
    if no_year and no_year != display:
        out.append(no_year)
    no_vendor = _VENDOR.sub("", no_year).strip()
    if no_vendor and no_vendor not in out:
        out.append(no_vendor)
    return out


# ---------------------------------------------------------------- scanning

@dataclass(frozen=True)
class Found:
    name: str
    kind: str          # lnk | uwp
    launch: str
    exe: str           # target exe file name ("" = unknown)


def _start_menu_dirs() -> list[Path]:
    dirs = []
    for env in ("ProgramData", "APPDATA"):
        base = os.environ.get(env)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return [d for d in dirs if d.is_dir()]


def _shortcut_target(shell: Any, lnk: Path) -> str:
    with contextlib.suppress(Exception):
        return str(shell.CreateShortcut(str(lnk)).TargetPath or "")
    return ""


def scan_start_menu() -> list[Found]:
    if sys.platform != "win32":
        return []
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("WScript.Shell")
        found: list[Found] = []
        for d in _start_menu_dirs():
            for lnk in d.rglob("*.lnk"):
                name = lnk.stem.strip()
                if not name or _JUNK.search(name):
                    continue
                target = _shortcut_target(shell, lnk)
                if target and not target.lower().endswith(".exe"):
                    continue                     # documents, web links, help files
                exe = Path(target).name if target else ""
                if exe.lower() in ("msiexec.exe", "unins000.exe", "uninstall.exe"):
                    continue
                found.append(Found(name, "lnk", str(lnk), exe))
        del shell
        return found
    finally:
        pythoncom.CoUninitialize()


def scan_store_apps(timeout: float = 20.0) -> list[Found]:
    """Packaged (Store) apps — Calculator, Settings, Terminal, Photos …"""
    if sys.platform != "win32":
        return []
    cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
           "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
           "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout,  # noqa: S603
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        rows = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
    except (OSError, subprocess.SubprocessError, ValueError):
        _log.warning("store app scan failed", extra={"action": "apps.scan"})
        return []
    if isinstance(rows, dict):
        rows = [rows]
    out = []
    for row in rows:
        name, aumid = str(row.get("Name") or "").strip(), str(row.get("AppID") or "")
        if name and "!" in aumid and "\\" not in aumid and not _JUNK.search(name):
            out.append(Found(name, "uwp", aumid, ""))
    return out


# ---------------------------------------------------------------- building

def load_curated(path: Path = ALIASES_FILE) -> dict[str, dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    apps: dict[str, dict[str, Any]] = data.get("apps") or {}
    return apps


def load_builtin(path: Path = BUILTIN_FILE) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def build(found: list[Found], curated: dict[str, dict[str, Any]] | None = None,
          builtin: dict[str, Any] | None = None, now: float | None = None) -> Catalog:
    curated = load_curated() if curated is None else curated
    builtin = load_builtin() if builtin is None else builtin
    apps: dict[str, AppEntry] = {}
    builtin_aliases: dict[str, list[str]] = {}
    for alias, app_id in (builtin.get("aliases") or {}).items():
        builtin_aliases.setdefault(app_id, []).append(alias)
    for app_id, spec in (builtin.get("apps") or {}).items():
        apps[app_id] = AppEntry(app_id, app_id, "builtin", "", tuple(spec.get("process", [])),
                                tuple([app_id, *builtin_aliases.get(app_id, [])]))

    # one entry per display name; a shortcut beats a Store entry of the same name
    seen: dict[str, Found] = {}
    for f in found:
        k = normalize(f.name)
        if k not in seen or (seen[k].kind == "uwp" and f.kind == "lnk"):
            seen[k] = f

    taken: set[str] = set()
    for cid, spec in curated.items():
        rxs = [re.compile(p, re.IGNORECASE) for p in spec.get("match") or []]
        names = [str(n) for n in spec.get("names") or []]
        spoken = [str(n) for n in spec.get("spoken") or []]
        hits = [f for k, f in seen.items() if k not in taken and any(
            rx.search(f.name) or (f.exe and rx.search(f.exe)) for rx in rxs)]
        # the newest version when several match ("Premiere Pro 2025" and "2026")
        hit = max(hits, key=lambda f: (f.kind == "lnk", f.name)) if hits else None
        if cid in apps:                                    # builtin: add spoken names
            if hit is not None:
                taken.add(normalize(hit.name))             # not listed twice
            a = apps[cid]
            apps[cid] = AppEntry(a.id, names[0] if names else a.name, a.kind, a.launch,
                                 a.process, tuple(dict.fromkeys([*a.aliases, *names, *spoken])))
        elif hit is not None:
            taken.add(normalize(hit.name))
            process = tuple(spec.get("process") or ([hit.exe] if hit.exe else []))
            apps[cid] = AppEntry(cid, hit.name, hit.kind, hit.launch, process, tuple(
                dict.fromkeys([*short_names(hit.name), *names, *spoken])))
        else:                                              # known, but not on this PC
            apps[cid] = AppEntry(cid, names[0] if names else cid, "missing",
                                 aliases=tuple(dict.fromkeys([*names, *spoken])),
                                 installed=False)
    for k, f in seen.items():
        if k in taken:
            continue
        app_id = slug(f.name)
        base, n = app_id, 2
        while app_id in apps:
            if apps[app_id].kind == "builtin" or normalize(apps[app_id].name) == k:
                break
            app_id, n = f"{base}-{n}", n + 1
        if app_id in apps:
            continue
        apps[app_id] = AppEntry(app_id, f.name, f.kind, f.launch,
                                (f.exe,) if f.exe else (), tuple(short_names(f.name)))
    return Catalog(apps, time.time() if now is None else now)


def scan() -> Catalog:
    t0 = time.perf_counter()
    found = scan_start_menu() + scan_store_apps()
    cat = build(found)
    _log.info(f"app catalog: {len(cat.installed())} installed apps "
              f"({time.perf_counter() - t0:.1f}s)", extra={"action": "apps.scan",
                                                           "status": "ok"})
    return cat

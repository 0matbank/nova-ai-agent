"""Intent & Task Router (plan §49.1): classify a request and decide whether a
deterministic skill can do it or AI is needed. Not a separate heavy AI:
rules first (Bangla / Banglish / English), local model only for the rest.
Commands never go through an LLM (plan §45)."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from typing import Any

from core.router.device import device_command
from core.router.dictation import dictation_command
from core.scheduler.parse import looks_scheduled
from core.skills.urls import URL_RX
from providers.provider_base import Limits, ProviderRequest

# Categories from plan §49.1.
CATEGORIES = ("question", "pc_status", "screenshot", "processes", "open_windows",
              "pc_control", "creative",
              "media",
              "files", "coding", "browser", "research", "project", "reminder", "social",
              "document", "communication")


@dataclass(frozen=True)
class Intent:
    category: str
    confidence: float
    source: str                                 # rule | ai | default
    skill: tuple[str, str, dict[str, Any]] | None = None   # deterministic route
    matched: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


def _rx(*parts: str) -> re.Pattern[str]:
    return re.compile("|".join(parts), re.IGNORECASE)


# Order matters: first match wins.
READ_ONLY_SKILLS: dict[str, tuple[str, str, dict[str, Any]]] = {
    "pc_status": ("windows", "status", {}),
    "screenshot": ("screenshot", "capture", {}),
    "processes": ("windows", "processes", {}),
    "open_windows": ("windows", "open_windows", {}),
}

# V1.1 (owner 2026-10-16): "কোন কোন app/software/window খোলা আছে" asks what is on the
# screen — the open windows, not the process list (llama-server.exe is no "app").
_WHICH = (r"(\b(ki\s*ki|kii\s*ki+|kon\s*kon|konkon|which|what|ki)\b|কী\s*কী|কি\s*কি|কিকি|"
          r"কোন\s*কোন|কোনো\s*কোনো|কন[ুো]?\s*কন[ুো]?|কোনগুলো|কোন)")
_APPWORD = (r"(\b(apps?|software|softwares|programs?|windows?|tabs?)\b|অ্যাপ\S*|এপ\S*|"
            r"সফ\S*ও?য়্?ার|সফটওয়্যার|সফটওয়ার|প্রোগ্রাম|উইন্ডো)")
_OPENNESS = (r"(\b(open|khola|chalu|running|cholche|chole)\b|ওপেন|অপেন|খোলা|চালু|চালো|"
             r"চলছে)")
_THERE = r"(\b(ache|asa|ase|are|is)\b|আছে|আসে|রয়েছে)"
# "X খোলা আছে?" asks about a state — it is never a command to open X
STATE_QUESTION = re.compile(rf"{_OPENNESS}\s*{_THERE}", re.IGNORECASE)
OPEN_WINDOWS_RX = re.compile(
    rf"{_WHICH}.{{0,40}}?{_APPWORD}.{{0,30}}?{_OPENNESS}|"
    rf"{_WHICH}\s*(ki\s*)?{_OPENNESS}\s*{_THERE}|"
    rf"{_APPWORD}.{{0,20}}?{_OPENNESS}\s*{_THERE}.{{0,10}}?({_WHICH}|\?)|"
    r"\bopen\s+(windows|apps)\b|\bwhat('?s| is)\s+open\b|"
    r"(কী|কি)\s*(কী|কি)?\s*(খোলা|ওপেন)\s*(আছে|আসে)",
    re.IGNORECASE)
_PROCESS_WORD = re.compile(r"\bprocess(es)?\b|প্রসেস|প্রোসেস|background|ব্যাকগ্রাউন্ড",
                           re.IGNORECASE)


def is_open_windows_question(text: str) -> bool:
    return bool(OPEN_WINDOWS_RX.search(text)) and not _PROCESS_WORD.search(text)

RULES: list[tuple[str, re.Pattern[str], tuple[str, str, dict[str, Any]] | None]] = [
    # An explicit web address wins: "prothomalo.com খোলো" is browser work.
    ("browser", URL_RX, None),
    ("screenshot", _rx(r"screen\s*shot", r"\bskin\s*shot\b", r"স্ক্রিনশট", r"স্ক্রি?ন\s*শ্?ট",
                       r"\bscreen\s*(ta\s*)?(dekhao|dekhaw|"
                       r"dikhao|pathao)\b", r"স্ক্রিন\s*দেখাও"),
     ("screenshot", "capture", {})),
    ("processes", _rx(r"\bprocess(es)?\s*(list|gula|গুলো)?\b.*\b(dekhao|bolo|show|list)\b",
                      r"\bki\s*ki\s*(app|program|process)?\s*chol(che|ce)\b", r"কী কী চলছে",
                      r"\btask\s*manager\b", r"\bwhat.*running\b",
                      r"(কি|কী)\s*(কি|কী\s*)?(কাজ|কাস|app|অ্যাপ|প্রোগ্রাম|process|প্রসেস)?\s*"
                      r"(চলছে|চলতেছে|তলছে)",
                      r"\b(process|প্রসেস)\S*.*(চলছে|cholche|running)",
                      r"(কিকি|কি\s*কি|কী\s*কী)\s*(চল|চোল)\S*"),
     ("windows", "processes", {})),
    ("pc_status", _rx(r"\bpc\s*(er\s*)?(status|obostha|condition|health)\b",
                      # up to two words in between; tolerant of Whisper spellings
                      r"পিসি\S*(\s+\S+){0,2}\s+(অবস্থা|অবস্তা|অবস্হা|স্ট্যাটাস|স্টেটাস|"
                      r"হেল্থ|হেলথ)",
                      r"\b(cpu|ram|gpu|vram|disk|memory)\b.*\b(koto|status|usage|obostha|"
                      r"kemon|left|free|how much)\b",
                      r"\b(koto|how much)\b.*\b(cpu|ram|gpu|disk)\b", r"(র‍্যাম|সিপিইউ).*(কত|অবস্থা)"),
     ("windows", "status", {})),
    ("reminder", _rx(r"\bremind", r"মনে\s*করি(য়|য)ে", r"\bmone\s*kori(ye|e|a)\b", r"\balarm\b",
                     r"\b\d+\s*(minute|min|hour|ghonta|মিনিট|ঘণ্টা)\s*(por|pore|পর)\b",
                     r"\b(protidin|প্রতিদিন|every\s*day|daily)\b"), None),
    # Current events need live sources (plan §38) — a local model cannot know them.
    ("research", _rx(r"\b(news|latest\s+news|headlines?|breaking|current\s+events?|weather)\b",
                     r"(খবর|নিউজ|সংবাদ|সর্বশেষ|লেটেস্ট|আবহাওয়া)",
                     r"\b(khobor|khabar|akhon\s*ki\s*hocche)\b"), None),
    # Phase 22: administrator work → the Windows Operator with the admin (broker) skill
    ("pc_control", _rx(r"\b(pc|computer|windows)\s*(restart|reboot|shut\s*down|shutdown)\b",
                       r"\b(restart|reboot|shutdown|shut\s*down)\s*(cancel|bondho|thamao|"
                       r"batil)\b",
                       r"\b(firewall|winget|flush\s*dns|dns\s*flush|defender\s*(scan|update)|"
                       r"print\s*spooler|spooler|w32time|time\s*resync)\b",
                       r"(?<!npm )(?<!pip )(?<!yarn )\b(install|uninstall)\s*(koro|kore\s*dao|"
                       r"dao)\b",
                       r"\b(as\s*admin|administrator)\b",
                       r"(রিস্টার্ট|শাটডাউন|ফায়ারওয়াল|ইনস্টল)"), None),
    ("coding", _rx(r"\b(code|coding|bug|debug|refactor|compile|build|repo|repository|git|"
                   r"commit|function|script)\b", r"কোড", r"\bfix\s*koro\b"), None),
    ("browser", _rx(r"\b(website|browser|chrome|site|login|url|web\s*page)\b",
                    r"ওয়েবসাইট", r"ব্রাউজার"), None),
    # Phase 20: packing is file work, even of photos / videos ("photos folder zip koro")
    ("files", _rx(r"\b(zip|unzip|archive|extract\s*koro)\b", r"জিপ"), None),
    # Phase 20 (plan §37): video / audio / image work → the Media agent
    ("media", _rx(r"\b(video|videos|audio|mp4|mkv|mov|webm|avi|mp3|wav|m4a|flac|gif|trim|"
                  r"subtitles?|thumbnail|ffmpeg|frames?|jpe?g|png|webp|images?|photos?|"
                  r"chobi)\b", r"ভিডিও", r"অডিও", r"ছবি", r"সাবটাইটেল"), None),
    ("files", _rx(r"\b(file|folder|download(s)?\s*folder|rename|zip|unzip)\b", r"ফাইল",
                  r"ফোল্ডার"), None),
    ("pc_control", _rx(r"\b(open|kholo|khol|close|bondho|band|launch|start)\b.*\b(app|notepad|"
                       r"chrome|paint|vs\s*code|calculator|explorer)\b",
                       r"\b(notepad|chrome|paint|calculator)\b.*\b(kholo|khol|open|bondho|close)\b",
                       r"(খোলো|বন্ধ\s*কর)"), None),
    # Phase 16: PC work for the Windows Operator agent (its tools, its approvals)
    ("pc_control", _rx(r"\b(powershell|command\s*prompt|cmd|terminal|services?|"
                       r"installed\s*(apps?|programs?|software)|ip\s*address|wi-?fi|"
                       r"[a-z]\s*drive|disk\s*space|windows\s*version|startup\s*apps?)\b",
                       r"পাওয়ারশেল", r"ড্রাইভ"), None),
    ("document", _rx(r"\b(pdf|docx?|word\s*file|excel|xlsx|csv|spreadsheet|document|report|"
                     r"checklist)\b", r"ডকুমেন্ট", r"রিপোর্ট"), None),
    ("social", _rx(r"\b(facebook|fb|tiktok|instagram|youtube|caption|reel|post\s*koro)\b"),
     None),
    ("research", _rx(r"\b(research|compare|khuje|search\s*kore)\b", r"খুঁজে", r"তুলনা"), None),
    ("communication", _rx(r"\b(email|mail|message\s*pathao|whatsapp)\b", r"ইমেইল"), None),
]

AI_PROMPT = (
    "Classify the user's request into exactly one category from this list: "
    + ", ".join(CATEGORIES)
    + '. The request may be Bangla, English or Banglish. Reply ONLY with JSON like '
    '{"intent": "question", "confidence": 0.8}.'
)


# Phone → PC (Phase 6 pass "authorized phone → PC command works"; audit 2026-10-15):
# opening / closing a KNOWN app is deterministic, so it goes straight to the
# app-control skill (app.open / app.close are BLUE — still logged and gated).
APP_NAMES = {
    "notepad": "notepad", "নোটপ্যাড": "notepad", "calculator": "calculator", "calc": "calculator",
    "ক্যালকুলেটর": "calculator", "paint": "paint", "mspaint": "paint", "পেইন্ট": "paint",
    "chrome": "chrome", "google chrome": "chrome", "ক্রোম": "chrome", "edge": "edge",
    "microsoft edge": "edge", "vs code": "vscode", "vscode": "vscode",
    "visual studio code": "vscode", "file explorer": "explorer", "explorer": "explorer",
}
_OPEN = _rx(r"\b(open|kholo|khol|khulo|launch|start|chalu\s*koro|on\s*koro)\b",
            r"(খোলো|খুলো|খোল|চালু\s*কর)")
_CLOSE = _rx(r"\b(close|bondho|bondo|band|off\s*koro|quit|exit)\b", r"(বন্ধ)")
_APP_RX = re.compile("|".join(rf"(?<![\w-]){re.escape(n)}(?![\w-])"
                              for n in sorted(APP_NAMES, key=len, reverse=True)),
                     re.IGNORECASE)


def app_command(text: str) -> tuple[str, str, dict[str, Any]] | None:
    """"notepad kholo" → app-control.open; "chrome bondho koro" → app-control.close."""
    m = _APP_RX.search(text)
    if m is None:
        return None
    app = APP_NAMES[m.group(0).lower()]
    if _CLOSE.search(text):
        return ("app-control", "close", {"app": app})
    if _OPEN.search(text):
        return ("app-control", "open", {"app": app})
    return None


# Plan §57 screen understanding: "screen-e ki error dekhacche", "স্ক্রিনে কী আছে"
SCREEN_Q = re.compile(
    r"\b(screen|display|monitor)\b.*\b(ki|kí|what|which|error|dekhacche|dekhache|dekhay|"
    r"lekha|written|says?|showing|open|ache|chole)\b|"
    r"\b(what|which|ki)\b.*\b(on|in)\s+(my\s+|the\s+)?(screen|display)\b|"
    r"(স্ক্রিন|পর্দা)\S*.*(কী|কি|কোন|এরর|"
    r"error|দেখাচ্ছে|লেখা|আছে|চলছে)", re.IGNORECASE)


def is_screen_question(text: str) -> bool:
    return bool(SCREEN_Q.search(text))


# Phase 22: clear admin commands go straight to the admin skill (no AI planner — the
# live drill showed a small planner turning "restart" into stop + start + restart).
# Unclear ones ("7zip install koro") still go to the Windows Operator agent.
SERVICE_ALIASES = {
    "print spooler": "Spooler", "printer spooler": "Spooler", "spooler": "Spooler",
    "windows time": "W32Time", "w32time": "W32Time", "bits": "BITS",
    "windows update": "wuauserv", "wuauserv": "wuauserv",
    "windows search": "WSearch", "wsearch": "WSearch",
}
_SVC_ALIAS = re.compile(r"\b(" + "|".join(re.escape(k) for k in sorted(
    SERVICE_ALIASES, key=len, reverse=True)) + r")\b", re.IGNORECASE)
_SVC_NAMED = re.compile(r"\b([A-Za-z][A-Za-z0-9_.-]{1,79})\s+service\b", re.IGNORECASE)
_SVC_OP = [("restart", re.compile(r"\b(restart|reboot)\b|রিস্টার্ট", re.IGNORECASE)),
           ("stop", re.compile(r"\b(stop|bondho|bondo|band|off)\b|বন্ধ", re.IGNORECASE)),
           ("start", re.compile(r"\b(start|chalu|on)\b|চালু", re.IGNORECASE)),
           ("query", re.compile(r"\b(status|obostha|cholche|running|check)\b|অবস্থা|চলছে",
                                re.IGNORECASE))]
_POWER = re.compile(r"\b(restart|reboot|shut\s*down|shutdown)\b|রিস্টার্ট|রিস্টাট|শাটডাউন",
                    re.IGNORECASE)
_PC = re.compile(r"\b(pc|computer|windows|laptop)\b|পিসি|কম্পিউটার", re.IGNORECASE)
_CANCEL = re.compile(r"\b(cancel|thamao|batil|abort)\b|বাতিল|থামাও|ক্যান্?সেল|কান্সল|ক্যানসল",
                     re.IGNORECASE)
_MINUTES = re.compile(r"(\d{1,2})\s*(min|minute|minit|মিনিট)", re.IGNORECASE)
_PORT = re.compile(r"\b(\d{1,5}(?:-\d{1,5})?)\s*(no\.?\s*|number\s*)?(port|পোর্ট)|"
                   r"(port|পোর্ট)\s*(\d{1,5}(?:-\d{1,5})?)\b", re.IGNORECASE)
_FW = re.compile(r"\b(firewall|block|allow)\b|ফায়ারওয়াল", re.IGNORECASE)
_FW_REMOVE = re.compile(r"\b(remove|delete|muche|sorao|sorie|tule|uthiye)\b|মুছে|সরাও",
                        re.IGNORECASE)
_FW_ALLOW = re.compile(r"\b(allow|khulo|khule|open|unblock)\b|খুলে|খোলো", re.IGNORECASE)
ADMIN_TASKS = [
    ("flush_dns", re.compile(r"\b(flush\s*dns|dns\s*(flush|cache\s*(clear|porishkar)))\b",
                             re.IGNORECASE)),
    ("time_resync", re.compile(r"\b(time|somoy|ghori)\s*(sync|resync|thik\s*koro)\b|"
                               r"\btime\s*resync\b", re.IGNORECASE)),
    ("defender_update", re.compile(r"\bdefender\b.*\bupdate\b|\bvirus\s*definition",
                                   re.IGNORECASE)),
    ("defender_quick_scan", re.compile(r"\b(defender\b.*\bscan|virus\s*scan|quick\s*scan)\b",
                                       re.IGNORECASE)),
]


def admin_command(text: str) -> tuple[str, str, dict[str, Any]] | None:
    """'print spooler restart koro' → admin.service; 'PC restart koro' → admin.power;
    'restart cancel' → admin.power cancel; '9999 port block koro' → admin.firewall;
    'dns flush koro' → admin.task. Parameters are filled by rules, never guessed."""
    for name, rx in ADMIN_TASKS:
        if rx.search(text):
            return ("admin", "task", {"name": name})
    if _POWER.search(text) and _CANCEL.search(text):
        return ("admin", "power", {"op": "cancel"})
    port = _PORT.search(text)
    if port and _FW.search(text):
        value = port.group(1) or port.group(5)
        block = not _FW_ALLOW.search(text) or bool(re.search(r"\bblock\b|ব্লক", text, re.I))
        proto = "UDP" if re.search(r"\budp\b", text, re.IGNORECASE) else "TCP"
        out = bool(re.search(r"\b(out|outgoing|outbound|bahire)\b", text, re.IGNORECASE))
        name = f"{'Block' if block else 'Allow'} {proto} {value} {'out' if out else 'in'}"
        return ("admin", "firewall", {
            "op": "remove" if _FW_REMOVE.search(text) else "add", "name": name,
            "direction": "out" if out else "in", "rule_action": "block" if block else "allow",
            "protocol": proto, "port": value})
    alias = _SVC_ALIAS.search(text)
    named = _SVC_NAMED.search(text)
    service = SERVICE_ALIASES[alias.group(1).lower()] if alias else (
        named.group(1) if named else None)
    if service is not None:
        op = next((o for o, rx in _SVC_OP if rx.search(text)), None)
        if op is not None:
            return ("admin", "service", {"name": service, "op": op})
    if _POWER.search(text) and _PC.search(text) and not alias:
        m = _MINUTES.search(text)
        delay = min(max(int(m.group(1)) * 60, 60), 3600) if m else 60
        op = "restart" if re.search(r"restart|reboot|রিস্টার্ট", text, re.IGNORECASE) \
            else "shutdown"
        return ("admin", "power", {"op": op, "delay_seconds": delay})
    return None


def is_creative(text: str) -> bool:
    """A creative edit of footage (Creative Director), not a single media operation:
    "ei video theke 30 second er reel banao", "trip.mp4 cinematic edit koro"."""
    if _STYLE_NOTE.search(text):
        return True
    if _PUBLISH.search(text) and not _EDIT_WORD.search(text):
        return False                    # posting to a platform is Social work (V2), not editing
    footage = bool(_FOOTAGE.search(text) or _SOCIAL_CUT.search(text))
    if _CREATIVE.search(text) and footage:
        return True
    return footage and bool(_MAKE.search(text)) and not _SINGLE_OP.search(text)


_CREATIVE = re.compile(r"\b(edit|editing|reels?|shorts|highlights?|montage|cinematic|b-?roll|"
                       r"intro|outro)\b|এডিট|রিল", re.IGNORECASE)
_EDIT_WORD = re.compile(r"\b(edit|editing|montage|cinematic|highlights?|b-?roll)\b|এডিট",
                        re.IGNORECASE)
_PUBLISH = re.compile(r"\b(post|upload|publish|share)\b|পোস্ট|আপলোড", re.IGNORECASE)
_MAKE = re.compile(r"\b(banao|banaw|baniye\s*dao|make|create)\b|বানাও|বানিয়ে", re.IGNORECASE)
# one fixed operation is media work, not a creative edit ("… thumbnail banao")
_SINGLE_OP = re.compile(r"\b(thumbnail|trim|compress|convert|crop|resize|merge|jora|frames?|"
                        r"duration|resolution|info|audio\s*(alada|extract)|mp3|gif)\b|"
                        r"থাম্বনেইল|অডিও", re.IGNORECASE)
_FOOTAGE = re.compile(r"\b(video|videos|footage|clip|clips|mp4|mov|mkv)\b|ভিডিও|ফুটেজ",
                      re.IGNORECASE)
_SOCIAL_CUT = re.compile(r"\b(reels?|shorts|tiktok)\b|রিল", re.IGNORECASE)
_STYLE_NOTE = re.compile(r"\b(edit|editing)\s*style\b.*(mone\s*rakh|remember|bhalo\s*legeche)|"
                         r"এডিট\S*\s*স্টাইল.*(মনে\s*রাখ|ভালো\s*লেগেছে)", re.IGNORECASE)


def app_intent(res: Any) -> Intent:
    """V1.1: an app resolution (core/apps/resolver.py) as an intent — sure → the
    app-control skill; unclear / not installed → Nova asks (no skill runs)."""
    if res.status == "sure":
        return Intent("pc_control", 0.95, "rule", ("app-control", res.action,
                                                    {"app": res.app.id}),
                      matched=res.app.id, meta={"app_method": res.best.method,
                                                "app_score": round(res.best.score, 2)})
    return Intent("pc_control", 0.7, "rule", None, matched=res.phrase, meta={
        "app_ask": {"action": res.action, "status": res.status, "phrase": res.phrase,
                    "missing": res.best.app.name if res.status == "missing" and res.best
                    else None,
                    "choices": [m.app.name for m in res.candidates]},
        "app_choices": [m.app.id for m in res.candidates]})


QUICK_SKILLS = frozenset({("windows", "status"), ("windows", "processes"),
                          ("windows", "open_windows"), ("screenshot", "capture"),
                          ("app-control", "open"), ("app-control", "close"),
                          ("app-control", "view"), ("device", "volume"),
                          ("device", "media"), ("device", "brightness"),
                          ("desktop-ui", "dictate")})


def is_quick_request(text: str, apps: Any | None = None) -> bool:
    """V1.1 fast lane: a request a fixed skill answers in seconds, with no AI — it must
    not wait behind a long AI task (live 2026-10-23: "প্রিমিয়ার প্রো খোলো" waited 2 min
    behind one slow answer)."""
    if split_compound(text) is not None:
        return False                     # "X খোলো তারপর Y": multi-step work, normal lane
    if (apps is not None and not looks_scheduled(text) and not is_creative(text)
            and not is_screen_question(text) and not URL_RX.search(text)
            and (apps.inside_command(text) or ("",))[0] == "view"):
        return True                      # look at / answer the pop-up of an open app
    # no resolver here: it may rescan the PC and remembers attempts (not on submit)
    hit = classify_by_rules(text)
    if hit is not None and hit.skill is not None:
        return hit.skill[:2] in QUICK_SKILLS
    # the rules may only have seen "খোলো" (pc_control, no skill): a known app is quick
    return ((hit is None or hit.category == "pc_control") and apps is not None
            and not STATE_QUESTION.search(text) and apps.is_quick_command(text))


# CU-5 (plan §72): "X খোলো তারপর Y করো" — open X, then do Y inside it; the simple open
# fast path must not drop the rest of the goal
THEN_RX = re.compile(r"\b(then|and then|after that|tarpor|tarpore|erpor|tar\s+por)\b|"
                     r"তারপর|তার\s*পর|এরপর|এর\s*পর|তারপরে", re.IGNORECASE)


OWNER_NEXT_RX = re.compile(r"\b(i|i'll|i will|ami|amii|ame)\b|আমি|যামি|আমরা",
                           re.IGNORECASE)


def split_compound(text: str) -> tuple[str, str] | None:
    """(first part, the rest) when the request goes on after "then"."""
    m = THEN_RX.search(text)
    if m is None:
        return None
    head, tail = text[:m.start()].strip(" ,।."), text[m.end():].strip(" ,।.")
    if len(head.split()) < 2 or len(tail.split()) < 2:
        return None
    if OWNER_NEXT_RX.search(tail):
        # "…open X, then I will tell you the command" — the rest is the owner's turn,
        # not more work for Nova (owner's own phone sentence, 2026-10-16)
        return None
    return head, tail


def compound_app_task(text: str, apps: Any | None = None) -> Intent | None:
    """Opening an app is only the first step: the whole request goes to the computer-use
    loop, which opens the app and works inside it."""
    parts = split_compound(text)
    if parts is None or URL_RX.search(text):
        return None
    head = parts[0]
    fixed = app_command(head)
    if fixed is not None and fixed[1] == "open":
        app_id = str(fixed[2]["app"])
    elif apps is not None:
        res = apps.resolve_command(head)
        if res is None or res.status != "sure" or res.action != "open" or res.app is None:
            return None
        app_id = res.app.id
    else:
        return None
    return Intent("pc_control", 0.9, "rule", None, matched=app_id,
                  meta={"inside_app": app_id, "compound": True})


def classify_by_rules(text: str, apps: Any | None = None, chat_id: str = "") -> Intent | None:
    dictate = dictation_command(text)
    if dictate is not None:
        # V1.2: «লিখে দাও: …» — the words are text to type, never read as a request
        # (a time inside them is not a schedule, an app name is not an app to open)
        return Intent("pc_control", 0.95, "rule", dictate, matched="dictation")
    inside = (apps.inside_command(text) if apps is not None and not URL_RX.search(text)
              else None)
    if looks_scheduled(text):
        # Phase 19: "protidin raat 11 tay pc status bolo" is a job for later, not a
        # status request now — the time wins over the action words
        return Intent("reminder", 0.95, "rule", None, matched="schedule")
    if is_creative(text):
        return Intent("creative", 0.9, "rule", None, matched="creative edit")
    if is_screen_question(text) and not URL_RX.search(text):
        # plan §57: a question ABOUT the screen → screenshot + vision answer
        return Intent("screenshot", 0.9, "rule", None, matched="screen question")
    if inside is not None and inside[0] == "view":
        # owner 2026-10-23: "OBS-এ কি দেখছ" is about an app that is open — look at it,
        # never open it again
        return Intent("pc_control", 0.95, "rule", ("app-control", *inside),
                      matched=inside[1]["app"])
    if inside is not None:
        # work inside an open app → the Windows Operator (no fixed skill, no reopening)
        return Intent("pc_control", 0.9, "rule", None, matched=inside[1]["app"],
                      meta={"inside_app": inside[1]["app"]})
    if is_open_windows_question(text) and not URL_RX.search(text):
        # V1.1: "what is open" → the visible windows, before any app-name matching
        return Intent("open_windows", 0.9, "rule", READ_ONLY_SKILLS["open_windows"],
                      matched="open windows")
    if not URL_RX.search(text):
        compound = compound_app_task(text, apps)
        if compound is not None:
            return compound
        app = app_command(text)
        if app is not None:
            return Intent("pc_control", 0.95, "rule", app, matched=app[2]["app"])
        device = device_command(text)
        if device is not None:
            # V1.2: volume / mute / media / brightness — fixed rules, no AI
            return Intent("pc_control", 0.95, "rule", device, matched=f"device.{device[1]}")
        admin = admin_command(text)
        if admin is not None:
            return Intent("pc_control", 0.95, "rule", admin, matched=f"admin.{admin[1]}")
        if apps is not None and not STATE_QUESTION.search(text):
            # V1.1: any installed app, by spoken / misheard / learned name
            res = apps.resolve_command(text, chat_id)
            if res is not None:
                return app_intent(res)
    for category, rx, skill in RULES:
        m = rx.search(text)
        if m:
            return Intent(category, 0.9, "rule", skill, matched=m.group(0))
    return None


class IntentRouter:
    def __init__(self, provider_router: Any | None = None, apps: Any | None = None) -> None:
        self.providers = provider_router          # models.router.ProviderRouter
        self.apps = apps                          # core.apps.resolver.AppResolver (V1.1)

    async def classify(self, text: str, task_id: int | str = "system",
                       chat_id: str = "") -> Intent:
        if self.apps is not None:
            # the owner answering "which app?" or correcting a misheard name
            choice = await asyncio.to_thread(self._owner_choice, text, chat_id)
            if choice is not None:
                return Intent("pc_control", 0.95, "rule",
                              ("app-control", choice.action, {"app": choice.app.id}),
                              matched=choice.app.id,
                              meta={"app_method": "owner", "app_learned": choice.learned,
                                    "app_phrase": choice.phrase})
            # off the event loop: a name not found may rescan the installed apps
            hit = await asyncio.to_thread(classify_by_rules, text, self.apps, chat_id)
        else:
            hit = classify_by_rules(text)
        if hit is not None:
            if hit.meta.get("app_ask") and hit.meta["app_ask"]["status"] == "ask":
                return await self._app_ai_fallback(text, hit, task_id, chat_id)
            return hit
        if self.providers is not None:
            result = await self.providers.complete(ProviderRequest(
                task_id=task_id, task_type="intent_classification", user_request=text,
                system=AI_PROMPT, json_output=True, model_alias="intent_classification",
                limits=Limits(timeout_seconds=60, max_output_tokens=60)))
            if result.ok:
                try:
                    data = json.loads(result.answer)
                    cat = str(data.get("intent", "")).strip().lower()
                    conf = float(data.get("confidence", 0.5))
                except (ValueError, AttributeError):
                    cat, conf = "", 0.0
                if cat in CATEGORIES:
                    # AI may only route to fixed READ-ONLY skills (harmless if wrong);
                    # anything that acts is chosen by deterministic rules, never by AI.
                    return Intent(cat, max(0.0, min(conf, 1.0)), "ai",
                                  READ_ONLY_SKILLS.get(cat),
                                  meta={"provider": result.provider, "model": result.model})
        return Intent("question", 0.3, "default")

    def _owner_choice(self, text: str, chat_id: str) -> Any:
        assert self.apps is not None
        return self.apps.answer(chat_id, text) or self.apps.correction(chat_id, text)

    async def _app_ai_fallback(self, text: str, hit: Intent, task_id: int | str,
                               chat_id: str) -> Intent:
        """Only the unclear app name goes to the local model, with the few candidate
        apps — not the whole request to several models. It may only pick one of them."""
        ask, ids = hit.meta["app_ask"], hit.meta.get("app_choices") or []
        if self.providers is None or not ids:
            return hit
        listing = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(ask["choices"]))
        result = await self.providers.complete(ProviderRequest(
            task_id=task_id, task_type="simple",
            user_request=f"Heard: {ask['phrase']}\nFull command: {text}\nApps:\n{listing}",
            system=APP_PICK_PROMPT, limits=Limits(timeout_seconds=20, max_output_tokens=8)))
        if not result.ok:
            return hit
        m = re.search(r"\d+", result.answer)
        pick = int(m.group(0)) - 1 if m else -1
        if not 0 <= pick < len(ids):
            return hit
        if pick == 0 and self.apps is not None:
            # AI agrees with the closest sound-alike → act; otherwise still ask
            self.apps.note_picked(chat_id, ask["action"], ask["phrase"], ids[0])
            return Intent("pc_control", 0.85, "ai", ("app-control", ask["action"],
                                                     {"app": ids[0]}), matched=ids[0],
                          meta={"app_method": "ai", "provider": result.provider})
        order = [ids[pick], *[i for i in ids if i != ids[pick]]]
        names = dict(zip(ids, ask["choices"], strict=True))
        if self.apps is not None:
            self.apps.reorder_pending(chat_id, order)
        return Intent("pc_control", 0.7, "rule", None, matched=ask["phrase"], meta={
            "app_ask": {**ask, "choices": [names[i] for i in order]}, "app_choices": order})


APP_PICK_PROMPT = (
    "A voice command was transcribed by speech recognition and an app name in it may be "
    "misheard (it may be written in Bangla script). Which app from the numbered list did "
    "the speaker mean? Reply with ONLY the number, or 0 if none of them fits."
)

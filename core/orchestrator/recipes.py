"""V1.2 learned replay (Atticus item 4, adapted — the idea is MIT, the code is Nova's).

A request the Windows Operator did by planning step after step with the AI (owner's
logs: «pc off koro» 42 s, «নোভা পিসি রিস্টাট করো» 26 s) is kept once the verifier
PASSES it: its effect calls (skill, tool, params) under the request's gist. The next
time the same request comes — the words that matter, in any order, spelt a little
differently — the calls run straight away with no AI, through the SkillRunner as ever
(permission levels, approvals, audit unchanged), and the verifier checks them again.
Any failure: the recipe is dropped and the AI does the work as before.

Never kept: a request about "this/that" or "today/now" (it means something else next
time), reading-only work (its answer changes), screen clicks (layouts change), deletes,
text the AI wrote itself, anything secret. Unused for 30 days → forgotten.
Kept in `memories` (layer long_term, kind recipe), like the Speech Dictionary.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any

from core.apps.phonetic import key, normalize, similarity
from core.log import get_logger
from core.orchestrator.computer import sensitive
from core.voice.dictionary import Store

_log = get_logger("core")

KIND = "recipe"
FORGET_AFTER_DAYS = 30
MAX_CALLS = 3
ALIKE = 0.75                    # a word heard a little differently, by sound
_PATHS = re.compile(r"[A-Za-z]:\\\S*|\S+\.[A-Za-z0-9]{1,5}\b")
# effect calls that may run again later with no AI (never a delete, never a screen
# click or typing into whatever has the focus)
REPLAYABLE = {"app-control.open", "app-control.close", "files.write", "files.mkdir",
              "files.copy", "files.move", "admin.service", "admin.firewall",
              "admin.package", "admin.power", "admin.task"}
# text the AI writes itself (a file's content) is only replayed when the owner said it
WRITTEN = ("content", "text", "body")
# words that change nothing about the request
STOP = {"নোভা", "নুভা", "নোবা", "nova", "noba", "প্লিজ", "please", "plz", "pls", "একটু",
        "ektu", "তো", "ভাই", "bhai", "আমার", "আমাকে", "আমি", "amar", "amake", "ami", "my",
        "me", "i", "the", "a", "an", "to", "করো", "কর", "করে", "করুন", "করেন", "দাও",
        "দিন", "দেও", "koro", "kor", "kore", "korun", "dao", "do", "din", "can", "you",
        "could", "would", "just", "হবে"}
# "this / that / it": something else next time; "today / now": another day's facts
DEICTIC = re.compile(
    r"(?<![\wঀ-৿])(?:এটা|ওটা|সেটা|এটি|ওটি|সেটি|ওই|ওইটা|এগুলো|ওগুলো|সেগুলো|এখানে|ওখানে|"
    r"সেখানে|আজ|আজকে|আজকের|এখন|কাল|গতকাল|eta|ota|seta|oi|egula|ogula|ekhane|okhane|"
    r"aj|aaj|ajke|ajker|ekhon|kal|this|that|it|these|those|here|there|today|now|"
    r"tomorrow|yesterday)(?![\wঀ-৿])", re.IGNORECASE)


def gist(text: str) -> str:
    """The words that matter, by sound, in any order: «নোভা পিসি রিস্টার্ট করো» ==
    «পিসি রিস্টাট করো প্লিজ». Numbers stay (volume 30 ≠ volume 40)."""
    words = [w for w in normalize(text).split() if w not in STOP]
    return " ".join(sorted(k for k in (key(w) if not w.isdigit() else w for w in words)
                           if k))


def same_request(a: str, b: str) -> bool:
    """Two gists of one request: the same number of words, each heard a little
    differently at most («রিস্টার্ট» rastart ~ «রিস্টাট» rastat, «পিসিস» ~ «পিসি»).
    Numbers and short words must be the same («on» ≠ «off», 30 ≠ 40)."""
    x, y = a.split(), b.split()
    if len(x) != len(y):
        return False
    left = list(y)
    for w in x:
        match = next((v for v in left if v == w), None) or next(
            (v for v in left if not (w.isdigit() or v.isdigit()) and min(len(w), len(v)) > 3
             and similarity(w, v) >= ALIKE), None)
        if match is None:
            return False
        left.remove(match)
    return True


def _slot(g: str) -> str:
    """The store key: the gist itself, or its hash when long."""
    return g if len(g) <= 150 else "#" + hashlib.sha256(g.encode()).hexdigest()[:32]


def _fit_to_repeat(answer: str, calls: list[dict[str, Any]]) -> bool:
    """A reply to say again next time: no changing number (a size, a count, a time —
    a path's digits are none), and it names what the calls did (real run 2026-10-26: a
    garbled «আগের কোনো কাজের ফলাফল পাওয়া যায়নি» passed with the folder made)."""
    if re.search(r"[\d০-৯]", _PATHS.sub(" ", answer)):
        return False
    names = {re.split(r"[\\/]", v.rstrip("\\/"))[-1].lower()
             for c in calls for v in (c.get("params") or {}).values()
             if isinstance(v, str) and 2 <= len(v) <= 260}
    said = answer.lower()
    return any(n and n in said for n in names)


def _request_of(content: str) -> str:
    try:
        return str(json.loads(content).get("request", ""))
    except (ValueError, AttributeError):
        return ""


@dataclass(frozen=True)
class Recipe:
    slot: str
    calls: tuple[dict[str, Any], ...]      # {"skill", "tool", "params"}
    answer: str                            # the reply when it holds no changing facts
    request: str


class RecipeBook:
    def __init__(self, store: Store, now: Any = time.time) -> None:
        self.store = store
        self.now = now

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.now()))

    def find(self, text: str) -> Recipe | None:
        if DEICTIC.search(text) or sensitive(text, text):
            return None
        g = gist(text)
        if not g:
            return None
        rows = self.store.rows(KIND)
        slot = _slot(g)
        if slot not in rows:
            # the same request heard a little differently
            slot = next((s for s, (content, _src) in rows.items()
                         if same_request(g, gist(_request_of(content)))), "")
        if not slot:
            return None
        try:
            data = json.loads(rows[slot][0])
        except ValueError:
            self.store.drop(KIND, slot)
            return None
        last = max(str(data.get("used", "")), str(data.get("at", "")))
        old = time.strftime("%Y-%m-%d",
                            time.localtime(self.now() - FORGET_AFTER_DAYS * 86400))
        if last < old:
            self.store.drop(KIND, slot)             # unused for 30 days
            return None
        return Recipe(slot, tuple(data.get("calls") or ()), str(data.get("answer", "")),
                      str(data.get("request", "")))

    def learn(self, text: str, act: dict[str, Any]) -> bool:
        """Keep a PASSED operator run as a recipe, when every change it made can safely
        run again by itself."""
        if act.get("kind") != "operator" or not act.get("ok"):
            return False
        ev = act.get("evidence") or {}
        if ev.get("agent") == "recipe":
            return False                            # already one
        if DEICTIC.search(text) or sensitive(text, text):
            return False
        effects = ev.get("effects") or []
        if not effects or len(effects) > MAX_CALLS:
            return False
        said = normalize(text)
        calls = []
        for e in effects:
            skill, tool = str(e.get("skill", "")), str(e.get("tool", ""))
            params = dict(e.get("params") or {})
            if f"{skill}.{tool}" not in REPLAYABLE:
                return False
            if any(isinstance(v, str) and len(v) > 20 and normalize(v) not in said
                   for k, v in params.items() if k in WRITTEN):
                return False                        # the AI wrote it: not the owner's words
            calls.append({"skill": skill, "tool": tool, "params": params})
        g = gist(text)
        if not g:
            return False
        answer = str(act.get("answer", ""))
        self.store.put(KIND, _slot(g), json.dumps({
            "calls": calls, "request": text[:300], "at": self._today(),
            # a reply with numbers (sizes, counts, times) is out of date next time; the
            # digits of a path or file name are not such a number
            "answer": answer[:600] if _fit_to_repeat(answer, calls) else "",
        }, ensure_ascii=False), "learned")
        _log.info(f"recipe learned: {len(calls)} call(s)", extra={"action": "recipe.learn"})
        return True

    def used(self, recipe: Recipe) -> None:
        row = self.store.rows(KIND).get(recipe.slot)
        if row is None:
            return
        data = json.loads(row[0])
        if data.get("used") != self._today():
            data["used"] = self._today()
            self.store.put(KIND, recipe.slot, json.dumps(data, ensure_ascii=False), row[1])

    def forget(self, recipe: Recipe) -> None:
        self.store.drop(KIND, recipe.slot)

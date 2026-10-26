"""V1.2 learned replay (Atticus item 4, adapted — the idea is MIT, the code is Nova's).

A request the Windows Operator did by planning step after step with the AI (owner's
logs: «pc off koro» 42 s, «নোভা পিসি রিস্টাট করো» 26 s) becomes a recipe in two steps
(plan §69.4, the owner's rule): the verifier PASSES it → a draft (its effect calls,
skill/tool/params, under the request's gist) → the owner taps ✅. Only then: the next
time the same request comes — the words that matter, in any order, spelt a little
differently — the calls run straight away with no AI, through the SkillRunner as ever
(permission levels, approvals, audit unchanged), and the verifier checks them again.
Any failure: the recipe is dropped and the AI does the work as before.

Never kept: a request about "this/that" or "today/now" (it means something else next
time), reading-only work (its answer changes), screen clicks (layouts change), deletes,
text the AI wrote itself, anything secret. Unused for 30 days → forgotten; a draft not
answered in 7 days → dropped. A new way for a known request is a new draft: the approved
one stays in use until the owner approves the new one. `/recipes` lists and deletes.
Kept in `memories` (layer long_term, kinds recipe / recipe_draft), like the Speech
Dictionary.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

from core.apps.phonetic import key, normalize, similarity
from core.log import get_logger
from core.orchestrator.computer import sensitive
from core.voice.dictionary import Store

_log = get_logger("core")

KIND = "recipe"
DRAFT = "recipe_draft"
CALLBACK_PREFIX = "rc"
FORGET_AFTER_DAYS = 30
DRAFT_DAYS = 7
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
    id: str = ""


def steps_text(calls: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    """«files.mkdir (reports)» — what will run, short, for the owner to judge."""
    out = []
    for c in calls:
        shown = [re.split(r"[\\/]", v.rstrip("\\/"))[-1] if isinstance(v, str) else str(v)
                 for v in (c.get("params") or {}).values()]
        out.append(f"{c.get('skill')}.{c.get('tool')}"
                   + (f" ({', '.join(x[:40] for x in shown if x)})" if shown else ""))
    return "; ".join(out)


class RecipeBook:
    def __init__(self, store: Store, now: Any = time.time) -> None:
        self.store = store
        self.now = now

    def _today(self) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.now()))

    def _days_ago(self, days: int) -> str:
        return time.strftime("%Y-%m-%d", time.localtime(self.now() - days * 86400))

    def find(self, text: str) -> Recipe | None:
        """An approved recipe for this request (heard a little differently is fine)."""
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
        if max(str(data.get("used", "")), str(data.get("at", ""))) < self._days_ago(
                FORGET_AFTER_DAYS):
            self.store.drop(KIND, slot)             # unused for 30 days
            return None
        return Recipe(slot, tuple(data.get("calls") or ()), str(data.get("answer", "")),
                      str(data.get("request", "")), str(data.get("id", "")))

    def learn(self, text: str, act: dict[str, Any]) -> Recipe | None:
        """A PASSED operator run → a draft for the owner to approve, when every change it
        made can safely run again by itself. None: nothing to ask (not replayable, or
        the approved recipe already does exactly this)."""
        if act.get("kind") != "operator" or not act.get("ok"):
            return None
        ev = act.get("evidence") or {}
        if ev.get("agent") == "recipe":
            return None                             # already one
        if DEICTIC.search(text) or sensitive(text, text):
            return None
        effects = ev.get("effects") or []
        if not effects or len(effects) > MAX_CALLS:
            return None
        said = normalize(text)
        calls = []
        for e in effects:
            skill, tool = str(e.get("skill", "")), str(e.get("tool", ""))
            params = dict(e.get("params") or {})
            if f"{skill}.{tool}" not in REPLAYABLE:
                return None
            if any(isinstance(v, str) and len(v) > 20 and normalize(v) not in said
                   for k, v in params.items() if k in WRITTEN):
                return None                         # the AI wrote it: not the owner's words
            calls.append({"skill": skill, "tool": tool, "params": params})
        g = gist(text)
        if not g:
            return None
        known = self.find(text)
        if known is not None and list(known.calls) == calls:
            return None
        answer = str(act.get("answer", ""))
        draft = {"calls": calls, "request": text[:300], "at": self._today(),
                 "id": secrets.token_hex(4),
                 # a reply with numbers (sizes, counts, times) is out of date next time;
                 # the digits of a path or file name are not such a number
                 "answer": answer[:600] if _fit_to_repeat(answer, calls) else ""}
        self.store.put(DRAFT, _slot(g), json.dumps(draft, ensure_ascii=False), "learned")
        _log.info(f"recipe drafted: {len(calls)} call(s), waiting for the owner",
                  extra={"action": "recipe.learn"})
        return Recipe(_slot(g), tuple(calls), str(draft["answer"]), text[:300],
                      str(draft["id"]))

    def _draft_by_id(self, rid: str) -> tuple[str, dict[str, Any]] | None:
        for slot, (content, _src) in self.store.rows(DRAFT).items():
            try:
                data = json.loads(content)
            except ValueError:
                continue
            if data.get("id") == rid:
                if str(data.get("at", "")) < self._days_ago(DRAFT_DAYS):
                    self.store.drop(DRAFT, slot)
                    return None
                return slot, data
        return None

    def approve(self, rid: str) -> Recipe | None:
        """The owner's ✅: the draft becomes the recipe for its request."""
        found = self._draft_by_id(rid)
        if found is None:
            return None
        slot, data = found
        data["at"] = self._today()
        self.store.put(KIND, slot, json.dumps(data, ensure_ascii=False), "approved")
        self.store.drop(DRAFT, slot)
        _log.info("recipe approved by the owner", extra={"action": "recipe.learn"})
        return Recipe(slot, tuple(data["calls"]), str(data.get("answer", "")),
                      str(data.get("request", "")), rid)

    def reject(self, rid: str) -> bool:
        found = self._draft_by_id(rid)
        if found is None:
            return False
        self.store.drop(DRAFT, found[0])
        return True

    def all(self) -> list[Recipe]:
        out = []
        for slot, (content, _src) in sorted(self.store.rows(KIND).items()):
            try:
                data = json.loads(content)
            except ValueError:
                continue
            out.append(Recipe(slot, tuple(data.get("calls") or ()),
                              str(data.get("answer", "")), str(data.get("request", "")),
                              str(data.get("id", ""))))
        return out

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

    # ------------------------------------------------------------- the owner

    def ask(self, draft: Recipe) -> tuple[str, list[list[tuple[str, str]]]]:
        """The question to the owner, with its buttons."""
        text = (f"🧠 এই কাজটা মনে রাখব?\nপরের বার «{draft.request[:120]}» বললে AI ছাড়াই "
                f"সাথে সাথে করব (অনুমতি ও যাচাই আগের মতোই থাকবে)।\nধাপ: "
                f"{steps_text(draft.calls)}")
        return text, [[("✅ মনে রাখো", f"{CALLBACK_PREFIX}:y:{draft.id}"),
                       ("❌ না", f"{CALLBACK_PREFIX}:n:{draft.id}")]]

    def on_button(self, data: str) -> tuple[str, str]:
        """(toast, the message's new last line) for a ✅/❌ tap."""
        parts = data.split(":")
        if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
            return "Unknown button", ""
        if parts[1] == "y":
            done = self.approve(parts[2])
            return (("✅ মনে রাখলাম", "✅ মনে রাখলাম — পরের বার AI ছাড়াই।") if done else
                    ("⌛ আর নেই", "⌛ এই প্রস্তাব আর নেই (পুরনো বা আগেই সিদ্ধান্ত হয়েছে)।"))
        self.reject(parts[2])
        return "❌ ঠিক আছে", "❌ মনে রাখিনি।"

    def command(self, args: list[str]) -> str:
        """/recipes — the list; /recipes forget <n> — delete one."""
        recipes = self.all()
        if args and args[0] in ("forget", "delete", "remove", "মুছো") and len(args) > 1:
            try:
                chosen = recipes[int(args[1]) - 1]
            except (ValueError, IndexError):
                return "❌ এই নম্বরের কিছু নেই — /recipes দিয়ে তালিকা দেখুন।"
            self.forget(chosen)
            return f"🗑️ মুছে দিলাম: «{chosen.request[:80]}» — এখন থেকে আবার AI দিয়ে করব।"
        if not recipes:
            return ("🧠 এখনো কোনো কাজ মনে রাখা নেই।\nAI দিয়ে কোনো কাজ সফল হলে Nova "
                    "জিজ্ঞেস করবে মনে রাখবে কিনা।")
        lines = ["🧠 মনে রাখা কাজ (AI ছাড়া চলে):"]
        lines += [f"{n}. «{r.request[:80]}» → {steps_text(r.calls)}"
                  for n, r in enumerate(recipes, 1)]
        lines.append("মুছতে: /recipes forget <নম্বর>")
        return "\n".join(lines)

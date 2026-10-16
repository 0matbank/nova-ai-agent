"""Which language Nova replies in: the owner's own (owner rule, 2026-09-25).
Bangla script or Banglish (Bangla written in Latin letters) → "bn"; else "en".
Web addresses are ignored when deciding."""

from __future__ import annotations

import re

from core.skills.urls import URL_RX

_BN = re.compile(r"[ঀ-৿]")
BANGLISH = {
    "koro", "koren", "korun", "kore", "korbe", "korte", "kor", "kholo", "khule", "khol",
    "dekho", "dekhao", "dekhaw", "bolo", "bolen", "khojo", "khujo", "khuje", "giye", "jao",
    "ki", "keno", "kivabe", "kemon", "koto", "ache", "ase", "achhe", "nai", "naki",
    "ta", "ti", "gula", "gulo", "amar", "amake", "ami", "amii", "tumi", "tmi", "apni",
    "apnar", "ekta", "ektu", "kichu", "hobe", "hoy", "hoise", "hoyeche", "dao", "daw",
    "nao", "er", "theke", "jonno", "pathao", "lagbe", "valo", "bhalo", "thik", "ar", "o",
    "na", "hae", "haa", "acha", "accha", "namao", "bondho", "chalu", "shuru", "sesh",
    # Phase 17 ("git demo te mone rakho: sobsomoy …")
    "te", "mone", "rakho", "rakhbe", "rekho", "sobsomoy", "kokhono", "kokhonoi", "kintu",
    "jodi", "tahole", "ekhon", "age", "pore", "obostha", "hoyechilo", "korechilo", "kaj",
    # Phase 20 phone test ("… e ajker kajer checklist likho")
    "likho", "lekho", "ajker", "aajker", "kajer", "banao", "baniye", "baad", "kalo", "majhe",
    "duita", "jora", "lagao", "lagiye",
}


# "C:\Users\me\Downloads", "D:/work/x.txt", "\\server\share": folder names say
# nothing about the owner's language (Phase 16: file requests carry paths)
# POSIX absolute paths too ("/tmp/x/a.mp4") — tests and a future Linux worker
PATH_RX = _PATH = re.compile(r"(?:[a-zA-Z]:|\\\\|~)[\\/][^\s\"']*|(?<![\w.])/(?:[\w.-]+/)+[\w.-]+"
                             r"|(?:[\w.-]+[\\/]){2,}[\w.-]*")


def reply_language(text: str) -> str:
    plain = _PATH.sub(" ", URL_RX.sub(" ", text))
    if _BN.search(plain):
        return "bn"
    words = re.findall(r"[a-z]+", plain.lower())
    if words and sum(w in BANGLISH for w in words) >= max(1, len(words) // 5):
        return "bn"
    return "en"

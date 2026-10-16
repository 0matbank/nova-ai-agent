"""Nova AI Privileged/Admin Broker (plan §4, §17A, §47; Phase 22).

Runs elevated from a protected copy (C:\\Program Files\\Nova AI\\broker, installed
once by scripts/broker_install.py with the owner's UAC consent). It is NOT a
shell: it serves a fixed allowlist of admin operations over a restricted named
pipe, validates every parameter itself, and for every RED operation checks on its
own that the owner approved exactly this request (approval database, single use).

Standard library + pywin32 only — it must run from the protected Python copy.
"""

VERSION = "1.0.0"

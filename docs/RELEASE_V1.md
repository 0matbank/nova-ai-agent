# Nova AI — V1 release check (plan §42 + Phase 25)

Status: **V1 release candidate — stable (2026-10-16)**. Every §42 requirement verified
on the owner's PC/phone; all Phase 25 stress, security and failure tests passed,
including a real PC restart from the phone.

## §42 V1 requirements → evidence

| Requirement | Verified (docs/PHASES.md) |
|---|---|
| Telegram text command | Phase 2 onward, every phone test |
| Bangla / English / Banglish voice | Phase 9 (whisper large-v3 on GPU, phone) |
| PC screenshot | Phase 7 (/screenshot, phone) |
| CPU/GPU/RAM/disk status | Phase 6 + 16 (all drives, phone C/D re-test) |
| App open/close | Phase 6/7 ("notepad kholo", phone) |
| File search | Phase 6 + 16 (Downloads pdf count, phone) |
| File create/edit/move | Phase 6 + 18 (#50 checked on disk) |
| PowerShell | Phase 6 (read-only auto, others RED) |
| Terminal command | Phase 6 |
| Browser control | Phase 10 + 11 (site open, in-site search, multi-step) |
| Website login/session | Phase 10 (owner logs into the agent profile; sessions kept) |
| Download | Phase 10 (SHA-256 + size proof) |
| PDF/text reading | Phase 20 (documents skill) |
| Git clone/pull/diff | Phase 15 |
| Codex coding task | Phase 12 + 15 + 18 (#52 with cross-provider review) |
| Project build | Phase 15 (pull → edit → build → test → commit) |
| Browser test | Phase 10/11 drills (site checks, desktop/mobile device) |
| Approval system | Phase 4 (single use, expiry) + 22 (broker re-checks) + 24 (dashboard) |
| Git push after approval | Phase 15 (#38) |
| Scheduler | Phase 19 (reminder fired after a restart) |
| Task cancel | Phase 3 (/cancel) + dashboard |
| Progress reporting | Phase 3 notifier (throttled, critical exempt) |
| Task recovery after restart | Phase 3 + 19 + 23 (#69) + 25 (core killed mid-task, #75 resumed) |
| Local AI fallback | Phase 8 + 14 + 25 (all cloud failing → real qwen3 answers) |
| Activity logs | Phase 4 (/logs audit) + 24 (dashboard Logs) |
| Verifier | Phase 18 (verdicts with proofs on every COMPLETE) |

Beyond V1 (built): admin broker (Phase 22), self-update + rollback + graceful shutdown +
supervisor + autostart (Phase 23), local dashboard (Phase 24), creative automation
foundation (Phase 20).

## Phase 25 tests

| Scenario | Automated | Live on the owner's Nova |
|---|---|---|
| Codex limit | `tests/stress/test_provider_failures.py` — limit → Antigravity, back after the window | Phase 14 drill (real Codex, simulated 503) |
| Google outage | → local model; live news → honest "no live source" | all cloud failing → real local qwen3 answered (3.7 s) |
| Claude disabled/enabled | disabled never called; enabled takes review; signed-out skipped | — (no subscription; stays disabled) |
| Internet loss | providers + Telegram poller (backoff, recovers) | simulated in-process only (the PC's network was not cut) |
| PC restart | network-wait test | from the phone: run 1 → core gave up while the network was not up (5-min delay, fixed); run 2 → online 5 s after login, broker elevated, pre-reboot reminder fired |
| Browser crash | — | Nova's own Playwright browser killed → error reported, new session works (owner's Chrome untouched) |
| Worker crash | supervisor backoff tests | desktop + browser worker killed → back in ~3 s; core killed mid-task → back in 6 s, same task resumed |
| Duplicate action prevention | redelivered Telegram message (**bug found + fixed**), approval double-tap, ledger, 200-task load each once | — |
| Prompt injection | worst case: planner obeys the file → secrets unreadable, delete waits for approval, nothing leaks | — |
| Authentication attack | Telegram stranger/group/spoof, forged approvals, 200+ dashboard token guesses + Host spoof, worker/broker tokens | dashboard: 401 without token, 403 foreign Host |
| Resource pressure | relieve once, alert once, hysteresis, heavy local → cloud | RAM fix from 2026-10-16 (93–100 % → 66 %) |

## Known limits (honest)

- WhatsApp (Phase 21) deferred by the owner — code kept, disabled.
- Claude stays disabled until a subscription exists.
- Internet loss was simulated (providers + Telegram), not by cutting the PC's network.
- Code already running as the owner's account could read Nova's tokens/DB (Windows
  design); the broker still limits it to its fixed, parameter-checked operations.

## PC restart (done)

Run 2 (2026-10-16 21:31): the owner restarted the PC from the phone; after login the
broker started elevated, the supervisor started core + workers, core was up 5 s later,
Telegram connected, and the owner's next voice commands worked. Run 1 had exposed core
exiting when the network was not ready yet after login — fixed in 3d61dce.

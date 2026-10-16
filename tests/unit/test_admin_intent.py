"""Phase 22: clear admin commands are parsed by rules (no AI planner), unclear ones
go to the Windows Operator; nothing else is mistaken for admin work."""

from __future__ import annotations

from typing import Any

import pytest

from core.router.intent import classify_by_rules


@pytest.mark.parametrize("text, skill", [
    ("Print spooler service restart koro", ("admin", "service", {"name": "Spooler",
                                                                 "op": "restart"})),
    ("windows update service bondho koro", ("admin", "service", {"name": "wuauserv",
                                                                 "op": "stop"})),
    ("Spooler service cholche ki?", ("admin", "service", {"name": "Spooler", "op": "query"})),
    ("WinDefend service bondho koro", ("admin", "service", {"name": "WinDefend", "op": "stop"})),
    ("firewall e 9999 port er incoming TCP block koro", ("admin", "firewall", {
        "op": "add", "name": "Block TCP 9999 in", "direction": "in", "rule_action": "block",
        "protocol": "TCP", "port": "9999"})),
    ("9999 port er block rule muche dao", ("admin", "firewall", {
        "op": "remove", "name": "Block TCP 9999 in", "direction": "in",
        "rule_action": "block", "protocol": "TCP", "port": "9999"})),
    ("dns flush koro", ("admin", "task", {"name": "flush_dns"})),
    ("defender diye quick scan koro", ("admin", "task", {"name": "defender_quick_scan"})),
    ("PC restart koro", ("admin", "power", {"op": "restart", "delay_seconds": 60})),
    ("pc shutdown koro 10 minute e", ("admin", "power", {"op": "shutdown",
                                                         "delay_seconds": 600})),
    ("restart cancel koro", ("admin", "power", {"op": "cancel"})),
])
def test_clear_admin_commands(text: str, skill: tuple[str, str, dict[str, Any]]) -> None:
    hit = classify_by_rules(text)
    assert hit is not None and hit.category == "pc_control" and hit.skill == skill


@pytest.mark.parametrize("text, category", [
    ("7zip install koro", "pc_control"),            # Windows Operator finds the winget id
    ("npm install koro git demo te", "coding"),
    ("notepad kholo", "pc_control"),
    ("PC er obostha ki", "pc_status"),
    ("protidin raat 11 tay pc status bolo", "reminder"),
    ("git demo test koro", "coding"),
])
def test_not_mistaken_for_fixed_admin_work(text: str, category: str) -> None:
    hit = classify_by_rules(text)
    assert hit is not None and hit.category == category
    assert hit.skill is None or hit.skill[0] != "admin"

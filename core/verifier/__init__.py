"""Verifier / Reality Checker (plan §30, §54; Phase 18): a task is done only when its
result is proven by something other than the agent's own word."""

from core.verifier.checks import check_effect, pids_alive

__all__ = ["check_effect", "pids_alive"]

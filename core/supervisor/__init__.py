"""Phase 23 supervisor: starts core + workers, restarts them, applies /update with
automatic rollback. Standard library only (it must keep running while `uv sync`
replaces the venv's packages during an update)."""

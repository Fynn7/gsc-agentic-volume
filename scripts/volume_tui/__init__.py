"""Volume Agent TUI: control/display layer over the existing Codex CLI harness.

No agent decision loop lives here. Runs are started only via
scripts/run_local_agent.py as a subprocess; history/verify only read evidence.
"""

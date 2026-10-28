"""New-Run flow: validates settings/services, writes config, no second agent loop."""
from __future__ import annotations

from pathlib import Path


def build_new_run(task_kind: str, prompt: str, settings: dict, cfg_mod) -> tuple[dict, list[str]]:
    """Build the effective launcher config; returns (config, problems).

    - task-a/task-b use the audited TASK_A/TASK_B strings.
    - custom uses the caller-edited prompt and never inherits A/B.
    - settings are validated with cfg_mod.validate_settings when available.
    """
    problems: list[str] = []
    validate = getattr(cfg_mod, "validate_settings", None)
    if callable(validate):
        problems.extend(validate(settings))
    try:
        effective = cfg_mod.build_effective_config(task_kind, prompt, settings)
    except ValueError as error:
        problems.append(str(error))
        return {}, problems
    return effective, problems


def service_gate(settings: dict, env_mod, services_mod) -> list[dict]:
    """Fresh Inference+Renderer+model check; blocks doomed runs before any side effect."""
    try:
        items = env_mod.check_environment(settings)["items"]
    except Exception as error:
        return [{"name": "Environment", "ok": False, "detail": str(error)[:160]}]
    down = [i for i in items if i.get("name", "").startswith(("Inference", "Renderer")) and not i.get("ok")]
    try:
        down += services_mod.model_problems(settings)
    except Exception:
        pass
    return down


def write_config(effective: dict, task_kind: str, cfg_mod) -> Path:
    return cfg_mod.write_run_config(effective, task_kind)


def temporary_context_offer(settings: dict, problems: list[dict]) -> dict | None:
    """Return the lone Model-context blocker when a temporary instance can cover it.

    Pure predicate: Local mode, exactly one problem, and it is the context
    mismatch — the user's Ollama is reachable but loaded with a smaller
    context length than the run requires. Anything else still needs a manual
    fix.
    """
    if settings.get("mode") != "Local" or len(problems) != 1:
        return None
    only = problems[0]
    return only if only.get("name") == "Model context" else None

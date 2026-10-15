"""Owned input errors.

Every rejection carries a stable code so a caller can tell an unsupported
feature from invalid declared data, and so a missing/unsupported input never
turns into a guessed interpretation.
"""

from __future__ import annotations


class InputError(Exception):
    """A refusal to interpret an input. Never a warning about a usable input."""

    def __init__(self, code: str, message: str, *, stage: str = "prepare") -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.stage = stage


class DependencyError(Exception):
    """A missing or unusable runtime dependency. The message must be actionable."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

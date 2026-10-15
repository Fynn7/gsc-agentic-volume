"""Decode and analysis budgets.

These are development defaults from docs/design.md. They are configurable per
request and the effective values are written into the run manifest, so a run
records what it actually did rather than what the default claims.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import InputError
from .contract import LIMITS, BUDGET_FIELDS, DEFINITIONS

CHUNK_BYTES = BUDGET_FIELDS["chunkBytes"]["default"]
DECODED_INPUT_LIMIT_BYTES = BUDGET_FIELDS["decodedInputLimitBytes"]["default"]
QUANTILE_SAMPLE_LIMIT = BUDGET_FIELDS["quantileSampleLimit"]["default"]
MODEL_HISTOGRAM_BINS = LIMITS["modelHistogramBins"]
QUANTILES = tuple(LIMITS["modelQuantiles"])
MAX_DIMENSION = DEFINITIONS["Dimension3"]["items"]["maximum"]


@dataclass(frozen=True)
class Budgets:
    chunk_bytes: int = CHUNK_BYTES
    decoded_input_limit_bytes: int = DECODED_INPUT_LIMIT_BYTES
    quantile_sample_limit: int = QUANTILE_SAMPLE_LIMIT

    @classmethod
    def from_request(cls, raw: object) -> "Budgets":
        """Apply a request override, or refuse it. Budgets are never silently raised."""
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise InputError("invalid_request", "budgets must be an object", stage="inspect")
        defaults = cls()
        unknown = set(raw) - {"chunkBytes", "decodedInputLimitBytes", "quantileSampleLimit"}
        if unknown:
            raise InputError("invalid_request", f"unknown budget keys: {sorted(unknown)}", stage="inspect")
        chunk = _positive_int(raw.get("chunkBytes", defaults.chunk_bytes), "chunkBytes")
        limit = _positive_int(raw.get("decodedInputLimitBytes", defaults.decoded_input_limit_bytes), "decodedInputLimitBytes")
        sample = _positive_int(raw.get("quantileSampleLimit", defaults.quantile_sample_limit), "quantileSampleLimit")
        if sample > QUANTILE_SAMPLE_LIMIT:
            raise InputError(
                "invalid_request",
                f"quantileSampleLimit {sample} exceeds the contract maximum {QUANTILE_SAMPLE_LIMIT}",
                stage="inspect",
            )
        return cls(chunk_bytes=chunk, decoded_input_limit_bytes=limit, quantile_sample_limit=sample)

    def as_json(self) -> dict:
        return {
            "chunkBytes": self.chunk_bytes,
            "decodedInputLimitBytes": self.decoded_input_limit_bytes,
            "quantileSampleLimit": self.quantile_sample_limit,
        }

    def check_decoded_size(self, decoded_bytes: int, what: str) -> None:
        if decoded_bytes > self.decoded_input_limit_bytes:
            raise InputError(
                "decoded_input_too_large",
                f"{what} decodes to {decoded_bytes} bytes, above the {self.decoded_input_limit_bytes} byte budget",
            )


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise InputError("invalid_request", f"{name} must be a positive integer", stage="inspect")
    return value

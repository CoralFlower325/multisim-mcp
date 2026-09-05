"""Validation helpers for transactional native Multisim parameter sweeps."""

from __future__ import annotations

import itertools
import hashlib
import json
import math
from collections.abc import Mapping
from typing import Any


def prepare_native_sweep(
    readiness: Mapping[str, Any],
    candidates: list[Any],
    approval: Mapping[str, Any],
    *,
    max_combinations: int = 64,
) -> tuple[list[dict[str, float]], list[str]]:
    """Validate approval/readiness and expand bounded candidate value grids."""
    if not isinstance(readiness, Mapping) or readiness.get("state") != "ready-for-com-parameter-sweep":
        raise ValueError("readiness must be ready-for-com-parameter-sweep")
    readiness_digest = readiness.get("readiness_digest")
    if not isinstance(readiness_digest, str) or len(readiness_digest) != 64:
        raise ValueError("readiness must include a valid readiness_digest")
    unsigned_readiness = dict(readiness)
    unsigned_readiness.pop("readiness_digest", None)
    expected_digest = hashlib.sha256(
        json.dumps(
            unsigned_readiness,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    if readiness_digest != expected_digest:
        raise ValueError("readiness_digest does not match readiness")
    if not isinstance(approval, Mapping):
        raise ValueError("approval must be an object")
    allowed = {"approved", "runtime_gate", "restore_original_values", "review_note"}
    unknown = set(approval) - allowed
    if unknown:
        raise ValueError(f"approval contains unknown fields: {sorted(unknown)}")
    for key in ("approved", "runtime_gate", "restore_original_values"):
        if approval.get(key) is not True:
            raise ValueError(f"approval.{key} must be true")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("candidates must be a non-empty array")
    if len(candidates) > 16:
        raise ValueError("candidates must contain at most 16 parameters")
    allowed_targets = {
        str(item.get("refdes") or item.get("target", "")).removesuffix(".value").casefold()
        for item in readiness.get("candidates", [])
        if isinstance(item, Mapping)
    }
    normalized: list[tuple[str, list[float]]] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, Mapping):
            raise ValueError("each candidate must be an object")
        refdes = item.get("refdes")
        values = item.get("values")
        if not isinstance(refdes, str) or not refdes.strip():
            raise ValueError("candidate.refdes must be a non-empty string")
        key = refdes.strip().casefold()
        if key in seen or key not in allowed_targets:
            raise ValueError(f"candidate {refdes!r} is not in readiness candidates")
        if not isinstance(values, list) or not values or len(values) > 16:
            raise ValueError(f"candidate {refdes}.values must contain 1..16 numbers")
        parsed: list[float] = []
        for value in values:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"candidate {refdes}.values must contain finite numbers")
            numeric = float(value)
            if not math.isfinite(numeric) or numeric <= 0:
                raise ValueError(f"candidate {refdes}.values must be positive finite numbers")
            parsed.append(numeric)
        normalized.append((refdes.strip(), parsed))
        seen.add(key)
    total = math.prod(len(values) for _, values in normalized)
    if total > max_combinations:
        raise ValueError(f"candidate grid contains {total} combinations; maximum is {max_combinations}")
    combinations = [
        {refdes: value for (refdes, _), value in zip(normalized, product)}
        for product in itertools.product(*(values for _, values in normalized))
    ]
    return combinations, [refdes for refdes, _ in normalized]


__all__ = ["prepare_native_sweep"]

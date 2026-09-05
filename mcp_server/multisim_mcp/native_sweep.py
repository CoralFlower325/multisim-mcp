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


_METRICS = frozenset({"mean", "min", "max", "peak_to_peak", "rms", "final", "abs_max"})
_DIRECTIONS = frozenset({"minimize", "maximize", "target"})


def _numeric_series(payload: Any, signal: str) -> list[float]:
    """Extract one output series from the normalized COM result envelope."""
    if not isinstance(payload, Mapping):
        return []
    rows = payload.get("rows")
    if isinstance(rows, (list, tuple)):
        numeric_rows = [
            [float(value) for value in row if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))]
            for row in rows
            if isinstance(row, (list, tuple))
        ]
        numeric_rows = [row for row in numeric_rows if row]
        if numeric_rows:
            # A single-output request normally has one row; for multi-row envelopes
            # the final row is the requested signal rather than the shared x-axis.
            return numeric_rows[-1]
    results = payload.get("results")
    if isinstance(results, Mapping):
        nested = results.get(signal)
        if nested is None and len(results) == 1:
            nested = next(iter(results.values()))
        return _numeric_series(nested, signal)
    nested = payload.get(signal)
    return _numeric_series(nested, signal)


def _metric_value(series: list[float], metric: str) -> float:
    if metric == "mean":
        return sum(series) / len(series)
    if metric == "min":
        return min(series)
    if metric == "max":
        return max(series)
    if metric == "peak_to_peak":
        return max(series) - min(series)
    if metric == "rms":
        return math.sqrt(sum(value * value for value in series) / len(series))
    if metric == "final":
        return series[-1]
    return max(abs(value) for value in series)


def rank_native_sweep_results(
    sweep_result: Mapping[str, Any], objective: Mapping[str, Any]
) -> dict[str, Any]:
    """Rank completed native sweep records against one explicit scalar objective."""
    if not isinstance(sweep_result, Mapping) or sweep_result.get("state") != "completed":
        raise ValueError("sweep_result must be a completed native sweep result")
    records = sweep_result.get("results")
    if not isinstance(records, list) or not records or len(records) > 64:
        raise ValueError("sweep_result.results must contain 1..64 records")
    if not isinstance(objective, Mapping):
        raise ValueError("objective must be an object")
    allowed = {"signal", "metric", "direction", "target"}
    unknown = set(objective) - allowed
    if unknown:
        raise ValueError(f"objective contains unknown fields: {sorted(unknown)}")
    signal = objective.get("signal")
    metric = str(objective.get("metric", "")).strip().lower()
    direction = str(objective.get("direction", "")).strip().lower()
    if not isinstance(signal, str) or not signal.strip() or len(signal) > 256 or "\x00" in signal:
        raise ValueError("objective.signal must be a non-empty signal name")
    if metric not in _METRICS:
        raise ValueError(f"objective.metric must be one of: {', '.join(sorted(_METRICS))}")
    if direction not in _DIRECTIONS:
        raise ValueError("objective.direction must be minimize, maximize, or target")
    target = objective.get("target")
    if direction == "target":
        if isinstance(target, bool) or not isinstance(target, (int, float)) or not math.isfinite(float(target)):
            raise ValueError("objective.target must be a finite number for target direction")
        target = float(target)
    elif target is not None:
        raise ValueError("objective.target is only valid with target direction")

    ranked: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping):
            skipped.append({"index": index, "reason": "record is not an object"})
            continue
        series = _numeric_series(record.get("analysis"), signal.strip())
        if not series:
            skipped.append({"index": index, "reason": "signal has no finite samples"})
            continue
        value = _metric_value(series, metric)
        score = value if direction == "minimize" else -value if direction == "maximize" else abs(value - float(target))
        ranked.append(
            {
                "index": index,
                "parameters": record.get("parameters", {}),
                "signal": signal.strip(),
                "metric": metric,
                "direction": direction,
                "sample_count": len(series),
                "value": value,
                "score": score,
            }
        )
    ranked.sort(key=lambda item: (float(item["score"]), int(item["index"])))
    payload: dict[str, Any] = {
        "state": "completed" if ranked else "no-scorable-results",
        "objective": {"signal": signal.strip(), "metric": metric, "direction": direction, **({"target": target} if target is not None else {})},
        "ranked_results": ranked,
        "skipped_results": skipped,
        "best": ranked[0] if ranked else None,
        "source_mutated": False,
    }
    payload["ranking_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload


__all__ = ["prepare_native_sweep", "rank_native_sweep_results"]

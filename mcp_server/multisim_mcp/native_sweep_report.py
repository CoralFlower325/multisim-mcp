"""Baseline comparison and auditable reports for native Multisim sweeps."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _validated(value: Mapping[str, Any], field: str, state: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("state") != state:
        raise ValueError(f"value must have state {state}")
    digest = value.get(field)
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError(f"value must include a valid {field}")
    unsigned = dict(value)
    unsigned.pop(field, None)
    if digest != _digest(unsigned):
        raise ValueError(f"{field} does not match value")
    return dict(value)


def _numeric_parameters(value: object) -> dict[str, float]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("parameters must be a non-empty object")
    result: dict[str, float] = {}
    for key, raw in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError("parameter names must be non-empty strings")
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("parameter values must be finite numbers")
        numeric = float(raw)
        if not math.isfinite(numeric):
            raise ValueError("parameter values must be finite numbers")
        result[key.strip()] = numeric
    return result


def compare_native_sweep_baseline(ranking: Mapping[str, Any]) -> dict[str, Any]:
    """Compare the ranked best record with the original-value baseline record."""
    verified = _validated(ranking, "ranking_digest", "completed")
    if verified.get("quality_state") != "valid":
        raise ValueError("ranking quality must be valid before baseline comparison")
    ranked = verified.get("ranked_results")
    originals = _numeric_parameters(verified.get("original_values"))
    best = verified.get("best")
    if not isinstance(ranked, list) or not ranked or not isinstance(best, Mapping):
        raise ValueError("ranking must contain ranked_results and best")
    folded_originals = {key.casefold(): value for key, value in originals.items()}
    baseline: Mapping[str, Any] | None = None
    for item in ranked:
        if not isinstance(item, Mapping):
            continue
        parameters = _numeric_parameters(item.get("parameters"))
        if {key.casefold() for key in parameters} == set(folded_originals) and all(
            key.casefold() in folded_originals
            and math.isclose(value, folded_originals[key.casefold()], rel_tol=1e-9, abs_tol=1e-18)
            for key, value in parameters.items()
        ):
            baseline = item
            break
    if baseline is None:
        raise ValueError("the candidate grid does not contain the original-value baseline")
    objective = verified.get("objective")
    if not isinstance(objective, Mapping):
        raise ValueError("ranking objective is invalid")
    baseline_value = float(baseline["value"])
    best_value = float(best["value"])
    direction = objective.get("direction")
    if direction == "minimize":
        improvement = baseline_value - best_value
        denominator = abs(baseline_value)
    elif direction == "maximize":
        improvement = best_value - baseline_value
        denominator = abs(baseline_value)
    elif direction == "target":
        target = float(objective["target"])
        baseline_distance = abs(baseline_value - target)
        best_distance = abs(best_value - target)
        improvement = baseline_distance - best_distance
        denominator = baseline_distance
    else:
        raise ValueError("ranking objective direction is invalid")
    tolerance = max(1e-12, abs(float(baseline.get("score", 0.0))) * 1e-9)
    state = "improved" if improvement > tolerance else "regressed" if improvement < -tolerance else "unchanged"
    baseline_parameters = _numeric_parameters(baseline.get("parameters"))
    best_parameters = _numeric_parameters(best.get("parameters"))
    changes = [
        {
            "refdes": refdes,
            "before": folded_originals.get(refdes.casefold()),
            "after": value,
        }
        for refdes, value in best_parameters.items()
        if refdes.casefold() in folded_originals
        and not math.isclose(value, folded_originals[refdes.casefold()], rel_tol=1e-9, abs_tol=1e-18)
    ]
    payload: dict[str, Any] = {
        "state": state,
        "objective": dict(objective),
        "baseline": dict(baseline),
        "best": dict(best),
        "baseline_parameters": baseline_parameters,
        "best_parameters": best_parameters,
        "parameter_changes": changes,
        "metric_improvement": improvement,
        "relative_improvement_percent": (improvement / denominator * 100.0) if denominator > 0 else None,
        "ranking_digest": verified["ranking_digest"],
        "source_mutated": False,
    }
    if isinstance(verified.get("circuit"), Mapping):
        payload["circuit"] = dict(verified["circuit"])
    payload["comparison_digest"] = _digest(payload)
    return payload


def _report_markdown(
    comparison: Mapping[str, Any], optimized_copy_name: str | None = None
) -> str:
    objective = comparison["objective"]
    baseline = comparison["baseline"]
    best = comparison["best"]
    changes = comparison["parameter_changes"]
    relative = comparison.get("relative_improvement_percent")
    relative_text = "不可计算" if relative is None else f"{float(relative):.4g}%"
    rows = "\n".join(
        f"| {item['refdes']} | {item['before']:.12g} | {item['after']:.12g} |"
        for item in changes
    ) or "| 无变化 | - | - |"
    return (
        "# Multisim 原生参数优化报告\n\n"
        f"- 结论：**{comparison['state']}**\n"
        f"- 信号：`{objective['signal']}`\n"
        f"- 指标：`{objective['metric']}`\n"
        f"- 方向：`{objective['direction']}`\n"
        f"- 基线值：`{float(baseline['value']):.12g}`\n"
        f"- 最佳值：`{float(best['value']):.12g}`\n"
        f"- 指标改善量：`{float(comparison['metric_improvement']):.12g}`\n"
        f"- 相对改善：`{relative_text}`\n\n"
        "## 参数变化\n\n"
        "| 元件 | 原值 | 建议值 |\n|---|---:|---:|\n"
        f"{rows}\n\n"
        "## 证据与边界\n\n"
        f"- Ranking digest: `{comparison['ranking_digest']}`\n"
        f"- Comparison digest: `{comparison['comparison_digest']}`\n"
        "- 本报告只比较已验证的扫描结果；源工程未被修改。\n\n"
        "## English summary\n\n"
        f"The best candidate is **{comparison['state']}** relative to the original-value baseline. "
        f"The measured `{objective['metric']}` changed from `{float(baseline['value']):.12g}` "
        f"to `{float(best['value']):.12g}`. Source circuit mutation: `false`.\n"
        + (
            f"\nOptimized Multisim copy: [`{optimized_copy_name}`]({optimized_copy_name}).\n"
            if optimized_copy_name
            else ""
        )
    )


def export_native_sweep_report(
    comparison: Mapping[str, Any],
    output_dir: str,
    optimized_copy_path: str | None = None,
) -> dict[str, Any]:
    """Write a report package and optionally include an optimized .ms14 copy."""
    if not isinstance(comparison, Mapping) or comparison.get("state") not in {
        "improved",
        "unchanged",
        "regressed",
    }:
        raise ValueError("comparison state is invalid")
    verified = _validated(comparison, "comparison_digest", str(comparison["state"]))
    if not isinstance(output_dir, str) or not output_dir.strip() or "\x00" in output_dir:
        raise ValueError("output_dir must be a non-empty path")
    unresolved = Path(output_dir).expanduser()
    if unresolved.is_symlink():
        raise ValueError("output_dir must not be a symbolic link")
    root = unresolved.resolve()
    if root == Path(root.anchor):
        raise ValueError("output_dir must not be a filesystem root")
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise FileExistsError("report output directory must be empty")
    optimized_source: Path | None = None
    if optimized_copy_path is not None:
        if (
            not isinstance(optimized_copy_path, str)
            or not optimized_copy_path.strip()
            or "\x00" in optimized_copy_path
        ):
            raise ValueError("optimized_copy_path must be a non-empty path")
        optimized_source = Path(optimized_copy_path).expanduser().resolve()
        if optimized_source.suffix.casefold() != ".ms14":
            raise ValueError("optimized_copy_path must end with .ms14")
        if optimized_source.is_symlink() or not optimized_source.is_file():
            raise ValueError("optimized_copy_path must be an existing regular file")
    comparison_path = root / "native-optimization-comparison.json"
    report_path = root / "native-optimization-report.md"
    comparison_path.write_text(
        json.dumps(verified, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )
    optimized_copy: Path | None = None
    if optimized_source is not None:
        optimized_copy = root / "optimized-circuit.ms14"
        shutil.copy2(optimized_source, optimized_copy)
    report_path.write_text(
        _report_markdown(verified, optimized_copy.name if optimized_copy else None),
        encoding="utf-8",
    )
    files = []
    artifact_paths = [comparison_path, report_path]
    if optimized_copy is not None:
        artifact_paths.append(optimized_copy)
    for path in artifact_paths:
        data = path.read_bytes()
        files.append(
            {
                "name": path.name,
                "size": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "kind": "multisim-mcp-native-sweep-report",
        "comparison_digest": verified["comparison_digest"],
        "files": files,
    }
    if optimized_copy is not None:
        manifest["optimized_copy"] = {
            "name": optimized_copy.name,
            "source_path": str(optimized_source),
            "sha256": next(item["sha256"] for item in files if item["name"] == optimized_copy.name),
        }
    manifest["manifest_digest"] = _digest(manifest)
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "state": "completed",
        "output_dir": str(root),
        "comparison_path": str(comparison_path),
        "report_path": str(report_path),
        "manifest_path": str(manifest_path),
        "manifest_digest": manifest["manifest_digest"],
        "optimized_copy_path": str(optimized_copy) if optimized_copy else None,
        "source_mutated": False,
    }


__all__ = ["compare_native_sweep_baseline", "export_native_sweep_report"]

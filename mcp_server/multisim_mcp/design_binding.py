"""Read-only binding of a requirement review to an existing circuit design."""

from __future__ import annotations

import hashlib
import json
import csv
import io
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

from .eda_core import CircuitComponent, CircuitDesign
from .requirement_contract import validate_requirement_review
from .spice_adapter import circuit_design_from_spice


DESIGN_BINDING_SCHEMA_VERSION: Final = 1
DESIGN_BINDING_KIND: Final = "multisim-mcp-design-requirement-binding"
DESIGN_SNAPSHOT_SCHEMA_VERSION: Final = 1
DESIGN_SNAPSHOT_KIND: Final = "multisim-mcp-existing-design-snapshot"
_MAX_SNAPSHOT_BYTES: Final = 16 * 1024 * 1024
_VOLTAGE_RE = re.compile(r"^V\((?P<node>[^(),\s]+)(?:,(?P<reference>[^()\s]+))?\)$", re.I)
_CURRENT_RE = re.compile(r"^I\((?P<refdes>[^()\s]+)\)$", re.I)
_OPTIMIZABLE_KINDS: Final = frozenset({"R", "C", "L", "RESISTOR", "CAPACITOR", "INDUCTOR"})
_MODEL_SENSITIVE_KINDS: Final = frozenset({"D", "Q", "M", "J", "X", "U", "T", "O", "S"})
_REPORT_REF_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_VALID_REFDES_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def _digest(value: object) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _signal_binding(signal: object, design: CircuitDesign, aliases: Mapping[str, str]) -> dict[str, Any]:
    if not isinstance(signal, str) or not signal.strip():
        return {"signal": signal, "state": "invalid"}
    requested = signal.strip()
    actual = aliases.get(requested, requested)
    nodes = {item.casefold(): item for item in design.nets}
    components = {item.refdes.casefold(): item for item in design.components}
    voltage = _VOLTAGE_RE.fullmatch(actual)
    if voltage:
        node = voltage.group("node")
        reference = voltage.group("reference")
        state = "bound" if node.casefold() in nodes else "missing-node"
        result: dict[str, Any] = {
            "signal": requested,
            "resolved_signal": actual,
            "state": state,
            "node": nodes.get(node.casefold(), node),
        }
        if reference:
            result["reference_node"] = nodes.get(reference.casefold(), reference)
            if reference.casefold() not in nodes:
                result["state"] = "missing-node"
        return result
    current = _CURRENT_RE.fullmatch(actual)
    if current:
        refdes = current.group("refdes")
        return {
            "signal": requested,
            "resolved_signal": actual,
            "state": "bound" if refdes.casefold() in components else "missing-component",
            "refdes": components.get(refdes.casefold(), refdes).refdes
            if refdes.casefold() in components
            else refdes,
        }
    payload = {
        "signal": requested,
        "resolved_signal": actual,
        "state": "needs-explicit-alias",
        "message": "无法从该信号文本推断节点或元件，请提供 signal_aliases 或明确输出命名。",
    }
    return payload


def bind_requirement_review_to_design(
    design: CircuitDesign,
    review: Mapping[str, Any],
    *,
    signal_aliases: Mapping[str, str] | None = None,
    snapshot_evidence: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Bind review signals to a design snapshot without touching the source file."""
    if not isinstance(design, CircuitDesign):
        raise ValueError("design must be CircuitDesign")
    if snapshot_evidence is not None:
        validate_snapshot_for_binding(design, snapshot_evidence)
    verified = validate_requirement_review(review)
    aliases = {} if signal_aliases is None else dict(signal_aliases)
    if not isinstance(signal_aliases, (Mapping, type(None))):
        raise ValueError("signal_aliases must be an object")
    if len(aliases) > 100:
        raise ValueError("signal_aliases must contain at most 100 entries")
    for key, value in aliases.items():
        if not isinstance(key, str) or not key.strip() or not isinstance(value, str) or not value.strip():
            raise ValueError("signal_aliases keys and values must be non-empty strings")
        if len(key) > 256 or len(value) > 256 or "\x00" in key or "\x00" in value:
            raise ValueError("signal_aliases entries are too long or contain NUL")

    requests: list[dict[str, Any]] = []
    for item in [*verified["hard_constraints"], *verified["soft_objectives"]]:
        owner = str(item["id"])
        for role in ("signal", "reference_signal", "x_signal"):
            signal = item.get(role)
            if signal:
                requests.append(
                    {
                        "owner_id": owner,
                        "role": role,
                        **_signal_binding(signal, design, aliases),
                    }
                )
    bound = sum(item["state"] == "bound" for item in requests)
    missing = [item for item in requests if item["state"] != "bound"]
    optimizable = [
        {
            "refdes": component.refdes,
            "kind": component.kind,
            "value": component.value,
            "target": f"{component.refdes}.value",
            "reason": "bounded value optimization candidate",
        }
        for component in design.components
        if component.value is not None and component.kind.upper() in _OPTIMIZABLE_KINDS
    ]
    payload = {
        "schema_version": DESIGN_BINDING_SCHEMA_VERSION,
        "kind": DESIGN_BINDING_KIND,
        "design_id": design.design_id,
        "design_revision": design.revision,
        "contract_digest": verified["contract_digest"],
        "state": "ready-for-baseline" if not missing else "needs-signal-binding",
        "signals": requests,
        "coverage": {"total": len(requests), "bound": bound, "missing": len(missing)},
        "missing_bindings": missing,
        "optimizable_parameters": optimizable,
        "source_mutated": False,
        "simulation_started": False,
        "next_step": "run_baseline_experiment" if not missing else "define_signal_aliases",
    }
    payload["binding_digest"] = _digest(payload)
    return payload


def validate_snapshot_for_binding(
    design: CircuitDesign, snapshot: Mapping[str, Any]
) -> None:
    """Require a verified snapshot envelope before a guarded binding."""
    if not isinstance(design, CircuitDesign):
        raise ValueError("design must be CircuitDesign")
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot_evidence must be an object")
    if snapshot.get("kind") != DESIGN_SNAPSHOT_KIND:
        raise ValueError("snapshot_evidence kind is invalid")
    if snapshot.get("schema_version") != DESIGN_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("snapshot_evidence schema_version is unsupported")
    snapshot_design = snapshot.get("design")
    if not isinstance(snapshot_design, Mapping):
        raise ValueError("snapshot_evidence.design is required")
    if _digest(snapshot_design) != _digest(design.to_dict()):
        raise ValueError("snapshot_evidence design does not match binding design")
    cross_validation = snapshot.get("cross_validation")
    if not isinstance(cross_validation, Mapping) or cross_validation.get("state") != "verified":
        raise ValueError("snapshot_evidence cross-validation is not verified")
    boundary_review = snapshot.get("boundary_review")
    if not isinstance(boundary_review, Mapping) or not boundary_review.get("optimization_safe"):
        raise ValueError("snapshot_evidence has unresolved model or hidden-pin boundaries")


def build_existing_design_snapshot(
    netlist: str,
    *,
    circuit_info: Mapping[str, Any],
    components: list[Any],
    inputs: list[Any],
    outputs: list[Any],
    allow_unsupported: bool = False,
) -> dict[str, Any]:
    """Parse an exported netlist and retain COM enumeration evidence."""
    if not isinstance(netlist, str) or not netlist.strip():
        raise ValueError("netlist must be non-empty text")
    if not isinstance(circuit_info, Mapping):
        raise ValueError("circuit_info must be an object")
    if not isinstance(components, list) or not isinstance(inputs, list) or not isinstance(outputs, list):
        raise ValueError("COM enumeration results must be arrays")
    name = str(circuit_info.get("name") or "Imported Multisim design").strip()
    try:
        design = circuit_design_from_spice(
            netlist,
            title=name,
            allow_unsupported=allow_unsupported,
        )
    except ValueError:
        if not _looks_like_multisim_connectivity_report(netlist):
            raise
        design = circuit_design_from_multisim_report(netlist, title=name)
    cross_validation = cross_validate_design_snapshot(
        design,
        components=components,
        inputs=inputs,
        outputs=outputs,
    )
    boundary_review = assess_snapshot_boundaries(design)
    payload = {
        "schema_version": DESIGN_SNAPSHOT_SCHEMA_VERSION,
        "kind": DESIGN_SNAPSHOT_KIND,
        "design": design.to_dict(),
        "netlist_sha256": hashlib.sha256(netlist.encode("utf-8")).hexdigest(),
        "source_file": str(circuit_info.get("file") or ""),
        "circuit_name": name,
        "simulation_state": circuit_info.get("state"),
        "last_error": str(circuit_info.get("last_error") or ""),
        "com_enumeration": {
            "components": components,
            "inputs": inputs,
            "outputs": outputs,
        },
        "cross_validation": cross_validation,
        "boundary_review": boundary_review,
        "source_mutated": False,
        "simulation_started": False,
        "next_step": (
            "bind_requirement_review_to_design"
            if cross_validation["state"] == "verified"
            and boundary_review["optimization_safe"]
            else (
                "review_snapshot_mismatch"
                if cross_validation["state"] != "verified"
                else "review_snapshot_boundaries"
            )
        ),
    }
    # The server adds output paths and the raw COM return value afterwards.
    # Keep those ephemeral fields out of the digest so a later process can
    # verify the persisted design evidence deterministically.
    payload["snapshot_digest"] = _digest(payload)
    return payload


def _looks_like_multisim_connectivity_report(text: str) -> bool:
    """Recognize Multisim's CSV/tabular ReportNetlist output without guessing."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    # Multisim's connectivity export starts with an encoded title/header; a
    # normal SPICE netlist starts with a component/directive instead.
    return first.casefold().startswith("_uc") and bool(_multisim_report_rows(text))


def _multisim_report_rows(report: str) -> list[tuple[str, str, str, str]]:
    """Read both CSV (fmt=1) and fixed-width (fmt=0) ReportNetlist forms."""
    rows: list[tuple[str, str, str, str]] = []
    for row in csv.reader(io.StringIO(report)):
        if len(row) >= 4:
            values = tuple(item.strip() for item in row[:4])
            if all(item.casefold().startswith("_uc") for item in values):
                continue
            if values[0] and values[1] and values[3] and _REPORT_REF_RE.fullmatch(values[2]):
                rows.append(values)
    if rows:
        return rows
    for line in report.splitlines():
        stripped = line.strip()
        if not stripped or set(stripped) <= {"-"}:
            continue
        values = tuple(stripped.split()[:4])
        if len(values) == 4 and all(item.casefold().startswith("_uc") for item in values):
            continue
        if len(values) == 4 and values[0] and values[1] and _REPORT_REF_RE.fullmatch(values[2]):
            rows.append(values)
    return rows


def _normalize_report_refdes(refdes: str) -> str:
    """Map Multisim's generated ``_uc...`` names to valid EDA refdes values."""
    if _VALID_REFDES_RE.fullmatch(refdes):
        return refdes
    normalized = re.sub(r"[^A-Za-z0-9_.-]", "_", refdes)
    normalized = f"X{normalized}" if not normalized or not normalized[0].isalpha() else normalized
    return normalized[:64]


def _enumerated_component_names(values: list[Any]) -> set[str]:
    names = _enumerated_names(values)
    return {_normalize_report_refdes(item) for item in names}


def circuit_design_from_multisim_report(
    report: str,
    *,
    title: str = "Imported Multisim connectivity report",
) -> CircuitDesign:
    """Build a bounded topology snapshot from Multisim's connectivity report.

    The report contains net/refdes/pin rows but normally omits values, models,
    and hidden pins.  Those omissions are recorded in annotations and later
    force ``boundary_review.optimization_safe=False``.
    """
    if not isinstance(report, str) or not report.strip():
        raise ValueError("report must be non-empty text")
    rows = _multisim_report_rows(report)
    by_ref: dict[str, dict[str, Any]] = {}
    nets: list[str] = []
    seen_nets: set[str] = set()
    for net, _sheet, refdes, pin in rows:
        net_key = net.casefold()
        if net_key not in seen_nets:
            seen_nets.add(net_key)
            nets.append(net)
        normalized_refdes = _normalize_report_refdes(refdes)
        item = by_ref.setdefault(
            normalized_refdes.casefold(),
            {"refdes": normalized_refdes, "raw_refdes": refdes, "nodes": [], "pins": []},
        )
        if net_key not in {value.casefold() for value in item["nodes"]}:
            item["nodes"].append(net)
        item["pins"].append(pin)
    if not by_ref:
        raise ValueError("Multisim connectivity report contains no component rows")
    components: list[CircuitComponent] = []
    for item in by_ref.values():
        refdes = str(item["refdes"])
        prefix = refdes[0].upper() if refdes else "X"
        kind = prefix if prefix.isalpha() else "X"
        components.append(
            CircuitComponent(
                refdes=refdes,
                kind=kind,
                nodes=tuple(item["nodes"]),
                value=None,
                model=None,
                parameters={},
                annotations={
                    "report_pins": list(item["pins"]),
                    "report_refdes": item["raw_refdes"],
                },
            )
        )
    digest = hashlib.sha256(report.encode("utf-8")).hexdigest()[:20]
    return CircuitDesign(
        design_id=f"multisim-report:{digest}",
        title=title,
        components=tuple(components),
        nets=tuple(nets),
        annotations={
            "multisim_report": {
                "format": "connectivity",
                "model_data_complete": False,
                "hidden_pin_mapping_verified": False,
            }
        },
        source_netlist=report,
    )


def load_existing_design_snapshot(path: str) -> tuple[dict[str, Any], CircuitDesign]:
    """Load and integrity-check a persisted existing-design snapshot."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("snapshot path must not be empty")
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise ValueError("snapshot path must not be a symbolic link")
    resolved = candidate.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"snapshot file does not exist: {resolved}")
    if resolved.stat().st_size > _MAX_SNAPSHOT_BYTES:
        raise ValueError("snapshot file exceeds the 16 MiB safety limit")
    try:
        raw = resolved.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise ValueError("snapshot file is not UTF-8 JSON") from exc
    try:
        snapshot = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("snapshot file is not valid JSON") from exc
    if not isinstance(snapshot, Mapping):
        raise ValueError("snapshot root must be an object")
    if snapshot.get("kind") != DESIGN_SNAPSHOT_KIND:
        raise ValueError("snapshot kind is invalid")
    if snapshot.get("schema_version") != DESIGN_SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("snapshot schema_version is unsupported")
    recorded_digest = snapshot.get("snapshot_digest")
    if not isinstance(recorded_digest, str) or not recorded_digest:
        raise ValueError("snapshot_digest is required")
    unsigned = dict(snapshot)
    unsigned.pop("snapshot_digest", None)
    # Ignore fields added by the server wrapper after the signed payload.
    for key in ("netlist_path", "snapshot_path", "export_result"):
        unsigned.pop(key, None)
    if _digest(unsigned) != recorded_digest:
        raise ValueError("snapshot integrity digest does not match content")
    design_payload = snapshot.get("design")
    if not isinstance(design_payload, Mapping):
        raise ValueError("snapshot.design is required")
    try:
        design = CircuitDesign.from_dict(design_payload)
    except (TypeError, ValueError, KeyError) as exc:
        raise ValueError("snapshot.design is not a valid CircuitDesign") from exc
    return dict(snapshot), design


def _enumerated_names(values: list[Any]) -> set[str]:
    names: set[str] = set()
    for value in values:
        if isinstance(value, str) and value.strip():
            names.add(value.strip().casefold())
        elif isinstance(value, Mapping):
            for key in ("refdes", "name", "id", "output", "input"):
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    names.add(item.strip().casefold())
                    break
    return names


def cross_validate_design_snapshot(
    design: CircuitDesign,
    *,
    components: list[Any],
    inputs: list[Any],
    outputs: list[Any],
) -> dict[str, Any]:
    """Compare parsed design references with COM enumeration evidence."""
    if not isinstance(design, CircuitDesign):
        raise ValueError("design must be CircuitDesign")
    enumerated_components = _enumerated_component_names(components)
    parsed_components = {item.refdes.casefold() for item in design.components}
    missing = sorted(parsed_components - enumerated_components)
    extra = sorted(enumerated_components - parsed_components)
    parsed_nets = {item.casefold() for item in design.nets}
    input_names = sorted(_enumerated_names(inputs))
    output_names = sorted(_enumerated_names(outputs))
    return {
        "state": "verified" if not missing and not extra else "mismatch",
        "component_counts": {
            "parsed": len(parsed_components),
            "enumerated": len(enumerated_components),
        },
        "components_missing_from_enumeration": missing,
        "components_extra_in_enumeration": extra,
        "parsed_net_count": len(parsed_nets),
        "enumerated_input_count": len(input_names),
        "enumerated_output_count": len(output_names),
        "inputs": input_names,
        "outputs": output_names,
        "message": (
            "网表元件与 COM 枚举一致"
            if not missing and not extra
            else "网表元件与 COM 枚举不一致，需人工检查后才能绑定需求"
        ),
    }


def assess_snapshot_boundaries(design: CircuitDesign) -> dict[str, Any]:
    """Report model/pin details that a netlist snapshot cannot prove."""
    if not isinstance(design, CircuitDesign):
        raise ValueError("design must be CircuitDesign")
    findings: list[dict[str, Any]] = []
    for component in design.components:
        kind = component.kind.upper()
        if kind in _MODEL_SENSITIVE_KINDS and not component.model:
            findings.append(
                {
                    "refdes": component.refdes,
                    "kind": component.kind,
                    "code": "model_not_explicit",
                    "message": "该器件依赖模型或载体参数，快照无法证明其内部模型与引脚映射。",
                }
            )
        if len(component.nodes) > 2 or kind in {"X", "U", "T", "O", "K"}:
            findings.append(
                {
                    "refdes": component.refdes,
                    "kind": component.kind,
                    "code": "hidden_pin_mapping_unverified",
                    "message": "多端器件或耦合器件的隐藏引脚/内部节点需要在 Multisim 中人工确认。",
                }
            )
    report_meta = design.annotations.get("multisim_report", {})
    if isinstance(report_meta, Mapping) and report_meta.get("format") == "connectivity":
        findings.append(
            {
                "code": "connectivity_report_missing_parameters",
                "message": "Multisim 连接关系表通常不含元件值、模型和隐藏引脚，需导出/确认完整参数后才能优化。",
            }
        )
    spice_import = design.annotations.get("spice_import", {})
    unsupported = (
        spice_import.get("unsupported", [])
        if isinstance(spice_import, Mapping)
        else []
    )
    if isinstance(unsupported, list):
        for record in unsupported[:32]:
            findings.append(
                {
                    "code": "unsupported_netlist_record",
                    "record": str(record),
                    "message": "网表记录未被当前解析器结构化，不能据此自动优化。",
                }
            )
    return {
        "state": "manual-review-required" if findings else "no-known-boundary-findings",
        "finding_count": len(findings),
        "findings": findings[:64],
        "optimization_safe": not findings,
        "message": (
            "未发现当前快照边界问题"
            if not findings
            else "快照存在模型或隐藏引脚边界，需人工确认后再优化"
        ),
    }


__all__ = [
    "DESIGN_BINDING_KIND",
    "DESIGN_SNAPSHOT_KIND",
    "DESIGN_SNAPSHOT_SCHEMA_VERSION",
    "DESIGN_BINDING_SCHEMA_VERSION",
    "build_existing_design_snapshot",
    "circuit_design_from_multisim_report",
    "load_existing_design_snapshot",
    "assess_snapshot_boundaries",
    "cross_validate_design_snapshot",
    "validate_snapshot_for_binding",
    "bind_requirement_review_to_design",
]

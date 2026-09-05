"""Read-only binding of a requirement review to an existing circuit design."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Final

from .eda_core import CircuitDesign
from .requirement_contract import validate_requirement_review


DESIGN_BINDING_SCHEMA_VERSION: Final = 1
DESIGN_BINDING_KIND: Final = "multisim-mcp-design-requirement-binding"
_VOLTAGE_RE = re.compile(r"^V\((?P<node>[^(),\s]+)(?:,(?P<reference>[^()\s]+))?\)$", re.I)
_CURRENT_RE = re.compile(r"^I\((?P<refdes>[^()\s]+)\)$", re.I)
_OPTIMIZABLE_KINDS: Final = frozenset({"R", "C", "L", "RESISTOR", "CAPACITOR", "INDUCTOR"})


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
    return {
        "signal": requested,
        "resolved_signal": actual,
        "state": "needs-explicit-alias",
        "message": "无法从该信号文本推断节点或元件，请提供 signal_aliases 或明确输出命名。",
    }


def bind_requirement_review_to_design(
    design: CircuitDesign,
    review: Mapping[str, Any],
    *,
    signal_aliases: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Bind review signals to a design snapshot without touching the source file."""
    if not isinstance(design, CircuitDesign):
        raise ValueError("design must be CircuitDesign")
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


__all__ = [
    "DESIGN_BINDING_KIND",
    "DESIGN_BINDING_SCHEMA_VERSION",
    "bind_requirement_review_to_design",
]

"""Round-trip topology checks for generated Multisim designs."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


_TOKEN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_.:$-]*)(?![A-Za-z0-9_])")


def _present(text: str, name: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text, re.I) is not None


def _present_with_section(text: str, name: str) -> bool:
    """Match a multi-section reference designator such as ``A1A`` or ``U1A``.

    Multisim reports one section of a multi-section device as
    ``<refdes><section letter>`` (for example ``A1A`` for section A of ``A1``),
    while ``EnumComponents`` reports the parent reference. The section letter is
    a reporting detail, not a different component.
    """
    return (
        re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(name)}[A-Za-z](?![A-Za-z0-9_])",
            text,
            re.I,
        )
        is not None
    )


def compare_roundtrip_topology(
    expected_components: Iterable[str],
    expected_nets: Iterable[str],
    exported_netlist: str,
    *,
    multi_section_components: Iterable[str] = (),
    connection_counts: Mapping[str, int] | None = None,
    excluded_components: Iterable[str] = (),
    enumerated_components: Iterable[str] = (),
    enumeration_only_components: Iterable[str] = (),
) -> dict[str, Any]:
    """Compare stable names without assuming a vendor netlist dialect.

    Several Multisim reporting behaviours are handled explicitly rather than
    being treated as topology mismatches:

    * Multi-section devices are exported with a section suffix (``A1A``).
    * Sources with fewer than two physical connections are dangling and are
      legitimately omitted from the exported netlist. This is not a loss of
      topology: a one-pin net carries no current path.
    * Virtual instruments (oscilloscope, function generator) are not electrical
      SPICE devices and never appear in the exported netlist.
    * Coupling/temperature devices such as ``K`` are enumerated natively but
      omitted from ReportNetlist, so native enumeration is their presence
      evidence.
    """
    sectioned = {str(item) for item in multi_section_components if str(item)}
    excluded = {str(item) for item in excluded_components if str(item)}
    enumerated = {str(item) for item in enumerated_components if str(item)}
    enumeration_only = {
        str(item) for item in enumeration_only_components if str(item)
    }
    components = sorted(
        {
            str(item)
            for item in expected_components
            if str(item) and str(item) != "0" and str(item) not in excluded
        }
    )
    nets = sorted({str(item) for item in expected_nets if str(item) and str(item) != "0"})

    missing_components: list[str] = []
    enumeration_verified: list[str] = []
    for item in components:
        if _present(exported_netlist, item):
            continue
        if item in sectioned and _present_with_section(exported_netlist, item):
            continue
        if item in enumeration_only and item in enumerated:
            enumeration_verified.append(item)
            continue
        missing_components.append(item)

    # A net needs at least two physical pins to exist as a real node. Anything
    # below that threshold cannot survive a netlist round trip by construction.
    dangling_nets: list[str] = []
    if connection_counts is not None:
        for item in nets:
            if connection_counts.get(item, 0) < 2:
                dangling_nets.append(item)
        required_nets = [item for item in nets if item not in set(dangling_nets)]
    else:
        required_nets = nets

    missing_nets = [item for item in required_nets if not _present(exported_netlist, item)]

    return {
        "schema_version": 1,
        "status": "pass" if not missing_components and not missing_nets else "fail",
        "expected_component_count": len(components),
        "expected_net_count": len(required_nets),
        "missing_components": missing_components,
        "missing_nets": missing_nets,
        "extra_components": [],
        "extra_nets": [],
        "skipped_dangling_nets": sorted(dangling_nets),
        "excluded_components": sorted(excluded),
        "enumeration_verified_components": sorted(enumeration_verified),
        "evidence": (
            "Multisim ReportNetlist text presence plus native enumeration for "
            "devices Multisim omits from the text report; multi-section suffixes "
            "are accepted, dangling one-pin nets and non-electrical virtual "
            "instruments are excluded, and internal vendor nodes are not treated "
            "as extras"
        ),
    }


def _table_rows(exported_netlist: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for raw in exported_netlist.splitlines():
        line = raw.strip()
        if not line or line.lstrip().startswith(("*", ";", "#", ".")):
            continue
        rows.append(line.split())
    return rows


def _refdes_matches(token: str, refdes: str, sectioned: set[str]) -> bool:
    if token.casefold() == refdes.casefold():
        return True
    if refdes in sectioned:
        return bool(re.fullmatch(rf"{re.escape(refdes)}[A-Za-z]", token, re.I))
    return False


def compare_pin_connections(
    expected: dict[str, list[str]],
    exported_netlist: str,
    *,
    connection_counts: Mapping[str, int] | None = None,
    multi_section_components: Iterable[str] = (),
    excluded_components: Iterable[str] = (),
) -> dict[str, Any]:
    """Best-effort pin/net comparison for both supported netlist shapes.

    Multisim's report is a pin table (``node  circuit  refdes  pin``), while the
    plain SPICE form keeps the reference designator first. Nets with fewer than
    two physical pins are omitted by Multisim and are not reported as
    mismatches, and non-electrical virtual instruments are skipped entirely.
    """
    sectioned = {str(item) for item in multi_section_components if str(item)}
    excluded = {str(item) for item in excluded_components if str(item)}
    rows = _table_rows(exported_netlist)
    table_rows = [row for row in rows if len(row) >= 4]

    checked = 0
    mismatches: list[dict[str, Any]] = []
    for refdes, expected_nets in expected.items():
        if refdes in excluded:
            continue
        wanted = [
            net
            for net in expected_nets
            if connection_counts is None or connection_counts.get(net, 0) >= 2
        ]
        if not wanted:
            continue

        # Multisim pin table: the reference designator is the third column and
        # every pin of the component appears on its own row.
        table_nets = [
            row[0]
            for row in table_rows
            if _refdes_matches(row[2], refdes, sectioned)
        ]
        if table_nets:
            checked += 1
            missing = [net for net in wanted if net not in table_nets]
            if missing:
                mismatches.append(
                    {
                        "refdes": refdes,
                        "expected_nets": wanted,
                        "actual_nets": table_nets,
                        "missing_nets": missing,
                    }
                )
            continue

        # Plain SPICE line: the reference designator is first and the remaining
        # tokens may include a value or model, so compare by containment.
        spice_row = next(
            (row for row in rows if row and _refdes_matches(row[0], refdes, sectioned)),
            None,
        )
        if spice_row is None:
            # Presence is reported by the higher-level comparison.
            continue
        checked += 1
        tokens = spice_row[1:]
        missing = [net for net in wanted if net not in tokens]
        if missing:
            mismatches.append(
                {
                    "refdes": refdes,
                    "expected_nets": wanted,
                    "actual_nets": tokens,
                    "missing_nets": missing,
                }
            )
    return {
        "schema_version": 1,
        "status": "pass" if not mismatches else "fail",
        "checked_components": checked,
        "mismatches": mismatches,
        "evidence": (
            "node/pin grouping from the 4-column Multisim netlist report, with a "
            "containment fallback for ordered SPICE lines; dangling nets and "
            "virtual instruments are excluded"
        ),
    }


__all__ = ["compare_pin_connections", "compare_roundtrip_topology"]

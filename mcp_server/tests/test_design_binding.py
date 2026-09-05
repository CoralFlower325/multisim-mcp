"""Tests for read-only binding of requirements to a design snapshot."""

from __future__ import annotations

import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from multisim_mcp import server
from multisim_mcp.design_binding import (
    assess_snapshot_boundaries,
    bind_requirement_review_to_design,
    build_existing_design_snapshot,
)
from multisim_mcp.eda_core import CircuitDesign
from multisim_mcp.requirement_contract import review_design_requirements


def _design() -> CircuitDesign:
    return CircuitDesign.from_dict(
        {
            "schema_version": 1,
            "design_id": "binding-demo",
            "title": "Binding demo",
            "revision": 3,
            "components": [
                {
                    "refdes": "R1",
                    "kind": "R",
                    "nodes": ["in", "out"],
                    "value": "1k",
                    "model": None,
                    "parameters": {},
                },
                {
                    "refdes": "C1",
                    "kind": "C",
                    "nodes": ["out", "0"],
                    "value": "10n",
                    "model": None,
                    "parameters": {},
                },
            ],
            "nets": ["in", "out", "0"],
            "parameters": {},
            "annotations": {},
        }
    )


class DesignBindingTest(unittest.TestCase):
    def test_flags_model_and_hidden_pin_boundaries(self) -> None:
        design = CircuitDesign.from_dict(
            {
                "schema_version": 1,
                "design_id": "boundary-demo",
                "title": "Boundary demo",
                "components": [
                    {"refdes": "D1", "kind": "D", "nodes": ["a", "0"], "value": None},
                    {"refdes": "X1", "kind": "X", "nodes": ["a", "b", "c"], "value": None, "model": "AMP"},
                ],
                "nets": ["a", "b", "c", "0"],
            }
        )
        result = assess_snapshot_boundaries(design)
        self.assertEqual(result["state"], "manual-review-required")
        self.assertFalse(result["optimization_safe"])
        self.assertGreaterEqual(result["finding_count"], 2)

    def test_builds_snapshot_from_exported_netlist_and_com_evidence(self) -> None:
        snapshot = build_existing_design_snapshot(
            "V1 in 0 5\nR1 in out 1k\nC1 out 0 10n\n.end\n",
            circuit_info={"name": "Opened", "file": "C:/demo.ms14", "state": 0},
            components=["V1", "R1", "C1"],
            inputs=["V1"],
            outputs=["V(out)"],
        )
        self.assertEqual(snapshot["kind"], "multisim-mcp-existing-design-snapshot")
        self.assertEqual(snapshot["design"]["title"], "Opened")
        self.assertEqual(snapshot["com_enumeration"]["outputs"], ["V(out)"])
        self.assertEqual(snapshot["cross_validation"]["state"], "verified")
        self.assertEqual(snapshot["next_step"], "bind_requirement_review_to_design")
        self.assertFalse(snapshot["source_mutated"])

    def test_snapshot_marks_com_netlist_mismatch_before_binding(self) -> None:
        snapshot = build_existing_design_snapshot(
            "V1 in 0 5\nR1 in out 1k\n.end\n",
            circuit_info={"name": "Opened", "file": "C:/demo.ms14"},
            components=["V1", "R1", "C_EXTRA"],
            inputs=[],
            outputs=[],
        )
        self.assertEqual(snapshot["cross_validation"]["state"], "mismatch")
        self.assertEqual(snapshot["next_step"], "review_snapshot_mismatch")
        self.assertEqual(
            snapshot["cross_validation"]["components_extra_in_enumeration"], ["c_extra"]
        )

    def test_binds_voltage_and_current_and_lists_optimizable_values(self) -> None:
        review = review_design_requirements(
            [
                {
                    "id": "output",
                    "metric": "mean",
                    "signal": "V(out)",
                    "operator": "between",
                    "lower": 0.0,
                    "upper": 5.0,
                    "unit": "V",
                },
                {
                    "id": "current",
                    "metric": "mean",
                    "signal": "I(R1)",
                    "operator": "at_most",
                    "target": 0.1,
                    "unit": "A",
                },
            ]
        )
        result = bind_requirement_review_to_design(_design(), review)
        self.assertEqual(result["state"], "ready-for-baseline")
        self.assertEqual(result["coverage"], {"total": 2, "bound": 2, "missing": 0})
        self.assertEqual(
            {item["refdes"] for item in result["optimizable_parameters"]}, {"R1", "C1"}
        )
        self.assertFalse(result["source_mutated"])

    def test_reports_missing_signal_and_supports_alias(self) -> None:
        review = review_design_requirements(
            [
                {
                    "id": "output",
                    "metric": "mean",
                    "signal": "OUT_PORT",
                    "operator": "at_least",
                    "target": 1.0,
                    "unit": "V",
                }
            ]
        )
        missing = bind_requirement_review_to_design(_design(), review)
        self.assertEqual(missing["state"], "needs-signal-binding")
        self.assertEqual(missing["next_step"], "define_signal_aliases")
        bound = bind_requirement_review_to_design(
            _design(), review, signal_aliases={"OUT_PORT": "V(out)"}
        )
        self.assertEqual(bound["state"], "ready-for-baseline")
        self.assertEqual(bound["signals"][0]["resolved_signal"], "V(out)")

    def test_server_snapshot_exports_without_touching_source(self) -> None:
        fake_client = Mock()
        fake_client.report_netlist.side_effect = lambda path, probes, fmt: (
            Path(path).write_text(
                "V1 in 0 5\nR1 in out 1k\nC1 out 0 10n\n.end\n",
                encoding="utf-8",
            )
            or path
        )
        fake_client.circuit_info.return_value = {
            "name": "Opened",
            "file": "C:/source.ms14",
            "state": 0,
            "last_error": "",
        }
        fake_client.enum_components.return_value = ["V1", "R1", "C1"]
        fake_client.enum_inputs.return_value = ["V1"]
        fake_client.enum_outputs.return_value = ["V(out)"]
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, "client", fake_client):
            result = server.snapshot_open_circuit(tmp)
            self.assertEqual(result["design"]["title"], "Opened")
            self.assertEqual(
                [item["refdes"] for item in result["design"]["components"]],
                ["V1", "R1", "C1"],
            )
            self.assertTrue(Path(result["snapshot_path"]).is_file())
            self.assertTrue(Path(result["netlist_path"]).is_file())
        self.assertFalse(result["source_mutated"])


if __name__ == "__main__":
    unittest.main()

"""Tests for read-only binding of requirements to a design snapshot."""

from __future__ import annotations

import unittest

from multisim_mcp.design_binding import bind_requirement_review_to_design
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


if __name__ == "__main__":
    unittest.main()

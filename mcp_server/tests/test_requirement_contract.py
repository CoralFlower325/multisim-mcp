"""Tests for requirement-contract normalisation and conflict pre-flight."""

from __future__ import annotations

import unittest

from multisim_mcp import server
from multisim_mcp.requirement_contract import review_design_requirements


def _voltage_constraints() -> list[dict[str, object]]:
    return [
        {
            "id": "vout-range",
            "metric": "mean",
            "signal": "V(out)",
            "operator": "between",
            "lower": 4.8,
            "upper": 5.2,
            "unit": "V",
        }
    ]


class RequirementContractTest(unittest.TestCase):
    def test_normalizes_hard_soft_preference_and_assumption(self) -> None:
        result = review_design_requirements(
            _voltage_constraints(),
            soft_objectives=[
                {
                    "id": "power",
                    "metric": "power",
                    "signal": "V(out)",
                    "goal": "minimize",
                    "weight": 2,
                    "unit": "W",
                },
                {
                    "id": "ripple",
                    "metric": "ripple",
                    "signal": "V(out)",
                    "goal": "target",
                    "target": 20,
                    "weight": 1,
                    "unit": "mV",
                },
            ],
            preferences=[{"id": "prefer-stock", "kind": "in_stock", "weight": 1}],
            assumptions=["输入源为理想直流源"],
            summary="5 V 低纹波输出",
        )
        self.assertEqual(result["state"], "ready-for-baseline")
        self.assertFalse(result["simulation_started"])
        self.assertEqual(result["next_step"], "run_baseline_experiment")
        self.assertEqual(result["hard_constraints"][0]["id"], "vout-range")
        self.assertAlmostEqual(result["soft_objectives"][0]["weight"], 2 / 3, places=6)
        self.assertEqual(result["assumptions"], ["输入源为理想直流源"])
        self.assertRegex(result["contract_digest"], r"^[0-9a-f]{64}$")

    def test_detects_non_overlapping_same_signal_constraints(self) -> None:
        result = review_design_requirements(
            [
                {
                    "id": "low",
                    "metric": "mean",
                    "signal": "V(out)",
                    "operator": "at_least",
                    "target": 5,
                    "unit": "V",
                },
                {
                    "id": "high",
                    "metric": "mean",
                    "signal": "V(out)",
                    "operator": "at_most",
                    "target": 4,
                    "unit": "V",
                },
            ]
        )
        self.assertEqual(result["state"], "conflict")
        self.assertEqual(result["next_step"], "resolve_conflicts_before_baseline")
        self.assertEqual(result["conflicts"][0]["ids"], ["low", "high"])
        suggestions = result["relaxation_suggestions"]
        self.assertEqual(len(suggestions), 2)
        self.assertEqual(
            {(item["id"], item["field"], item["suggested"]) for item in suggestions},
            {("low", "target", 4.0), ("high", "target", 5.0)},
        )
        self.assertEqual(result["conflicts"][0]["relaxation_suggestions"], suggestions)
        self.assertFalse(result["simulation_started"])

    def test_rejects_duplicate_ids_and_invalid_objective(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate requirement id"):
            review_design_requirements(
                _voltage_constraints(),
                soft_objectives=[
                    {
                        "id": "vout-range",
                        "metric": "power",
                        "signal": "V(out)",
                        "goal": "minimize",
                    }
                ],
            )
        with self.assertRaisesRegex(ValueError, "must be minimize, maximize, or target"):
            review_design_requirements(
                _voltage_constraints(),
                soft_objectives=[
                    {
                        "id": "power",
                        "metric": "power",
                        "signal": "V(out)",
                        "goal": "better",
                    }
                ],
            )

    def test_mcp_adapter_exposes_read_only_contract(self) -> None:
        result = server.review_design_requirements(
            _voltage_constraints(),
            soft_objectives=[
                {
                    "id": "power",
                    "metric": "power",
                    "signal": "V(out)",
                    "goal": "minimize",
                }
            ],
        )
        self.assertEqual(result["kind"], "multisim-mcp-requirement-review")
        self.assertFalse(result["simulation_started"])


if __name__ == "__main__":
    unittest.main()

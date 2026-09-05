from __future__ import annotations

import unittest
import hashlib
import json
from unittest.mock import Mock, patch

from multisim_mcp import server
from multisim_mcp.native_sweep import prepare_native_sweep


def _readiness() -> dict:
    payload = {
        "state": "ready-for-com-parameter-sweep",
        "candidates": [
            {"refdes": "R1", "target": "R1.value"},
            {"refdes": "C1", "target": "C1.value"},
        ],
    }
    payload["readiness_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload


def _approval() -> dict:
    return {
        "approved": True,
        "runtime_gate": True,
        "restore_original_values": True,
        "review_note": "test",
    }


def _readiness_for(candidates: list[dict[str, str]]) -> dict:
    payload = _readiness()
    payload["candidates"] = candidates
    payload.pop("readiness_digest")
    payload["readiness_digest"] = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    return payload


class NativeSweepTest(unittest.TestCase):
    def test_expands_bounded_grid(self) -> None:
        combinations, refs = prepare_native_sweep(
            _readiness(),
            [{"refdes": "R1", "values": [1000, 2000]}, {"refdes": "C1", "values": [1e-7]}],
            _approval(),
        )
        self.assertEqual(refs, ["R1", "C1"])
        self.assertEqual(combinations, [{"R1": 1000.0, "C1": 1e-7}, {"R1": 2000.0, "C1": 1e-7}])

    def test_rejects_unapproved_or_oversized_grid(self) -> None:
        with self.assertRaisesRegex(ValueError, "approval.approved"):
            prepare_native_sweep(_readiness(), [{"refdes": "R1", "values": [1]}], {"approved": False})
        with self.assertRaisesRegex(ValueError, "maximum"):
            prepare_native_sweep(
                _readiness(),
                [{"refdes": "R1", "values": [1, 2, 3, 4, 5, 6, 7, 8]} , {"refdes": "C1", "values": [1, 2, 3, 4, 5, 6, 7, 8, 9]}],
                _approval(),
            )

    def test_rejects_tampered_readiness(self) -> None:
        tampered = _readiness()
        tampered["candidates"][0]["refdes"] = "R9"
        with self.assertRaisesRegex(ValueError, "readiness_digest"):
            prepare_native_sweep(tampered, [{"refdes": "R1", "values": [1]}], _approval())

    def test_server_sweep_restores_original_values(self) -> None:
        fake = Mock()
        fake.enum_components.return_value = ["R1"]
        fake.get_rlc_value.return_value = {"component": "R1", "value": 1000.0}
        fake.run_dc_operating_point.return_value = {"results": {"V(out)": {"rows": [[1.0]]}}}
        with patch.object(server, "client", fake):
            result = server.run_native_parameter_sweep(
                _readiness_for([{"refdes": "R1", "target": "R1.value"}]),
                [{"refdes": "R1", "values": [900.0, 1100.0]}],
                "V(out)",
                _approval(),
            )
        self.assertEqual(result["state"], "completed")
        self.assertEqual(result["combination_count"], 2)
        self.assertTrue(result["restored_original_values"])
        self.assertEqual(fake.set_rlc_value.call_args_list[-1].args, ("R1", 1000.0))
        self.assertFalse(result["source_mutated"])


if __name__ == "__main__":
    unittest.main()

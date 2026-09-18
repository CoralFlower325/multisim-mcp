from __future__ import annotations

import unittest

from multisim_mcp.topology_validation import compare_pin_connections, compare_roundtrip_topology


class TopologyValidationTest(unittest.TestCase):
    def test_roundtrip_passes_for_expected_names(self) -> None:
        result = compare_roundtrip_topology(["R1"], ["vin", "out"], "R1 vin out 1k")
        self.assertEqual(result["status"], "pass")

    def test_roundtrip_reports_missing_names(self) -> None:
        result = compare_roundtrip_topology(["R1", "C1"], ["vin"], "R1 vin 0 1k")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_components"], ["C1"])

    def test_pin_connections_report_wrong_order_or_net(self) -> None:
        result = compare_pin_connections({"R1": ["vin", "out"]}, "R1 out 0 1k")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["mismatches"][0]["refdes"], "R1")


class MultiSectionReferenceTest(unittest.TestCase):
    """Multisim exports one section of a multi-section part as A1A/U1A."""

    def test_section_suffix_is_accepted(self) -> None:
        result = compare_roundtrip_topology(
            ["A1"],
            ["din", "dout"],
            "din  c  A1A  I1\ndout  c  A1A  O1\n",
            multi_section_components=["A1"],
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["missing_components"], [])

    def test_section_suffix_requires_opt_in(self) -> None:
        result = compare_roundtrip_topology(
            ["A1"], ["din"], "din  c  A1A  I1\n"
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_components"], ["A1"])

    def test_pin_table_is_parsed_by_refdes_column(self) -> None:
        exported = "din  c  A1A  I1\ndout  c  A1A  O1\n"
        result = compare_pin_connections(
            {"A1": ["din", "dout"]},
            exported,
            multi_section_components=["A1"],
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["checked_components"], 1)

    def test_pin_table_reports_missing_pin_net(self) -> None:
        result = compare_pin_connections(
            {"R1": ["vin", "out"]}, "vin  c  R1  1\n"
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["mismatches"][0]["missing_nets"], ["out"])


class DanglingNetTest(unittest.TestCase):
    """A net needs two physical pins; Multisim drops one-pin nets by design."""

    def test_single_pin_net_is_not_a_mismatch(self) -> None:
        result = compare_roundtrip_topology(
            ["L1", "L2"],
            ["aux", "out"],
            "out  c  L1  2\n0  c  L2  2\n",
            connection_counts={"aux": 1, "out": 2},
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["missing_nets"], [])
        self.assertEqual(result["skipped_dangling_nets"], ["aux"])

    def test_multi_pin_net_still_reported(self) -> None:
        result = compare_roundtrip_topology(
            ["R1"],
            ["vin", "out"],
            "R1 vin 0 1k\n",
            connection_counts={"vin": 2, "out": 2},
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_nets"], ["out"])


class VirtualInstrumentTest(unittest.TestCase):
    """Virtual instruments are not electrical SPICE devices."""

    def test_instruments_are_excluded(self) -> None:
        result = compare_roundtrip_topology(
            ["R1", "XSC1", "XFG1"],
            ["out"],
            "out  c  R1  1\n",
            excluded_components=["XSC1", "XFG1"],
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["missing_components"], [])
        self.assertEqual(
            sorted(result["excluded_components"]), ["XFG1", "XSC1"]
        )

    def test_excluded_component_skipped_in_pin_check(self) -> None:
        result = compare_pin_connections(
            {"XSC1": ["out"], "R1": ["out", "0"]},
            "out  c  R1  1\n0  c  R1  2\n",
            excluded_components=["XSC1"],
        )
        self.assertEqual(result["status"], "pass")


class EnumerationEvidenceTest(unittest.TestCase):
    """K (coupling) is enumerated natively but omitted from ReportNetlist."""

    def test_enumeration_satisfies_presence(self) -> None:
        result = compare_roundtrip_topology(
            ["K1", "L1"],
            ["out"],
            "out  c  L1  2\n",
            enumerated_components=["K1", "L1"],
            enumeration_only_components=["K1"],
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["enumeration_verified_components"], ["K1"])

    def test_enumeration_evidence_requires_actual_enumeration(self) -> None:
        result = compare_roundtrip_topology(
            ["K1"],
            ["out"],
            "out  c  L1  2\n",
            enumerated_components=["L1"],
            enumeration_only_components=["K1"],
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_components"], ["K1"])


if __name__ == "__main__":
    unittest.main()

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

    def test_instruments_are_reported_as_unverifiable(self) -> None:
        """Excluded instruments must not be silently asserted as present."""
        result = compare_roundtrip_topology(
            ["R1", "XSC1"],
            ["out"],
            "out  c  R1  1\n",
            excluded_components=["XSC1"],
        )
        self.assertEqual(result["presence_unverifiable_components"], ["XSC1"])

    def test_excluded_component_skipped_in_pin_check(self) -> None:
        result = compare_pin_connections(
            {"XSC1": ["out"], "R1": ["out", "0"]},
            "out  c  R1  1\n0  c  R1  2\n",
            excluded_components=["XSC1"],
        )
        self.assertEqual(result["status"], "pass")


class EnumerationEvidenceTest(unittest.TestCase):
    """Multisim's ReportNetlist omits devices its EnumComponents reports
    (K coupling, fully-dangling expanded subcircuit primitives, virtual
    instruments). Native enumeration is the decisive presence evidence."""

    def test_enumeration_satisfies_presence(self) -> None:
        result = compare_roundtrip_topology(
            ["K1", "L1"],
            ["out"],
            "out  c  L1  2\n",
            enumerated_components=["K1", "L1"],
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["enumeration_verified_components"], ["K1"])

    def test_dangling_expanded_primitive_is_rescued(self) -> None:
        # An expanded subcircuit resistor whose two nodes are both dangling is
        # reported by EnumComponents but omitted from ReportNetlist.
        result = compare_roundtrip_topology(
            ["RX888227461"],
            [],
            "",
            enumerated_components=["RX888227461"],
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["enumeration_verified_components"], ["RX888227461"])

    def test_enumeration_evidence_requires_actual_enumeration(self) -> None:
        result = compare_roundtrip_topology(
            ["K1"],
            ["out"],
            "out  c  L1  2\n",
            enumerated_components=["L1"],
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_components"], ["K1"])

    def test_enumeration_does_not_launder_absent_component(self) -> None:
        """Enumeration must not make a never-placed part look present."""
        result = compare_roundtrip_topology(
            ["R1", "C9"], ["a"], "a  c  R1  1\n",
            enumerated_components=["R1"],
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_components"], ["C9"])


class PinOrderSemanticsTest(unittest.TestCase):
    """Pin order is electrical semantics and must not be silently swapped.

    Multisim's real report is a pin table (``node circuit refdes pin``), so
    order is verified from the numeric pin column, not from row order.
    """

    @staticmethod
    def _report(rows) -> str:
        return "".join(f"{node:<18} c  {ref:<6} {pin}\n" for node, ref, pin in rows)

    def test_real_report_shape_correct_order_passes(self) -> None:
        result = compare_pin_connections(
            {"R1": ["va", "rb"]},
            self._report([("va", "R1", "1"), ("rb", "R1", "2")]),
        )
        self.assertEqual(result["status"], "pass")

    def test_real_report_shape_swap_is_detected(self) -> None:
        result = compare_pin_connections(
            {"R1": ["va", "rb"]},
            self._report([("rb", "R1", "1"), ("va", "R1", "2")]),
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["mismatches"][0]["reason"], "pin order mismatch")

    def test_pin_order_verified_independently_of_row_order(self) -> None:
        """Descending rows must still pass when the pin numbers are correct."""
        result = compare_pin_connections(
            {"C1": ["rb", "rc"]},
            self._report([("rc", "C1", "2"), ("rb", "C1", "1")]),
        )
        self.assertEqual(result["status"], "pass")

    def test_numeric_pin_swap_detected_with_descending_rows(self) -> None:
        result = compare_pin_connections(
            {"C1": ["rb", "rc"]},
            self._report([("rb", "C1", "2"), ("rc", "C1", "1")]),
        )
        self.assertEqual(result["status"], "fail")

    def test_non_numeric_pins_do_not_guess_order(self) -> None:
        """A/K and IN+/IN- are not positionally sortable without a pin map."""
        result = compare_pin_connections(
            {"D1": ["rl", "0"]},
            self._report([("0", "D1", "K"), ("rl", "D1", "A")]),
        )
        self.assertEqual(result["status"], "pass")

    def test_partially_reported_component_does_not_false_fail(self) -> None:
        """Multisim omits dangling pins; that must not read as a mismatch."""
        result = compare_pin_connections(
            {"R1": ["va", "rb"]},
            self._report([("va", "R1", "1")]),
            connection_counts={"va": 2, "rb": 1},
        )
        self.assertEqual(result["status"], "pass")

    def test_swapped_pins_are_reported_in_spice_form(self) -> None:
        result = compare_pin_connections({"R1": ["vin", "out"]}, "R1 out vin 1k")
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["mismatches"][0]["refdes"], "R1")

    def test_diode_polarity_swap_is_reported_in_spice_form(self) -> None:
        result = compare_pin_connections({"D1": ["a", "k"]}, "D1 k a 1N4001")
        self.assertEqual(result["status"], "fail")

    def test_correct_order_passes_in_spice_form(self) -> None:
        result = compare_pin_connections({"R1": ["vin", "out"]}, "R1 vin out 1k")
        self.assertEqual(result["status"], "pass")

    def test_net_case_difference_is_not_a_mismatch(self) -> None:
        result = compare_pin_connections({"R1": ["vin", "out"]}, "R1 VIN OUT 1k")
        self.assertEqual(result["status"], "pass")


class GenuineLossStillFailsTest(unittest.TestCase):
    """The relaxations must not weaken detection of a really dropped part."""

    def test_dropped_component_fails_without_enumeration(self) -> None:
        result = compare_roundtrip_topology(
            ["R1", "R2"], ["a", "b"],
            "a  c  R1  1\nb  c  R1  2\n",
            enumerated_components=["R1"],
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_components"], ["R2"])

    def test_instrument_exclusion_does_not_hide_part_drop(self) -> None:
        result = compare_roundtrip_topology(
            ["R1", "R2", "XSC1"], ["a"],
            "a  c  R1  1\n",
            excluded_components=["XSC1"],
            enumerated_components=["R1"],
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_components"], ["R2"])

    def test_section_suffix_does_not_rescue_other_device(self) -> None:
        result = compare_roundtrip_topology(
            ["A2"], ["x"], "x  c  A1A  1\n",
            multi_section_components=["A2"],
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_components"], ["A2"])

    def test_lost_multipin_net_alongside_dangling_net(self) -> None:
        result = compare_roundtrip_topology(
            ["R1", "R2"], ["floating", "lost"],
            "a  c  R1  1\na  c  R2  1\n",
            connection_counts={"floating": 1, "lost": 2},
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["missing_nets"], ["lost"])
        self.assertEqual(result["skipped_dangling_nets"], ["floating"])


if __name__ == "__main__":
    unittest.main()

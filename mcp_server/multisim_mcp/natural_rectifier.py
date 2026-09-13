"""Bounded natural-language planner for a diode bridge and smoothing stage."""
from __future__ import annotations

import re
from typing import Any


def parse_natural_rectifier(text: str) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip() or len(text) > 3000:
        raise ValueError("请输入有效的整流电源需求")
    if not re.search(r"整流|桥式|rectif|bridge", text, re.I):
        raise ValueError("当前入口需要明确桥式整流需求")
    if re.search(r"开关电源|switching|逆变|inverter|三相|three[- ]phase", text, re.I):
        raise ValueError("需求超出单相二极管整流合同范围")
    volts = re.findall(r"([0-9]+(?:\.[0-9]+)?)\s*V", text, re.I)
    loads = re.findall(r"(?:负载|load)\s*(?:为|=|:)?\s*([0-9]+(?:\.[0-9]+)?)\s*(mA|A)", text, re.I)
    if len(volts) > 1 or len(loads) > 1:
        raise ValueError("请只提供一个输入电压和一个负载电流")
    ac_v = float(volts[0]) if volts else 12.0
    load_text = loads[0] if loads else None
    load_a = (float(load_text[0]) / (1000 if load_text[1].lower() == "ma" else 1)) if load_text else .1
    if not 4 <= ac_v <= 48 or not 0.005 <= load_a <= 2:
        raise ValueError("输入需为4–48V，负载需为5mA–2A")
    frequency = 50.0 if re.search(r"50\s*Hz", text, re.I) else 60.0
    peak = ac_v * 2 ** .5
    estimated_dc = peak - 2 * .75
    if estimated_dc <= 0:
        raise ValueError("输入电压不足以越过桥式整流的二极管压降")
    return {
        "planning_method": "bounded-diode-bridge-parser",
        "text": text.strip(),
        "template_family": "rectifier_supply",
        "topology": "single_phase_bridge_with_reservoir",
        "derived": {"ac_rms_v": ac_v, "frequency_hz": frequency, "load_a": load_a,
                    "estimated_no_load_dc_v": round(estimated_dc, 6), "diode_count": 4},
        "proposal": {
            "title": "单相桥式整流与滤波",
            "application": text.strip(),
            "netlist": (f"V1 ac_p ac_n SINE(0 {peak:g} {frequency:g})\n"
                        "D1 ac_p vraw 1N4007\nD2 ac_n vraw 1N4007\n"
                        "D3 0 ac_p 1N4007\nD4 0 ac_n 1N4007\n"
                        "C1 vraw 0 1000u\nRLOAD vraw 0 10\n.end\n"),
            "probe_nets": ["ac_p", "ac_n", "vraw", "0"],
            "experiments": [{"type": "op"}, {"type": "tran", "commands": "tran 10u 200m"}],
            "checks": [
                {"analysis": "op", "net": "vraw", "quantity": "value", "min": estimated_dc * .7, "max": peak},
                {"analysis": "tran", "net": "vraw", "quantity": "ripple_vpp", "max": estimated_dc * .2},
            ],
        },
        "assumptions": ["使用四个1N4007和1000uF储能电容；实际二极管模型和纹波以原生仿真为准。",
                        "当前仅生成受限方案，不自动替换为开关电源或稳压控制器。"],
        "status": "unverified-native-proposal",
    }

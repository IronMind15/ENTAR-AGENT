"""PCB 设计计算技能测试（IPC-2221 公式 + 识别 + 工具）"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from skills import get_matched_skill
from skills import pcb_calc
from skills.pcb_calc import (
    calc_min_width, calc_max_current, calc_resistance_drop,
    _is_pcb_calc_query, _extract_current, _extract_oz,
    _extract_temp_rise, _extract_layer, _extract_width,
    _calc_impedance, _calc_via, _calc_skin, _calc_signal,
    _calc_thermal, _calc_led, _calc_divider, _calc_rc,
    _detect_calc_type, _microstrip_z, _solve_width,
)


class PCBFormulaTests(unittest.TestCase):
    """IPC-2221 公式正确性（对照参考值）"""

    def test_min_width_1A_10C_1oz_external(self):
        """1A、10℃、外层、1oz → 约 11.8 mil（IPC-2221 标准值，符合工程经验 10~12mil）"""
        w = calc_min_width(1.0, 1.0, 10.0, is_internal=False)
        self.assertAlmostEqual(w, 11.8, delta=1.0)

    def test_min_width_5A_20C_1oz_external(self):
        """5A、20℃、外层、1oz → 约 71 mil（IPC-2221，比 IPC-2152 的 64 保守）"""
        w = calc_min_width(5.0, 1.0, 20.0, is_internal=False)
        self.assertAlmostEqual(w, 71.5, delta=3.0)

    def test_max_current_roundtrip(self):
        """线宽求载流应与已知电流互逆"""
        w = calc_min_width(5.0, 1.0, 20.0, is_internal=False)
        cur = calc_max_current(w, 1.0, 20.0, is_internal=False)
        self.assertAlmostEqual(cur, 5.0, delta=0.2)

    def test_internal_is_narrower_than_external(self):
        """内层散热差，相同电流需更宽走线"""
        w_ext = calc_min_width(3.0, 1.0, 10.0, is_internal=False)
        w_int = calc_min_width(3.0, 1.0, 10.0, is_internal=True)
        self.assertGreater(w_int, w_ext * 1.9)

    def test_thicker_copper_less_width(self):
        """铜厚翻倍，所需线宽显著减小"""
        w_1oz = calc_min_width(5.0, 1.0, 10.0, is_internal=False)
        w_2oz = calc_min_width(5.0, 2.0, 10.0, is_internal=False)
        self.assertGreater(w_1oz, w_2oz)

    def test_resistance_drop(self):
        """电阻随长度线性增长，随截面增大而减小"""
        r_short = calc_resistance_drop(100, 1.0, 0.1, 3.0)
        r_long = calc_resistance_drop(100, 1.0, 0.2, 3.0)
        self.assertAlmostEqual(r_long[0], r_short[0] * 2, delta=1e-6)

    def test_voltage_power_consistency(self):
        """V = I·R，P = I²·R"""
        r, v, p = calc_resistance_drop(100, 1.0, 0.1, 3.0)
        self.assertAlmostEqual(v, 3.0 * r, delta=1e-6)
        self.assertAlmostEqual(p, 3.0 ** 2 * r, delta=1e-6)


class PCBParameterExtractionTests(unittest.TestCase):
    """参数提取"""

    def test_extract_current(self):
        self.assertAlmostEqual(_extract_current("3A 电流 1oz 外层"), 3.0)
        self.assertAlmostEqual(_extract_current("5 安培 走线多宽"), 5.0)
        self.assertIsNone(_extract_current("帮我算线宽"))

    def test_extract_oz_default(self):
        self.assertAlmostEqual(_extract_oz("3A 1oz"), 1.0)
        self.assertAlmostEqual(_extract_oz("2 oz 铜厚"), 2.0)
        self.assertAlmostEqual(_extract_oz("3A 外层"), 1.0)  # 默认 1oz

    def test_extract_temp_rise(self):
        self.assertAlmostEqual(_extract_temp_rise("温升 20"), 20.0)
        self.assertAlmostEqual(_extract_temp_rise("3A 外层"), 10.0)  # 默认 10

    def test_extract_layer(self):
        self.assertEqual(_extract_layer("内层走线"), "internal")
        self.assertEqual(_extract_layer("外层 走线"), "external")

    def test_extract_width(self):
        w, u = _extract_width("0.5mm 线宽 能过多少")
        self.assertAlmostEqual(w, 0.5)
        self.assertEqual(u, "mm")
        w, u = _extract_width("20mil 走线")
        self.assertAlmostEqual(w, 20.0)
        self.assertEqual(u, "mil")


class PCBQueryRecognitionTests(unittest.TestCase):
    """技能识别：PCB 计算 vs 普通查询"""

    def test_match_pcb_queries(self):
        for q in [
            "3A 电流 1oz 铜 外层 走线要多宽",
            "帮我算 PCB 走线宽度",
            "0.5mm 1oz 能过多少安",
            "内层走线 2A 需要多宽",
            "铜箔 5A 载流 线宽多少",
        ]:
            self.assertTrue(_is_pcb_calc_query(q), q)

    def test_not_match_normal_chat(self):
        for q in [
            "你好",
            "今天天气怎么样",
            "帮我查一下故障代码 d4-1",
            "标准 GB/T 34133 的试验方法",
        ]:
            self.assertFalse(_is_pcb_calc_query(q), q)

    def test_skill_routed_by_registry(self):
        """注册中心能路由到 pcb_calc 技能"""
        skill = get_matched_skill("3A 电流 1oz 外层走线要多宽")
        self.assertIsNotNone(skill)
        self.assertEqual(skill.name, "PCB设计计算")


class PCBHandleTests(unittest.TestCase):
    """端到端 handle 输出"""

    def test_width_direction(self):
        r = pcb_calc._handle_impl("3A 电流 1oz 铜 外层 走线要多宽")
        self.assertIn("最小线宽", r["answer"])
        self.assertIn("IPC-2221", r["answer"])

    def test_current_direction(self):
        r = pcb_calc._handle_impl("0.5mm 线宽 1oz 外层 能过多少安")
        self.assertIn("最大载流", r["answer"])

    def test_with_length_gives_drop(self):
        r = pcb_calc._handle_impl("3A 20mil 1oz 走 10cm 压降多少")
        self.assertIn("压降", r["answer"])

    def test_check_direction_enough(self):
        """100mil 足够承载 3A → 校验通过"""
        r = pcb_calc._handle_impl("3A 100mil 1oz 外层 能过吗")
        self.assertIn("可以承载", r["answer"])
        self.assertIn("余量", r["answer"])

    def test_check_direction_insufficient(self):
        """20mil 不够承载 3A → 给出所需最小线宽"""
        r = pcb_calc._handle_impl("3A 20mil 1oz 外层 能过吗")
        self.assertIn("不够", r["answer"])
        self.assertIn("最小", r["answer"])

    def test_empty_guide(self):
        r = pcb_calc._handle_impl("帮我算")
        self.assertIn("我可以做这些 PCB 计算", r["answer"])


class PCBToolTests(unittest.TestCase):
    """Agent 工具 calc_pcb_trace"""

    def test_tool_width_mode(self):
        from tools import execute_tool
        out = execute_tool("calc_pcb_trace", {"current_a": 3, "oz": 1})
        import json
        data = json.loads(out)
        self.assertEqual(data.get("mode"), "width")
        self.assertIn("min_width_mil", data)

    def test_tool_current_mode(self):
        from tools import execute_tool
        out = execute_tool("calc_pcb_trace", {"width_mil": 50, "oz": 1})
        import json
        data = json.loads(out)
        self.assertEqual(data.get("mode"), "current")
        self.assertIn("max_current_a", data)

    def test_tool_missing_args(self):
        from tools import execute_tool
        out = execute_tool("calc_pcb_trace", {})
        import json
        data = json.loads(out)
        self.assertIn("error", data)


class PCBExtraCalculatorTests(unittest.TestCase):
    """新增计算器公式验证"""

    def test_microstrip_impedance_formula(self):
        """微带线 Z0 公式：0.5mm 线宽、1mm 介质、FR4 → 约 96Ω"""
        z = _microstrip_z(0.5, 1.0, 0.035, 4.2)
        self.assertAlmostEqual(z, 96.0, delta=8.0)

    def test_solve_width_for_50ohm(self):
        """反推 50Ω 微带线宽（介质 1mm FR4）→ 约 2.6mm"""
        w = _solve_width(50, 1.0, 0.035, 4.2, stripline=False)
        self.assertAlmostEqual(w, 2.6, delta=0.8)

    def test_via_current_capacity(self):
        """0.3mm 过孔、默认孔壁 0.7mil、温升10 → 载流约 1.4A"""
        import re as _re
        res = _calc_via("0.3mm 过孔 能过多少安")
        rows = dict(res["rows"])
        self.assertIn("单孔最大载流", rows)
        cur = float(_re.search(r"[\d.]+", rows["单孔最大载流"]).group())
        self.assertTrue(1.0 < cur < 2.0, cur)

    def test_skin_depth(self):
        """1MHz 铜趋肤深度 ≈ 66μm"""
        res = _calc_skin("1MHz 趋肤深度")
        self.assertIn("66.0", res["rows"][2][1])

    def test_thermal_junction_temp(self):
        """TA=25 默认，P=5W，θJA=30 → TJ=175℃"""
        res = _calc_thermal("功耗 5W 热阻 30℃/W 结温多少")
        rows = dict(res["rows"])
        self.assertIn("175", rows["结温 TJ"])

    def test_led_resistor(self):
        """5V 白LED（3V）20mA → 100Ω"""
        res = _calc_led("5V 供电 白LED 20mA 限流电阻")
        rows = dict(res["rows"])
        self.assertIn("100", rows["限流电阻"])

    def test_divider(self):
        """5V R1=10k R2=10k → 2.5V"""
        res = _calc_divider("5V 分压 R1=10k R2=10k 输出多少")
        rows = dict(res["rows"])
        self.assertIn("2.5", rows["输出 Vout"])

    def test_rc_time_constant(self):
        """10kΩ 1μF → τ=10ms"""
        res = _calc_rc("10kΩ 1μF 时间常数")
        rows = dict(res["rows"])
        self.assertIn("10", rows["时间常数 τ"].split(" ")[0])

    def test_calc_type_routing(self):
        """各查询路由到正确计算器"""
        cases = {
            "50Ω 微带 FR4 介质 1mm 要多宽": "impedance",
            "0.3mm 过孔 能过多少安": "via",
            "1MHz 趋肤深度": "skin",
            "FR4 走线延迟多少": "signal",
            "功耗 5W 热阻 30 结温多少": "thermal",
            "5V 白LED 20mA 电阻": "led",
            "5V 分压 R1=10k R2=10k": "divider",
            "10kΩ 1μF 时间常数": "rc",
            "3A 1oz 走线要多宽": "trace",
        }
        for q, expected in cases.items():
            self.assertEqual(_detect_calc_type(q), expected, q)

    def test_extra_calc_end_to_end(self):
        """新增计算器通过技能 handle 输出"""
        for q in [
            "50Ω 微带 FR4 介质 1mm 要多宽",
            "0.3mm 过孔 1oz 孔壁 能过多少安",
            "1MHz 趋肤深度",
            "功耗 5W 热阻 30℃/W 结温多少",
            "5V 白LED 20mA 限流电阻",
        ]:
            r = pcb_calc._handle_impl(q)
            self.assertIn("【", r["answer"], q)
            self.assertTrue(r["source"].startswith("PCB计算"), q)


if __name__ == "__main__":
    unittest.main()

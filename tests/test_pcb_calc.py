"""PCB 设计计算技能测试（IPC-2221 公式 + 识别 + 工具）"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.skills import get_matched_skill
from scripts.skills import pcb_calc
from scripts.skills.pcb_calc import (
    calc_min_width, calc_max_current, calc_resistance_drop,
    _is_pcb_calc_query, _extract_current, _extract_oz,
    _extract_temp_rise, _extract_layer, _extract_width,
    _calc_impedance, _calc_via, _calc_skin, _calc_signal,
    _calc_thermal, _calc_led, _calc_divider, _calc_rc,
    _detect_calc_type, _microstrip_z, _solve_width,
    _calc_layout_check, _build_answer,
    _calc_copper_busbar, _calc_three_phase, _calc_snubber, _calc_pdn,
    _calc_smps, _calc_rated_current, _calc_via_parasitic,
    _calc_lc_resonance, _calc_supercapacitor, _calc_electric_power,
    _calc_vswr, _calc_555, _calc_battery_life, _calc_wire_drop,
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
        from scripts.tools import execute_tool
        out = execute_tool("calc_pcb_trace", {"current_a": 3, "oz": 1})
        import json
        data = json.loads(out)
        self.assertEqual(data.get("mode"), "width")
        self.assertIn("min_width_mil", data)

    def test_tool_current_mode(self):
        from scripts.tools import execute_tool
        out = execute_tool("calc_pcb_trace", {"width_mil": 50, "oz": 1})
        import json
        data = json.loads(out)
        self.assertEqual(data.get("mode"), "current")
        self.assertIn("max_current_a", data)

    def test_tool_missing_args(self):
        from scripts.tools import execute_tool
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
        """5V R1=10k R2=10k → 2.5V；双源 12V/V2=3V → 7.5V"""
        res = _calc_divider("5V 分压 R1=10k R2=10k 输出多少")
        rows = dict(res["rows"])
        self.assertIn("2.5", rows["输出 VR"])
        res2 = _calc_divider("12V 分压 V2=3V R1=10k R2=10k")
        rows2 = dict(res2["rows"])
        self.assertIn("7.5", rows2["输出 VR"])

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


class PCBFixedCalculatorsTests(unittest.TestCase):
    """修复回归：IPC-2152 / 差分对 / 安规 / 波长（此前无测试覆盖，差分与波长曾整体崩溃）"""

    def test_trace2152_reverse_width(self):
        """IPC-2152 已知电流 → 最小线宽"""
        r = pcb_calc._handle_impl("IPC-2152 5A 1oz 外层要多宽")
        self.assertIn("IPC-2152 最小线宽", r["answer"])
        self.assertTrue(r["source"].startswith("PCB计算(trace2152)"), r["source"])

    def test_trace2152_width_to_current(self):
        """IPC-2152 已知线宽 → 最大载流（对照 IPC-2221）"""
        r = pcb_calc._handle_impl("0.5mm 1oz IPC-2152 能过多少安")
        self.assertIn("IPC-2152 最大载流", r["answer"])

    def test_differential_reverse_width(self):
        """100Ω 差分微带 → 反推线宽（修复 re.compile + flags 冲突崩溃）"""
        r = pcb_calc._handle_impl("100Ω 差分微带 FR4 介质 0.4mm 间距 0.3mm 要多宽")
        self.assertIn("所需线宽", r["answer"])
        self.assertTrue(r["source"].startswith("PCB计算(differential)"), r["source"])

    def test_differential_forward(self):
        """给定差分参数 → 差分阻抗"""
        r = pcb_calc._handle_impl("0.1mm 线宽 0.1mm 间距 差分微带 FR4 介质 0.2mm 阻抗多少")
        self.assertIn("差分阻抗 Zdiff", r["answer"])

    def test_creepage_380v(self):
        """380V 爬电距离（安规查表 + 插值）"""
        r = pcb_calc._handle_impl("380V 爬电距离 PD2 材料组 II")
        self.assertIn("爬电距离", r["answer"])
        self.assertTrue(r["source"].startswith("PCB计算(creepage)"), r["source"])

    def test_signal_wavelength(self):
        """100MHz 波长（修复 _extract_length 元组误判崩溃）"""
        r = pcb_calc._handle_impl("100MHz 波长")
        self.assertIn("波长 λ", r["answer"])

    def test_2152_no_false_positive(self):
        """「0.2152 毫米」不应误路由到 IPC-2152 计算器"""
        self.assertEqual(pcb_calc._detect_calc_type("0.2152 毫米 走线能过多少安"), "trace")

    def test_creepage_2500v_full_table(self):
        """2.5kV 命中扩展后的爬电表（材料组 II → 17.5mm），不再低估为 7.1"""
        res = pcb_calc._calc_creepage("2.5kV 爬电距离 PD2 材料组 II")
        rows = dict(res["rows"])
        self.assertIn("17.5", rows["爬电距离"])

    def test_creepage_overflow_warning(self):
        """超出查表范围（如 10kV 峰值）输出警示"""
        res = pcb_calc._calc_creepage("10kV 电气间隙 PD2")
        rows = dict(res["rows"])
        self.assertIn("超出表范围", rows["电气间隙"])

    def test_signal_wavelength_khz(self):
        """100kHz 波长（低频同样支持，不静默跳过）"""
        r = pcb_calc._handle_impl("100kHz 波长")
        self.assertIn("波长 λ", r["answer"])


class PCBCompCheckTests(unittest.TestCase):
    """布局综合校验模式（v1.4.2）：一次输入多参数 → 走线+安规+空间冲突判定"""

    def test_detect_layout(self):
        """综合校验关键词 → layout 计算器"""
        self.assertEqual(pcb_calc._detect_calc_type("综合校验 3A 1oz 380V 材料组II 沟道5mm"), "layout")

    def test_is_pcb_query_layout(self):
        """综合校验类问题应被识别为 PCB 计算需求"""
        self.assertTrue(_is_pcb_calc_query("综合校验 3A 1oz 380V 沟道5mm"))

    def test_missing_params_hint(self):
        """缺参数时给出缺项提示"""
        res = _calc_layout_check("综合校验 3A 1oz")
        self.assertIn("电压", res["rows"][0][1])
        self.assertIn("可用宽度", res["rows"][0][1])

    def test_ok_layout_no_conflict(self):
        """沟道够宽 → ✅ 满足，有余量"""
        res = _calc_layout_check("综合校验 3A 1oz 380V 材料组II 沟道15mm")
        text = _build_answer(res["rows"], res["note"])
        self.assertIn("✅ 满足", text)
        self.assertIn("余量", text)

    def test_conflict_layout_suggestions(self):
        """沟道太窄 → ❌ 冲突，按优先级给出开槽/高CTI/三防漆建议"""
        res = _calc_layout_check("综合校验 10A 1oz 2.5kV 材料组III 沟道3mm")
        text = _build_answer(res["rows"], res["note"])
        self.assertIn("❌ 冲突", text)
        self.assertIn("开槽", text)
        self.assertIn("高 CTI", text)
        self.assertIn("三防漆", text)
        self.assertIn("超出", text)

    def test_handle_impl_routes_layout(self):
        """端到端：_handle_impl 正确路由到 layout 并输出综合校验结果"""
        r = pcb_calc._handle_impl("综合校验 3A 1oz 380V 材料组II 沟道15mm")
        self.assertIn("layout", r["source"])
        self.assertIn("综合校验", r["answer"])
        self.assertIn("走线约束", r["answer"])


class PCBBatch2Tests(unittest.TestCase):
    """批次2：以 pcb-tools.cn 为准的新增高价值计算器"""

    def test_copper_busbar_ampacity(self):
        """铜排 30×3 → ASTM B187 实测表参考"""
        res = _calc_copper_busbar("铜排 30mm宽 3mm厚 载流")
        text = _build_answer(res["rows"], res["note"])
        self.assertIn("ASTM", text)
        self.assertIn("65℃", text)

    def test_three_phase_power(self):
        """380V 100A cos0.85 → P≈55.95kW"""
        res = _calc_three_phase("380V 三相 100A 功率因数0.85 多少千瓦")
        rows = dict(res["rows"])
        self.assertIn("55.95", rows["有功 P"])

    def test_snubber_double_freq(self):
        """双频法 100/60MHz 220pF → Cp≈123.7pF"""
        res = _calc_snubber("原始振铃100MHz 加220pF后60MHz 算吸收电路")
        rows = dict(res["rows"])
        self.assertIn("123.7", rows["开关节点寄生电容 Cp"])

    def test_pdn_target_impedance(self):
        """1.2V 3% 5A → 目标阻抗 7.2mΩ"""
        res = _calc_pdn("Vdd1.2V 纹波3% 阶跃电流5A 目标阻抗多少")
        rows = dict(res["rows"])
        self.assertIn("7.2", rows["目标阻抗 Z_target"])

    def test_smps_buck_efficiency(self):
        """Buck 24→12 5A 10mΩ → 效率 99.8%"""
        res = _calc_smps("Buck 24V转12V 5A Rds10mΩ 效率多少")
        rows = dict(res["rows"])
        self.assertIn("99.8", rows["总损耗 / 效率"])

    def test_rated_current_transformer(self):
        """1000kVA 10/0.4kV → 0.4kV 侧 1443A"""
        res = _calc_rated_current("1000kVA 变压器 10kV/0.4kV 额定电流")
        rows = dict(res["rows"])
        self.assertIn("1443", rows["0.4 kV 侧额定电流"])

    def test_via_parasitic_capacitance(self):
        """板厚1.6 焊盘0.6 反焊盘1.0 → Cvia≈14.2pF"""
        res = _calc_via_parasitic("板厚1.6mm 焊盘0.6 反焊盘1.0 寄生电容")
        rows = dict(res["rows"])
        self.assertIn("14.2", rows["寄生电容 Cvia"])


class PCBBatch3Tests(unittest.TestCase):
    """批次3：常用电路计算器"""

    def test_lc_resonance(self):
        """100μH 10nF → ≈159kHz"""
        res = _calc_lc_resonance("100μH 10nF 谐振频率")
        rows = dict(res["rows"])
        self.assertIn("159", rows["谐振频率 f0"])

    def test_supercapacitor(self):
        """100F 2.7V→1V 100mA → 恒流放电 ≈1700s"""
        res = _calc_supercapacitor("100F 超级电容 2.7V放到1V 负载100mA 能撑多久")
        rows = dict(res["rows"])
        self.assertIn("1700", rows["恒流放电"])

    def test_electric_power(self):
        """12V 3A → 36W"""
        res = _calc_electric_power("12V 3A 功率多少")
        rows = dict(res["rows"])
        self.assertIn("36", rows["功率"])

    def test_vswr(self):
        """VSWR1.5 → RL≈13.98dB"""
        res = _calc_vswr("VSWR1.5 回波损耗多少")
        rows = dict(res["rows"])
        self.assertIn("14", rows["回波损耗 RL"])

    def test_timer_555_astable(self):
        """1k/10k/0.1μF → f≈685Hz"""
        res = _calc_555("555 R1=1k R2=10k C=0.1μF 频率多少")
        rows = dict(res["rows"])
        self.assertIn("685", rows["频率 f"])

    def test_battery_life(self):
        """2000mAh / 100mA → 20小时"""
        res = _calc_battery_life("2000mAh 电池 平均电流100mA 续航多少")
        rows = dict(res["rows"])
        self.assertIn("20", rows["续航时间"])

    def test_wire_drop(self):
        """2.5mm² 铜线 50m 10A → 压降≈3.4V"""
        res = _calc_wire_drop("2.5mm² 铜线 50m 10A 压降多少")
        rows = dict(res["rows"])
        self.assertIn("3.4", rows["压降 ΔV"])

    def test_new_calculators_end_to_end(self):
        """批次2/3 计算器通过技能 handle 输出且非提示"""
        for q in [
            "铜排 30mm宽 3mm厚 载流", "380V 三相 100A 功率因数0.85 多少千瓦",
            "原始振铃100MHz 加220pF后60MHz 算吸收电路", "Vdd1.2V 纹波3% 阶跃电流5A 目标阻抗多少",
            "Buck 24V转12V 5A Rds10mΩ 效率多少", "1000kVA 变压器 10kV/0.4kV 额定电流",
            "100μH 10nF 谐振频率", "12V 3A 功率多少", "20dBm 是几瓦",
            "2.4GHz 波长多少", "VSWR1.5 回波损耗多少", "555 R1=1k R2=10k C=0.1μF 频率多少",
            "10kΩ 100nF 低通截止频率多少", "运放 Rf=10k R1=1k 同相增益多少",
            "3.3V I2C 总线电容100pF 上拉多少", "2.5mm² 铜线 50m 10A 压降多少",
        ]:
            r = pcb_calc._handle_impl(q)
            self.assertIn("【", r["answer"], q)
            self.assertNotIn("【提示】", r["answer"], q)


class PCBTempRiseAndDefaultsTests(unittest.TestCase):
    """v1.5.6 王哥反馈：温升识别不全面 / 默认值不标注 / 多组合补全"""

    # ── 温升识别（王哥反馈核心 bug：「写了30度却用10度」）──

    def test_temp_rise_degree_syntax(self):
        """「30度」「30℃」应识别为温升，不再静默落默认10"""
        for q in ["10A 1oz 30度 走线要多宽", "10A 1oz 30℃，走内层多宽"]:
            v, prov = pcb_calc._extract_temp_rise_ex(q)
            self.assertAlmostEqual(v, 30.0, msg=q)
            self.assertTrue(prov, q)

    def test_temp_rise_absent_is_default(self):
        """未提温升 → 默认10 且标记未提供"""
        v, prov = pcb_calc._extract_temp_rise_ex("5A 1oz 走线要多宽")
        self.assertAlmostEqual(v, 10.0)
        self.assertFalse(prov)

    def test_old_syntax_still_works(self):
        """原「温升10」写法不受影响"""
        self.assertAlmostEqual(_extract_temp_rise("温升 20"), 20.0)

    # ── 层识别扩充（原来只认「内层」）──

    def test_layer_extended_words(self):
        """内电层/中间层/表层/顶层等常见说法应被识别"""
        for q, expected in [
            ("走内电层 5A", "internal"),
            ("中间层 5A", "internal"),
            ("走表层 5A", "external"),
            ("顶层 5A", "external"),
        ]:
            v, prov = pcb_calc._extract_layer_ex(q)
            self.assertEqual(v, expected, q)
            self.assertTrue(prov, q)

    def test_layer_absent_default_external(self):
        """未指明层 → 外层且标记未提供"""
        v, prov = pcb_calc._extract_layer_ex("5A 走线要多宽")
        self.assertEqual(v, "external")
        self.assertFalse(prov)

    # ── oz 识别扩充 ──

    def test_oz_angsi(self):
        """「1盎司」应识别为 1oz"""
        v, prov = pcb_calc._extract_oz_ex("1盎司 5A 走线")
        self.assertAlmostEqual(v, 1.0)
        self.assertTrue(prov)

    # ── 默认值标注 + 多组合表格 ──

    def test_default_marked_in_output(self):
        """信息不全时，输出标注默认值"""
        r = pcb_calc._handle_impl("10A 1oz 走线要多宽")
        self.assertIn("默认，未提供", r["answer"])

    def test_all_provided_no_default_note(self):
        """参数全给时，不出现默认标注，也不出现补全表格"""
        r = pcb_calc._handle_impl("3A 1oz 外层 温升10度 走线要多宽")
        self.assertNotIn("默认，未提供", r["answer"])
        self.assertNotIn("参数补全参考", r["answer"])

    def test_multi_combo_table_when_missing(self):
        """温升未给 → 出现 30℃/50℃ 补全行"""
        r = pcb_calc._handle_impl("10A 1oz 走线要多宽")
        self.assertIn("参数补全参考", r["answer"])
        self.assertIn("温升 30℃", r["answer"])
        self.assertIn("温升 50℃", r["answer"])

    def test_multi_combo_layer_when_unspecified(self):
        """层未指明 → 补内层"""
        r = pcb_calc._handle_impl("10A 1oz 走线要多宽")
        self.assertIn("内层 →", r["answer"])

    def test_30_degree_actually_used(self):
        """「30度」应真实参与计算：结果与默认10度不同且线宽更窄"""
        import re
        r30 = pcb_calc._handle_impl("10A 1oz 30度 走线要多宽")
        r10 = pcb_calc._handle_impl("10A 1oz 走线要多宽")
        w30 = float(re.search(r"最小线宽】([\d.]+)", r30["answer"]).group(1))
        w10 = float(re.search(r"最小线宽】([\d.]+)", r10["answer"]).group(1))
        self.assertLess(w30, w10)

    # ── 工具通道校验 ──

    def test_tool_rejects_oz_hallucination(self):
        """LLM 幻觉 oz=57 应被拦截（王哥「紫铜2×2」案例）"""
        from scripts.tools.calc_pcb_trace import execute
        out = execute({"width_mil": 78.74, "oz": 57, "temp_rise": 30})
        self.assertIn("铜厚 oz 取值异常", out)
        self.assertIn("铜排", out)  # 提示是铜排问题

    def test_tool_is_internal_false_string(self):
        """is_internal 传字符串 false 不应误判为内层"""
        import json
        from scripts.tools.calc_pcb_trace import execute
        out = execute({"current_a": 10, "oz": 1, "temp_rise": 30, "is_internal": "false"})
        data = json.loads(out)
        self.assertFalse(data["is_internal"])


class PCBCompletionTests(unittest.TestCase):
    """v1.5.6 通用参数补足层：缺失参数 → 多组合表格；多组输入 → 多组结果"""

    def test_impedance_missing_er_gets_completion_table(self):
        """阻抗缺介电常数/铜厚 → 追加参数补足表格"""
        r = pcb_calc._handle_impl("50Ω 微带 介质 1mm 要多宽")
        self.assertIn("参数补足参考", r["answer"])
        self.assertIn("介电常数 4.4", r["answer"])
        self.assertIn("铜厚 1.5oz", r["answer"])

    def test_creepage_missing_pd_gets_completion_table(self):
        """安规缺污染等级/材料组 → 追加补足表格"""
        r = pcb_calc._handle_impl("380V 爬电距离")
        self.assertIn("参数补足参考", r["answer"])
        self.assertIn("污染等级 1", r["answer"])
        self.assertIn("材料组 IIIa", r["answer"])

    def test_copper_busbar_missing_gets_completion_table(self):
        """铜排缺温升/密度/材料 → 追加补足表格"""
        r = pcb_calc._handle_impl("铜排 30mm宽 3mm厚 载流")
        self.assertIn("参数补足参考", r["answer"])
        self.assertIn("温升 50℃", r["answer"])
        self.assertIn("材料 黄铜", r["answer"])

    def test_full_params_no_completion_table(self):
        """参数全给 → 不出现补足表格"""
        r = pcb_calc._handle_impl("380V 爬电距离 PD2 材料组II")
        self.assertNotIn("参数补足参考", r["answer"])

    def test_trace_multi_current_groups(self):
        """trace 多组输入：3A 和 5A → 两组结果"""
        r = pcb_calc._handle_impl("3A 和 5A 各要多宽")
        self.assertIn("【3A 时】", r["answer"])
        self.assertIn("【5A 时】", r["answer"])

    def test_target_param_not_counted_missing(self):
        """反算：求解目标（线宽）不算缺失参数，只补介电常数/铜厚"""
        r = pcb_calc._handle_impl("50Ω 微带 介质 1mm 要多宽")
        self.assertNotIn("未指定线宽", r["answer"])
        self.assertIn("未指定介电常数、铜厚", r["answer"])

    def test_table_is_markdown_format(self):
        """补足表格用 markdown 格式（钉钉 reply_markdown 渲染）"""
        r = pcb_calc._handle_impl("380V 爬电距离")
        self.assertIn("| 参数 | 爬电距离 |", r["answer"])
        self.assertIn("|------|------|", r["answer"])


class PCBCreepageBusbarFixTests(unittest.TestCase):
    """v1.6.0 王哥反馈修复：安规 AC/DC / 海拔 / 紫铜路由 / 铜排温度"""

    def test_creepage_ac_vs_dc_clearance_differs(self):
        """交流 vs 直流 1000V：电气间隙不同（直流峰值=电压本身，不乘√2）"""
        import re as _re
        ac = pcb_calc._handle_impl("交流1000V的爬电距离")["answer"]
        dc = pcb_calc._handle_impl("直流1000V的爬电距离")["answer"]
        self.assertIn("交流", ac)
        self.assertIn("直流", dc)
        self.assertLess(float(_re.search(r"【电气间隙】([\d.]+)", dc).group(1)),
                        float(_re.search(r"【电气间隙】([\d.]+)", ac).group(1)))

    def test_altitude_variants(self):
        """海拔多种写法都应识别（3000M海拔/海拔3000m/3000米海拔/大写M）"""
        for q in ["3000M海拔", "海拔3000m", "3000米海拔", "海拔3000M"]:
            self.assertEqual(pcb_calc._extract_altitude_m(q), 3000, q)

    def test_altitude_affects_clearance(self):
        """海拔 3000M → 电气间隙含海拔修正"""
        r = pcb_calc._handle_impl("在3000M海拔情况下，交流1000V的爬电距离")["answer"]
        self.assertIn("含海拔修正", r)

    def test_zitong_routes_to_busbar(self):
        """「紫铜 2x4能走多少电流」→ 铜排计算器（不再落 LLM 瞎算）"""
        q = "计算一下，紫铜 2x4能走多少电流"
        self.assertTrue(pcb_calc._is_pcb_calc_query(q))
        self.assertEqual(pcb_calc._detect_calc_type(q), "copper_busbar")

    def test_zitong_weight_still_metal_weight(self):
        """「紫铜板 100×50×3mm 重量」仍走金属重量，不被紫铜误路由"""
        self.assertEqual(pcb_calc._detect_calc_type("紫铜板 100×50×3mm 重量多少"), "metal_weight")

    def test_busbar_ambient_vs_temp_rise_label(self):
        """铜排区分环境温度 vs 温升标注；模糊的『温度』提示歧义"""
        a = pcb_calc._handle_impl("铜排 30mm宽 3mm厚 环境温度60℃ 载流")["answer"]
        self.assertIn("环境温度 60.0℃", a)
        t = pcb_calc._handle_impl("铜排 30mm宽 3mm厚 温升50℃ 载流")["answer"]
        self.assertIn("温升 50.0℃", t)
        v = pcb_calc._handle_impl("紫铜 2x2 温度60度 载流")["answer"]
        self.assertIn("按温升理解", v)

    def test_busbar_tool_registered(self):
        """铜排工具已注册且可调用（LLM 有正确工具，不再瞎算）"""
        from scripts.tools import execute_tool, get_tool_names
        self.assertIn("calc_copper_busbar", get_tool_names())
        out = execute_tool("calc_copper_busbar", {"w_mm": 30, "h_mm": 3})
        self.assertIn("牛顿散热法", out)


if __name__ == "__main__":
    unittest.main()

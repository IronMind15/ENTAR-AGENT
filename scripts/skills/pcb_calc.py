"""
PCB 设计计算技能 — 走线 / 阻抗 / 过孔 / 热 / 电源 / 通用电路计算

识别用户在 PCB 设计过程中的计算需求，纯本地公式计算，不调 LLM，秒回。
公式与查表以主管提供的权威来源 pcb-tools.cn 为准（data/pcb/pcb_calculation_formulas.json），
与文件冲突处以文件为准。

支持的子计算器（按关键词自动路由）：
  1. trace       走线：IPC-2221 线宽 / 载流 / 压降（含官网速查表对照）
  2. trace2152   走线：IPC-2152 精确载流（ΔT^0.5·A^0.65 近似，与 IPC-2221 对照）
  3. impedance   阻抗：IPC-2141 微带线 / 带状线特征阻抗，支持已知目标阻抗反推线宽
  4. differential差分对：Zdiff = 2×Z0×(1−0.48e^(−0.96s/h)) / 带状线 0.347e^(−2.9s/b)
  5. via         过孔：载流能力 / 电阻 / 并联过孔数（孔壁环形截面）
  6. skin        趋肤深度：δ ≈ 66/√f mm（铜）
  7. signal      信号：走线延迟（ps/inch）与波长 / 1/4 波长
  8. thermal     热设计：结温 TJ = TA + PD×(Rjc+Rcs+Rsa) 热阻链模型，可反推 Rsa_max
  9. led         LED 限流电阻：R = (V - N×Vf) / I，支持串联 N 颗
  10. divider    电阻分压：VR = (V1-V2)×R2/(R1+R2)+V2 双源通用式
  11. rc         RC 时间常数 / 截止频率 / 瞬态 V(t)
  12. creepage   安规：IEC-60664-1 电气间隙 / 爬电距离（查表 + 线性插值）
  13. layout     综合校验：载流 + 安规 + 空间冲突判定
  14. copper_busbar  铜排载流：牛顿散热 + ASTM B187 实测表 + 电流密度反推
  15. three_phase    三相电功率：P/Q/S + 电流反推 + 星三角
  16. snubber        RC 吸收电路：双频法 Cp/Lp/R_snub/C_snub
  17. pdn            电源完整性：目标阻抗 / 去耦电容
  18. smps           开关电源效率与损耗分解（Buck/Boost/Buck-Boost）
  19. buck           Buck 降压转换器元件选型（D/Lmin/Co/Ipeak）
  20. rated_current  额定电流速算（变压器/电机）
  21. via_parasitic  过孔寄生 C/L/截止频率
  22. via_thermal    热过孔热阻（阵列并联）
  23. lc_resonance   LC 谐振频率（含反向求解）
  24. reactance      感抗 / 容抗
  25. supercapacitor 超级电容工作时间
  26. battery_life   电池续航
  27. battery_charging 电池充电时间
  28. iot_battery    IoT 平均电流与电池寿命
  29. wire_drop      导线压降
  30. induction_heating 感应加热功率
  31. opamp          运放增益 / 带宽
  32. opamp_filter   运放有源滤波器
  33. diff_amp       差动放大器
  34. wheatstone     惠斯通电桥
  35. transistor_bias 三极管静态工作点（固定/分压偏置）
  36. zener          稳压管限流电阻
  37. lm317          LM317 输出电压 / R2 反推
  38. mc34063        MC34063 输出 / 限流电阻
  39. i2c_pullup     I2C 上拉电阻范围
  40. parallel_resistance 电阻并联
  41. series_capacitor    电容串联 / 分压
  42. electric_power      电功率与欧姆定律互推
  43. adc            ADC 分辨率 / SNR / 动态范围
  44. rs485          RS-485 终端电阻 / 距离
  45. crystal        晶振匹配电容
  46. smd_pad        SMD 焊盘（IPC-7351）
  47. vswr           VSWR / 回波损耗
  48. frequency_wavelength 频率波长换算
  49. pwm            PWM 占空比 / 频率
  50. metal_weight   金属重量
  51. emc_convert    EMC 单位换算
  52. crosstalk      3W 串扰原则
  53. lc_filter      LC 滤波器（f0/Z0/Q）
  54. timer_555      555 定时器（非稳态/单稳态）

核心公式（走线）：
  I = k × ΔT^0.44 × A^0.725（IPC-2221）
  A = 宽(mil) × 厚(mil)，k = 0.048 外层 / 0.024 内层
  1 oz 铜 ≈ 35 μm ≈ 1.378 mil
"""

import json
import logging
import math
import re

from skills import BaseSkill, register

logger = logging.getLogger("pcb_calc")

# ===== IPC-2221 常量 =====
COPPER_RESISTIVITY = 1.724e-8    # 铜电阻率 Ω·m
MIL_TO_MM = 0.0254
OZ_TO_MIL = 0.035 / MIL_TO_MM    # 1 oz ≈ 1.378 mil
OZ_TO_MM = 0.035                 # 1 oz ≈ 0.035 mm
K_EXTERNAL = 0.048
K_INTERNAL = 0.024
EXP_B = 0.44
EXP_C = 0.725
DEFAULT_TEMP_RISE = 10
SAFETY_MARGIN = 1.2

# ===== IPC-2152 常量（工程近似拟合，I ≈ k·ΔT^0.5·A^0.65）=====
K_2152_EXTERNAL = 0.089
K_2152_INTERNAL = 0.063

# ===== 差分对阻抗 =====
DEFAULT_DIFF_S_RATIO = 1.0        # 线间距 s 缺省按 s/h = 1.0

# ===== IEC-60664-1 安规查表（单位 mm）=====
# 电气间隙：峰值电压 kV → (PD1, PD2, PD3)；1.5kV 起缺失格为工程插值近似
_CLEARANCE_MM = {
    0.5: (0.04, 0.2, 0.8),
    1.5: (0.5, 0.8, 1.5),
    2.5: (1.5, 2.0, 3.2),
    4.0: (3.0, 3.8, 5.5),
    6.0: (5.5, 6.0, 8.0),
}
# 爬电距离：工作电压 RMS(V) → (材料组 I, II, III)；PD2 基准表（IEC-60664-1 表 4A）
_CREEPAGE_MM = {
    63:   (0.63, 0.9, 1.25),
    400:  (2.0, 2.8, 4.0),
    800:  (4.0, 5.6, 8.0),
    1000: (5.0, 7.1, 10.0),
    1600: (8.0, 11.3, 16.0),
    2500: (12.5, 17.5, 25.0),
    4000: (20.0, 28.3, 40.0),
}
_MATERIAL_COL = {"I": 0, "II": 1, "III": 2, "IIIa": 2, "IIIb": 2}
_CREEPAGE_PD_FACTOR = {1: 0.6, 2: 1.0, 3: 1.6}     # PD1/PD3 为工程估计
_ALTITUDE_FACTOR = {2000: 1.0, 3000: 1.14, 4000: 1.29, 5000: 1.48}

# ===== 匹配正则 =====
_PCB_WORDS = [
    "线宽", "走线", "铜厚", "铜箔", "pcb", "PCB", "电路板", "布线",
    "内层", "外层", "载流", "压降", "损耗", "阻抗", "微带", "带状",
    "过孔", "via", "趋肤", "结温", "热阻", "限流", "分压", "时间常数",
    "led", "LED", "波长", "延迟", "介质", "差分", "爬电", "电气间隙",
]
_CALC_INTENT = [
    "多宽", "能过", "够不够", "能不能", "载流", "计算", "要多宽",
    "几安", "多大", "够用", "支持", "算一下", "怎么算", "多少",
]
# 明确的计算主题词：即使没有数值单位，命中即视为计算需求
_CALC_TOPIC_WORDS = [
    "阻抗", "微带", "带状", "趋肤", "过孔", "结温", "热阻",
    "限流", "分压", "时间常数", "波长", "延迟", "介电常数",
    "差分", "爬电", "电气间隙", "creepage", "clearance",
]

_RE_CURRENT = re.compile(r'(\d+(?:\.\d+)?)\s*(?:[Aa]|安|安培)')
_RE_OZ = re.compile(r'(\d+(?:\.\d+)?)\s*oz', re.IGNORECASE)
_RE_TEMP = re.compile(r'(?:温升|ΔT|δT|dT)\s*[=:]?\s*(\d+)', re.IGNORECASE)
_RE_WIDTH = re.compile(r'(\d+(?:\.\d+)?)\s*(mm|毫米|mil)')
_RE_LENGTH = re.compile(r'(\d+(?:\.\d+)?)\s*(cm|厘米|mm|毫米|m\b|米|inch|英寸)')
_RE_MM = re.compile(r'(\d+(?:\.\d+)?)\s*(?:mm|毫米)')            # 毫米值
_RE_MIL = re.compile(r'(\d+(?:\.\d+)?)\s*mil')                    # mil 值
_RE_VOLT = re.compile(r'(\d+(?:\.\d+)?)\s*[Vv](?![A-Za-z0-9])')  # 避免 \b：汉字也是 word 字符，V后接汉字会被 \b 判定无边界

# ===== 新增计算器正则 =====
_RE_2152 = re.compile(r'(?<![\d.])\s*2152\b|ipc\s*-?\s*2152', re.IGNORECASE)   # 防 0.2152mm / 0.2152 毫米 误伤
_RE_KV = re.compile(r'(\d+(?:\.\d+)?)\s*kv', re.IGNORECASE)          # 防 _RE_VOLT 不匹配 kV
_RE_S = re.compile(r'(?:线距|间距|s\s*=)\s*(\d+(?:\.\d+)?)\s*(mm|毫米|mil)', re.IGNORECASE)
_RE_S2 = re.compile(r'(\d+(?:\.\d+)?)\s*(mm|毫米|mil)\s*(?:线距|间距)', re.IGNORECASE)
_RE_W_LABEL = re.compile(r'(?:线宽|w\s*=)\s*(\d+(?:\.\d+)?)\s*(mm|毫米|mil)', re.IGNORECASE)
_RE_H_LABEL = re.compile(r'(?:介质|层厚|h\s*=)\s*(\d+(?:\.\d+)?)\s*(mm|毫米|mil)', re.IGNORECASE)
_RE_B_LABEL = re.compile(r'(?:两平面间距|b\s*=)\s*(\d+(?:\.\d+)?)\s*(mm|毫米|mil)', re.IGNORECASE)
_RE_CTI = re.compile(r'(?:CTI|cti)\s*(\d+)')
_RE_ALT = re.compile(r'(?:海拔|高度)\s*(\d+)\s*m')
_RE_AVAIL = re.compile(r'(?:可用宽度|可用空间|沟道宽度|沟道|净空|走线空间|空间宽度)\s*(?:约|为)?\s*(\d+(?:\.\d+)?)\s*(mm|毫米|mil)', re.IGNORECASE)


def _is_pcb_calc_query(query: str) -> bool:
    """判断是否为 PCB 设计计算需求"""
    q = query.strip().lower()
    # 负向守卫：差分方程/差分法 是数学术语，非 PCB 计算
    if "差分方程" in q or "差分法" in q or "差分隐私" in q:
        return False
    # 新增主题词直接命中（IPC-2152 带边界防 0.2152mm 误伤）
    if _RE_2152.search(q) or "ziff" in q:
        return True
    if any(w in q for w in ["综合校验", "布局校验", "组合校验", "沟道", "净空校验", "走线空间"]):
        return True
    # 独立主题：无 PCB 词也能识别为计算需求（铜排/三相/电源类等）
    if any(w in q for w in ["铜排", "汇流条", "母线", "busbar", "三相", "3相", "three 相",
                            "吸收电路", "吸收电阻", "振铃", "目标阻抗", "去耦", "电源完整性",
                            "寄生电容", "寄生电感", "热过孔", "开关电源", "占空比",
                            "额定电流", "变压器容量", "电抗", "感抗", "容抗",
                            "555", "定时器", "串联电容", "并联电阻", "惠斯通", "电桥",
                            "差动放大", "三极管", "稳压管", "齐纳", "zener", "lm317", "mc34063",
                            "上拉电阻", "感应加热", "超级电容", "电池寿命", "续航",
                            "驻波", "回波损耗", "vswr", "串扰", "运放", "sallen",
                            "adc", "分辨率", "rs485", "晶振", "负载电容", "焊盘",
                            "欧姆定律", "电功率", "金属重量", "多少瓦", "谐振", "波长",
                            "时间常数", "工作点", "功率多少", "dbm", "滤波", "发射",
                            "睡眠电流", "加热", "充电时间", "走线间距", "增益",
                            "截止频率", "等效", "并联", "串联", "充电", "续航"]):
        return True
    if "差分" in q or any(w in q for w in ["爬电", "电气间隙", "creepage", "clearance", "安规间距", "绝缘间距"]):
        return True
    has_pcb = any(w.lower() in q for w in _PCB_WORDS)
    if has_pcb:
        # 含明确数值单位（A/oz/mm）或计算主题词（阻抗/趋肤/过孔等）→ 计算需求
        if _RE_CURRENT.search(q) or _RE_OZ.search(q) or _RE_WIDTH.search(q):
            return True
        if any(w in q for w in _CALC_TOPIC_WORDS):
            return True
        has_intent = any(w in q for w in _CALC_INTENT)
        return has_intent or "算" in q or "多宽" in q or "多少" in q
    if _RE_WIDTH.search(q) and (_RE_OZ.search(q) or _RE_CURRENT.search(q)):
        return any(w in q for w in ["能过", "载流", "几安", "过多少", "安培", "压降"])
    return False


# ===== 通用工具 =====

def _num(v: float, digits: int = 2) -> str:
    if digits <= 0:
        return f"{v:.0f}"   # 整数格式不 rstrip：_num(10,0) 必须 "10" 而非 "1"
    s = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return s if s not in ("", "-") else "0"


def _first(patterns, query, group=1):
    """按顺序尝试多个正则，返回第一个匹配的 float"""
    for p in patterns:
        m = re.search(p, query, re.IGNORECASE)
        if m:
            try:
                return float(m.group(group))
            except (ValueError, IndexError):
                continue
    return None


def _build_answer(rows: list[tuple[str, str]], note: str) -> str:
    """统一格式化：编号行 + 【】标签 + 分隔线 + 提示"""
    lines = []
    for i, (label, val) in enumerate(rows, 1):
        lines.append(f"{i}. 【{label}】{val}")
    lines.append("---")
    lines.append(note)
    return "\n".join(lines)


def _layer_note(is_internal: bool) -> str:
    return "内层" if is_internal else "外层"


# ===== 安规 / 差分 / IPC-2152 通用辅助 =====

def _lin_interp(x: float, x0: float, y0: float, x1: float, y1: float) -> float:
    """线性插值"""
    return y0 + (y1 - y0) * (x - x0) / (x1 - x0)


# ===== 网站速查表对照（data/pcb/tables，权威基准） =====

_TRACE_AMPACITY: list[dict] | None = None


def _trace_ampacity_table():
    """懒加载网站 IPC-2221 1oz 外层 10℃ 速查表（data/pcb/tables）"""
    global _TRACE_AMPACITY
    if _TRACE_AMPACITY is None:
        try:
            from pathlib import Path
            p = Path(__file__).resolve().parents[2] / "data" / "pcb" / "tables" / "trace_ampacity_1oz_10c.json"
            _TRACE_AMPACITY = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            _TRACE_AMPACITY = []
    return _TRACE_AMPACITY


def _ampacity_ref(width_mil: float):
    """1oz 外层 10℃ 场景按网站速查表线性插值；不适用/超范围返回 None"""
    rows = _trace_ampacity_table()
    if not rows:
        return None
    ws = [r["width_mil"] for r in rows]
    if width_mil < ws[0] or width_mil > ws[-1]:
        return None
    for i in range(len(ws) - 1):
        if ws[i] <= width_mil <= ws[i + 1]:
            return _lin_interp(width_mil, ws[i], rows[i]["current_A"],
                               ws[i + 1], rows[i + 1]["current_A"])
    return rows[-1]["current_A"]


def _to_mm(v: float, unit: str) -> float:
    """值 + 单位 → mm"""
    return v if unit in ("mm", "毫米") else v * MIL_TO_MM


def _extract_labeled(pattern, query: str):
    """带标签正则提取 (值, 单位)，返回 (float|None, str)

    注意：pattern 若是已编译正则（自带 IGNORECASE），不能再传 flags 参数，
    否则 Python 3.7+ 抛 ValueError（差分计算器曾因此整体崩溃）。
    """
    m = pattern.search(query) if isinstance(pattern, re.Pattern) \
        else re.search(pattern, query, re.IGNORECASE)
    if m:
        return float(m.group(1)), m.group(2)
    return None, "mm"


def _extract_w_label(q):
    return _extract_labeled(_RE_W_LABEL, q)


def _extract_s(q):
    v, u = _extract_labeled(_RE_S, q)
    if v is not None:
        return v, u
    return _extract_labeled(_RE_S2, q)


def _extract_h(q):
    v, u = _extract_labeled(_RE_H_LABEL, q)
    if v is not None:
        return v, u
    # "Xmm介质/层厚" 顺序变体（如"0.2mm介质"）
    m = re.search(r'(\d+(?:\.\d+)?)\s*(mm|毫米|mil)\s*(?:介质|层厚)', q, re.IGNORECASE)
    if m:
        return float(m.group(1)), m.group(2)
    return None, "mm"


def _extract_b(q):
    return _extract_labeled(_RE_B_LABEL, q)


def _extract_voltage(q):
    """提取电压，(值, "kv"|"v"|"")，kV 优先"""
    m = _RE_KV.search(q)
    if m:
        return float(m.group(1)), "kv"
    m = _RE_VOLT.search(q)
    if m:
        return float(m.group(1)), "v"
    return None, ""


def _extract_pd(q: str) -> int:
    """污染等级，默认 2"""
    m = re.search(r'(?:污染|pd)\s*(?:等级|degree)?\s*(\d)', q, re.IGNORECASE)
    if m and int(m.group(1)) in (1, 2, 3):
        return int(m.group(1))
    return 2


_CTI_GROUPS = [(600, "I"), (400, "II"), (175, "IIIa"), (100, "IIIb")]


def _extract_material(q: str) -> tuple[str, bool]:
    """材料组 + 是否由 CTI 推断，默认 "II"。IIIa/IIIb 共用查表列 III"""
    m_cti = _RE_CTI.search(q)
    if m_cti:
        cti = int(m_cti.group(1))
        for thr, g in _CTI_GROUPS:
            if cti >= thr:
                return g, True
        return "IIIb", True
    m = re.search(r'(?:材料组|材料|组|material\s*group)\s*\(?(IIIa|IIIb|III|II|I)\)?', q, re.IGNORECASE)
    if m:
        return m.group(1).upper(), False
    return "II", False


def _extract_altitude_m(q: str):
    m = _RE_ALT.search(q)
    return int(m.group(1)) if m else None


def _altitude_factor(alt_m):
    if alt_m is None or alt_m <= 2000:
        return 1.0
    alts = sorted(_ALTITUDE_FACTOR)
    if alt_m >= alts[-1]:
        return _ALTITUDE_FACTOR[alts[-1]]
    for i in range(len(alts) - 1):
        a0, a1 = alts[i], alts[i + 1]
        if a0 <= alt_m <= a1:
            return _lin_interp(alt_m, a0, _ALTITUDE_FACTOR[a0], a1, _ALTITUDE_FACTOR[a1])
    return 1.0


def _lookup_clearance(peak_v_kv: float, pd: int):
    """电气间隙查表（线性插值），返回 (mm, 是否超出查表范围)"""
    col = max(0, min(2, pd - 1))
    keys = sorted(_CLEARANCE_MM)
    if peak_v_kv <= keys[0]:
        return _CLEARANCE_MM[keys[0]][col], False
    if peak_v_kv >= keys[-1]:
        return _CLEARANCE_MM[keys[-1]][col], True
    for i in range(len(keys) - 1):
        x0, x1 = keys[i], keys[i + 1]
        if x0 <= peak_v_kv <= x1:
            y0 = _CLEARANCE_MM[x0][col]
            y1 = _CLEARANCE_MM[x1][col]
            return _lin_interp(peak_v_kv, x0, y0, x1, y1), False
    return _CLEARANCE_MM[keys[-1]][col], True


def _lookup_creepage(v_rms: float, pd: int, group: str):
    """爬电距离查表（线性插值 + PD 因子），返回 (mm, 是否超出查表范围)"""
    col = _MATERIAL_COL.get(group, 1)
    keys = sorted(_CREEPAGE_MM)
    if v_rms <= keys[0]:
        base, approx = _CREEPAGE_MM[keys[0]][col], False
    elif v_rms >= keys[-1]:
        base, approx = _CREEPAGE_MM[keys[-1]][col], True
    else:
        base, approx = None, False
        for i in range(len(keys) - 1):
            x0, x1 = keys[i], keys[i + 1]
            if x0 <= v_rms <= x1:
                y0 = _CREEPAGE_MM[x0][col]
                y1 = _CREEPAGE_MM[x1][col]
                base = _lin_interp(v_rms, x0, y0, x1, y1)
                break
        if base is None:
            base, approx = _CREEPAGE_MM[keys[-1]][col], True
    return base * _CREEPAGE_PD_FACTOR.get(pd, 1.0), approx


# ===== 走线计算（IPC-2221） =====

def _k_factor(is_internal: bool) -> float:
    return K_INTERNAL if is_internal else K_EXTERNAL


def calc_min_width(current_a: float, oz: float, temp_rise: float,
                   is_internal: bool) -> float:
    """已知电流 → 最小线宽（mil）"""
    k = _k_factor(is_internal)
    area_mil2 = (current_a / (k * temp_rise ** EXP_B)) ** (1.0 / EXP_C)
    return area_mil2 / (oz * OZ_TO_MIL)


def calc_max_current(width_mil: float, oz: float, temp_rise: float,
                     is_internal: bool) -> float:
    """已知线宽（mil）→ 最大载流（A）"""
    k = _k_factor(is_internal)
    area_mil2 = width_mil * oz * OZ_TO_MIL
    return k * temp_rise ** EXP_B * area_mil2 ** EXP_C


def calc_resistance_drop(width_mil: float, oz: float, length_m: float,
                         current_a: float) -> tuple[float, float, float]:
    """计算电阻（Ω）、压降（V）、功率损耗（W）"""
    thickness_m = oz * OZ_TO_MM * 1e-3
    width_m = width_mil * MIL_TO_MM * 1e-3
    area_m2 = max(thickness_m * width_m, 1e-12)
    r = COPPER_RESISTIVITY * length_m / area_m2
    return r, current_a * r, current_a ** 2 * r


# ===== IPC-2152 载流（近似拟合，非官方公式）=====

def _ipc2152_max_current(width_mil: float, oz: float, temp_rise: float,
                         is_internal: bool) -> float:
    """IPC-2152 已知线宽 → 载流（I ≈ k·ΔT^0.5·A^0.65）"""
    k = K_2152_INTERNAL if is_internal else K_2152_EXTERNAL
    area_mil2 = width_mil * oz * OZ_TO_MIL
    return k * temp_rise ** 0.5 * area_mil2 ** 0.65


def _ipc2152_min_width(current_a: float, oz: float, temp_rise: float,
                       is_internal: bool) -> float:
    """IPC-2152 已知电流 → 最小线宽（mil）"""
    k = K_2152_INTERNAL if is_internal else K_2152_EXTERNAL
    area_mil2 = (current_a / (k * temp_rise ** 0.5)) ** (1.0 / 0.65)
    return area_mil2 / (oz * OZ_TO_MIL)


def _smps_area_for_current(i: float, dt: float) -> float:
    """SMPS.us IPC-2152 通用拟合：面积(mil²) = f(电流, 温升)"""
    return (117.555 * dt ** -0.913 + 1.15) * i ** 0.84 * dt ** -0.108 + 1.159


def _smps_current_for_width(width_mil: float, oz: float, dt: float) -> float:
    """SMPS.us 拟合反解：已知线宽 → 电流（二分）"""
    area = width_mil * oz * OZ_TO_MIL
    lo, hi = 0.001, 2000.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if _smps_area_for_current(mid, dt) < area:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


# ===== 阻抗计算（IPC-2141） =====

def _microstrip_z(w, h, t, er):
    """微带线特征阻抗（单位需一致，mm 或 mil）"""
    return 87 / math.sqrt(er + 1.41) * math.log(5.98 * h / (0.8 * w + t))


def _stripline_z(w, b, t, er):
    """带状线特征阻抗（两参考平面间距 b）"""
    return 60 / math.sqrt(er) * math.log(4 * b / (0.67 * math.pi * (0.8 * w + t)))


def _solve_width(target_z, h, t, er, stripline=False, b=None):
    """二分反解线宽（单位统一为 mm），返回宽 mm"""
    lo, hi = 0.01, 20.0
    for _ in range(60):
        mid = (lo + hi) / 2
        z = (_stripline_z(mid, b, t, er) if stripline
             else _microstrip_z(mid, h, t, er))
        if z > target_z:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _diff_z(w_mm: float, s_mm: float, hb_mm: float, t_mm: float,
            er: float, stripline: bool) -> float:
    """差分阻抗（IPC-2141 近似）。微带：2Z0(1−0.48e^(−0.96s/h))；带状：2Z0(1−0.347e^(−2.9s/b))"""
    z0 = _stripline_z(w_mm, hb_mm, t_mm, er) if stripline \
        else _microstrip_z(w_mm, hb_mm, t_mm, er)
    if stripline:
        factor = 1 - 0.347 * math.exp(-2.9 * s_mm / hb_mm)
    else:
        factor = 1 - 0.48 * math.exp(-0.96 * s_mm / hb_mm)
    return 2 * z0 * factor


def _solve_diff_width(target_zdiff: float, hb_mm: float, s_mm: float,
                      t_mm: float, er: float, stripline: bool) -> float:
    """二分反解差分线宽（mm）"""
    lo, hi = 0.01, 20.0
    for _ in range(60):
        mid = (lo + hi) / 2
        z = _diff_z(mid, s_mm, hb_mm, t_mm, er, stripline)
        if z > target_zdiff:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _calc_impedance(q: str) -> dict:
    """阻抗计算，返回 {rows, source}"""
    is_stripline = "带状" in q or "stripline" in q.lower()
    er = _first([r'介电常数\s*(\d+(?:\.\d+)?)', r'εr\s*=\s*(\d+(?:\.\d+)?)'], q)
    if er is None:
        er = 4.2  # FR4 默认介电常数
    oz = _extract_oz(q)
    t_mm = oz * OZ_TO_MM

    if is_stripline:
        b = _first([r'(?:两平面间距|间距|b\s*=)\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)',
                    r'(?:介质总厚度|总厚度|介质厚度|厚度)\s*(?:为|约)?\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)',
                    r'(\d+(?:\.\d+)?)\s*(?:mm|毫米)\s*(?:介质|两平面|间距)'], q)
    else:
        h = _first([r'(?:介质|层厚|到地|到参考平面|h\s*=)\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)',
                    r'(?:板厚|厚度)\s*(?:为|约)?\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)',
                    r'(\d+(?:\.\d+)?)\s*(?:mm|毫米)\s*(?:介质|层高|板厚|顶层|到地)'], q)
    if (b if is_stripline else h) is None:
        return {"rows": [("提示", "阻抗计算需要介质高度/两平面间距。如「50Ω 微带 FR4 介质 1mm 要多宽」；板厚可直接作为介质高度，如「1.6mm板厚 FR4 50Ω 微带要多宽」")], "note": ""}

    # 目标阻抗（反推线宽）
    target_z = _first([r'(?:目标|匹配)?\s*(\d+(?:\.\d+)?)\s*(?:Ω|欧|ohm)', r'(\d+(?:\.\d+)?)\s*欧姆'], q)
    w_lab, w_lab_unit = _extract_w_label(q)
    if w_lab is not None:
        width_mm, width_unit = w_lab, w_lab_unit
    else:
        width_mm, width_unit = _extract_width(q)

    if target_z is not None:
        w_solved = _solve_width(
            target_z, h, t_mm, er,
            stripline=is_stripline, b=b if is_stripline else None)
        z = (_stripline_z(w_solved, b, t_mm, er) if is_stripline
             else _microstrip_z(w_solved, h, t_mm, er))
        rows = [
            ("计算目标", f"目标 {target_z}Ω{' 带状线' if is_stripline else ' 微带线'} → 求线宽"),
            ("介电常数 εr", str(er)),
            ("铜厚", f"{oz} oz（{t_mm * 1000:.0f} μm）"),
            ("参考间距", f"{b if is_stripline else h} mm"),
            ("所需线宽", f"{_num(w_solved * 1000)} μm（{_num(w_solved)} mm ≈ {_num(w_solved / MIL_TO_MM)} mil）"),
            ("实际阻抗", f"{_num(z, 1)} Ω"),
            ("标准", "IPC-2141 近似公式"),
        ]
        note = "近似值，精确设计请用叠层仿真（如 Si9000）或向板厂要叠层参数验证"
        return {"rows": rows, "note": note}

    if width_mm is not None:
        w_mm = width_mm if width_unit == "mm" else width_mm * MIL_TO_MM
        z = (_stripline_z(w_mm, b, t_mm, er) if is_stripline
             else _microstrip_z(w_mm, h, t_mm, er))
        rows = [
            ("计算目标", f"给定线宽 → 特征阻抗（{'带状线' if is_stripline else '微带线'}）"),
            ("线宽", f"{_num(w_mm * 1000)} μm（{_num(w_mm / MIL_TO_MM)} mil）"),
            ("介电常数 εr", str(er)),
            ("铜厚", f"{oz} oz"),
            ("参考间距", f"{b if is_stripline else h} mm"),
            ("特征阻抗 Z0", f"{_num(z, 1)} Ω"),
            ("标准", "IPC-2141 近似公式"),
        ]
        note = "常用目标：单端 50Ω、USB 90Ω 差分、以太网 100Ω 差分。近似的（±5%），关键设计用仿真验证"
        return {"rows": rows, "note": note}

    return {"rows": [
        ("提示", "阻抗计算需要：线宽（或目标阻抗）+ 介质厚度。如「50Ω 微带 FR4 介质 1mm 要多宽」"),
    ], "note": ""}


# ===== 过孔计算 =====

def _calc_via(q: str) -> dict:
    """过孔载流 / 电阻 / 并联数"""
    # 孔径（mm 或 mil）
    d_mm = _first([r'(?:孔径|钻孔|via)\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)',
                   r'(\d+(?:\.\d+)?)\s*(?:mm|毫米)\s*(?:孔径|孔|via)',
                   r'(\d+(?:\.\d+)?)\s*(?:mm|毫米)'], q)
    d_mil = _first([r'(?:孔径|钻孔|via)\s*(\d+(?:\.\d+)?)\s*mil'], q)
    if d_mm is not None:
        d_mil_val = d_mm / MIL_TO_MM
    elif d_mil is not None:
        d_mil_val = d_mil
    else:
        return {"rows": [("提示", "过孔计算需要孔径（如「0.3mm 过孔」或「10mil 钻孔」）+ 孔壁铜厚")], "note": ""}

    # 孔壁铜厚：oz 或 μm，默认 0.7mil（约 18μm，板厂下限）
    # 注：μm 优先于 oz——题目可能同时给"25μm（1oz）"，μm 是更精确的孔壁标称
    oz = _first([r'(\d+(?:\.\d+)?)\s*oz'], q)
    um = _first([r'(\d+(?:\.\d+)?)\s*(?:μm|um|微米)'], q)
    if um is not None:
        wall_mil = um / 1000 / MIL_TO_MM
    elif oz is not None:
        wall_mil = oz * OZ_TO_MIL
    else:
        wall_mil = 0.7

    temp_rise = _extract_temp_rise(q)
    # 环形截面（平方 mil）
    area_mil2 = math.pi * d_mil_val * wall_mil
    # 载流（IPC-2221，过孔按外层 k）
    max_cur = K_EXTERNAL * temp_rise ** EXP_B * area_mil2 ** EXP_C

    # 板厚（用于电阻）
    thickness_mm = _first([r'(?:板厚|板子厚度)\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)'], q)
    rows = [
        ("计算目标", "过孔载流能力 / 电阻 / 并联数"),
        ("孔径", f"{d_mil_val:.1f} mil（{_num(d_mm)} mm）" if d_mm else f"{_num(d_mil)} mil"),
        ("孔壁铜厚", f"{_num(wall_mil * MIL_TO_MM * 1000)} μm（{_num(wall_mil)} mil）"),
        ("环形截面", f"{_num(area_mil2, 1)} mil²"),
        ("温升", f"{temp_rise} ℃"),
        ("单孔最大载流", f"{_num(max_cur)} A"),
        ("安全电流", f"{_num(max_cur * 0.8)} A（80% 裕量）"),
    ]

    # 电流 → 并联数
    current = _extract_current(q)
    if current is not None:
        n = math.ceil(current / (max_cur * 0.9))
        rows.append(("所需并联过孔", f"{n} 个（单孔 {_num(max_cur)}A × 0.9 折减）"))

    # 板厚 → 电阻
    if thickness_mm is not None:
        area_m2 = area_mil2 * (MIL_TO_MM * 1e-3) ** 2
        r = COPPER_RESISTIVITY * (thickness_mm * 1e-3) / max(area_m2, 1e-12)
        rows.append(("单孔电阻", f"{_num(r * 1000, 3)} mΩ"))
        if current is not None:
            rows.append(("单孔压降", f"{_num(current * r, 4)} V"))
            rows.append(("单孔损耗", f"{_num(current ** 2 * r, 4)} W"))

    rows.append(("标准", "IPC-2221 环形截面经验公式"))
    note = ("孔壁铜厚默认按 18μm（板厂下限），大电流建议 35μm(1oz)/70μm(2oz)。"
            "过孔阵列载流受出入口铜箔限制，需 ≤ 相连走线载流。并联经验：N 孔 ≈ 单孔 × N × 0.9。"
            "网站经验口径：单孔载流 ≈ 外层同等线宽载流的 80%")
    return {"rows": rows, "note": note}


# ===== 趋肤深度 =====

def _calc_skin(q: str) -> dict:
    m = re.search(r'(\d+(?:\.\d+)?)\s*(GHz|MHz|kHz|Hz)', q, re.IGNORECASE)
    if m is None:
        return {"rows": [("提示", "趋肤深度需要频率，如「1MHz 趋肤深度」或「100kHz 铜的趋肤效应」")], "note": ""}
    f = float(m.group(1))
    mult = {"GHZ": 1e9, "MHZ": 1e6, "KHZ": 1e3, "HZ": 1}[m.group(2).upper()]
    f_hz = f * mult
    delta_mm = 66 / math.sqrt(f_hz)
    delta_um = delta_mm * 1000
    rows = [
        ("计算目标", "铜导体的趋肤深度（高频时电流集中在表面）"),
        ("频率", f"{f} {m.group(2)}"),
        ("趋肤深度 δ", f"{delta_um:.1f} μm（{_num(delta_mm, 4)} mm）"),
    ]
    # 铜厚利用判断：题目给出铜厚（oz 或 μm）时判断走线是否被完全利用
    m_th = re.search(r'(\d+(?:\.\d+)?)\s*(?:μm|um|微米)', q, re.IGNORECASE) \
        or re.search(r'(\d+(?:\.\d+)?)\s*oz', q, re.IGNORECASE)
    if m_th is not None:
        h_um = float(m_th.group(1)) * 35 if "oz" in m_th.group(0).lower() else float(m_th.group(1))
        ratio = h_um / delta_um
        if ratio >= 2:
            judge = "❌ 未被完全利用（厚度 ≥ 2δ，高频电阻显著增大）"
        elif ratio >= 1:
            judge = "⚠️ 部分利用（厚度 1~2δ，存在可感趋肤效应）"
        else:
            judge = "✅ 完全利用（厚度 < δ，趋肤效应可忽略）"
        rows.append(("铜厚利用判断", f"铜厚 {_num(h_um, 0)} μm / δ={_num(delta_um, 1)} μm = {_num(ratio, 2)}×δ → {judge}"))
    rows.append(("建议", "线宽/孔径 ≥ 2×δ 时高频电阻显著增大，大电流高频走线需加宽或并联"))
    rows.append(("标准", "δ ≈ 66/√f mm（20℃ 铜）"))
    return {"rows": rows, "note": "趋肤深度指导高频走线：当截面尺寸接近 δ 时电阻明显上升，建议走线尺寸 >> δ"}


# ===== 信号完整性（延迟 + 波长） =====

def _calc_signal(q: str) -> dict:
    er = _first([r'介电常数\s*(\d+(?:\.\d+)?)'], q)
    if er is None:
        er = 4.2
    is_stripline = "带状" in q
    # 有效介电常数（微带，需线宽和介质高度）
    w, wu = _extract_width(q)
    h = _first([r'(?:介质|层厚|h\s*=)\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)'], q)
    if not is_stripline and w is not None and h is not None:
        w_mm = w if wu == "mm" else w * MIL_TO_MM
        er_eff = (er + 1) / 2 + (er - 1) / 2 * (1 + 12 * h / w_mm) ** -0.5
    else:
        er_eff = er  # 带状线用 εr

    t_pd_ps_inch = 84.72 * math.sqrt(er_eff)
    rows = [("计算目标", "走线传播延迟 / 波长"),
            ("介电常数", f"{er}（有效 {er_eff:.2f}）"),
            ("传播延迟", f"{_num(t_pd_ps_inch, 1)} ps/inch（{_num(t_pd_ps_inch * 0.3937, 1)} ps/cm）")]

    # 长度 → 总延迟（_extract_length 无匹配返回 (None, "m")，须判断 length[0]）
    length = _extract_length(q)
    if length[0] is not None:
        len_unit = length[1]
        len_mm = {"m": 1000, "cm": 10, "厘米": 10, "inch": 25.4,
                  "英寸": 25.4}.get(len_unit, 1) * length[0]
        total_ps = t_pd_ps_inch * len_mm / 25.4
        rows.append(("总延迟", f"{_num(total_ps, 1)} ps（{_num(total_ps / 1000, 3)} ns）"))

    # 频率 → 波长
    m = re.search(r'(\d+(?:\.\d+)?)\s*(GHz|MHz|kHz|Hz)', q, re.IGNORECASE)
    if m is not None:
        f = float(m.group(1))
        f_hz = f * {"GHZ": 1e9, "MHZ": 1e6, "KHZ": 1e3, "HZ": 1}[m.group(2).upper()]
        lam = 3e8 / (f_hz * math.sqrt(er_eff)) * 1000  # mm
        rows.append(("波长 λ", f"{_num(lam, 1)} mm"))
        rows.append(("1/4 波长", f"{_num(lam / 4, 1)} mm"))

    rows.append(("标准", "IPC-2141 / 电磁传播"))
    return {"rows": rows, "note": "FR-4 微带约 140~150 ps/inch，带状线约 170~180 ps/inch。1/4 波长用于阻抗匹配段长度"}


# ===== 热设计（结温） =====

def _calc_thermal(q: str) -> dict:
    ta = _first([r'(?:环境温度|环境|TA|Ta|ta)\s*(?:[A-Za-z]*)\s*[=:]?\s*(\d+(?:\.\d+)?)',
                 r'(\d+(?:\.\d+)?)\s*℃\s*(?:环境|室温)',
                 r'(?:环境温度|环境|TA|Ta|ta)\s*[=:]?\s*(\d+(?:\.\d+)?)'], q)
    power = _first([r'(\d+(?:\.\d+)?)\s*[Ww](?![A-Za-z0-9])'], q)
    if power is None:
        # LDO 场景：功耗 = (Vin − Vout) × Iout（如「输入15V、输出5V、负载电流60mA」）
        m_vin = re.search(r'(?:输入|Vin)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V', q, re.IGNORECASE)
        m_vout = re.search(r'(?:输出|Vout)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V', q, re.IGNORECASE)
        m_iout = re.search(r'(?:负载电流|Iout|输出电流)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(mA|A)', q, re.IGNORECASE)
        if m_iout is None:
            m_iout = re.search(r'电流\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(mA|A)', q, re.IGNORECASE)
        if m_vin and m_vout and m_iout:
            iout_a = float(m_iout.group(1)) * (1e-3 if m_iout.group(2).lower().startswith("m") else 1.0)
            power = (float(m_vin.group(1)) - float(m_vout.group(1))) * iout_a
    tj_max = _first([r'(?:结温上限|最大结温|Tjmax|Tj_max)\s*(\d+(?:\.\d+)?)'], q)
    rjc = _first([r'(?:Rjc|结到壳)\s*(\d+(?:\.\d+)?)'], q)
    rcs = _first([r'(?:Rcs|壳到散热片)\s*(\d+(?:\.\d+)?)'], q)
    rsa = _first([r'(?:Rsa|散热片到环境)\s*(\d+(?:\.\d+)?)'], q)
    theta = _first([r'(\d+(?:\.\d+)?)\s*(?:℃/W|K/W|°C/W)'], q)
    # 铜箔面积缩放：datasheet RθJA 基于 1in²=6.45cm² 铜箔，实际面积不同按 ∝1/√A 近似修正
    m_area = re.search(r'(\d+(?:\.\d+)?)\s*cm\s*[²2]', q)
    if theta is not None and m_area and ("in²" in q or "平方英寸" in q or "6.45" in q):
        theta = theta * math.sqrt(6.45 / float(m_area.group(1)))

    if ta is None:
        ta = 25.0
    rows = [("计算目标", "结温 TJ = TA + PD × (Rjc + Rcs + Rsa)"),
            ("环境温度 TA", f"{ta} ℃")]

    # 热阻链模式（Rjc + Rcs + Rsa，网站口径）
    if rjc is not None:
        total_r = rjc + (rcs or 0) + (rsa or 0)
        rows.append(("热阻链", f"Rjc {_num(rjc)} + Rcs {_num(rcs or 0)} + Rsa {_num(rsa or 0)} = {_num(total_r)} ℃/W"))
        if power is not None:
            tj = ta + power * total_r
            limit = tj_max or 125
            rows.append(("结温 TJ", f"{_num(tj)} ℃"))
            rows.append(("判断", "✅ 可接受" if tj <= limit else f"⚠️ 超 {limit}℃，需加散热/降低功耗"))
        else:
            rows.append(("提示", "需提供功耗 P（W）。如「功耗 5W Rjc0.5 Rcs1 Rsa10 结温多少」"))
        if tj_max is not None and power is not None:
            rsa_max = (tj_max - ta) / power - rjc - (rcs or 0)
            rows.append(("散热片最大 Rsa", f"{_num(rsa_max, 2)} ℃/W" + ("（当前 Rsa 不够，需更大散热片）" if rsa is not None and rsa_max < rsa else "")))
        rows.append(("标准", "热阻链模型（Rjc 查 datasheet，Rcs≈0.3~1.0，Rsa≈3~15）"))
        return {"rows": rows, "note": "Rjc 来自器件 datasheet；Rcs 为导热垫/硅脂（0.3~1.0）；Rsa 为散热片到环境（自然对流铝翅片 3~15）。结温超 datasheet 上限（通常 125℃）会降寿"}

    # 单 θJA 简化模式（兼容旧用法）
    if theta is not None and power is not None:
        tj = ta + theta * power
        rows.append(("热阻 θJA", f"{theta} ℃/W"))
        rows.append(("功耗 P", f"{power} W"))
        rows.append(("结温 TJ", f"{_num(tj)} ℃"))
        rows.append(("判断", "✅ 可接受" if tj <= 100 else "⚠️ 超 100℃，需加散热/降低功耗"))
        rows.append(("反推允许功耗", f"若限 100℃：Pmax = {_num((100 - ta) / theta)} W"))
        rows.append(("标准", "热阻模型（θJA）"))
        return {"rows": rows, "note": "θJA 来自器件 datasheet（取决于封装、铺铜、气流）。结温超过 datasheet 上限（通常 125℃）会降寿"}

    rows.append(("提示", "需提供热阻和功耗。完整链式：「功耗 5W Rjc0.5 Rcs1 Rsa10 结温多少」；简化：「功耗 5W 热阻 30℃/W 结温多少」"))
    return {"rows": rows, "note": ""}


# ===== 通用小电路 =====

def _calc_led(q: str) -> dict:
    v = _first([r'(?:电源|供电|VCC)\s*(\d+(?:\.\d+)?)\s*V', r'(\d+(?:\.\d+)?)\s*[Vv](?![A-Za-z0-9])'], q)
    vf = _first([r'(?:压降|Vf|vf)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V', r'(\d+(?:\.\d+)?)\s*V\s*(?:压降|Vf)'], q)
    i_ma = _first([r'(\d+(?:\.\d+)?)\s*mA', r'(\d+(?:\.\d+)?)\s*(?:毫安|ma)'], q)
    n = _first([r'(\d+)\s*颗', r'(\d+)\s*个', r'(\d+)\s*只', r'(\d+)\s*盏',
                r'串联\s*(\d+)'], q) or 1
    if v is None or (vf is None and i_ma is None):
        return {"rows": [("提示", "LED 限流电阻需要：电源电压 + LED 压降（红~2V，白/蓝~3V）+ 电流。如「5V 供电 白LED 20mA 限流电阻」「12V 3颗白LED串联 30mA」")], "note": ""}

    if vf is None:
        vf = 3.0 if any(c in q for c in "白蓝翠绿") else 2.0
    if i_ma is None:
        i_ma = 20.0
    i = i_ma / 1000
    if v <= n * vf:
        return {"rows": [("提示", f"电源 {v}V 不足串联 {n} 颗 LED（{n}×Vf≈{_num(n * vf)}V），需降为单颗或提高电压")], "note": ""}
    r = (v - n * vf) / i
    rows = [
        ("计算目标", f"LED 限流电阻 R = (V - {n}×Vf) / I" if n > 1 else "LED 限流电阻 R = (V - Vf) / I"),
        ("电源电压", f"{v} V"),
        ("LED 压降", f"{vf} V × {n} 颗 = {_num(n * vf)} V"),
        ("电流", f"{i_ma:.0f} mA"),
        ("限流电阻", f"{_num(r)} Ω（取标准值 {_num(r // 10 * 10)}Ω 或 {_num(r // 100 * 100)}Ω）"),
        ("电阻功率", f"{_num(i ** 2 * r, 3)} W（建议选 2 倍余量）"),
        ("标准", "欧姆定律"),
    ]
    return {"rows": rows, "note": "Vf 参考：红 1.8~2.2V / 黄·橙 2.0~2.3V / 绿 2.2V / 蓝·白·翠绿 3.0~3.4V / UV 3.4~3.8V。严禁 LED 直接并联（Vf 差异致电流不均烧毁）；N×Vf 须严格小于 Vcc"}


def _calc_divider(q: str) -> dict:
    vin = _first([r'(?:输入|Vin|vin)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V',
                  r'(\d+(?:\.\d+)?)\s*V\s*(?:输入|分压)',
                  r'从\s*(\d+(?:\.\d+)?)\s*[Vv]'], q)
    v2 = _first([r'(?:偏置|V2|v2)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V', r'(\d+(?:\.\d+)?)\s*V\s*(?:偏置|到\s*V2)'], q)
    r1 = _first([r'[Rr]1\s*(?:取|为|接|[=:]\s*)?\s*(\d+(?:\.\d+)?)'], q)
    r2 = _first([r'[Rr]2\s*(?:取|为|接|[=:]\s*)?\s*(\d+(?:\.\d+)?)\s*(?:k?Ω)?',
                 r'(\d+(?:\.\d+)?)\s*kΩ'], q)
    vout_target = _first([r'(?:得到|输出|降到|变到|调出|需要)\s*(\d+(?:\.\d+)?)\s*[Vv]'], q)
    if vin is None or r2 is None:
        return {"rows": [("提示", "分压需要：输入电压 + R1 + R2。如「5V 分压 R1=10k R2=10k 输出多少」；反推 R1「从5V得到3.3V R2取10kΩ 求R1」")], "note": ""}
    v2 = v2 or 0.0
    # 反推 R1：已知目标输出电压（接地分压 V2=0），vout_target 需 >0 防除零
    if r1 is None and vout_target is not None and v2 == 0 and vout_target > 0:
        r1 = r2 * (vin - vout_target) / vout_target
        rows = [
            ("计算目标", "电阻分压 → 反推 R1（已知 Vout）"),
            ("输入 V1", f"{vin} V"),
            ("目标输出", f"{vout_target} V"),
            ("R2", f"{r2} kΩ"),
            ("所需 R1", f"{_num(r1, 2)} kΩ（R2×(Vin−Vout)/Vout）"),
            ("标准", "Vout=Vin×R2/(R1+R2) → R1=R2×(Vin−Vout)/Vout"),
        ]
        return {"rows": rows, "note": "用 E24/E96 就近标准值。R1+R2 并联于负载会拉低输出，阻值需远小于负载阻抗"}
    if r1 is None:
        return {"rows": [("提示", "反推 R1 需给目标输出电压（如「从5V得到3.3V R2取10kΩ 求R1」）")], "note": ""}
    vout = (vin - v2) * r2 / (r1 + r2) + v2
    i = (vin - v2) / (r1 + r2)
    rows = [
        ("计算目标", "电阻分压 VR = (V1 - V2) × R2 / (R1 + R2) + V2"),
        ("输入 V1", f"{vin} V"),
        ("偏置 V2", f"{v2} V" + ("（接地 0V）" if v2 == 0 else "")),
        ("R1 / R2", f"{r1} kΩ / {r2} kΩ"),
        ("回路电流", f"{_num(i * 1000, 2)} mA"),
        ("输出 VR", f"{_num(vout, 3)} V"),
        ("R1 / R2 功耗", f"{_num(i ** 2 * r1 * 1000, 3)} mW / {_num(i ** 2 * r2 * 1000, 3)} mW"),
        ("标准", "欧姆定律 / 叠加原理"),
    ]
    return {"rows": rows, "note": "V2=0 时退化为 VR=V1×R2/(R1+R2)。注意：R1+R2 并联于负载会拉低输出，阻值需远小于负载阻抗"}


def _calc_rc(q: str) -> dict:
    r = _first([r'(\d+(?:\.\d+)?)\s*(?:kΩ|KΩ|k欧|MΩ|M欧)', r'r\s*=\s*(\d+(?:\.\d+)?)'], q)
    c_val = _first([r'(\d+(?:\.\d+)?)\s*(?:μF|uF|µF|nF|pF)'], q)
    if r is None or c_val is None:
        return {"rows": [("提示", "RC 需要电阻和电容，如「10kΩ 电阻 1μF 电容 时间常数」")], "note": ""}
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:kΩ|KΩ|k欧|MΩ|M欧)', q, re.IGNORECASE)
    r_mult = 1e3 if m and "k" in m.group(0).lower() else 1e6
    c_m = re.search(r'(\d+(?:\.\d+)?)\s*(μF|uF|µF|nF|pF)', q, re.IGNORECASE)
    c_unit = c_m.group(2).lower()
    c_mult = {"μf": 1e-6, "uf": 1e-6, "µf": 1e-6, "nf": 1e-9, "pf": 1e-12}[c_unit]
    tau = r * r_mult * c_val * c_mult
    fc = 1 / (2 * math.pi * tau)
    rows = [
        ("计算目标", "RC 时间常数 / 截止频率 / 瞬态"),
        ("电阻", f"{r} {'kΩ' if r_mult == 1e3 else 'MΩ'}"),
        ("电容", f"{c_val} {c_unit}"),
        ("时间常数 τ", f"{_num(tau * 1e3, 2)} ms（{_num(tau * 1e6, 1)} μs）"),
        ("截止频率 f", f"{_num(fc, 2)} Hz"),
    ]
    # 瞬态：给定时刻 t 求充放电电压 V(t) = Vi + (V0 - Vi)·e^(-t/τ)
    t_m = re.search(r'(\d+(?:\.\d+)?)\s*(ms|毫秒|μs|us|s\b)', q, re.IGNORECASE)
    if t_m:
        t_num = float(t_m.group(1))
        t_s = t_num * {"ms": 1e-3, "毫秒": 1e-3, "us": 1e-6, "μs": 1e-6, "s": 1.0}[t_m.group(2).lower()]
        vi = _first([r'(?:终值|趋向|Vi)\s*(\d+(?:\.\d+)?)\s*V', r'(\d+(?:\.\d+)?)\s*V\s*(?:终值|趋向)'], q)
        v0 = _first([r'(?:初值|初始|V0)\s*(\d+(?:\.\d+)?)\s*V'], q)
        if vi is not None:
            v0 = v0 or 0.0
            vt = vi + (v0 - vi) * math.exp(-t_s / tau)
            rows.append(("瞬态", f"{_num(t_s * 1000, 2)} ms 时电压 ≈ {_num(vt, 3)} V（V0={_num(v0)}V → Vi={_num(vi)}V）"))
    rows.append(("标准", "τ = RC，f = 1/(2πRC)，V(t)=Vi+(V0−Vi)e^(−t/τ)"))
    return {"rows": rows, "note": "τ 为充放电到 63% 的时间；截止频率指增益 -3dB 处。瞬态示例：「10kΩ 1μF 初值0V 终值5V 5ms后电压多少」"}


# ===== 差分对阻抗（IPC-2141 近似）=====

def _calc_differential(q: str) -> dict:
    """差分对阻抗：给定参数求 Zdiff，或已知目标（90Ω/100Ω）反推线宽"""
    is_stripline = "带状" in q or "stripline" in q.lower()
    er = _first([r'介电常数\s*(\d+(?:\.\d+)?)', r'εr\s*=\s*(\d+(?:\.\d+)?)'], q)
    if er is None:
        er = 4.2
    oz = _extract_oz(q)
    t_mm = oz * OZ_TO_MM

    w_val, w_unit = _extract_w_label(q)
    s_val, s_unit = _extract_s(q)
    h_val, h_unit = _extract_h(q)
    b_val, b_unit = _extract_b(q)

    hb_val, hb_unit = (b_val, b_unit) if is_stripline else (h_val, h_unit)
    hb_mm = _to_mm(hb_val, hb_unit) if hb_val is not None else None
    s_mm = _to_mm(s_val, s_unit) if s_val is not None else None
    w_mm = _to_mm(w_val, w_unit) if w_val is not None else None

    target_z = _first([r'(?:目标|匹配)?\s*(\d+(?:\.\d+)?)\s*(?:Ω|欧|ohm)',
                       r'(\d+(?:\.\d+)?)\s*欧姆'], q)

    # 变宽对比："线宽从 A 改为 B，差分阻抗怎么变化"
    m_change = re.search(r'从\s*(\d+(?:\.\d+)?)\s*(mm|毫米|mil)\s*(?:改为|变成|变为|改成)\s*(\d+(?:\.\d+)?)\s*(mm|毫米|mil)', q, re.IGNORECASE)
    if m_change is not None and hb_mm is not None:
        w1_mm = _to_mm(float(m_change.group(1)), m_change.group(2))
        w2_mm = _to_mm(float(m_change.group(3)), m_change.group(4))
        s_used = s_mm if s_mm is not None else hb_mm * DEFAULT_DIFF_S_RATIO
        z1 = _diff_z(w1_mm, s_used, hb_mm, t_mm, er, is_stripline)
        z2 = _diff_z(w2_mm, s_used, hb_mm, t_mm, er, is_stripline)
        trend = "阻抗升高" if z2 > z1 else "阻抗降低"
        rows = [
            ("计算目标", f"差分阻抗随线宽变化（{'差分带状线' if is_stripline else '差分微带线'}）"),
            ("介质高度 h", f"{_num(hb_mm)} mm"),
            ("线间距 s", f"{_num(s_used)} mm"),
            ("介电常数 εr", str(er)),
            ("铜厚", f"{oz} oz"),
            (f"线宽 {_num(w1_mm * 1000, 0)}μm", f"差分阻抗 Zdiff = {_num(z1, 1)} Ω"),
            (f"线宽 {_num(w2_mm * 1000, 0)}μm", f"差分阻抗 Zdiff = {_num(z2, 1)} Ω"),
            ("变化趋势", f"{trend}（{_num((z2 - z1) / z1 * 100, 1)}%）"),
            ("标准", "Zdiff=2Z0(1−0.48e^(−0.96s/h))；线宽越窄阻抗越高"),
        ]
        return {"rows": rows, "note": "线宽减小 → 差分阻抗升高（同 s/h），反之亦然。匹配容差通常 ±10%"}
    # 反推线宽模式
    if target_z is not None:
        if hb_mm is None:
            return {"rows": [("提示", "差分反推线宽需要介质高度/两平面间距。如「100Ω 差分微带 FR4 介质 0.4mm 间距 0.3mm 要多宽」")], "note": ""}
        s_used = s_mm if s_mm is not None else hb_mm * DEFAULT_DIFF_S_RATIO
        w_solved = _solve_diff_width(target_z, hb_mm, s_used, t_mm, er, is_stripline)
        z = _diff_z(w_solved, s_used, hb_mm, t_mm, er, is_stripline)
        rows = [
            ("计算目标", f"目标差分 {target_z}Ω{'（差分带状线）' if is_stripline else '（差分微带线）'} → 求线宽"),
            ("线间距 s", f"{_num(s_used)} mm" + ("（缺省按 s/h=1.0）" if s_val is None else "")),
            ("参考间距", f"{_num(hb_mm)} mm"),
            ("介电常数 εr", str(er)),
            ("铜厚", f"{oz} oz"),
            ("所需线宽", f"{_num(w_solved * 1000)} μm（{_num(w_solved)} mm ≈ {_num(w_solved / MIL_TO_MM)} mil）"),
            ("实际差分阻抗", f"{_num(z, 1)} Ω"),
            ("标准", "IPC-2141 差分近似公式"),
        ]
        note = "常用目标：USB 90Ω、以太网 100Ω、HDMI 100Ω。近似值（±10%），关键设计用 Polar SI9000 或板厂叠层验证"
        return {"rows": rows, "note": note}

    # 正向模式
    if w_mm is not None and hb_mm is not None:
        s_used = s_mm if s_mm is not None else hb_mm * DEFAULT_DIFF_S_RATIO
        z0 = (_stripline_z(w_mm, hb_mm, t_mm, er) if is_stripline
              else _microstrip_z(w_mm, hb_mm, t_mm, er))
        zd = _diff_z(w_mm, s_used, hb_mm, t_mm, er, is_stripline)
        rows = [
            ("计算目标", f"给定差分对参数 → 差分阻抗（{'差分带状线' if is_stripline else '差分微带线'}）"),
            ("线宽", f"{_num(w_mm * 1000)} μm（{_num(w_mm / MIL_TO_MM)} mil）"),
            ("线间距 s", f"{_num(s_used)} mm"),
            ("参考间距", f"{_num(hb_mm)} mm"),
            ("介电常数 εr", str(er)),
            ("铜厚", f"{oz} oz"),
            ("单端阻抗 Z0", f"{_num(z0, 1)} Ω"),
            ("差分阻抗 Zdiff", f"{_num(zd, 1)} Ω"),
            ("标准", "IPC-2141 差分近似公式"),
        ]
        note = "Zdiff = 2×Z0×(1−0.48e^(−0.96s/h))，间距越小耦合越强、差分阻抗越低（最多降约 12%）。适用 w/h≤0.2、s/h≤3"
        return {"rows": rows, "note": note}

    return {"rows": [("提示", "差分阻抗需要线宽+介质高度+间距（或目标阻抗）。如「100Ω 差分微带 FR4 介质 0.4mm 间距 0.3mm 要多宽」")], "note": ""}


# ===== IPC-2152 精确载流 =====

def _calc_trace2152(q: str) -> dict:
    """IPC-2152 走线载流（近似拟合），与 IPC-2221 对照输出"""
    current = _extract_current(q)
    oz = _extract_oz(q)
    temp_rise = _extract_temp_rise(q)
    is_internal = _extract_layer(q) == "internal"
    width, width_unit = _extract_width(q)

    def _wid_mil(v, u):
        return v if u == "mil" else v / MIL_TO_MM

    if current is not None and width is not None:
        w_mil = _wid_mil(width, width_unit)
        max_cur = _ipc2152_max_current(w_mil, oz, temp_rise, is_internal)
        i2221 = calc_max_current(w_mil, oz, temp_rise, is_internal)
        if current <= max_cur:
            verdict = f"✅ 可以承载 {current}A（IPC-2152 最大 {_num(max_cur)}A，余量 {_num(max_cur / current)}×）"
        else:
            verdict = f"❌ 不够！{current}A 按 IPC-2152 需最小 {_num(_ipc2152_min_width(current, oz, temp_rise, is_internal))}mil"
        rows = [
            ("计算目标", "IPC-2152 校验给定线宽能否承载电流（对照 IPC-2221）"),
            ("电流 / 线宽", f"{current} A / {width} {width_unit}"),
            ("铜厚 / 温升", f"{oz} oz / {temp_rise} ℃"),
            ("所在层", _layer_note(is_internal)),
            ("IPC-2152 最大载流", f"{_num(max_cur)} A"),
            ("IPC-2221 最大载流", f"{_num(i2221)} A"),
            ("结论", verdict),
            ("标准", "IPC-2152 近似（ΔT^0.5·A^0.65）"),
        ]
        note = "IPC-2152 通常允许比 IPC-2221 高约 1.5~2 倍电流（散热模型更精确），并认为内外层散热差异小于 IPC-2221 的假设。精确值以板厂为准"
        return {"rows": rows, "note": note}

    if width is not None:
        w_mil = _wid_mil(width, width_unit)
        max_cur = _ipc2152_max_current(w_mil, oz, temp_rise, is_internal)
        i2221 = calc_max_current(w_mil, oz, temp_rise, is_internal)
        rows = [
            ("计算目标", "IPC-2152 已知线宽 → 最大载流（对照 IPC-2221）"),
            ("线宽", f"{width} {width_unit}"),
            ("铜厚 / 温升", f"{oz} oz / {temp_rise} ℃"),
            ("所在层", _layer_note(is_internal)),
            ("IPC-2152 最大载流", f"{_num(max_cur)} A"),
            ("IPC-2221 最大载流", f"{_num(i2221)} A"),
            ("安全电流", f"{_num(max_cur * 0.8)} A（80% 裕量）"),
            ("标准", "IPC-2152 近似（ΔT^0.5·A^0.65）"),
        ]
        note = "IPC-2152 允许电流通常高于 IPC-2221（同线宽可多过 1.5~2 倍）。精确设计请用板厂工具（如嘉立创 IPC-2152）验证"
        return {"rows": rows, "note": note}

    if current is not None:
        w2152 = _ipc2152_min_width(current, oz, temp_rise, is_internal)
        w2221 = calc_min_width(current, oz, temp_rise, is_internal)
        rows = [
            ("计算目标", "IPC-2152 已知电流 → 最小线宽（对照 IPC-2221）"),
            ("电流", f"{current} A"),
            ("铜厚 / 温升", f"{oz} oz / {temp_rise} ℃"),
            ("所在层", _layer_note(is_internal)),
            ("IPC-2152 最小线宽", f"{_num(w2152)} mil（≈ {_num(w2152 * MIL_TO_MM)} mm）"),
            ("IPC-2221 最小线宽", f"{_num(w2221)} mil（对照，更保守）"),
            ("推荐线宽", f"{_num(w2152 * SAFETY_MARGIN)} mil（留 20% 裕量）"),
            ("标准", "IPC-2152 近似（ΔT^0.5·A^0.65）"),
        ]
        note = "IPC-2152 结果通常比 IPC-2221 窄（同电流允许更细走线），可降低成本。精确设计请与板厂确认"
        return {"rows": rows, "note": note}

    return {"rows": [("提示", "IPC-2152 计算需要电流或线宽。如「IPC-2152 5A 1oz 外层要多宽」")], "note": ""}


# ===== 安规：电气间隙 / 爬电距离（IEC-60664-1）=====

def _calc_creepage(q: str) -> dict:
    """IEC-60664-1 电气间隙与爬电距离（精简查表 + 线性插值）"""
    v, unit = _extract_voltage(q)
    if v is None:
        return {"rows": [("提示", "安规间距需要电压。如「380V 爬电距离」「2.5kV 电气间隙 PD2 材料组II」")], "note": ""}
    pd = _extract_pd(q)
    group, from_cti = _extract_material(q)
    reinforced = "加强" in q or "reinforced" in q.lower()
    alt = _extract_altitude_m(q)

    if unit == "kv":
        v_peak_kv = v
        v_rms = v * 1000
        volt_desc = f"{v} kV"
    else:
        v_rms = v
        v_peak_kv = v_rms * 1.414 / 1000
        volt_desc = f"{v} V（峰值 ≈ {_num(v_peak_kv, 3)} kV）"

    clear, _ci = _lookup_clearance(v_peak_kv, pd)
    creep, _ri = _lookup_creepage(v_rms, pd, group)
    if reinforced:
        creep *= 2
    alt_f = _altitude_factor(alt)
    clear *= alt_f

    rows = [
        ("计算目标", "IEC-60664-1 电气间隙 / 爬电距离"),
        ("电压", volt_desc),
        ("污染等级", f"PD{pd}"),
        ("材料组", f"{group}" + ("（由 CTI 推断）" if from_cti else "")),
        ("绝缘", "加强绝缘（爬电 ×2）" if reinforced else "基本/功能绝缘"),
        ("电气间隙", f"{_num(clear, 3)} mm"
         + ("（超出表范围，取上界）" if _ci else "")
         + ("（含海拔修正）" if alt_f > 1 else "")),
        ("爬电距离", f"{_num(creep, 3)} mm"
         + ("（超出表范围，取上界）" if _ri else "")
         + ("（加强 ×2）" if reinforced else "")),
        ("最终要求", f"取较大值 {_num(max(clear, creep), 3)} mm"),
        ("标准", "IEC-60664-1（部分格为工程近似，仅供参考）"),
    ]
    note = ("电气间隙防击穿按峰值电压，爬电距离防表面漏电按工作电压，取两者较大值。"
            "FR4 环氧 CTI 约 175~300，按材料组 IIIa/IIIb 更保守；污染等级越高间距越大。"
            "海拔 >2000m 需乘修正系数。精确设计请查 IEC-60664-1 原表")
    return {"rows": rows, "note": note}


# ===== 布局综合校验（走线载流 + 安规间距 + 空间冲突判定） =====

def _extract_avail_width(query: str) -> tuple[float | None, str]:
    """提取可用宽度 / 沟道宽度，(值, "mm"|"mil")"""
    m = _RE_AVAIL.search(query)
    if not m:
        return None, "mm"
    return float(m.group(1)), ("mil" if m.group(2) == "mil" else "mm")


def _calc_layout_check(q: str) -> dict:
    """综合校验：一次输入电流+铜厚+电压+材料组+可用宽度，同时输出走线约束+安规间距+合计占用并判定冲突

    合计占用采用保守假设：走线占中线宽 + 两侧各留安规间距（w + 2×spacing）。
    冲突时按「开槽 → 高 CTI 板材 → 三防漆 → 改铜厚/改布局」优先级给设计建议。
    """
    current = _extract_current(q)
    oz = _extract_oz(q)
    temp_rise = _extract_temp_rise(q)
    is_internal = _extract_layer(q) == "internal"
    v, unit = _extract_voltage(q)
    pd = _extract_pd(q)
    group, from_cti = _extract_material(q)
    avail, avail_unit = _extract_avail_width(q)

    missing = []
    if current is None:
        missing.append("电流（如 3A）")
    if v is None:
        missing.append("电压（如 380V / 2.5kV）")
    if avail is None:
        missing.append("可用宽度（如 沟道5mm）")
    if missing:
        return {"rows": [("提示", f"综合校验需要「电流 + 铜厚 + 电压 + 材料组 + 可用宽度」。"
                                  f"缺：{'、'.join(missing)}。示例：「综合校验 3A 1oz 380V 材料组II 沟道5mm」")], "note": ""}

    # ---- 走线约束：判定用 IPC-2221（保守），附 IPC-2152 参考 ----
    w2221_mil = calc_min_width(current, oz, temp_rise, is_internal)
    w2152_mil = _ipc2152_min_width(current, oz, temp_rise, is_internal)
    w_mm = w2221_mil * MIL_TO_MM
    w2152_mm = w2152_mil * MIL_TO_MM

    # ---- 安规间距 ----
    if unit == "kv":
        v_peak_kv, v_rms = v, v * 1000
        volt_desc = f"{v} kV"
    else:
        v_rms, v_peak_kv = v, v * 1.414 / 1000
        volt_desc = f"{v} V（峰值 ≈ {_num(v_peak_kv, 3)} kV）"
    clear, _ = _lookup_clearance(v_peak_kv, pd)
    creep, _ = _lookup_creepage(v_rms, pd, group)
    spacing = max(clear, creep)

    # ---- 合计占用 + 判定 ----
    occupy = w_mm + 2 * spacing
    avail_mm = avail if avail_unit == "mm" else avail * MIL_TO_MM
    ok = occupy <= avail_mm
    margin = avail_mm - occupy

    rows = [
        ("计算目标", "高压走线布局综合校验（载流 + 安规 + 空间冲突判定）"),
        ("电流 / 铜厚 / 温升", f"{current} A / {oz} oz（约 {oz * 35:.0f} μm）/ {temp_rise} ℃"),
        ("所在层", _layer_note(is_internal)),
        ("电压 / 污染等级 / 材料组", f"{volt_desc} / PD{pd} / {group}" + ("（由 CTI 推断）" if from_cti else "")),
        ("走线约束（IPC-2221）", f"最小线宽 {_num(w2221_mil)} mil（≈ {_num(w_mm)} mm）"),
        ("走线约束（IPC-2152）", f"最小线宽 {_num(w2152_mil)} mil（≈ {_num(w2152_mm)} mm，更窄可放宽）"),
        ("安规间距", f"电气间隙 {_num(clear, 3)} mm / 爬电距离 {_num(creep, 3)} mm → 取 {_num(spacing, 3)} mm"),
        ("合计占用（线宽 + 两侧间距）", f"{_num(w_mm)} + 2×{_num(spacing, 3)} = {_num(occupy)} mm"),
        ("可用宽度", f"{avail} {avail_unit}（≈ {_num(avail_mm)} mm）"),
    ]

    if ok:
        rows.append(("结论", f"✅ 满足：占用 {_num(occupy)} mm ≤ 可用 {_num(avail_mm)} mm，余量 {_num(margin)} mm"))
        note = ("判定基于保守假设：走线占中线宽 + 两侧各留安规间距。IPC-2152 允许更窄线宽（见上），"
                "实际布板若采用 IPC-2152 线宽可进一步释放空间")
        return {"rows": rows, "note": note}

    rows.append(("结论", f"❌ 冲突：占用 {_num(occupy)} mm > 可用 {_num(avail_mm)} mm，超出 {_num(-margin)} mm"))

    # ---- 冲突 → 按优先级给设计建议（尽量定量） ----
    suggestions = []
    suggestions.append("① 开槽：高压走线与相邻导体间开槽，沿面路径 ≥ 爬电距离，等效释放间距需求")

    if group != "I":
        creep_hi, _ = _lookup_creepage(v_rms, pd, "II")
        if creep_hi < creep:
            new_occupy = w_mm + 2 * max(clear, creep_hi)
            verdict = "可满足" if new_occupy <= avail_mm else "仍冲突"
            suggestions.append(f"② 换高 CTI 板材（→材料组II，CTI≥400）：爬电 {_num(creep, 3)}→{_num(creep_hi, 3)} mm，"
                               f"占用→{_num(new_occupy)} mm（{verdict}）")

    if pd > 1:
        creep_c, _ = _lookup_creepage(v_rms, pd - 1, group)
        if creep_c < creep:
            new_occupy = w_mm + 2 * max(clear, creep_c)
            verdict = "可满足" if new_occupy <= avail_mm else "仍冲突"
            suggestions.append(f"③ 三防漆涂覆（按 PD{pd}→PD{pd - 1} 评估）：爬电 {_num(creep, 3)}→{_num(creep_c, 3)} mm，"
                               f"占用→{_num(new_occupy)} mm（{verdict}；需验证涂层质量与认证要求）")
    else:
        suggestions.append("③ 三防漆涂覆：涂层后爬电距离可进一步缩短（工程经验，需验证）")

    if oz < 2.0:
        w2oz_mm = calc_min_width(current, 2.0, temp_rise, is_internal) * MIL_TO_MM
        new_occupy = w2oz_mm + 2 * spacing
        verdict = "可满足" if new_occupy <= avail_mm else "仍冲突"
        suggestions.append(f"④ 铜厚 {oz}→2oz：线宽 {_num(w_mm)}→{_num(w2oz_mm)} mm，"
                           f"占用→{_num(new_occupy)} mm（{verdict}）")
    suggestions.append("⑤ 调整布局：增大沟道宽度，或将高压走线移至更宽区域")

    rows.append(("设计建议（按优先级）", "； ".join(suggestions)))
    note = ("建议按「开槽 → 高 CTI 板材 → 三防漆 → 改铜厚/改布局」优先级尝试。"
            "开槽 / 三防漆涉及制造工艺与安规认证，需与板厂及安规工程师确认；材料组与铜厚影响已按本表定量评估")
    return {"rows": rows, "note": note}


# ===== 铜排载流（pcb-tools 权威：牛顿散热 + ASTM B187 实测表） =====

_COPPER_AMPACITY: dict | None = None


def _copper_ampacity_table():
    """懒加载 ASTM B187 铜排载流实测表（data/pcb/tables）"""
    global _COPPER_AMPACITY
    if _COPPER_AMPACITY is None:
        try:
            from pathlib import Path
            p = Path(__file__).resolve().parents[2] / "data" / "pcb" / "tables" / "copper_busbar_ampacity.json"
            _COPPER_AMPACITY = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            _COPPER_AMPACITY = {"data": []}
    return _COPPER_AMPACITY


def _busbar_material(q: str) -> tuple[float, float, str]:
    """材料 → (电阻率, 温度系数, 名称)"""
    if "黄铜" in q or "brass" in q.lower():
        return 6.2e-8, 0.002, "黄铜"
    if "铝" in q or "alum" in q.lower():
        return 2.82e-8, 0.00429, "铝"
    return 1.72e-8, 0.00393, "紫铜"


def _busbar_lookup(t_mm: float, w_mm: float):
    """ASTM B187 表最近邻，返回三档载流 {30,50,65} + 匹配尺寸"""
    data = _copper_ampacity_table().get("data") or []
    if not data:
        return None
    best, best_d = None, float("inf")
    for row in data:
        d = abs(row[0] - t_mm) * 10 + abs(row[1] - w_mm)
        if d < best_d:
            best, best_d = row, d
    return {"30C": best[7], "50C": best[9], "65C": best[11],
            "T": best[0], "W": best[1]}


def _calc_copper_busbar(q: str) -> dict:
    """铜排载流：牛顿散热 / ASTM B187 实测表 / 电流密度反推"""
    rho, _, mat = _busbar_material(q)
    temp_rise = _extract_temp_rise(q)
    if temp_rise == DEFAULT_TEMP_RISE:
        temp_rise = 65  # 铜排惯例温升（镀锡 ≤65℃）
    orient = "侧立" if "侧立" in q else ("平放" if "平放" in q or "水平" in q else "平放")

    # 宽度 × 厚度：标签式或 W×H 式
    w = _first([r'(\d+(?:\.\d+)?)\s*mm(?:宽|宽度)', r'(?:宽度|宽)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mm'], q)
    h = _first([r'(\d+(?:\.\d+)?)\s*mm(?:厚|厚度)', r'(?:厚度|厚)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mm'], q)
    m_wh = re.search(r'(\d+(?:\.\d+)?)\s*[×x*]\s*(\d+(?:\.\d+)?)', q)
    if m_wh:
        a, b = float(m_wh.group(1)), float(m_wh.group(2))
        if w is None:
            w, h = max(a, b), min(a, b)
        elif h is None:
            h = min(a, b)

    current = _extract_current(q)
    length = _first([r'(?:长度|长)\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)'], q)
    m_lu = re.search(r'(?:长度|长)\s*(\d+(?:\.\d+)?)\s*(mm|cm|m)', q)

    if w is not None and h is not None:
        w_m, h_m = w / 1000, h / 1000
        kt = 6.9 if orient == "侧立" else 4.7
        i_newton = math.sqrt(kt * 2 * (w_m + h_m) * w_m * h_m * temp_rise / rho)
        i_exp = w * (h + 8.5)
        rows = [
            ("计算目标", f"铜排载流（{mat}，{orient}，{_num(w)}×{_num(h)} mm，温升 {temp_rise}℃）"),
            ("牛顿散热法", f"{_num(i_newton)} A（Kt={kt}）"),
            ("经验公式", f"{_num(i_exp)} A（I≈W×(H+8.5)）"),
        ]
        lookup = _busbar_lookup(h, w)
        if lookup:
            rows.append(("ASTM B187 表", f"30℃ {_num(lookup['30C'])}A / 50℃ {_num(lookup['50C'])}A / 65℃ {_num(lookup['65C'])}A"
                         f"（最接近 {_num(lookup['T'])}×{_num(lookup['W'])}）"))
        if m_lu:
            l_m = float(m_lu.group(1)) * {"mm": 0.001, "cm": 0.01, "m": 1}[m_lu.group(2)]
            r_val = rho * l_m / (w_m * h_m)
            rows.append(("电阻", f"{_num(r_val * 1000, 2)} mΩ（{m_lu.group(1)}{m_lu.group(2)} 长）"))
            if current:
                rows.append(("压降 / 损耗", f"{_num(current * r_val, 3)} V / {_num(current ** 2 * r_val, 1)} W"))
        rows.append(("标准", "牛顿散热 + ASTM B187（环境40℃ 水平放置 C11000）"))
        note = ("推荐以 ASTM B187 实测表为准，牛顿公式为估算。电流密度经验 5~8 A/mm²（严苛 4）。"
                "载流受散热条件（侧立/平放、间距）影响大，现场需实测")
        return {"rows": rows, "note": note}

    if current is not None:
        j = _first([r'(?:电流密度|J)\s*(\d+(?:\.\d+)?)'], q) or 6.0
        area_mm2 = current / j
        rows = [
            ("计算目标", f"铜排截面反推（{current}A，密度 {j} A/mm²）"),
            ("所需截面积", f"{_num(area_mm2)} mm²"),
            ("推荐截面", " / ".join(f"{_num(a)}×{_num(area_mm2 / a, 1)}mm" for a in (20, 30, 50) if a <= area_mm2) or f"{_num(area_mm2, 0)}×1mm"),
            ("经验公式参考", f"I≈W×(H+8.5) → 宽 {_num(current / 20, 1)}mm 约配 {_num(20 - 8.5)}mm 厚"),
            ("标准", "电流密度法 / 经验公式"),
        ]
        return {"rows": rows, "note": "电流密度 5~8 A/mm² 为常用范围，严苛取 4。铜排散热差异大，建议与实测对照"}
    return {"rows": [("提示", "铜排载流需要宽度+厚度（如「铜排 30mm宽 3mm厚 载流」或「30×3 铜排」），或电流反推截面（「铜排 200A 要多大的」）")], "note": ""}


# ===== 三相电功率（P=√3·UL·IL·cosφ） =====

def _calc_three_phase(q: str) -> dict:
    """三相功率：P/Q/S + 电流反推"""
    ul = _first([r'(?:线电压|UL|电压)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V', r'(\d+(?:\.\d+)?)\s*V\s*(?:三相|线电压)'], q)
    il = _first([r'(?:线电流|IL|电流)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*A', r'(\d+(?:\.\d+)?)\s*A\s*(?:线电流|负载)'], q)
    if il is None:
        il = _first([r'(\d+(?:\.\d+)?)\s*A\b'], q)
    cosf = _first([r'(?:功率因数|cos)\s*[=:]?\s*(\d+(?:\.\d+)?)', r'cosφ\s*[=:]?\s*(\d+(?:\.\d+)?)'], q)
    power_kw = _first([r'(?:功率|P)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*kW', r'(\d+(?:\.\d+)?)\s*kW\s*(?:功率|负载|电机)'], q)
    ul = ul or 380.0
    sinf = math.sqrt(max(0.0, 1 - cosf ** 2)) if cosf is not None else None

    if il is not None and cosf is not None:
        s = math.sqrt(3) * ul * il
        p, qr = s * cosf, s * sinf if sinf is not None else None
        rows = [
            ("计算目标", "三相电功率（Y/Δ 通用）"),
            ("线电压 / 线电流", f"{_num(ul)} V / {_num(il)} A"),
            ("功率因数", f"{cosf}" + (f"（sinφ={_num(sinf, 3)}）" if sinf is not None else "")),
            ("有功 P", f"{_num(p / 1000, 2)} kW"),
            ("无功 Q", f"{_num(qr / 1000, 2)} kvar" if qr is not None else "-"),
            ("视在 S", f"{_num(s / 1000, 2)} kVA"),
            ("标准", "P=√3·UL·IL·cosφ"),
        ]
        return {"rows": rows, "note": "三相平衡公式。星形 Uφ=UL/√3、Iφ=IL；三角形 Uφ=UL、Iφ=IL/√3"}
    if power_kw is not None and cosf is not None:
        il_calc = power_kw * 1000 / (math.sqrt(3) * ul * cosf)
        rows = [
            ("计算目标", "三相电流反推"),
            ("功率 / 线电压", f"{power_kw} kW / {_num(ul)} V"),
            ("功率因数", f"{cosf}"),
            ("线电流 IL", f"{_num(il_calc)} A"),
            ("标准", "IL = P/(√3·UL·cosφ)"),
        ]
        return {"rows": rows, "note": "0.4kV 系统速算：I≈1.9×P(kW)"}
    return {"rows": [("提示", "三相功率需要：线电压+线电流+功率因数（如「380V 三相 100A 功率因数0.85 多少千瓦」），或功率+电压反推电流")], "note": ""}


# ===== Snubber RC 吸收电路（双频法） =====

def _calc_snubber(q: str) -> dict:
    """双频法：Cp→Lp→R_snub→C_snub→功耗"""
    m_f1 = re.search(r'(?:原始|无|F1|f1|第一)\s*(?:振铃|频率|振荡)?\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(MHz|kHz)'
                     r'|(?:振铃|振荡|开关节点|SW)\s*(?:频率)?\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(MHz|kHz)', q, re.IGNORECASE)
    m_f2 = re.search(r'(?:加.*?后|F2|f2|第二)\s*(?:振铃|频率)?\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(MHz|kHz)'
                     r'|(?:频率|振铃)\s*(?:变|降|变为|降为)\s*(?:为|到|至)?\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(MHz|kHz)', q, re.IGNORECASE)
    m_c = re.search(r'(?:测试电容|Ctest|C_test)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(pF|nF|pf|nf)', q, re.IGNORECASE)
    if not m_c:
        m_c = re.search(r'(\d+(?:\.\d+)?)\s*(pF|nF|pf|nf)', q, re.IGNORECASE)
    if not (m_f1 and m_f2 and m_c):
        return {"rows": [("提示", "Snubber 双频法需要：原始振铃频率 + 加测试电容后频率 + 测试电容值。如「原始振铃100MHz 加220pF后60MHz 算吸收电路」")], "note": ""}
    g1, g2 = m_f1.group(1) or m_f1.group(3), m_f1.group(2) or m_f1.group(4)
    g3, g4 = m_f2.group(1) or m_f2.group(3), m_f2.group(2) or m_f2.group(4)
    f1 = float(g1) * (1e6 if g2[0].lower() == "m" else 1e3)
    f2 = float(g3) * (1e6 if g4[0].lower() == "m" else 1e3)
    ctest = float(m_c.group(1)) * (1e-12 if m_c.group(2)[0].lower() == "p" else 1e-9)
    x = (f1 / f2) ** 2 - 1
    cp = ctest / x
    lp = 1 / ((2 * math.pi * f1) ** 2 * cp)
    r_snub = math.sqrt(lp / cp)
    c_snub = cp * (3 if ("3倍" in q or "三倍" in q or "3×" in q) else 2)   # Snubber 电容可取 2~3×Cp
    rows = [
        ("计算目标", "Snubber RC 吸收电路（双频法）"),
        ("振铃频率", f"{_num(f1 / 1e6)} MHz → 加 {_num(ctest * 1e12)}pF 后 {_num(f2 / 1e6)} MHz"),
        ("开关节点寄生电容 Cp", f"{_num(cp * 1e12, 1)} pF"),
        ("开关回路寄生电感 Lp", f"{_num(lp * 1e9, 1)} nH"),
        ("推荐吸收电阻 R_snub", f"{_num(r_snub, 1)} Ω（=√(Lp/Cp)）"),
        ("推荐吸收电容 C_snub", f"{_num(c_snub * 1e12, 0)} pF（2×Cp）"),
    ]
    vin = _first([r'(?:输入|Vin)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
    fsw = _first([r'(?:开关频率|fsw)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*kHz'], q)
    if vin is not None and fsw is not None:
        pr = c_snub * vin ** 2 * fsw * 1000
        rows.append(("吸收电阻功耗", f"{_num(pr, 3)} W（C_snub×Vin²×fsw）"))
    rows.append(("标准", "双频法 + 临界阻尼 R=√(Lp/Cp)"))
    return {"rows": rows, "note": "测试电容建议使振铃降至原来 1/2~2/3。C_snub 取 (1~3)×Cp，常用 2×Cp。电阻选功率 ≥2×PR 余量"}


# ===== PDN 电源完整性（目标阻抗与去耦） =====

def _calc_pdn(q: str) -> dict:
    """PDN：ΔVmax → Z_target → C_total"""
    vdd = _first([r'(?:Vdd|VDD|电压|电源轨)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
    ripple_pct = _first([r'(?:纹波|ripple)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*%'], q)
    if vdd is None or ripple_pct is None:
        return {"rows": [("提示", "PDN 目标阻抗需要：电源电压 + 允许纹波% + 阶跃电流。如「Vdd1.2V 纹波3% 阶跃电流5A 目标阻抗多少」")], "note": ""}
    dv = vdd * ripple_pct / 100
    rows = [("计算目标", "PDN 目标阻抗 / 去耦"),
            ("电源 Vdd", f"{vdd} V"),
            ("允许纹波", f"{ripple_pct}%（ΔVmax={_num(dv * 1000, 1)} mV）")]
    di = _first([r'(?:阶跃电流|动态电流|ΔI)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*A'], q)
    ft = _first([r'(?:转折频率|f_transit|ft)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:MHz|kHz)'], q)
    if di is not None:
        zt = dv / di
        rows.append(("目标阻抗 Z_target", f"{_num(zt * 1000, 2)} mΩ"))
        if ft is not None:
            m_ft = re.search(r'(?:转折频率|f_transit|ft)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(MHz|kHz)', q, re.IGNORECASE)
            ft_hz = ft * (1e6 if (m_ft and m_ft.group(2)[0].lower() == "m") else 1e3)
            c = di / (dv * 2 * math.pi * ft_hz)
            rows.append(("最小去耦总电容", f"{_num(c * 1e6, 2)} μF（转折 {_num(ft_hz / 1e6)} MHz）"))
    else:
        rows.append(("提示", "还需阶跃电流 ΔI（A）才能算目标阻抗"))
    rows.append(("标准", "Z_target = ΔVmax / ΔImax"))
    return {"rows": rows, "note": "去耦电容覆盖 DC 到转折频率，更高频由电源/地平面间分布电容负责。实际需考虑 ESR/ESL"}


# ===== 开关电源效率与损耗分解（Buck/Boost/Buck-Boost） =====

def _calc_smps(q: str) -> dict:
    """效率 η = Pout / (Pout + P_loss)，损耗分解"""
    m_io = re.search(r'(\d+(?:\.\d+)?)\s*V\s*(?:转|到|→|->)\s*(\d+(?:\.\d+)?)\s*V', q)
    if m_io:
        vin, vout = float(m_io.group(1)), float(m_io.group(2))
    else:
        vin = _first([r'(?:输入|Vin)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
        vout = _first([r'(?:输出|Vout)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
    iout = _first([r'(?:输出电流|Iout)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*A', r'(\d+(?:\.\d+)?)\s*A\s*(?:输出|负载)'], q)
    if iout is None:
        iout = _first([r'(\d+(?:\.\d+)?)\s*A\b'], q)
    if vin is None or vout is None or iout is None:
        return {"rows": [("提示", "开关电源损耗需要：Vin + Vout + Iout（可加 Rds/Qg/fsw/VD/DCR）。如「Buck 24V转12V 5A Rds10mΩ 效率多少」")], "note": ""}
    topo = "Buck" if ("buck" in q.lower() or "降压" in q) else ("Boost" if ("boost" in q.lower() or "升压" in q) else "Buck-Boost")
    if topo == "Buck":
        d = vout / vin
    elif topo == "Boost":
        d = 1 - vin / vout
    else:
        d = vout / (vout + vin)
    pout = vout * iout
    rows = [("计算目标", f"开关电源效率与损耗（{topo}）"),
            ("Vin / Vout / Iout", f"{vin} V / {vout} V / {iout} A"),
            ("占空比 D", f"{_num(d, 3)}"),
            ("输出功率 Pout", f"{_num(pout)} W")]
    p_loss = 0.0
    m_rdson = re.search(r'(?:Rds|rdson|导通电阻)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(mΩ|Ω)', q, re.IGNORECASE)
    if m_rdson:
        r = float(m_rdson.group(1)) * (1e-3 if m_rdson.group(2)[0].lower() == "m" else 1)
        pc = iout ** 2 * r * d
        p_loss += pc
        rows.append(("MOSFET 导通损耗", f"{_num(pc)} W（Iout²×Rds×D={_num(r * 1000, 1)}mΩ）"))
    m_qg = re.search(r'(?:Qg|栅极电荷)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*nC', q, re.IGNORECASE)
    m_fsw = re.search(r'(?:开关频率|fsw)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*kHz', q, re.IGNORECASE)
    m_vg = re.search(r'(?:栅极驱动|Vg)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V', q, re.IGNORECASE)
    if m_qg and m_fsw and m_vg:
        ps = float(m_qg.group(1)) * 1e-9 * float(m_vg.group(1)) * float(m_fsw.group(1)) * 1e3
        p_loss += ps
        rows.append(("MOSFET 开关损耗", f"{_num(ps)} W（Qg×Vg×fsw）"))
    m_dcr = re.search(r'(?:DCR|电感电阻)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(mΩ|Ω)', q, re.IGNORECASE)
    if m_dcr:
        rdcr = float(m_dcr.group(1)) * (1e-3 if m_dcr.group(2)[0].lower() == "m" else 1)
        pdcr = iout ** 2 * rdcr
        p_loss += pdcr
        rows.append(("电感铜损", f"{_num(pdcr)} W（Iout²×DCR）"))
    eff = pout / (pout + p_loss) * 100 if p_loss else None
    if eff is not None:
        rows.append(("总损耗 / 效率", f"{_num(p_loss)} W / {_num(eff, 1)}%"))
    rows.append(("标准", "占空比 + MOSFET/电感损耗模型"))
    return {"rows": rows, "note": "同步整流 VD≈0，续流二极管损耗 P=Iout×VD×(1-D)。P_sw≈Qg×Vg×fsw。损耗未含铜线/磁芯损耗"}


# ===== Buck 降压转换器元件选型 =====

def _calc_buck(q: str) -> dict:
    """D / Lmin / Co / Ipeak"""
    m_io = re.search(r'(\d+(?:\.\d+)?)\s*V\s*(?:转|到|→|->)\s*(\d+(?:\.\d+)?)\s*V', q)
    if m_io:
        vin, vout = float(m_io.group(1)), float(m_io.group(2))
    else:
        vin = _first([r'(?:输入|Vin)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
        vout = _first([r'(?:输出|Vout)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
    iout = _first([r'(?:输出电流|Iout)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*A', r'(\d+(?:\.\d+)?)\s*A\s*(?:输出|负载)'], q)
    if iout is None:
        iout = _first([r'(\d+(?:\.\d+)?)\s*A\b'], q)
    fsw = _first([r'(?:开关频率|fsw)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*kHz'], q)
    if vin is None or vout is None or iout is None:
        return {"rows": [("提示", "Buck 选型需要：Vin + Vout + Iout + 开关频率。如「Buck 24V转5V 3A 500kHz 电感多大」")], "note": ""}
    vf = _first([r'(?:续流压降|Vf)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q) or 0.5
    d = (vout + vf) / (vin + vf)
    fsw_hz = (fsw or 500) * 1e3
    d_il = 0.3 * (iout or 1)  # 纹波电流取 30% Iout
    lmin = (vin - vout) * d / (fsw_hz * d_il)
    d_vout = 0.01 * vout
    co = d_il / (8 * fsw_hz * d_vout)
    ipeak = (iout or 1) + d_il / 2
    rows = [
        ("计算目标", "Buck 降压转换器元件选型"),
        ("Vin / Vout / Iout", f"{vin} V / {vout} V / {iout} A"),
        ("占空比 D", f"{_num(d, 3)}（(Vout+Vf)/(Vin+Vf)）"),
        ("最小电感 Lmin", f"{_num(lmin * 1e6, 1)} μH（ΔIL=30%×Iout={_num(d_il, 2)}A）"),
        ("输出电容 Co", f"{_num(co * 1e6, 1)} μF（1% 纹波）"),
        ("峰值电流 Ipeak", f"{_num(ipeak, 2)} A"),
        ("标准", "Buck 连续导通模式（CCM）"),
    ]
    return {"rows": rows, "note": "电感额定电流 ≥ Ipeak×1.2 余量。高频→电感电容更小但开关损耗增加，同步整流可提效至 95%+"}


# ===== 额定电流速算（变压器 / 电机） =====

def _calc_rated_current(q: str) -> dict:
    """变压器 I=S/(√3U)；电机 I≈1.9×P(kW)（0.4kV）/ 5.7×P（220V）"""
    s_kva = _first([r'(?:容量|S)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*kVA'], q)
    if s_kva is None:
        s_kva = _first([r'(\d+(?:\.\d+)?)\s*kVA'], q)
    p_kw = _first([r'(?:功率|P)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*kW', r'(\d+(?:\.\d+)?)\s*kW\s*(?:电机|负载|功率)'], q)
    is_transformer = ("变压器" in q or "配变" in q)
    if is_transformer:
        if s_kva is None:
            return {"rows": [("提示", "变压器额定电流需要容量（kVA）+ 电压（kV）。如「1000kVA 变压器 10kV/0.4kV 额定电流」")], "note": ""}
        m_uv = re.findall(r'(\d+(?:\.\d+)?)\s*kV', q, re.IGNORECASE)
        if not m_uv:
            return {"rows": [("提示", "变压器额定电流需要电压（kV），如「1000kVA 变压器 10kV/0.4kV 额定电流」")], "note": ""}
        rows = [("计算目标", "变压器额定电流速算"), ("容量 S", f"{s_kva} kVA")]
        for u in m_uv:
            uf = float(u)
            rows.append((f"{_num(uf)} kV 侧额定电流", f"{_num(s_kva / (math.sqrt(3) * uf))} A"))
        rows.append(("速算口诀", "0.4kV 侧 I≈1.443×S；12kV 一次侧 I≈0.06×S；40.5kV I≈0.017×S"))
        rows.append(("标准", "I = S/(√3·U)"))
        return {"rows": rows, "note": "口诀：0.4kV 二次侧 I≈1.443×S；40.5kV 一次侧 I≈0.017×S；12kV 一次侧 I≈0.06×S"}
    if p_kw is not None:
        if "单相" in q or "220" in q:
            i = 5.7 * p_kw
            rows = [("计算目标", "单相 220V 电机额定电流速算"), ("功率", f"{p_kw} kW"), ("额定电流", f"{_num(i)} A"), ("标准", "I≈5.7×P(kW)")]
            return {"rows": rows, "note": "经验估算，实际以产品样本为准"}
        i = 1.9 * p_kw
        rows = [("计算目标", "三相 0.4kV 电机额定电流速算"), ("功率", f"{p_kw} kW"), ("额定电流", f"{_num(i)} A"), ("标准", "I≈1.9×P(kW)")]
        return {"rows": rows, "note": "含功率因数与效率综合因子，0.4kV 系统常用口诀 I≈1.9×P(kW)"}
    return {"rows": [("提示", "额定电流速算：变压器「1000kVA 10kV 额定电流」；电机「30kW 电机电流多少」")], "note": ""}


# ===== 过孔寄生参数（Cvia / Lvia / 截止频率） =====

def _calc_via_parasitic(q: str) -> dict:
    """Cvia=1.41·εr·T·D1/(D2−D1)；Lvia=5.08·h·(ln(4h/d)+1)"""
    er = _first([r'介电常数\s*(\d+(?:\.\d+)?)', r'εr\s*[=:]?\s*(\d+(?:\.\d+)?)'], q) or 4.2
    t = _first([r'(?:板厚|T)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mm',
                r'(\d+(?:\.\d+)?)\s*mm\s*(?:板厚|厚度|厚)'], q)
    d1 = _first([r'(?:焊盘直径|D1|(?<!反)焊盘|pad)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:mm)?'], q)
    d2 = _first([r'(?:反焊盘|D2|antipad|anti[\s-]?pad)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:mm)?'], q)
    h = _first([r'(?:过孔长度|h)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mm',
                r'(\d+(?:\.\d+)?)\s*mm\s*(?:过孔长度|板厚|厚度|长)'], q)
    d = _first([r'(?:过孔直径|孔径|d)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mm',
                r'(\d+(?:\.\d+)?)\s*mm\s*(?:孔径|钻孔|通孔)'], q)
    if (t is not None and d1 is not None and d2 is not None) or (h is not None and d is not None):
        rows = [("计算目标", "过孔寄生电容 / 电感 / 截止频率"), ("介电常数 εr", f"{er}")]
        if t is not None and d1 is not None and d2 is not None:
            cvia = 1.41 * er * t * d1 / (d2 - d1)
            rows.append(("寄生电容 Cvia", f"{_num(cvia, 3)} pF"))
            if h is not None:
                rows.append(("-3dB 截止频率", f"{_num(1 / (2 * math.pi * 50 * cvia * 1e-12) / 1e9, 2)} GHz（Z0=50Ω）"))
        if h is not None and d is not None:
            lvia = 5.08 * h * (math.log(4 * h / d) + 1)
            rows.append(("寄生电感 Lvia", f"{_num(lvia, 3)} nH"))
            if "感抗" in q or "阻抗" in q or "电抗" in q:
                m_fr = re.search(r'(\d+(?:\.\d+)?)\s*(GHz|MHz|kHz)', q, re.IGNORECASE)
                if m_fr is not None:
                    fv = float(m_fr.group(1))
                    fu_raw = m_fr.group(2)
                    fhz = fv * {"ghz": 1e9, "mhz": 1e6, "khz": 1e3}[fu_raw.lower()]
                    xl = 2 * math.pi * fhz * lvia * 1e-9
                    # :g 显示频率，避免 _num 的 rstrip("0") 把 10 削成 1；单位跟随输入（GHz/MHz/kHz）
                    rows.append(("感抗 XL", f"{_num(xl, 1)} Ω（2πfL @ {fv:g}{fu_raw}）"))
        rows.append(("标准", "Cvia=1.41εr·T·D1/(D2−D1)；Lvia=5.08h(ln(4h/d)+1)"))
        return {"rows": rows, "note": "高速信号注意过孔残桩（Stub），可用背钻/盲埋孔优化。示例：「板厚1.6mm 焊盘0.6 反焊盘1.0 过孔寄生」"}
    return {"rows": [("提示", "过孔寄生需要：板厚+焊盘+反焊盘（算 Cvia），或长度+孔径（算 Lvia）。如「板厚1.6mm 焊盘0.6 反焊盘1.0 寄生电容」")], "note": ""}


# ===== 热过孔热阻（阵列导热） =====

def _calc_via_thermal(q: str) -> dict:
    """Rsingle=H/(kcu·Acu+kfill·Afill)；Rtotal=Rsingle/N"""
    d = _first([r'(?:孔径|直径|D)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mm', r'(\d+(?:\.\d+)?)\s*mm\s*(?:孔径|孔|过孔|via)', r'(\d+(?:\.\d+)?)\s*mm\s*(?:热过孔)?'], q)
    if d is None:
        d = _first([r'(\d+(?:\.\d+)?)\s*mm'], q)
    cu_um = _first([r'(?:孔壁铜|铜厚)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*μm', r'(\d+(?:\.\d+)?)\s*oz'], q)
    h = _first([r'(?:板厚|过孔长度|H)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mm'], q)
    n = _first([r'(\d+)\s*个', r'(\d+)\s*颗', r'(\d+)\s*孔'], q) or 1
    if d is None or h is None:
        return {"rows": [("提示", "热过孔热阻需要：孔径 + 板厚 + 孔壁铜厚 + 数量。如「0.3mm 热过孔 板厚1.6mm 孔壁20μm 20个 热阻多少」")], "note": ""}
    r_inner = d / 2
    r_outer = r_inner + (cu_um if cu_um is not None and cu_um > 1 else 20) / 1000
    acu = math.pi * (r_outer ** 2 - r_inner ** 2)
    afill = math.pi * r_inner ** 2
    kfill = {"空气": 0.026, "焊锡": 50, "树脂": 1.5, "纯铜": 390}
    fill = "空气"
    for k in kfill:
        if k in q:
            fill = k
    kcu = 390
    r_single = (h / 1000) / (kcu * acu * 1e-6 + kfill[fill] * afill * 1e-6)
    r_total = r_single / n
    rows = [
        ("计算目标", "热过孔热阻（单孔/阵列）"),
        ("孔径 / 板厚", f"{_num(d)} mm / {_num(h)} mm"),
        ("孔壁铜厚", f"{_num(cu_um if cu_um is not None else 20)} μm" if cu_um is None or cu_um > 1 else f"{_num(cu_um)} μm"),
        ("填充介质", f"{fill}（k={kfill[fill]} W/m·K）"),
        ("单孔热阻", f"{_num(r_single, 2)} ℃/W"),
        ("阵列总热阻", f"{_num(r_total, 3)} ℃/W（{n} 孔并联）"),
        ("标准", "R = H/(kcu·Acu + kfill·Afill)，并联 R/N"),
    ]
    return {"rows": rows, "note": "热过孔应直接置于散热焊盘下方；孔径建议 ≤0.3mm 防焊锡流失；大孔径需塞孔。铜 k=390，空气 0.026，焊锡 50，树脂 1.5"}


# ===== 计算类型路由 =====

# ===== LC 谐振频率（含反向求解） =====

def _val_unit(q, pat):
    """提取 (值, 单位字符串) 或 (None, None)。单位须为捕获组（可用 ? 可选）"""
    m = re.search(pat, q, re.IGNORECASE)
    if not m:
        return None, None
    g = m.groups()
    return float(g[0]), g[1] if len(g) >= 2 and g[1] else None


def _u(v, unit, table):
    """值 × 单位换算系数"""
    return v * table.get(unit.lower(), 1.0)


def _calc_lc_resonance(q: str) -> dict:
    """LC 谐振：f=1/(2π√LC)；反向求 L 或 C"""
    l_val, lu = _val_unit(q, r'(?:电感|L)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(μH|uH|mH|H)')
    if l_val is None:
        l_val, lu = _val_unit(q, r'(\d+(?:\.\d+)?)\s*(μH|uH|mH)')
    c_val, cu = _val_unit(q, r'(?:电容|C)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(μF|uF|nF|pF)')
    if c_val is None:
        c_val, cu = _val_unit(q, r'(\d+(?:\.\d+)?)\s*(μF|uF|nF|pF)')
    f_val, fu = _val_unit(q, r'(?:频率|f|谐振)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(GHz|MHz|kHz|Hz)')
    l_uh = _u(l_val, lu, {"uh": 1, "μh": 1, "mh": 1e3, "h": 1e6}) if l_val is not None else None
    c_uf = _u(c_val, cu, {"uf": 1, "μf": 1, "nf": 1e-3, "pf": 1e-6}) if c_val is not None else None
    f_mhz = _u(f_val, fu, {"ghz": 1e3, "mhz": 1, "khz": 1e-3, "hz": 1e-6}) if f_val is not None else None

    if l_uh is not None and c_uf is not None:
        f = 1 / (2 * math.pi * math.sqrt(l_uh * 1e-6 * c_uf * 1e-6))
        rows = [("计算目标", "LC 谐振频率"),
                ("电感 / 电容", f"{_num(l_uh)} μH / {_num(c_uf)} μF"),
                ("谐振频率 f0", f"{_num(f / 1e6, 2)} MHz（{_num(f / 1e3, 0)} kHz）"),
                ("标准", "f = 1/(2π√(LC))")]
        return {"rows": rows, "note": "高 Q 电路对元件精度要求高；实际元件含 ESR/DCR 会略偏"}
    if f_mhz is not None and c_uf is not None and l_val is None:
        l = 1 / ((2 * math.pi * f_mhz * 1e6) ** 2 * c_uf * 1e-6) * 1e6
        rows = [("计算目标", "由 f、C 反推电感"), ("频率 / 电容", f"{_num(f_mhz)} MHz / {_num(c_uf)} μF"), ("所需电感", f"{_num(l, 2)} μH"), ("标准", "L = 1/((2πf)²C)")]
        return {"rows": rows, "note": ""}
    if f_mhz is not None and l_uh is not None:
        c = 1 / ((2 * math.pi * f_mhz * 1e6) ** 2 * l_uh * 1e-6) * 1e6
        rows = [("计算目标", "由 f、L 反推电容"), ("频率 / 电感", f"{_num(f_mhz)} MHz / {_num(l_uh)} μH"), ("所需电容", f"{_num(c, 2)} μF"), ("标准", "C = 1/((2πf)²L)")]
        return {"rows": rows, "note": ""}
    return {"rows": [("提示", "LC 谐振需要 L+C（求 f），或 f+C（求 L）、f+L（求 C）。如「100μH 10nF 谐振频率」")], "note": ""}


# ===== 感抗 / 容抗 =====

def _calc_reactance(q: str) -> dict:
    """XL=2πfL；XC=1/(2πfC)；谐振条件"""
    f_val, fu = _val_unit(q, r'(\d+(?:\.\d+)?)\s*(GHz|MHz|kHz|Hz)')
    l_val, lu = _val_unit(q, r'(?:电感|L)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(μH|uH|mH|H)')
    c_val, cu = _val_unit(q, r'(?:电容|C)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(μF|uF|nF|pF)')
    if f_val is None:
        return {"rows": [("提示", "感抗/容抗需要频率 + 电感或电容。如「1MHz 100μH 感抗」「100kHz 10nF 容抗」")], "note": ""}
    f = _u(f_val, fu, {"ghz": 1e9, "mhz": 1e6, "khz": 1e3, "hz": 1})
    rows = [("计算目标", "感抗 / 容抗"), ("频率", f"{f_val} {fu}")]
    if l_val is not None:
        l = _u(l_val, lu, {"uh": 1e-6, "μh": 1e-6, "mh": 1e-3, "h": 1})
        rows.append(("感抗 XL", f"{_num(2 * math.pi * f * l)} Ω（2πfL={_num(l_val)} {lu}）"))
    if c_val is not None:
        c = _u(c_val, cu, {"uf": 1e-6, "μf": 1e-6, "nf": 1e-9, "pf": 1e-12})
        rows.append(("容抗 XC", f"{_num(1 / (2 * math.pi * f * c), 1)} Ω（1/(2πfC)={_num(c_val)} {cu}）"))
    rows.append(("标准", "XL=2πfL；XC=1/(2πfC)"))
    return {"rows": rows, "note": "直流时 XL=0（短路）、XC=∞（开路）。XL=XC 时谐振 f0=1/(2π√LC)"}


# ===== 超级电容工作时间 =====

def _calc_supercapacitor(q: str) -> dict:
    """E=0.5C(V0²−Vcut²)；恒流 t=C(V0−Vcut)/I；恒功率 t=E/P"""
    c_f = _first([r'(?:容量|C)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:F|f|mF)', r'(\d+(?:\.\d+)?)\s*F\b'], q)
    v0 = _first([r'(?:充满|初始|V0)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
    vcut = _first([r'(?:截止|Vcut)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
    m_vv = re.search(r'(\d+(?:\.\d+)?)\s*V\s*(?:放到|降至|降到|到)\s*(\d+(?:\.\d+)?)\s*V', q)
    if m_vv:
        v0, vcut = float(m_vv.group(1)), float(m_vv.group(2))
    i_a = _first([r'(?:负载|负载电流|I)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:mA|A)'], q)
    p_w = _first([r'(?:负载功率|P)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*W'], q)
    if c_f is None or v0 is None or vcut is None:
        return {"rows": [("提示", "超级电容需要容量 + 初始电压 + 截止电压。如「100F 超级电容 2.7V放到1V 负载100mA 能撑多久」")], "note": ""}
    e = 0.5 * c_f * (v0 ** 2 - vcut ** 2)
    rows = [("计算目标", "超级电容工作时间"),
            ("容量 / 电压窗口", f"{c_f} F / {v0}V→{vcut}V"),
            ("可用能量", f"{_num(e)} J")]
    if i_a is not None:
        i = i_a / 1000 if "m" in str(i_a).lower() or "ma" in q.lower() else i_a
        t = c_f * (v0 - vcut) / i
        rows.append(("恒流放电", f"{_num(t)} s（{_num(t / 60, 1)} min）"))
    elif p_w is not None:
        rows.append(("恒功率放电", f"{_num(e / p_w)} s（{_num(e / p_w / 60, 1)} min）"))
    rows.append(("标准", "E=0.5C(V0²−Vcut²)"))
    return {"rows": rows, "note": "RTC 保电 0.1~1F/5.5V；IoT 峰值缓冲 1~100F/2.7V；工业 UPS 100~3000F/2.7V"}


# ===== 电池续航 / 充电 =====

def _calc_battery_life(q: str) -> dict:
    """T = 容量 / 平均电流"""
    cap = _first([r'(?:容量|电池)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mAh', r'(\d+(?:\.\d+)?)\s*mAh'], q)
    i = _first([r'(?:平均电流|电流|负载)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:mA|A)'], q)
    if cap is None or i is None:
        return {"rows": [("提示", "电池续航需要容量（mAh）+ 平均电流（mA）。如「2000mAh 电池 平均电流100mA 续航多少」")], "note": ""}
    i_ma = i if "m" in str(i).lower() or "ma" in q.lower() else i * 1000
    h = cap / i_ma
    rows = [("计算目标", "电池续航（恒流）"),
            ("容量 / 平均电流", f"{cap} mAh / {_num(i_ma)} mA"),
            ("续航时间", f"{_num(h, 1)} 小时（{_num(h / 24, 1)} 天）"),
            ("标准", "T = 容量 / 电流")]
    return {"rows": rows, "note": "理论值，实际受温度/老化/放电速率影响。IoT 可用平均电流法更精确（见 iot 类）"}


def _calc_battery_charging(q: str) -> dict:
    """T = (目标%−当前%)×容量 / (充电电流×(1−损耗%))"""
    cap = _first([r'(?:容量|电池)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mAh', r'(\d+(?:\.\d+)?)\s*mAh'], q)
    icharge = _first([r'(?:充电电流|充电器|充电)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:mA|A)', r'(\d+(?:\.\d+)?)\s*A\b'], q)
    if cap is None or icharge is None:
        return {"rows": [("提示", "充电时间需要容量 + 充电电流。如「3000mAh 电池 1A 充电器 从20%充到80%多久」")], "note": ""}
    tgt = _first([r'到\s*(\d+)\s*%', r'(\d+)\s*%\s*(?:为止|目标)'], q)
    cur_pct = _first([r'从\s*(\d+)\s*%', r'(\d+)\s*%\s*(?:开始|起)'], q)
    loss = _first([r'(?:损耗|效率损失)\s*(\d+)\s*%'], q) or 15
    i = icharge if "m" in str(icharge).lower() or "ma" in q.lower() else icharge * 1000
    tgt, cur_pct = tgt or 100, cur_pct or 0
    h = (tgt - cur_pct) / 100 * cap / (i * (1 - loss / 100))
    rows = [("计算目标", "电池充电时间"),
            ("容量 / 充电电流", f"{cap} mAh / {_num(i)} mA"),
            ("电量范围", f"{cur_pct}% → {tgt}%"),
            ("充电损耗假设", f"{loss}%（线性/开关/无线 10~30%）"),
            ("充电时间", f"{_num(h, 2)} 小时"),
            ("标准", "T=(目标%−当前%)×容量/(I×(1−损耗%))")]
    return {"rows": rows, "note": "充电损耗：线性 10~20%，开关 5~15%，无线 15~30%。末段恒压充电会明显慢于恒流段"}


# ===== IoT 设备平均电流与电池寿命 =====

def _calc_iot_battery(q: str) -> dict:
    """Iavg = (Σ Ii·ti + Isleep·tsleep) / Tcycle；Life = Cbat / Iavg"""
    cap = _first([r'(?:容量|电池)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mAh', r'(\d+(?:\.\d+)?)\s*mAh'], q)
    itx = _first([r'(?:发射|TX)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mA',
                  r'(?:采样|工作|传感)\s*(?:一次|时)?\s*(?:电流|耗电)?\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mA',
                  r'电流\s*(\d+(?:\.\d+)?)\s*mA'], q)
    m_cy = re.search(r'(?:周期|cycle)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(s|秒|min|分钟)', q, re.IGNORECASE)
    if m_cy is None:
        m_cy = re.search(r'每\s*(\d+(?:\.\d+)?)\s*(s|秒|min|分钟)', q, re.IGNORECASE)
    cycle = float(m_cy.group(1)) if m_cy else None
    m_sleep = re.search(r'(?:睡眠|sleep|休眠)\s*(?:状态)?\s*[（(]?\s*[^）)]*?(?:电流|功耗)?\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(uA|μA|mA)', q, re.IGNORECASE)
    if itx is None or cycle is None or cap is None:
        return {"rows": [("提示", "IoT 电池寿命需要：发射电流 + 周期 + 容量（+ 睡眠电流）。如「LoRa 发射100mA 周期15分钟 睡眠5uA 2000mAh 寿命多久」；周期工作「每10秒采样50ms 电流30mA 休眠5uA」")], "note": ""}
    m_ttx = re.search(r'(?:发射时长|发包时长|耗时|持续)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(ms|s|毫秒)', q, re.IGNORECASE)
    ttx = 0.5
    if m_ttx:
        ttx = float(m_ttx.group(1)) / 1000 if m_ttx.group(2) in ("ms", "毫秒") else float(m_ttx.group(1))
    isleep_ua = 0.0
    if m_sleep:
        isleep_ua = float(m_sleep.group(1)) * (1000 if m_sleep.group(2)[0].lower() == "m" else 1)
    cycle_s = cycle * 60 if (m_cy and (m_cy.group(2) in ("min", "分钟"))) else cycle
    iavg_ua = (itx * ttx * 1000 + (cycle_s - ttx) * isleep_ua) / cycle_s
    life_days = cap / (iavg_ua / 1000) / 24
    rows = [("计算目标", "IoT 设备电池寿命"),
            ("发射 / 睡眠", f"{_num(itx)} mA × {_num(ttx * 1000, 0)} ms / {_num(isleep_ua)} μA"),
            ("工作周期", f"{_num(cycle_s)} s"),
            ("平均电流 Iavg", f"{_num(iavg_ua, 1)} μA"),
            ("电池寿命", f"{_num(life_days, 0)} 天（{_num(life_days / 365, 1)} 年）"),
            ("标准", "Iavg=(ΣIi·ti+Isleep·tsleep)/Tcycle")]
    return {"rows": rows, "note": "LoRa SF12 约每15min一包；NB-IoT 约每1h一包（TX 200mA）；BLE 广播 12mA/1s。实际需计入电池自放电"}


# ===== 导线压降 =====

def _calc_wire_drop(q: str) -> dict:
    """R=ρL/A；ΔV=IR；Ploss=I²R"""
    area = _first([r'(?:截面|面积|A)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mm[²2]', r'(\d+(?:\.\d+)?)\s*mm[²2]'], q)
    length = _first([r'(?:长度|长)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*m', r'(\d+(?:\.\d+)?)\s*m\b'], q)
    current = _extract_current(q)
    if area is None or length is None or current is None:
        return {"rows": [("提示", "导线压降需要：截面积 + 长度 + 电流（+ 材料）。如「2.5mm² 铜线 50m 10A 压降多少」")], "note": ""}
    rho = 1.724e-8 if "铝" not in q else 2.82e-8
    r = rho * length / (area * 1e-6)
    dv = current * r
    rows = [("计算目标", "电线电缆压降"),
            ("线缆", f"{area} mm² {('铝' if '铝' in q else '铜')}线 {length} m"),
            ("电阻", f"{_num(r, 3)} Ω"),
            ("压降 ΔV", f"{_num(dv, 3)} V"),
            ("压降占比", f"{_num(dv / 24 * 100, 1)}%（24V 参考 ≤3%）" if '24' in q else f"{_num(dv / 220 * 100, 1)}%（220V 参考 ≤5%）"),
            ("功率损耗", f"{_num(current ** 2 * r, 2)} W"),
            ("标准", "R=ρL/A；ΔV=IR")]
    return {"rows": rows, "note": "铜 ρ=1.724e-8，铝 ρ=2.82e-8。往返接线长度要 ×2。压降建议：220V≤5%，24V≤3%，12V 关键负载≤3%"}


# ===== 感应加热功率 =====

def _calc_induction_heating(q: str) -> dict:
    """Q=m·cp·ΔT；P=Q/t/η"""
    mass = _first([r'(?:质量|工件)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:kg|g)', r'(\d+(?:\.\d+)?)\s*(?:kg|g)'], q)
    dt = _first([r'(?:温升|ΔT)\s*[=:]?\s*(\d+(?:\.\d+)?)'], q)
    time_s = _first([r'(?:时间|加热时间|目标)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:s|秒|min)', r'(\d+(?:\.\d+)?)\s*(?:秒|s\b|min)'], q)
    if mass is None or dt is None or time_s is None:
        return {"rows": [("提示", "感应加热需要：工件质量 + 温升 + 加热时间。如「钢件2kg 加热到温升300℃ 目标10秒 功率多少」")], "note": ""}
    cp = 0.49 if "钢" in q else (0.9 if "铝" in q else (0.385 if "铜" in q else 0.5))
    mass_kg = mass / 1000 if "g" in str(mass).lower() and "kg" not in q else mass
    t = time_s * (60 if "min" in str(time_s).lower() else 1)
    q_kj = mass_kg * cp * dt
    p_theory = q_kj / t
    p_real = p_theory / 0.7
    rows = [("计算目标", "感应加热功率"),
            ("工件 / 温升", f"{_num(mass_kg)} kg（cp={cp} kJ/(kg·K)）/{dt}℃"),
            ("所需热量 Q", f"{_num(q_kj)} kJ"),
            ("理论功率", f"{_num(p_theory)} kW"),
            ("实际功率(η≈70%)", f"{_num(p_real)} kW"),
            ("标准", "Q=m·cp·ΔT；P=Q/t/η")]
    return {"rows": rows, "note": "cp：碳钢0.49/铜0.385/铝0.900/不锈钢0.50 kJ/(kg·K)。感应加热总效率 60~85%（含逆变器+线圈）"}


# ===== 运算放大器 =====

def _res_ohm(v, unit):
    if not unit:
        return v
    u = unit[0].lower()
    return v * (1e6 if u == "m" else (1e3 if u == "k" else 1.0))


def _calc_opamp(q: str) -> dict:
    """运放增益：Av=1+Rf/R1（同相）或 -Rf/R1（反相）；带宽 BW=GBW/(1+Rf/R1)"""
    rf_v, rf_u = _val_unit(q, r'(?:Rf|反馈电阻)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(kΩ|KΩ|MΩ|k|Ω|欧)')
    r1_v, r1_u = _val_unit(q, r'(?:R1|Rg|接地电阻|增益电阻)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(kΩ|KΩ|MΩ|k|Ω|欧)')
    gbw_v, gbw_u = _val_unit(q, r'(?:GBW|增益带宽积)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(MHz|kHz)')
    if rf_v is None or r1_v is None:
        return {"rows": [("提示", "运放增益需要 Rf 和 R1。如「运放 Rf=10k R1=1k 同相增益多少」；加 GBW 可算带宽")], "note": ""}
    rf, r1 = _res_ohm(rf_v, rf_u), _res_ohm(r1_v, r1_u)
    is_inv = "反相" in q or "inverting" in q.lower()
    ng = 1 + rf / r1
    av = -rf / r1 if is_inv else ng
    rows = [("计算目标", f"运放{'反相' if is_inv else '同相'}增益"),
            ("Rf / R1", f"{_num(rf / 1e3 if rf >= 1000 else rf)} {'kΩ' if rf >= 1000 else 'Ω'} / {_num(r1 / 1e3 if r1 >= 1000 else r1)} {'kΩ' if r1 >= 1000 else 'Ω'}"),
            ("增益 Av", f"{_num(av, 2)}（{_num(20 * math.log10(abs(av)), 1)} dB）"),
            ("噪声增益", f"{_num(ng, 2)}（=1+Rf/R1，决定带宽）")]
    m_sig = re.search(r'(?:输入信号|输入)\s*(\d+(?:\.\d+)?)\s*(mVpp|Vpp|mV|V)', q, re.IGNORECASE)
    if m_sig is not None:
        vin_amp = float(m_sig.group(1)) * (1e-3 if m_sig.group(2).lower().startswith("m") else 1.0)
        rows.append(("输出幅度", f"{_num(abs(av) * vin_amp, 3)} V（Av×{_num(vin_amp, 3)}V）"))
    if gbw_v is not None:
        gbw = gbw_v * (1e6 if gbw_u[0].lower() == "m" else 1e3)
        bw = gbw / ng
        rows.append(("闭环带宽", f"{_num(bw / 1e3, 0)} kHz" if bw < 1e6 else f"{_num(bw / 1e6, 2)} MHz"))
    rows.append(("标准", "Av=1+Rf/R1；BW=GBW/噪声增益"))
    return {"rows": rows, "note": "反相放大器信号增益 -Rf/R1，但噪声增益仍是 1+Rf/R1（决定带宽）。LM358 GBW≈1MHz，TL072≈3MHz，OPA2134≈8MHz"}


# ===== 运放有源滤波器 =====

def _calc_opamp_filter(q: str) -> dict:
    """一阶/二阶 Sallen-Key 截止频率 fc=1/(2πRC)"""
    r_v, r_u = _val_unit(q, r'(?:电阻|R)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(kΩ|KΩ|MΩ|k|Ω|欧)')
    if r_v is None:
        r_v, r_u = _val_unit(q, r'(\d+(?:\.\d+)?)\s*(kΩ|KΩ|MΩ|k)')
    c_v, c_u = _val_unit(q, r'(?:电容|C)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(μF|uF|nF|pF)')
    if c_v is None:
        c_v, c_u = _val_unit(q, r'(\d+(?:\.\d+)?)\s*(μF|uF|nF|pF)')
    if r_v is None or c_v is None:
        return {"rows": [("提示", "滤波器截止频率需要 R + C。如「10kΩ 100nF 低通截止频率多少」；或给 fc 求 R「1kHz 100nF 要多大电阻」")], "note": ""}
    r = _res_ohm(r_v, r_u)
    c = c_v * {"uf": 1e-6, "μf": 1e-6, "nf": 1e-9, "pf": 1e-12}.get(c_u.lower(), 1e-6)
    fc = 1 / (2 * math.pi * r * c)
    order = "二阶" if "二阶" in q or "2阶" in q or "sallen" in q.lower() else "一阶"
    rows = [("计算目标", f"{order}低通/高通截止频率"),
            ("R / C", f"{r_v} {r_u} / {c_v} {c_u}"),
            ("截止频率 fc", f"{_num(fc, 1)} Hz（{_num(fc / 1e3, 2)} kHz）"),
            ("斜率", "-20dB/dec" if order == "一阶" else "-40dB/dec（Q=0.707 巴特沃斯）"),
            ("标准", "fc=1/(2πRC)")]
    return {"rows": rows, "note": "二阶 Sallen-Key 等阻等容时 Q=0.707（巴特沃斯平坦）。运放 GBW 需 ≥10~100×fc；电容优先选标准值再反算电阻"}


# ===== 差动放大器 =====

def _calc_diff_amp(q: str) -> dict:
    """差模增益 Adm=R2/R1；Vout=(R2/R1)V+ − (R4/R3)V−"""
    r1 = _first([r'(?:R1)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*k'], q)
    r2 = _first([r'(?:R2)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*k'], q)
    vp = _first([r'(?:正相|V\+|Vp)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
    vn = _first([r'(?:反相|V-|Vm)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
    if r1 is None or r2 is None:
        return {"rows": [("提示", "差动放大器需要 R1/R2。如「差动放大 R1=10k R2=100k 增益多少」；输出电压加 V+/V-")], "note": ""}
    adm = r2 / r1
    rows = [("计算目标", "差动放大器"),
            ("R1 / R2", f"{_num(r1)} kΩ / {_num(r2)} kΩ"),
            ("差模增益", f"{_num(adm)}（{_num(20 * math.log10(adm), 1)} dB）")]
    if vp is not None and vn is not None:
        vout = (r2 / r1) * vp - (r2 / r1) * vn
        rows.append(("输出电压", f"{_num(vout, 3)} V（=(R2/R1)(V+−V−)）"))
    rows.append(("标准", "Adm=R2/R1（R1=R3, R2=R4）"))
    return {"rows": rows, "note": "共模抑制依赖电阻匹配精度，建议 0.1% 匹配电阻。集成仪表放大器 INA128/AD620 可提供 100dB+ CMRR"}


# ===== 惠斯通电桥 =====

def _calc_wheatstone(q: str) -> dict:
    """Vout=Vex(R3/(R1+R3)−R4/(R2+R4))；平衡 R1/R3=R2/R4"""
    vex = _first([r'(?:激励|Vex)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V', r'(\d+(?:\.\d+)?)\s*V\s*(?:激励)'], q)
    r = []
    for name in ("R1", "R2", "R3", "R4"):
        v_v, v_u = _val_unit(q, rf'{name}\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(kΩ|KΩ|k|Ω|欧)?')
        r.append(_res_ohm(v_v, v_u) if v_v is not None else None)
    if vex is None or any(x is None for x in r):
        return {"rows": [("提示", "惠斯通电桥需要激励电压 + R1~R4。如「惠斯通电桥 5V 激励 R1=100 R2=100 R3=100 R4=100 输出多少」")], "note": ""}
    r1, r2, r3, r4 = r
    vout = vex * (r3 / (r1 + r3) - r4 / (r2 + r4))
    balanced = abs(r1 * r4 - r2 * r3) < 1e-6
    rows = [("计算目标", "惠斯通电桥"),
            ("激励 / 桥臂", f"{_num(vex)} V / R1={_num(r1 / 1000, 3)}k R2={_num(r2 / 1000, 3)}k R3={_num(r3 / 1000, 3)}k R4={_num(r4 / 1000, 3)}k"),
            ("输出电压", f"{_num(vout * 1000, 2)} mV"),
            ("平衡状态", "✅ 平衡 Vout=0" if balanced else "不平衡（R4 平衡值 = " + _num(r3 * r2 / r1, 0) + " Ω）"),
            ("标准", "Vout=Vex(R3/(R1+R3)−R4/(R2+R4))")]
    return {"rows": rows, "note": "平衡条件 R1/R3=R2/R4 → R4=R3·R2/R1。应变片常用此桥，微应变输出 mV 级需仪表放大"}


# ===== 三极管静态工作点 =====

def _calc_transistor_bias(q: str) -> dict:
    """固定偏置：Ib=(Vcc−Vbe)/Rb, Ic=βIb, Vce=Vcc−IcRc；分压偏置：Vb=VccR2/(R1+R2)"""
    vcc = _first([r'(?:电源|Vcc)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
    if vcc is None:
        return {"rows": [("提示", "三极管工作点需要电源电压 Vcc + 偏置电阻。如「Vcc12V Rb470k Rc2k β100 工作点多少」；分压偏置「Vcc12V R1=47k R2=10k Rc=2k Re=1k」")], "note": ""}
    beta = _first([r'(?:β|beta|hFE)\s*[=:]?\s*(\d+(?:\.\d+)?)'], q)
    vbe = 0.7
    if "分压" in q:
        r1 = _first([r'(?:R1)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:k|kΩ|KΩ)'], q)
        r2 = _first([r'(?:R2)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:k|kΩ|KΩ)'], q)
        rc = _first([r'(?:Rc|R_C)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:k|kΩ|KΩ|Ω|欧)'], q)
        re = _first([r'(?:Re|R_E)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:k|kΩ|KΩ|Ω|欧)'], q)
        if r1 is None or r2 is None or rc is None or re is None:
            return {"rows": [("提示", "分压偏置需要 Vcc+R1+R2+Rc+Re。如「Vcc12V R1=47k R2=10k Rc=2k Re=1k 工作点」")], "note": ""}
        vb = vcc * r2 / (r1 + r2)
        ve = vb - vbe
        ic = ve / re * 1000 if re >= 1 else ve / re
        vce = vcc - ic * (rc + re)
        rows = [("计算目标", "三极管分压偏置工作点"),
                ("基极电压 Vb", f"{_num(vb, 2)} V"),
                ("发射极 Ve", f"{_num(ve, 2)} V"),
                ("集电极电流 Ic", f"{_num(ic)} mA"),
                ("集射电压 Vce", f"{_num(vce, 2)} V"),
                ("工作区", "饱和" if vce <= 0.2 else ("放大" if vce > 0.3 else "临界")),
                ("标准", "Vb=Vcc·R2/(R1+R2)；Ic≈Ve/Re")]
        return {"rows": rows, "note": "分压偏置受 β 影响小，稳定性好。放大区 Vce>0.3V，饱和 Vce≤0.2V，截止 Vbe<0.5V"}
    rb = _first([r'(?:Rb|R_B)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:k|kΩ|KΩ|Ω|欧)'], q)
    rc = _first([r'(?:Rc|R_C)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:k|kΩ|KΩ|Ω|欧)'], q)
    if rb is None or rc is None:
        return {"rows": [("提示", "固定偏置需要 Vcc+Rb+Rc+β。如「Vcc12V Rb470k Rc2k β100 工作点多少」")], "note": ""}
    rb_o = rb * 1e3 if rb < 10 else rb * 1e3
    rc_o = rc * 1e3 if rc < 10 else rc * 1e3
    ib = (vcc - vbe) / rb_o
    ic = (beta or 100) * ib
    vce = vcc - ic * rc_o
    rows = [("计算目标", "三极管固定偏置工作点"),
            ("基极电流 Ib", f"{_num(ib * 1e6, 1)} μA"),
            ("集电极电流 Ic", f"{_num(ic * 1e3, 1)} mA"),
            ("集射电压 Vce", f"{_num(vce, 1)} V"),
            ("工作区", "饱和" if vce <= 0.2 else "放大"),
            ("标准", "Ib=(Vcc−Vbe)/Rb；Ic=β·Ib")]
    return {"rows": rows, "note": "固定偏置受 β 离散影响大，批量生产建议用分压偏置。硅管 Vbe≈0.7V，锗管≈0.3V"}


# ===== 稳压管限流电阻 =====

def _calc_zener(q: str) -> dict:
    """R=(Vs−Vz)/(Iz+IL)；功耗校核"""
    vs = _first([r'(?:电源|Vs)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V', r'(\d+(?:\.\d+)?)\s*[Vv](?![A-Za-z0-9])'], q)
    vz = _first([r'(?:稳压值|Vz)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V', r'(\d+(?:\.\d+)?)\s*V\s*(?:稳压|齐纳)'], q)
    il = _first([r'(?:负载电流|IL)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mA'], q)
    if vs is None or vz is None:
        return {"rows": [("提示", "稳压管需要电源电压 + 稳压值（+ 负载电流）。如「12V 供电 5V1 稳压管 负载10mA 限流电阻」")], "note": ""}
    iz = _first([r'(?:工作电流|Iz)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mA'], q)
    il_ma = il or 0
    iz_ma = iz or 5
    i_total = (iz_ma + il_ma) / 1000
    r = (vs - vz) / i_total
    rows = [("计算目标", "稳压管限流电阻"),
            ("电源 / 稳压值", f"{_num(vs)} V / {_num(vz)} V"),
            ("电流 Iz+IL", f"{iz_ma} mA + {il_ma} mA = {_num((iz_ma + il_ma), 0)} mA"),
            ("限流电阻", f"{_num(r)} Ω"),
            ("电阻功耗", f"{_num((vs - vz) * i_total, 2)} W（需 ≥2× 余量）"),
            ("稳压管功耗", f"{_num(vz * iz_ma / 1000, 2)} W"),
            ("标准", "R=(Vs−Vz)/(Iz+IL)")]
    return {"rows": rows, "note": "Iz 建议取 Iz_max 的 30~50%。大电流负载改用 LDO 或开关电源。常见 1N4728A(3.3V/1W)、1N4733A(5.1V/1W)"}


# ===== LM317 / LM1117 可调稳压器 =====

def _calc_lm317(q: str) -> dict:
    """Vout=Vref(1+R2/R1)+Iadj·R2；Vref=1.25"""
    r1_v, r1_u = _val_unit(q, r'(?:R1)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(kΩ|KΩ|k|Ω|欧)?')
    r2_v, r2_u = _val_unit(q, r'(?:R2)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(kΩ|KΩ|k|Ω|欧)?')
    r1 = _res_ohm(r1_v, r1_u) if r1_v is not None else None
    r2 = _res_ohm(r2_v, r2_u) if r2_v is not None else None
    vout_target = _first([r'(?:输出|Vout)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
    if r1 is None or r2 is None:
        if vout_target is None:
            return {"rows": [("提示", "LM317 需要 R1+R2（算输出电压），或目标电压（反推 R2）。如「LM317 R1=240 R2=720 输出多少」「LM317 要5V R1=240 R2多少」")], "note": ""}
        r1_o = 240
        r2_o = (vout_target / 1.25 - 1) * r1_o
        rows = [("计算目标", "LM317 输出电阻反推"),
                ("目标电压", f"{vout_target} V"),
                ("R1(建议 240Ω)", f"{_num(r1_o)} Ω"),
                ("所需 R2", f"{_num(r2_o)} Ω（{_num(r2_o / 1000, 2)} kΩ）"),
                ("标准", "Vout≈1.25(1+R2/R1)")]
        return {"rows": rows, "note": "典型：3.3V→R2≈393Ω，5V→R2≈720Ω，12V→R2≈2064Ω（R1=240Ω）。输入输出压差需 ≥2~3V"}
    r1_o, r2_o = r1, r2
    vout = 1.25 * (1 + r2_o / r1_o)
    rows = [("计算目标", "LM317 输出电压"),
            ("R1 / R2", f"{_num(r1_o)} Ω / {_num(r2_o)} Ω"),
            ("输出电压", f"{_num(vout, 2)} V"),
            ("标准", "Vout≈1.25(1+R2/R1)")]
    return {"rows": rows, "note": "R1 推荐 240Ω 保证最小负载电流。Iadj(≈50~100μA)×R2 项通常可忽略。压差需 ≥2~3V"}


# ===== MC34063 DC-DC =====

def _calc_mc34063(q: str) -> dict:
    """Vout=1.25(1+R2/R1)；Rsc=0.3/Ipeak"""
    r1 = _first([r'(?:R1)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:k|kΩ|Ω|欧)'], q)
    r2 = _first([r'(?:R2)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:k|kΩ|Ω|欧)'], q)
    ipeak = _first([r'(?:峰值电流|Ipeak)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*A'], q)
    if r1 is None or r2 is None:
        if ipeak is None:
            return {"rows": [("提示", "MC34063 需要 R1+R2（算输出电压），或峰值电流（算限流电阻）。如「MC34063 R1=1k R2=3k 输出多少」「MC34063 峰值1.2A 限流电阻」")], "note": ""}
        rsc = 0.3 / ipeak
        rows = [("计算目标", "MC34063 限流电阻"),
                ("峰值电流", f"{ipeak} A"),
                ("限流电阻 Rsc", f"{_num(rsc * 1000, 1)} mΩ（0.3/Ipeak）"),
                ("标准", "Rsc=0.3/Ipeak")]
        return {"rows": rows, "note": "内部开关限流最大 1.5A。频率范围 20~100kHz，CT 定时电容决定频率（CT 过小无法起振）"}
    r1_o, r2_o = (r1 * 1e3, r2 * 1e3) if r1 < 10 else (r1, r2)
    vout = 1.25 * (1 + r2_o / r1_o)
    rows = [("计算目标", "MC34063 输出电压"),
            ("R1 / R2", f"{_num(r1_o)} Ω / {_num(r2_o)} Ω"),
            ("输出电压", f"{_num(vout, 2)} V"),
            ("标准", "Vout=1.25(1+R2/R1)")]
    return {"rows": rows, "note": "1.25V 为内部基准。支持升压/降压/反压，频率 20~100kHz，峰值电流 ≤1.5A"}


# ===== I2C 上拉电阻 =====

def _calc_i2c_pullup(q: str) -> dict:
    """Rp_max=Tr/(0.8473·Cb)；Rp_min≈Vcc·0.1/Iol"""
    vcc = _first([r'(?:电源|Vcc|VDD|Vdd|vdd)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V',
                  r'(\d+(?:\.\d+)?)\s*V\s*(?:i2c|I2C|总线)'], q)
    cb = _first([r'(?:总线电容|Cb)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*pF'], q)
    if cb is None:
        # 多器件/走线电容聚合：N 个器件 × Ci + 走线 pF/cm × cm（+ 走线电容 XpF）
        cb_agg = 0.0
        m_dev = re.search(r'(\d+)\s*个\s*器件\s*[（(]?\s*(?:每个|各)?\s*Ci\s*[=:]?\s*(\d+(?:\.\d+)?)\s*pF', q, re.IGNORECASE)
        m_cm = re.search(r'(\d+(?:\.\d+)?)\s*(?:cm|厘米)\s*[，,]?\s*(?:约|大约)?\s*(\d+(?:\.\d+)?)\s*pF\s*/?\s*cm', q, re.IGNORECASE)
        m_trace = re.search(r'走线电容\s*(?:约|为)?\s*(\d+(?:\.\d+)?)\s*pF', q, re.IGNORECASE)
        if m_dev:
            cb_agg += float(m_dev.group(1)) * float(m_dev.group(2))
        if m_cm:
            cb_agg += float(m_cm.group(1)) * float(m_cm.group(2))
        if m_trace:
            cb_agg += float(m_trace.group(1))
        # 有明确聚合来源才用聚合值；否则退化为任意 pF 值（如 "总线100pF" 无标签）
        cb = cb_agg if cb_agg > 0 else _first([r'(\d+(?:\.\d+)?)\s*pF'], q)
    if vcc is None or cb is None:
        return {"rows": [("提示", "I2C 上拉需要电源电压 + 总线电容（+ 速率）。如「3.3V I2C 总线电容100pF 上拉多少」")], "note": ""}
    mode = "Fast-Plus" if "fast-plus" in q.lower() or "1m" in q else ("Fast" if "fast" in q.lower() or "400k" in q else "Standard")
    tr = {"Standard": 1000e-9, "Fast": 300e-9, "Fast-Plus": 120e-9}[mode]
    cb_f = cb * 1e-12
    rp_max = tr / (0.8473 * cb_f)
    rp_min = vcc * 0.1 / 0.003
    rows = [("计算目标", f"I2C 上拉电阻（{mode}）"),
            ("电源 / 总线电容", f"{_num(vcc)} V / {_num(cb)} pF"),
            ("上拉最大值", f"{_num(rp_max / 1e3, 1)} kΩ（由上升时间限制）"),
            ("上拉最小值", f"{_num(rp_min, 0)} Ω（由驱动能力限制）"),
            ("推荐", f"{_num((rp_max + rp_min) / 2 / 1e3, 1)} kΩ（常用 2.2~10k）"),
            ("标准", "Rp_max=Tr/(0.8473·Cb)；Rp_min≈Vcc·0.1/Iol")]
    # 给定上拉电阻 → 反推上升时间（盲用 4.7kΩ 是否满足 Tr 要求）
    m_rp = _first([r'(\d+(?:\.\d+)?)\s*kΩ\s*(?:上拉|电阻)'], q)
    if m_rp is not None and ("上升时间" in q or "满足" in q or "盲用" in q):
        tr_actual = 0.8473 * m_rp * 1000 * cb_f
        ok = "✅ 满足" if tr_actual <= tr else "❌ 不满足"
        rows.append(("给定上拉上升时间", f"{_num(tr_actual * 1e9, 0)} ns（0.8473×{_num(m_rp, 1)}kΩ×{_num(cb)}pF，要求≤{_num(tr * 1e9, 0)}ns）{ok}"))
    return {"rows": rows, "note": "Standard 总线电容≤400pF，Fast≤400pF，Fast-Plus≤550pF。Rp 越小驱动越强但功耗越高；多节点需计算总 Cb"}


# ===== 电阻 / 电容串并联 =====

def _calc_parallel_resistance(q: str) -> dict:
    """1/Req=Σ1/Ri；两电阻 Req=R1R2/(R1+R2)"""
    vals = re.findall(r'(\d+(?:\.\d+)?)\s*(?:kΩ|KΩ|k|Ω|欧)', q)
    if not vals or len(vals) < 2:
        return {"rows": [("提示", "并联电阻需要至少两个电阻。如「10k 和 20k 并联 等效多少」")], "note": ""}
    ohms = [float(v) * 1e3 for v in vals] if any("k" in s for s in re.findall(r'\d+(?:\.\d+)?\s*(kΩ|KΩ|k)', q)) else [float(v) for v in vals]
    req = 1 / sum(1 / o for o in ohms)
    rows = [("计算目标", "并联电阻等效值"),
            ("电阻", " ∥ ".join(f"{_num(o / 1e3, 2)}k" if o >= 1e3 else f"{_num(o)}Ω" for o in ohms)),
            ("等效电阻", f"{_num(req)} Ω" if req < 1000 else f"{_num(req / 1e3, 2)} kΩ"),
            ("标准", "1/Req=Σ1/Ri")]
    return {"rows": rows, "note": "Req 始终小于最小支路电阻；两个相同 R 并联 → R/2"}


def _calc_series_capacitor(q: str) -> dict:
    """1/Ceq=Σ1/Ci；两电容 Ceq=C1C2/(C1+C2)；分压 Vn=Ceq·V/Cn"""
    vals = re.findall(r'(\d+(?:\.\d+)?)\s*(μF|uF|nF|pF)', q, re.IGNORECASE)
    if not vals or len(vals) < 2:
        return {"rows": [("提示", "串联电容需要至少两个电容。如「10μF 和 20μF 串联 等效多少」")], "note": ""}
    unit = vals[0][1].lower()
    mult = {"μf": 1, "uf": 1, "nf": 1e-3, "pf": 1e-6}[unit]
    cap = [float(v) * mult for v, _ in vals]
    ceq = 1 / sum(1 / c for c in cap)
    v_total = _first([r'(?:电压|V)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
    rows = [("计算目标", "电容串联等效值"),
            ("电容", " ↔ ".join(f"{_num(c / mult)} {vals[i][1]}" for i, c in enumerate(cap))),
            ("等效电容", f"{_num(ceq / mult, 2)} {vals[0][1]}"),
            ("标准", "1/Ceq=Σ1/Ci")]
    if v_total is not None:
        for i, c in enumerate(cap):
            vn = ceq * v_total / c
            rows.append((f"C{i + 1} 分压", f"{_num(vn, 1)} V"))
    return {"rows": rows, "note": "串联总容量小于最小单个；总耐压=各电容耐压之和。小电容承受更高电压，需核对耐压；高压串联建议并均压电阻"}


# ===== 电功率与欧姆定律 =====

def _calc_electric_power(q: str) -> dict:
    """P=VI；P=I²R；P=V²/R 互推"""
    v = _first([r'(?:电压|V)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V', r'(\d+(?:\.\d+)?)\s*[Vv](?![A-Za-z0-9])'], q)
    i = _extract_current(q)
    r = _first([r'(?:电阻|R)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:kΩ|KΩ|Ω|欧)'], q)
    p = _first([r'(?:功率|P)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*W'], q)
    if (v is not None and i is not None):
        rows = [("计算目标", "电功率 P=V×I"),
                ("电压 / 电流", f"{_num(v)} V / {_num(i)} A"),
                ("功率", f"{_num(v * i)} W"),
                ("标准", "P=V·I")]
        return {"rows": rows, "note": "欧姆表：V=IR、I=V/R、R=V/I、P=VI、P=I²R、P=V²/R。SMD 封装功率：0402 0.063W/0603 0.1W/0805 0.125W/1206 0.25W/2512 1W"}
    if v is not None and r is not None:
        r_o = r * 1e3 if r < 10 else r
        rows = [("计算目标", "电功率 P=V²/R"),
                ("电压 / 电阻", f"{_num(v)} V / {_num(r_o)} Ω"),
                ("功率", f"{_num(v ** 2 / r_o)} W"),
                ("电流", f"{_num(v / r_o)} A"),
                ("标准", "P=V²/R")]
        return {"rows": rows, "note": "同上"}
    if p is not None and v is not None:
        rows = [("计算目标", "功率反推电流"),
                ("功率 / 电压", f"{_num(p)} W / {_num(v)} V"),
                ("电流", f"{_num(p / v)} A"),
                ("标准", "I=P/V")]
        return {"rows": rows, "note": "同上"}
    return {"rows": [("提示", "电功率需要至少两个量。如「12V 3A 功率多少」「5V 100Ω 功率多少」")], "note": ""}


# ===== ADC 分辨率与精度 =====

def _calc_adc(q: str) -> dict:
    """1LSB=Vref/2^N；SNR=6.02N+1.76；DR=20log10(2^N)"""
    bits = _first([r'(?:位数|bit|比特)\s*[=:]?\s*(\d+)', r'(\d+)\s*位'], q)
    vref = _first([r'(?:参考|Vref)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
    if bits is None:
        return {"rows": [("提示", "ADC 需要位数。如「12位 ADC Vref3.3V 分辨率多少」「16位 ADC 信噪比」")], "note": ""}
    vref = vref or 3.3
    lsb = vref / (2 ** bits)
    snr = 6.02 * bits + 1.76
    dr = 20 * math.log10(2 ** bits)
    rows = [("计算目标", f"{bits} 位 ADC 性能"),
            ("参考电压", f"{_num(vref)} V"),
            ("1 LSB 分辨率", f"{_num(lsb * 1000, 2)} mV"),
            ("理论 SNR", f"{_num(snr, 1)} dB（6.02N+1.76）"),
            ("动态范围", f"{_num(dr, 1)} dB"),
            ("满量程码值", f"{2 ** bits - 1}"),
            ("标准", "1LSB=Vref/2^N")]
    return {"rows": rows, "note": "理论 SNR 仅含量化噪声；实际受采样抖动、运放噪声、基准噪声影响明显更低"}


# ===== RS-485 总线 =====

def _calc_rs485(q: str) -> dict:
    """RT=Z0；Baud×Distance≤10^8"""
    z0 = _first([r'(?:特征阻抗|Z0|Z₀)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*Ω'], q)
    baud = _first([r'(\d+(?:\.\d+)?)\s*(?:Mbps|kbps|bps)'], q)
    if z0 is None and baud is None:
        return {"rows": [("提示", "RS-485 需要：特征阻抗（算终端电阻），或波特率（算距离）。如「RS485 120Ω 终端电阻多少」「115200bps RS485 多远」")], "note": ""}
    rows = [("计算目标", "RS-485 总线设计")]
    if z0 is not None:
        rows.append(("终端电阻 RT", f"{z0} Ω（=Z0，仅两端各接一个）"))
    if baud is not None:
        mult = 1e6 if "m" in str(baud).lower() else (1e3 if "k" in str(baud).lower() else 1)
        bps = baud * mult
        dist = 1e8 / bps
        rows.append(("最大距离", f"{_num(dist / 1000, 2)} km（Baud×Distance≤10^8）" if dist >= 1000 else f"{_num(dist)} m"))
        rows.append(("参考", "9600→10.4km / 115200→868m / 1Mbps→100m / 10Mbps→10m"))
    rows.append(("标准", "RT=Z0；Baud×Distance≤10^8 bps·m"))
    return {"rows": rows, "note": "标准双绞线 STP=120Ω，CAT5=100Ω，UTP=150Ω。远距离低速优先，>10Mbps 距离骤降"}


# ===== 无源晶振匹配电容 =====

def _calc_crystal(q: str) -> dict:
    """CL=(C1+Cin)(C2+Cout)/(C1+Cin+C2+Cout)+Cstray；对称 C1=C2=2(CL−Cstray)−Cin"""
    cl = _first([r'(?:负载电容|CL)\s*(?:约|估算|为)?\s*[=:]?\s*(\d+(?:\.\d+)?)\s*pF'], q)
    cstray = _first([r'(?:寄生电容|Cstray|杂散|寄生)\s*(?:约|估算|为)?\s*[=:]?\s*(\d+(?:\.\d+)?)\s*pF'], q)
    cin = _first([r'(?:引脚电容|Cin)\s*(?:约|估算|为)?\s*[=:]?\s*(\d+(?:\.\d+)?)\s*pF'], q)
    if cl is None:
        return {"rows": [("提示", "晶振匹配需要负载电容 CL。如「晶振 CL=12.5pF 寄生3pF 引脚2pF 匹配电容多少」」")], "note": ""}
    cstray = cstray or 3.0
    cin = cin or 2.0
    c1 = 2 * (cl - cstray - cin)   # 杂散全部进括号：C1=C2=2(CL−Cstray−Cin)
    rows = [("计算目标", "晶振匹配电容（对称）"),
            ("负载电容 CL", f"{cl} pF"),
            ("寄生 / 引脚", f"Cstray {_num(cstray)} pF / Cin {_num(cin)} pF"),
            ("匹配电容 C1=C2", f"{_num(c1, 1)} pF（推荐 E24：{_num(round(c1 / 5) * 5, 0)}pF 或就近标准值）"),
            ("标准", "C1=C2=2(CL−Cstray−Cin)")]
    # 频偏估算：给定实际 C1（或用户选定的 C1），相对标称 CL 的频率偏差
    if any(w in q for w in ["频偏", "频率偏差", "偏差"]):
        c1_actual = _first([r'(?:实际|实选|选用|取)\s*C1\s*=\s*C2\s*[=:]?\s*(\d+(?:\.\d+)?)\s*pF',
                            r'C1\s*=\s*C2\s*选\s*为\s*(\d+(?:\.\d+)?)\s*pF'], q) or c1
        # 实际负载电容 = C1/2 + 总杂散（Cstray 已含题目给的杂散总值；不重复加默认 Cin）
        cl_actual = c1_actual / 2 + cstray
        # 频偏近似：Δf/f ≈ (C_mot/2)·(1/(C0+CL_actual) − 1/(C0+CL标称))，C_mot=0.02pF、C0=7pF 为常见假设
        c_mot, c0 = 0.02, 7.0
        ppm = (c_mot / 2) * (1 / (c0 + cl_actual) - 1 / (c0 + cl)) * 1e6
        rows.append(("频率偏差（估算）", f"{_num(ppm, 0)} ppm ≈ {_num(ppm / 1e4, 3)}%"
                    + ("（CL 偏小→频率偏高）" if ppm > 0 else "（CL 偏大→频率偏低）")))
        rows.append(("估算假设", "C_mot=0.02pF、C0=7pF（典型无源晶振），实际以 datasheet 为准"))
    return {"rows": rows, "note": "实测频率偏高→CL 偏小→调大 C1/C2；偏低→调小。良好布线 Cstray 2~5pF，STM32/ESP32 引脚电容典型 2~5pF"}


# ===== SMD 焊盘尺寸（IPC-7351） =====

def _calc_smd_pad(q: str) -> dict:
    """wpad=W+2JS；lpad=T+JT+JH（Nominal JS=0.03, JT=JH=0.35）"""
    l = _first([r'(?:长度|L)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mm'], q)
    w = _first([r'(?:宽度|W)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*mm'], q)
    m_wh = re.search(r'(\d+(?:\.\d+)?)\s*[×x*]\s*(\d+(?:\.\d+)?)\s*mm', q)
    if m_wh and (l is None or w is None):
        a, b = float(m_wh.group(1)), float(m_wh.group(2))
        if l is None:
            l, w = max(a, b), min(a, b)
        elif w is None:
            w = min(a, b)
    if l is None or w is None:
        return {"rows": [("提示", "SMD 焊盘需要封装体长宽。如「0603 封装 1.6×0.8mm 焊盘多大」或直接给封装名「0805 焊盘」」")], "note": ""}
    if "least" in q.lower() or "最密" in q:
        js, jt, jh = 0.0, 0.15, 0.15
    elif "most" in q.lower() or "最大" in q or "手工" in q:
        js, jt, jh = 0.05, 0.55, 0.55
    else:
        js, jt, jh = 0.03, 0.35, 0.35
    wpad, lpad = w + 2 * js, l + jt + jh
    rows = [("计算目标", f"SMD 焊盘（IPC-7351 Nominal）"),
            ("封装体", f"{_num(l)} × {_num(w)} mm"),
            ("焊盘宽 wpad", f"{_num(wpad, 2)} mm（W+2×{js}）"),
            ("焊盘长 lpad", f"{_num(lpad, 2)} mm（T+{jt}+{jh}）"),
            ("标准", "IPC-7351（JS/JT/JH 等级）")]
    return {"rows": rows, "note": "Nominal 常规批量；Most 手工/维修（焊盘大）；Least 高密度组装。0402/0603/0805 等常用封装尺寸可查表"}


# ===== VSWR 驻波比 / 回波损耗 =====

def _calc_vswr(q: str) -> dict:
    """Γ=(VSWR−1)/(VSWR+1)；VSWR=(1+Γ)/(1−Γ)；RL=−20log10Γ；ML=−10log10(1−Γ²)"""
    vswr = _first([r'vswr\s*[=:]?\s*(\d+(?:\.\d+)?)', r'驻波比\s*[=:]?\s*(\d+(?:\.\d+)?)'], q)
    gamma = _first([r'(?:反射系数|Γ)\s*[=:]?\s*(0?\.\d+|\d+(?:\.\d+)?)'], q)
    if vswr is None and gamma is None:
        return {"rows": [("提示", "VSWR 需要驻波比或反射系数。如「VSWR1.5 回波损耗多少」「反射系数0.2 驻波比」」")], "note": ""}
    if gamma is None:
        gamma = (vswr - 1) / (vswr + 1)
    vswr = (1 + gamma) / (1 - gamma)
    rl = -20 * math.log10(gamma)
    ml = -10 * math.log10(1 - gamma ** 2)
    rows = [("计算目标", "VSWR / 回波损耗"),
            ("驻波比 VSWR", f"{_num(vswr, 2)}"),
            ("反射系数 Γ", f"{_num(gamma, 3)}"),
            ("回波损耗 RL", f"{_num(rl, 1)} dB"),
            ("失配损耗 ML", f"{_num(ml, 2)} dB"),
            ("标准", "Γ=(VSWR−1)/(VSWR+1)；RL=−20log10Γ")]
    return {"rows": rows, "note": "天线调试目标 VSWR<1.5；回波损耗>20dB 匹配良好。VSWR<1.5 优秀、<2 良好、3 较差、5 很差"}


# ===== 频率 / 波长换算 =====

def _calc_frequency_wavelength(q: str) -> dict:
    """λ=c/f；f=c/λ"""
    f_v, f_u = _val_unit(q, r'(\d+(?:\.\d+)?)\s*(GHz|MHz|kHz|Hz)')
    if f_v is None:
        lam = _first([r'(?:波长|λ)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:m|mm|cm)'], q)
        if lam is None:
            return {"rows": [("提示", "频率波长换算需要频率或波长。如「2.4GHz 波长多少」「1/4波长 5cm 对应频率」」")], "note": ""}
        lam_m = lam / 1000 if "m" in str(lam).lower() and "mm" not in q else lam
        f = 3e8 / lam_m
        rows = [("计算目标", "波长→频率"), ("波长", f"{_num(lam_m * 1000)} mm"), ("频率", f"{_num(f / 1e6, 1)} MHz（{_num(f / 1e9, 3)} GHz）"), ("标准", "f=c/λ")]
        return {"rows": rows, "note": "1/4λ 单极天线≈1.5dBi；1/2λ 偶极子≈2.15dBi。PCB 天线实际长度需×缩短系数 0.6~0.9"}
    f = _u(f_v, f_u, {"ghz": 1e9, "mhz": 1e6, "khz": 1e3, "hz": 1})
    lam = 3e8 / f
    rows = [("计算目标", "频率→波长"),
            ("频率", f"{f_v} {f_u}"),
            ("波长 λ", f"{_num(lam * 1000, 1)} mm"),
            ("1/4 波长", f"{_num(lam * 1000 / 4, 1)} mm"),
            ("1/2 波长", f"{_num(lam * 1000 / 2, 1)} mm"),
            ("标准", "λ=c/f")]
    return {"rows": rows, "note": "PCB 天线实际长度需×缩短系数 0.6~0.9。1/4λ 单极≈1.5dBi，1/2λ 偶极≈2.15dBi"}


# ===== PWM 占空比 / 频率 =====

def _calc_pwm(q: str) -> dict:
    """T=1/f；Ton=T·D%；D=Ton/(Ton+Toff)"""
    f_v, f_u = _val_unit(q, r'(?:频率|f)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(kHz|Hz|MHz)')
    d = _first([r'(?:占空比|D)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*%'], q)
    if f_v is None and d is None:
        return {"rows": [("提示", "PWM 需要频率或占空比。如「PWM 10kHz 占空比25% Ton多少」「1kHz 40% 占空比」」")], "note": ""}
    rows = [("计算目标", "PWM 占空比 / 频率")]
    if f_v is not None:
        f = _u(f_v, f_u, {"khz": 1e3, "hz": 1, "mhz": 1e6})
        t = 1 / f
        rows.append(("周期 T", f"{_num(t * 1e6, 1)} μs"))
        if d is not None:
            rows.append(("Ton", f"{_num(t * d / 100 * 1e6, 1)} μs"))
            rows.append(("Toff", f"{_num(t * (1 - d / 100) * 1e6, 1)} μs"))
    elif d is not None:
        ton = _first([r'(?:Ton)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:μs|us|ms|s)'], q)
        toff = _first([r'(?:Toff)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:μs|us|ms|s)'], q)
        if ton is not None and toff is not None:
            rows.append(("占空比", f"{_num(ton / (ton + toff) * 100, 1)}%（Ton/(Ton+Toff)）"))
    rows.append(("标准", "T=1/f；Ton=T·D%"))
    return {"rows": rows, "note": "LED 调光 1kHz；电机调速 5~20kHz；舵机 50Hz 5~10%；D 类功放 250kHz~1MHz"}


# ===== 金属材料重量 =====

def _calc_metal_weight(q: str) -> dict:
    """Weight=L·W·T·ρ"""
    m_dims = re.search(r'(\d+(?:\.\d+)?)\s*[×x*]\s*(\d+(?:\.\d+)?)\s*[×x*]\s*(\d+(?:\.\d+)?)\s*(?:mm|cm)?', q)
    if m_dims:
        dims = [m_dims.group(1), m_dims.group(2), m_dims.group(3)]
    else:
        dims = re.findall(r'(\d+(?:\.\d+)?)\s*(?:mm|cm|米|m)', q)
    density = {"钢": 7.85, "不锈钢": 7.93, "铝": 2.70, "紫铜": 8.96, "铜": 8.96, "黄铜": 8.50, "铅": 11.34, "铁": 7.85}
    mat = next((k for k in density if k in q), "铜")
    if len(dims) < 3:
        return {"rows": [("提示", "金属重量需要长+宽+厚（+材料）。如「紫铜板 100×50×3mm 重量多少」」")], "note": ""}
    cm = [float(d) / 10 for d in dims[:3]]
    g = cm[0] * cm[1] * cm[2] * density[mat]
    rows = [("计算目标", f"{mat}重量"),
            ("尺寸", " × ".join(f"{_num(d)} cm" for d in cm)),
            ("体积", f"{_num(cm[0] * cm[1] * cm[2], 2)} cm³"),
            ("重量", f"{_num(g, 1)} g（{_num(g / 1000, 3)} kg）"),
            ("标准", "W=L·W·T·ρ")]
    return {"rows": rows, "note": f"密度：紫铜 8.96 / H62黄铜 8.50 / 铝 2.70 / 钢 7.85 / 不锈钢 7.93 / 铅 11.34 g/cm³"}


# ===== EMC 单位换算 =====

def _calc_emc_convert(q: str) -> dict:
    """dBV/dBuV/dBm/dBW 互转"""
    db = _first([r'(\d+(?:\.\d+)?)\s*(dBuV|dBV|dBm|dBW)', r'(?:dBuV|dBV|dBm|dBW)\s*[=:]?\s*(\d+(?:\.\d+)?)'], q)
    v = _first([r'(?:电压|V)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*V'], q)
    p = _first([r'(?:功率|P)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:mW|W)'], q)
    if db is not None:
        u = "dBm"
        m = re.search(r'(dBuV|dBV|dBm|dBW)', q)
        u = m.group(1) if m else "dBm"
        rows = [("计算目标", f"{u} 换算")]
        if u == "dBuV":
            rows.append(("电压", f"{_num(10 ** (db / 20) / 1e6 * 1e3, 4)} mV（{_num(10 ** (db / 20), 1)} μV）"))
            rows.append(("@50Ω 功率", f"{_num(db - 107, 1)} dBm"))
        elif u == "dBV":
            rows.append(("电压", f"{_num(10 ** (db / 20), 3)} V"))
            rows.append(("dBuV", f"{_num(db + 120, 1)} dBuV"))
        elif u == "dBW":
            rows.append(("功率", f"{_num(10 ** (db / 10), 3)} W"))
            rows.append(("dBm", f"{_num(db + 30, 1)} dBm"))
        elif u == "dBm":
            rows.append(("功率", f"{_num(10 ** (db / 10), 3)} mW"))
            rows.append(("@50Ω 电压", f"{_num(db + 107, 1)} dBuV"))
        rows.append(("标准", "dBuV=dBV+120；dBm=dBW+30"))
        return {"rows": rows, "note": "电压用 20log，功率用 10log（P∝V²）。0dBm=1mW@50Ω≈107dBuV"}
    if v is not None:
        rows = [("计算目标", "电压→dB"),
                ("电压", f"{_num(v * 1000, 1)} mV"),
                ("dBV", f"{_num(20 * math.log10(v), 1)}"),
                ("dBuV", f"{_num(20 * math.log10(v) + 120, 1)}"),
                ("标准", "dBV=20log10(V)")]
        return {"rows": rows, "note": "同上"}
    if p is not None:
        pw = p / 1000 if "m" in str(p).lower() else p
        rows = [("计算目标", "功率→dB"),
                ("功率", f"{_num(pw)} W"),
                ("dBW", f"{_num(10 * math.log10(pw), 2)}"),
                ("dBm", f"{_num(10 * math.log10(pw) + 30, 2)}"),
                ("标准", "dBm=10log10(P)+30")]
        return {"rows": rows, "note": "同上"}
    return {"rows": [("提示", "EMC 换算需要 dB 值或电压/功率。如「56dBuV 是多少mV」「3.3V 是 dBuV」「20dBm 是几瓦」」")], "note": ""}


# ===== 走线串扰（3W 原则） =====

def _calc_crosstalk(q: str) -> dict:
    """S≥2W（边缘间距≥2倍线宽）；中心间距≥3W"""
    w_v, w_u = _val_unit(q, r'(?:线宽|W)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(mm|mil)')
    if w_v is None:
        return {"rows": [("提示", "3W 原则需要线宽。如「线宽0.2mm 走线间距多少」」")], "note": ""}
    w = w_v
    rows = [("计算目标", "3W 串扰抑制原则"),
            ("线宽", f"{_num(w)} {w_u}"),
            ("最小边缘间距", f"{_num(2 * w)} {w_u}（S≥2W）"),
            ("中心间距", f"{_num(3 * w)} {w_u}（≥3W 耦合抑制 ~70%）"),
            ("标准", "S≥2W（边缘）/ ≥3W（中心）")]
    return {"rows": rows, "note": "靠近信号层的地平面能有效阻截电场力线。高速设计还需避免上下层平行走线重叠"}


# ===== LC 滤波器 =====

def _calc_lc_filter(q: str) -> dict:
    """f0=1/(2π√LC)；Z0=√(L/C)；Q=Z0/ZL"""
    l_v, l_u = _val_unit(q, r'(?:电感|L)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(μH|uH|mH|H)')
    if l_v is None:
        l_v, l_u = _val_unit(q, r'(\d+(?:\.\d+)?)\s*(μH|uH|mH)')
    c_v, c_u = _val_unit(q, r'(?:电容|C)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(μF|uF|nF|pF)')
    if c_v is None:
        c_v, c_u = _val_unit(q, r'(\d+(?:\.\d+)?)\s*(μF|uF|nF|pF)')
    zl = _first([r'(?:负载|ZL)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*Ω'], q)
    if l_v is None or c_v is None:
        return {"rows": [("提示", "LC 滤波器需要 L+C（+负载阻抗）。如「10μH 100nF LC 滤波 负载50Ω 截止频率」」")], "note": ""}
    l = _u(l_v, l_u, {"uh": 1e-6, "μh": 1e-6, "mh": 1e-3, "h": 1})
    c = _u(c_v, c_u, {"uf": 1e-6, "μf": 1e-6, "nf": 1e-9, "pf": 1e-12})
    f0 = 1 / (2 * math.pi * math.sqrt(l * c))
    z0 = math.sqrt(l / c)
    # 第二组 L/C（"若改用/换用 XμH 和 YnF"）——须在下方 q 被品质因数覆盖前使用原始入参 q
    m2 = re.search(r'(?:若改用|改为|换用)\s*(\d+(?:\.\d+)?)\s*(μH|uH|mH)\s*(?:电感)?\s*(?:和|与|、)?\s*(\d+(?:\.\d+)?)\s*(μF|uF|nF|pF)', q, re.IGNORECASE)
    q = z0 / zl if zl else None   # 注意：q 在此被重定义为品质因数，覆盖入参（历史遗留命名冲突）
    rows = [("计算目标", "LC 滤波器参数"),
            ("L / C", f"{_num(l_v)} {l_u} / {_num(c_v)} {c_u}"),
            ("谐振/截止频率", f"{_num(f0 / 1e3, 2)} kHz（{_num(f0 / 1e6, 3)} MHz）")]
    if m2 is not None:
        l2 = _u(float(m2.group(1)), m2.group(2), {"uh": 1e-6, "μh": 1e-6, "mh": 1e-3, "h": 1})
        c2 = _u(float(m2.group(3)), m2.group(4), {"uf": 1e-6, "μf": 1e-6, "nf": 1e-9, "pf": 1e-12})
        f0_2 = 1 / (2 * math.pi * math.sqrt(l2 * c2))
        rows.append(("第二组 L/C", f"{_num(float(m2.group(1)))} {m2.group(2)} / {_num(float(m2.group(3)))} {m2.group(4)}"))
        rows.append(("第二组截止频率", f"{_num(f0_2 / 1e3, 2)} kHz（{_num(f0_2 / 1e6, 3)} MHz）"))
    rows += [("特征阻抗 Z0", f"{_num(z0, 1)} Ω"),
             ("品质因数 Q", f"{_num(q, 2)}（=Z0/ZL）" if q else "-"),
             ("响应", "巴特沃斯平坦" if q is not None and abs(q - 0.707) < 0.01 else ("出现过冲" if q and q > 1 else "常规")),
             ("标准", "f0=1/(2π√LC)；Z0=√(L/C)")]
    return {"rows": rows, "note": "Q≈0.707→巴特沃斯平坦；Q>1→截止频率附近过冲。实际电感存在 SRF，高于 SRF 时呈容性"}


# ===== 555 定时器 =====

def _calc_555(q: str) -> dict:
    """非稳态 f=1.44/((R1+2R2)C)；单稳态 t=1.1RC"""
    r1 = _first([r'(?:R1)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:k|kΩ|Ω|欧)'], q)
    r2 = _first([r'(?:R2)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:k|kΩ|Ω|欧)'], q)
    c_v, c_u = _val_unit(q, r'(?:电容|C)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(μF|uF|nF|pF)')
    r = _first([r'(?:定时电阻|R)\s*[=:]?\s*(\d+(?:\.\d+)?)\s*(?:k|kΩ|Ω|欧)'], q)
    if c_v is None or (r1 is None and r is None):
        return {"rows": [("提示", "555 需要 R1+R2+C（非稳态）或 R+C（单稳态）。如「555 R1=1k R2=10k C=0.1μF 频率多少」「555 定时1秒 R=10k C多大」」")], "note": ""}
    if r1 is not None and r2 is not None:
        r1_o, r2_o = (r1 * 1e3, r2 * 1e3) if r1 < 10 else (r1, r2)
        c = c_v * {"uf": 1e-6, "μf": 1e-6, "nf": 1e-9, "pf": 1e-12}[c_u.lower()]
        f = 1.44 / ((r1_o + 2 * r2_o) * c)
        th, tl = 0.693 * (r1_o + r2_o) * c, 0.693 * r2_o * c
        duty = (r1_o + r2_o) / (r1_o + 2 * r2_o)
        rows = [("计算目标", "555 非稳态振荡器"),
                ("R1 / R2 / C", f"{_num(r1_o / 1e3, 2)}k / {_num(r2_o / 1e3, 2)}k / {_num(c_v)} {c_u}"),
                ("频率 f", f"{_num(f, 1)} Hz（{_num(f / 1e3, 2)} kHz）"),
                ("高电平 tH", f"{_num(th * 1e3, 1)} ms"),
                ("低电平 tL", f"{_num(tl * 1e3, 1)} ms"),
                ("占空比", f"{_num(duty * 100, 1)}%（恒 >50%）"),
                ("标准", "f=1.44/((R1+2R2)C)")]
        return {"rows": rows, "note": "占空比恒 >50%；要 <50% 需在 R2 上并二极管。常数 1.44≈1/ln2，0.693=ln2"}
    r_o = (r * 1e3) if r is not None and r < 10 else r
    c = c_v * {"uf": 1e-6, "μf": 1e-6, "nf": 1e-9, "pf": 1e-12}[c_u.lower()]
    t = 1.1 * r_o * c
    rows = [("计算目标", "555 单稳态定时"),
            ("R / C", f"{_num(r_o)} Ω / {_num(c_v)} {c_u}"),
            ("定时宽度 t", f"{_num(t * 1e3, 2)} ms（1.1RC）"),
            ("标准", "t=1.1RC")]
    return {"rows": rows, "note": "单稳态 t=1.1RC（1.1≈ln3），电容从 0V 充到 2/3Vcc"}


def _detect_calc_type(q: str) -> str:
    """判断用户需要哪个子计算器"""
    if any(w in q for w in ["综合校验", "布局校验", "组合校验", "空间校验", "净空校验", "走线空间校验"]):
        return "layout"
    if ("lc" in q.lower() and "滤" in q) or "LC滤波" in q or "LC 滤波" in q:
        return "lc_filter"
    if "谐振" in q:
        return "lc_resonance"
    # 晶振/负载电容 优先于 via_parasitic（晶振匹配题也含"寄生电容"字样）
    if "晶振" in q or "负载电容" in q:
        return "crystal"
    # Snubber 优先于 via_parasitic（双频法题含"寄生电容/参数"字样）
    if any(w in q for w in ["吸收电路", "吸收电阻", "snubber", "Snubber", "振铃"]):
        return "snubber"
    # 过孔寄生：需过孔/板厚等语境，避免劫持晶振、Snubber 的"寄生电容"
    if any(w in q for w in ["寄生电容", "寄生电感", "过孔寄生", "寄生参数", "via寄生"]) \
            and any(w in q for w in ["过孔", "via", "VIA", "板厚", "焊盘", "反焊盘", "孔径", "钻孔"]):
        return "via_parasitic"
    if any(w in q for w in ["感抗", "容抗", "电抗"]):
        return "reactance"
    if any(w in q for w in ["热过孔", "散热过孔", "过孔热阻", "过孔阵列"]):
        return "via_thermal"
    if any(w in q for w in ["铜排", "汇流条", "母线", "busbar", "Busbar"]):
        return "copper_busbar"
    if "三相" in q or "3相" in q or "three-phase" in q.lower():
        return "three_phase"
    # Buck 选型优先于 pdn 的"纹波"（Buck 题常含"输出/输入纹波"字样）。
    # 注意：①不把"纹波电流"作为独立触发词（PDN 题会误伤）；②含"效率/损耗"的 Buck 题走 smps（拓扑名≠选型需求）
    if any(w in q for w in ["buck", "Buck", "降压", "最小电感", "输出电容", "输入电容"]) \
            and not any(w in q for w in ["效率", "损耗", "Rds", "Qg", "导通"]) \
            or ("占空比" in q and any(w in q for w in ["输入", "fsw", "开关频率"])):
        return "buck"
    if any(w in q for w in ["目标阻抗", "去耦", "pdn", "PDN", "电源完整性", "纹波"]):
        return "pdn"
    # 开关电源：若仅为走线语境（"开关电源中走线承载多大电流"）且无损耗参数 → 不走 smps
    if any(w in q for w in ["开关电源", "效率", "导通损耗", "开关损耗", "mos损耗", "续流二极管损耗"]):
        has_trace_ctx = any(w in q for w in ["走线", "线宽", "载流", "铜厚", "承载", "温升"])
        has_smps_ctx = any(w in q for w in ["效率", "损耗", "Rds", "Qg", "导通", "续流", "开关频率"])
        if not (has_trace_ctx and not has_smps_ctx):
            return "smps"
    if any(w in q for w in ["额定电流", "变压器", "电机电流", "电流速算"]):
        return "rated_current"
    if _RE_2152.search(q):
        return "trace2152"
    if any(w in q for w in ["差分阻抗", "差分对", "差分", "ziff"]):
        return "differential"
    if any(w in q for w in ["爬电", "电气间隙", "creepage", "clearance", "安规间距", "绝缘间距"]):
        return "creepage"
    if any(w in q for w in ["延迟", "传播时延", "走线时延", "1/4", "四分之一波长"]):
        return "signal"
    # 注意排除 RS-485 语境：RS-485 题也含"特征阻抗"，若命中 impedance 会算成微带阻抗
    if any(w in q for w in ["阻抗", "微带", "带状", "特征阻抗", "50欧", "50Ω", "欧姆匹配"]) \
            and "rs485" not in q.lower() and "rs-485" not in q.lower():
        return "impedance"
    if any(w in q for w in ["过孔", "via", "VIA", "孔径", "钻孔"]):
        return "via"
    if "趋肤" in q or "趋肤效应" in q:
        return "skin"
    if any(w in q for w in ["结温", "热阻", "θja", "θJA", "tj", "TJ", "散热"]):
        return "thermal"
    if any(w in q for w in ["稳压管", "稳压二极管", "稳压值", "齐纳", "zener", "Zener"]):
        return "zener"
    if "限流" in q or ("led" in q.lower()) or "发光二极管" in q:
        return "led"
    if "分压" in q or re.search(r'从\s*\d+(?:\.\d+)?\s*[Vv].*?(?:得到|降到|变到|输出)\s*\d+(?:\.\d+)?\s*[Vv]', q):
        return "divider"
    if "时间常数" in q or re.search(r'\brc\b', q.lower()):
        return "rc"
    # ===== 批次3：常用电路类 =====
    if "555" in q or "定时器" in q:
        return "timer_555"
    if "串联" in q:
        return "series_capacitor"
    if "并联" in q:
        return "parallel_resistance"
    if any(w in q for w in ["惠斯通", "电桥"]):
        return "wheatstone"
    if any(w in q for w in ["差动放大", "差分放大"]):
        return "diff_amp"
    if any(w in q for w in ["三极管", "bjt", "BJT", "静态工作点", "工作点"]):
        return "transistor_bias"
    if any(w in q for w in ["稳压管", "齐纳", "zener", "Zener"]):
        return "zener"
    if "lm317" in q.lower() or "lm1117" in q.lower():
        return "lm317"
    if "mc34063" in q.lower():
        return "mc34063"
    if "i2c" in q.lower() or "上拉电阻" in q:
        return "i2c_pullup"
    if "感应加热" in q or "加热" in q or "钢件" in q or "工件" in q:
        return "induction_heating"
    if "超级电容" in q:
        return "supercapacitor"
    if "充电" in q and "电池" in q:
        return "battery_charging"
    if "iot" in q.lower() or "低功耗" in q or "lora" in q.lower() or "发射" in q \
            or ("采样" in q and any(w in q for w in ["电池", "休眠", "睡眠", "续航", "mA", "mAh"])) \
            or ("周期" in q and "电池" in q):
        return "iot_battery"
    if "电池" in q and any(w in q for w in ["续航", "寿命", "能用多久"]):
        return "battery_life"
    if "压降" in q and "线" in q and not any(w in q for w in ["mil", "oz", "铜厚", "线宽", "内层", "外层", "走线"]):
        return "wire_drop"
    if "lc" in q.lower() and "滤" in q:
        return "lc_filter"
    if any(w in q for w in ["谐振", "lc 谐振"]):
        return "lc_resonance"
    if any(w in q for w in ["感抗", "容抗", "电抗"]):
        return "reactance"
    if any(w in q for w in ["波长", "频率波长", "天线"]):
        return "frequency_wavelength"
    if any(w in q for w in ["驻波", "回波损耗", "vswr", "VSWR"]):
        return "vswr"
    if any(w in q for w in ["占空比", "pwm", "PWM"]):
        return "pwm"
    if any(w in q for w in ["金属", "重量"]):
        return "metal_weight"
    if any(k in q.lower() for k in ["dbuv", "dbmv", "dbm", "dbw", "dbµv", "emc"]):
        return "emc_convert"
    if any(w in q for w in ["3w", "3W", "串扰", "走线间距"]):
        return "crosstalk"
    if any(w in q for w in ["运放", "opamp", "OpAmp", "放大器"]):
        return "opamp"
    if "sallen" in q.lower() or "运放滤波" in q or "低通" in q or "截止频率" in q:
        return "opamp_filter"
    if ("adc" in q.lower() or "分辨率" in q or "位数" in q) \
            and not any(w in q for w in ["分压", "R1", "R2", "接GND", "得到"]):
        return "adc"
    if "rs485" in q.lower() or "rs-485" in q.lower() or "485" in q:
        return "rs485"
    if "晶振" in q or "负载电容" in q:
        return "crystal"
    if "焊盘" in q or "smd" in q.lower():
        return "smd_pad"
    if any(w in q for w in ["欧姆定律", "电功率", "多少瓦", "功率多少"]):
        return "electric_power"
    return "trace"


# ===== 走线格式化（保留，含三种方向） =====

def format_result(direction: str, **kw) -> str:
    """格式化走线计算结果（width / current / check 三方向）"""
    lines = []
    num = 1

    def add(label, text):
        nonlocal num
        lines.append(f"{num}. 【{label}】{text}")
        num += 1

    if direction == "width":
        add("计算目标", "已知电流 → 求最小走线宽度")
        add("电流", f"{kw['current']} A")
        add("铜厚", f"{kw['oz']} oz（约 {kw['oz'] * 35:.0f} μm）")
        add("温升", f"{kw['temp_rise']} ℃")
        add("所在层", _layer_note(kw["is_internal"]))
        add("最小线宽", f"{_num(kw['width_mil'])} mil（≈ {_num(kw['width_mm'])} mm）")
        margin = kw.get("margin", SAFETY_MARGIN)
        add("推荐线宽", f"{_num(kw['width_mil'] * margin)} mil（留 {_num((margin - 1) * 100, 0)}% 裕量）")
        add("标准", "IPC-2221 经验公式")
        if kw.get("length_m"):
            r, v, p = kw["resistance"]
            add("电阻", f"{_num(r * 1000, 3)} mΩ")
            add("压降", f"{_num(v, 3)} V")
            add("功率损耗", f"{_num(p, 3)} W")
            add("提示", "长度不影响载流，只影响压降/损耗")
    elif direction == "current":
        add("计算目标", "已知线宽 → 求最大载流")
        add("线宽", f"{kw['width']} {kw['width_unit']}（≈ {_num(kw['width_mil'])} mil）")
        add("铜厚", f"{kw['oz']} oz（约 {kw['oz'] * 35:.0f} μm）")
        add("温升", f"{kw['temp_rise']} ℃")
        add("所在层", _layer_note(kw["is_internal"]))
        add("最大载流", f"{_num(kw['max_current'])} A")
        add("安全电流", f"{_num(kw['max_current'] * 0.8)} A（取 80% 裕量）")
        add("标准", "IPC-2221 经验公式")
        if not kw["is_internal"] and abs(kw["oz"] - 1.0) < 1e-9 and abs(kw["temp_rise"] - 10) < 1e-9:
            ref = _ampacity_ref(kw["width_mil"])
            if ref is not None:
                add("官网速查表", f"{_num(ref)} A（IPC-2221 官方曲线参考，与公式 ±10% 差异）")
    elif direction == "check":
        add("计算目标", "校验给定线宽能否承载目标电流")
        add("电流", f"{kw['current']} A")
        add("线宽", f"{kw['width']} {kw['width_unit']}（≈ {_num(kw['width_mil'])} mil）")
        add("铜厚", f"{kw['oz']} oz（约 {kw['oz'] * 35:.0f} μm）")
        add("温升", f"{kw['temp_rise']} ℃")
        add("所在层", _layer_note(kw["is_internal"]))
        add("该线宽最大载流", f"{_num(kw['max_current'])} A")
        add("结论", kw["verdict"])
        add("标准", "IPC-2221 经验公式")
        if kw.get("length_m"):
            r, v, p = kw["resistance"]
            add("电阻", f"{_num(r * 1000, 3)} mΩ")
            add("压降", f"{_num(v, 3)} V")
            add("功率损耗", f"{_num(p, 3)} W")
            add("提示", "长度不影响载流，只影响压降/损耗")

    lines.append("---")
    lines.append("提示：IPC-2221 结果偏保守，实际受板层堆叠、临近铺铜、散热影响，建议与板厂确认")
    return "\n".join(lines)


# ===== 参数提取 =====

def _extract_current(query: str) -> float | None:
    m = _RE_CURRENT.search(query)
    return float(m.group(1)) if m else None


def _extract_oz(query: str) -> float:
    m = _RE_OZ.search(query)
    return float(m.group(1)) if m else 1.0


def _extract_temp_rise(query: str) -> float:
    m = _RE_TEMP.search(query)
    return float(m.group(1)) if m else DEFAULT_TEMP_RISE


def _extract_margin(query: str) -> float:
    """提取安全裕量百分比（如「裕量30%」→ 1.3），默认 1.2"""
    m = re.search(r'(?:裕量|余量|安全系数)\s*(\d+(?:\.\d+)?)\s*%', query)
    return 1 + float(m.group(1)) / 100 if m else SAFETY_MARGIN


def _extract_layer(query: str) -> str:
    return "internal" if "内层" in query.lower() else "external"


def _extract_width(query: str) -> tuple[float | None, str]:
    # 优先带"宽/线宽/宽度"标签的值，避免把"50mm长、0.5mm宽"里的 50mm 误当线宽
    m = re.search(r'(?:线宽|宽度|宽)\s*(?:为|约|是)?\s*(\d+(?:\.\d+)?)\s*(mm|毫米|mil)'
                  r'|(\d+(?:\.\d+)?)\s*(mm|毫米|mil)\s*(?:宽|线宽|宽度)', query, re.IGNORECASE)
    if not m:
        m = _RE_WIDTH.search(query)
    if not m:
        return None, "mm"
    if m.group(1) is not None:
        return float(m.group(1)), ("mil" if m.group(2) == "mil" else "mm")
    return float(m.group(3)), ("mil" if m.group(4) == "mil" else "mm")


def _extract_length(query: str) -> tuple[float | None, str]:
    m = _RE_LENGTH.search(query)
    if not m:
        return None, "m"
    return float(m.group(1)), m.group(2)


# ===== 主入口 =====

def _handle_impl(query: str) -> dict:
    q = query.strip()
    if not q:
        return {"answer": "请输入 PCB 设计计算需求，例如「3A 电流 1oz 铜外层走线要多宽」", "source": ""}

    ctype = _detect_calc_type(q)

    # 非走线计算器
    if ctype != "trace":
        fn = {
            "trace2152": _calc_trace2152,
            "differential": _calc_differential,
            "creepage": _calc_creepage,
            "layout": _calc_layout_check,
            "impedance": _calc_impedance,
            "via": _calc_via,
            "skin": _calc_skin,
            "signal": _calc_signal,
            "thermal": _calc_thermal,
            "led": _calc_led,
            "divider": _calc_divider,
            "rc": _calc_rc,
            "copper_busbar": _calc_copper_busbar,
            "three_phase": _calc_three_phase,
            "snubber": _calc_snubber,
            "pdn": _calc_pdn,
            "smps": _calc_smps,
            "buck": _calc_buck,
            "rated_current": _calc_rated_current,
            "via_parasitic": _calc_via_parasitic,
            "via_thermal": _calc_via_thermal,
            "lc_resonance": _calc_lc_resonance,
            "reactance": _calc_reactance,
            "supercapacitor": _calc_supercapacitor,
            "battery_life": _calc_battery_life,
            "battery_charging": _calc_battery_charging,
            "iot_battery": _calc_iot_battery,
            "wire_drop": _calc_wire_drop,
            "induction_heating": _calc_induction_heating,
            "opamp": _calc_opamp,
            "opamp_filter": _calc_opamp_filter,
            "diff_amp": _calc_diff_amp,
            "wheatstone": _calc_wheatstone,
            "transistor_bias": _calc_transistor_bias,
            "zener": _calc_zener,
            "lm317": _calc_lm317,
            "mc34063": _calc_mc34063,
            "i2c_pullup": _calc_i2c_pullup,
            "parallel_resistance": _calc_parallel_resistance,
            "series_capacitor": _calc_series_capacitor,
            "electric_power": _calc_electric_power,
            "adc": _calc_adc,
            "rs485": _calc_rs485,
            "crystal": _calc_crystal,
            "smd_pad": _calc_smd_pad,
            "vswr": _calc_vswr,
            "frequency_wavelength": _calc_frequency_wavelength,
            "pwm": _calc_pwm,
            "metal_weight": _calc_metal_weight,
            "emc_convert": _calc_emc_convert,
            "crosstalk": _calc_crosstalk,
            "lc_filter": _calc_lc_filter,
            "timer_555": _calc_555,
        }[ctype]
        try:
            res = fn(q)
        except Exception as e:
            logger.exception(f"PCB 计算器 [{ctype}] 异常: {e}")
            return {"answer": f"这个计算需要的信息不完整，请补充参数后重试（{str(e)[:50]}）", "source": ""}
        answer = _build_answer(res["rows"], res["note"]) if res["rows"] else "请补充计算参数"
        return {"answer": answer, "source": f"PCB计算({ctype})"}

    # ===== 走线计算（原逻辑） =====
    current = _extract_current(q)
    oz = _extract_oz(q)
    temp_rise = _extract_temp_rise(q)
    is_internal = _extract_layer(q) == "internal"
    width, width_unit = _extract_width(q)
    length, length_unit = _extract_length(q)

    # 温度对压降的影响（无需宽度/电流参数）：压降 + 从 A℃ 升到 B℃
    m_dv0 = re.search(r'压降\s*(?:为|是|约)?\s*(\d+(?:\.\d+)?)\s*(mV|V)', q)
    m_t0 = re.search(r'(\d+(?:\.\d+)?)\s*(?:℃|°\s*C|°)\s*时', q)
    m_t1 = re.search(r'(?:升到|升至|升温到)\s*(\d+(?:\.\d+)?)\s*[℃°C]', q)
    if m_dv0 and m_t0 and m_t1 and ("温度系数" in q or "%" in q):
        dv0 = float(m_dv0.group(1)) * (1e-3 if m_dv0.group(2) == "mV" else 1.0)
        t0, t1 = float(m_t0.group(1)), float(m_t1.group(1))
        alpha = 0.004
        dv1 = dv0 * (1 + alpha * (t1 - t0))
        rows = [
            ("计算目标", "温度对走线压降的影响"),
            ("初始压降", f"{_num(dv0 * 1000, 1)} mV（{_num(t0, 0)}℃）"),
            ("温度变化", f"{_num(t0, 0)}℃ → {_num(t1, 0)}℃，ΔT={_num(t1 - t0, 0)}℃"),
            ("电阻倍率", f"{_num(1 + alpha * (t1 - t0), 3)}×（铜 α≈0.004/℃）"),
            ("新压降", f"{_num(dv1 * 1000, 1)} mV（{_num(dv1, 3)} V）"),
            ("标准", "R(T)=R₀(1+α·ΔT)，压降同比变化"),
        ]
        return {"answer": _build_answer(rows, "温度升高铜电阻增大，压降随温度线性增加。铜 α≈0.4%/℃"), "source": "PCB计算(trace温度压降)"}

    def _to_meters(v, unit):
        return {"m": 1.0, "cm": 0.01, "厘米": 0.01, "mm": 0.001, "毫米": 0.001,
                "inch": 0.0254, "英寸": 0.0254}.get(unit, 1.0) * v

    if current is not None and width is not None:
        width_mil = width if width_unit == "mil" else width / MIL_TO_MM
        max_cur = calc_max_current(width_mil, oz, temp_rise, is_internal)
        if current <= max_cur:
            verdict = f"✅ 可以承载 {current}A（该线宽最大 {_num(max_cur)}A，余量 {_num(max_cur / current)}×）"
        else:
            need_mil = calc_min_width(current, oz, temp_rise, is_internal)
            verdict = f"❌ 不够！{current}A 需要最小 {_num(need_mil)}mil，当前 {_num(width_mil)}mil 只能过 {_num(max_cur)}A"
        kw = dict(direction="check", current=current, width=width,
                  width_unit=width_unit, width_mil=width_mil, oz=oz,
                  temp_rise=temp_rise, is_internal=is_internal,
                  max_current=max_cur, verdict=verdict)
        if length is not None:
            kw["length_m"] = _to_meters(length, length_unit)
            kw["resistance"] = calc_resistance_drop(width_mil, oz, kw["length_m"], current)
        return {"answer": format_result(**kw), "source": "PCB计算(trace)"}

    if width is not None and current is None:
        width_mil = width if width_unit == "mil" else width / MIL_TO_MM
        max_cur = calc_max_current(width_mil, oz, temp_rise, is_internal)
        kw = dict(direction="current", width=width, width_unit=width_unit,
                  width_mil=width_mil, oz=oz, temp_rise=temp_rise,
                  is_internal=is_internal, max_current=max_cur)
        return {"answer": format_result(**kw), "source": "PCB计算(trace)"}

    if current is not None:
        width_mil = calc_min_width(current, oz, temp_rise, is_internal)
        kw = dict(direction="width", current=current, oz=oz,
                  temp_rise=temp_rise, is_internal=is_internal,
                  width_mil=width_mil, width_mm=width_mil * MIL_TO_MM,
                  margin=_extract_margin(q))
        if length is not None:
            kw["length_m"] = _to_meters(length, length_unit)
            kw["resistance"] = calc_resistance_drop(width_mil, oz, kw["length_m"], current)
        return {"answer": format_result(**kw), "source": "PCB计算(trace)"}

    return {
        "answer": (
            "我可以做这些 PCB 计算：\n\n"
            "1. 走线：算线宽「3A 1oz 外层要多宽」/ 算载流「0.5mm 能过多少安」/ 校验「3A 20mil 够吗」/ 压降\n"
            "2. 阻抗：「50Ω 微带 FR4 介质 1mm 要多宽」或「0.5mm 微带阻抗多少」\n"
            "3. 过孔：「0.3mm 过孔 1oz 孔壁 能过多少安」「过孔 并联几个」\n"
            "4. 趋肤：「1MHz 趋肤深度」\n"
            "5. 信号：「FR4 微带 走 10cm 延迟多少」/「100MHz 波长」\n"
            "6. 热：「功耗 5W 热阻 30℃/W 结温多少」\n"
            "7. LED 限流电阻：「5V 白LED 20mA 电阻多少」\n"
            "8. 分压：「5V 分压 R1=10k R2=10k 输出多少」\n"
            "9. RC：「10kΩ 1μF 时间常数」\n"
            "10. 差分对：「100Ω 差分微带 FR4 介质 0.4mm 间距 0.3mm 要多宽」\n"
            "11. IPC-2152：「IPC-2152 5A 1oz 外层要多宽」\n"
            "12. 安规：「380V 爬电距离」/「2.5kV 电气间隙」"
        ),
        "source": "",
    }


# ===== 注册技能类 =====

@register
class PCBCalcSkill(BaseSkill):
    """PCB 设计计算：走线/阻抗/过孔/热/通用电路（秒回）"""
    name = "PCB设计计算"
    description = ("PCB 设计计算：走线线宽/载流/压降（IPC-2221/2152）、阻抗（IPC-2141 微带/带状/差分对）、"
                   "过孔载流、趋肤深度、走线延迟/波长、结温散热、安规爬电距离、LED限流/分压/RC、布局综合校验，秒回")
    priority = 90  # 低于故障代码(100)，高于 RAG Agent(50)

    @classmethod
    def match(cls, query: str) -> bool:
        return _is_pcb_calc_query(query)

    @classmethod
    def handle(cls, query: str, **kwargs) -> dict:
        return _handle_impl(query)


# 向后兼容
handle = _handle_impl

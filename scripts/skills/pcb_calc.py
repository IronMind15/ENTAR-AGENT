"""
PCB 设计计算技能 — 走线 / 阻抗 / 过孔 / 热 / 通用电路计算

识别用户在 PCB 设计过程中的计算需求，纯本地公式计算，不调 LLM，秒回。

支持的子计算器（按关键词自动路由）：
  1. trace       走线：IPC-2221 线宽 / 载流 / 压降（已知电流求宽度、已知宽度求载流、校验）
  2. trace2152   走线：IPC-2152 精确载流（ΔT^0.5·A^0.65 近似，与 IPC-2221 对照）
  3. impedance   阻抗：IPC-2141 微带线 / 带状线特征阻抗，支持已知目标阻抗反推线宽
  4. differential差分对：Zdiff = 2×Z0×(1−0.48e^(−0.96s/h))，支持反推线宽（USB 90Ω/以太网 100Ω）
  5. via         过孔：载流能力 / 电阻 / 并联过孔数（孔壁环形截面）
  6. skin        趋肤深度：δ ≈ 66/√f mm（铜）
  7. signal      信号：走线延迟（ps/inch）与波长 / 1/4 波长
  8. thermal     热设计：结温 TJ = TA + θJA × P，可反推允许功耗
  9. led         LED 限流电阻：R = (V - Vf) / I
  10. divider    电阻分压：Vout = Vin × R2 / (R1 + R2)
  11. rc         RC 时间常数：τ = RC，截止频率 f = 1 / (2πRC)
  12. creepage   安规：IEC-60664-1 电气间隙 / 爬电距离（查表 + 线性插值）
  13. layout     综合校验：一次输入电流+铜厚+电压+材料组+可用宽度，同时输出走线约束+安规间距+合计占用并判定冲突，冲突时给设计建议

核心公式（走线）：
  I = k × ΔT^0.44 × A^0.725（IPC-2221）
  A = 宽(mil) × 厚(mil)，k = 0.048 外层 / 0.024 内层
  1 oz 铜 ≈ 35 μm ≈ 1.378 mil
"""

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
_RE_TEMP = re.compile(r'温升\s*(\d+)')
_RE_WIDTH = re.compile(r'(\d+(?:\.\d+)?)\s*(mm|毫米|mil)')
_RE_LENGTH = re.compile(r'(\d+(?:\.\d+)?)\s*(cm|厘米|m\b|米|inch|英寸)')
_RE_MM = re.compile(r'(\d+(?:\.\d+)?)\s*(?:mm|毫米)')            # 毫米值
_RE_MIL = re.compile(r'(\d+(?:\.\d+)?)\s*mil')                    # mil 值
_RE_VOLT = re.compile(r'(\d+(?:\.\d+)?)\s*V\b', re.IGNORECASE)

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
    return _extract_labeled(_RE_H_LABEL, q)


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
    """差分阻抗（IPC-2141 近似）。微带：2Z0(1−0.48e^(−0.96s/h))；带状：2Z0(1−0.37e^(−2.9s/b))"""
    z0 = _stripline_z(w_mm, hb_mm, t_mm, er) if stripline \
        else _microstrip_z(w_mm, hb_mm, t_mm, er)
    if stripline:
        factor = 1 - 0.37 * math.exp(-2.9 * s_mm / hb_mm)
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
        b = _first([r'(?:两平面间距|间距|b\s*=)\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)'], q)
        if b is None:
            b = 1.0  # 默认 1mm
    else:
        h = _first([r'(?:介质|层厚|到地|到参考平面|h\s*=)\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)',
                    r'(\d+(?:\.\d+)?)\s*(?:mm|毫米)\s*(?:介质|层高)'], q)
        if h is None:
            h = 1.0

    # 目标阻抗（反推线宽）
    target_z = _first([r'(?:目标|匹配)?\s*(\d+(?:\.\d+)?)\s*(?:Ω|欧|ohm)', r'(\d+(?:\.\d+)?)\s*欧姆'], q)
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
    oz = _first([r'(\d+(?:\.\d+)?)\s*oz'], q)
    um = _first([r'(\d+(?:\.\d+)?)\s*(?:μm|um|微米)'], q)
    if oz is not None:
        wall_mil = oz * OZ_TO_MIL
    elif um is not None:
        wall_mil = um / 1000 / MIL_TO_MM
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
            "过孔阵列载流受出入口铜箔限制，需 ≤ 相连走线载流。并联经验：N 孔 ≈ 单孔 × N × 0.9")
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
    rows = [
        ("计算目标", "铜导体的趋肤深度（高频时电流集中在表面）"),
        ("频率", f"{f} {m.group(2)}"),
        ("趋肤深度 δ", f"{delta_mm * 1000:.1f} μm（{_num(delta_mm, 4)} mm）"),
        ("建议", "线宽/孔径 ≥ 2×δ 时高频电阻显著增大，大电流高频走线需加宽或并联"),
        ("标准", "δ ≈ 66/√f mm（20℃ 铜）"),
    ]
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
    ta = _first([r'(?:环境温度|环境|TA|Ta|ta)\s*(\d+(?:\.\d+)?)', r'(\d+(?:\.\d+)?)\s*℃\s*(?:环境|室温)'], q)
    theta = _first([r'(\d+(?:\.\d+)?)\s*(?:℃/W|K/W|°C/W)'], q)
    power = _first([r'(\d+(?:\.\d+)?)\s*W\b'], q)

    rows = [("计算目标", "结温 TJ = TA + θJA × P")]
    if ta is None:
        rows.append(("环境温度 TA", "未提供，用 25℃（室温）"))
        ta = 25.0
    if theta is not None:
        rows.append(("热阻 θJA", f"{theta} ℃/W"))
    if power is not None:
        rows.append(("功耗 P", f"{power} W"))

    # 完整计算
    if theta is not None and power is not None:
        tj = ta + theta * power
        rows.append(("结温 TJ", f"{_num(tj)} ℃"))
        rows.append(("判断", "✅ 可接受" if tj <= 100 else "⚠️ 超 100℃，需加散热/降低功耗"))
        rows.append(("反推允许功耗", f"若限 100℃：Pmax = {_num((100 - ta) / theta)} W"))
    else:
        rows.append(("提示", "需提供热阻 θJA（℃/W）和功耗 P（W）。如「功耗 5W 热阻 30℃/W 结温多少」"))

    rows.append(("标准", "热阻模型（参考器件 datasheet θJA）"))
    return {"rows": rows, "note": "θJA 来自器件 datasheet（取决于封装、铺铜、气流）。结温超过 datasheet 上限（通常 125℃）会降寿"}


# ===== 通用小电路 =====

def _calc_led(q: str) -> dict:
    v = _first([r'(?:电源|供电|VCC)\s*(\d+(?:\.\d+)?)\s*V', r'(\d+(?:\.\d+)?)\s*V\b'], q)
    vf = _first([r'(?:压降|Vf|vf)\s*(\d+(?:\.\d+)?)\s*V', r'(\d+(?:\.\d+)?)\s*V\s*(?:压降|Vf)'], q)
    i_ma = _first([r'(\d+(?:\.\d+)?)\s*mA', r'(\d+(?:\.\d+)?)\s*(?:毫安|ma)'], q)
    if v is None or (vf is None and i_ma is None):
        return {"rows": [("提示", "LED 限流电阻需要：电源电压 + LED 压降（红~2V，白/蓝~3V）+ 电流。如「5V 供电 白LED 20mA 限流电阻」")], "note": ""}

    if vf is None:
        vf = 3.0 if any(c in q for c in "白蓝") else 2.0
    if i_ma is None:
        i_ma = 20.0
    i = i_ma / 1000
    r = (v - vf) / i
    rows = [
        ("计算目标", "LED 限流电阻 R = (V - Vf) / I"),
        ("电源电压", f"{v} V"),
        ("LED 压降 Vf", f"{vf} V"),
        ("电流", f"{i_ma:.0f} mA"),
        ("限流电阻", f"{_num(r)} Ω（取标准值 {_num(r // 10 * 10)}Ω 或 {_num(r // 100 * 100)}Ω）"),
        ("电阻功率", f"{_num(i ** 2 * r, 3)} W（建议选 2 倍余量）"),
        ("标准", "欧姆定律"),
    ]
    return {"rows": rows, "note": "红/黄 LED 约 1.8~2.2V，白/蓝/绿约 2.8~3.4V，选标准阻值取邻近值并核算电流"}


def _calc_divider(q: str) -> dict:
    vin = _first([r'(?:输入|Vin|vin)\s*(\d+(?:\.\d+)?)\s*V', r'(\d+(?:\.\d+)?)\s*V\s*(?:输入|分压)'], q)
    r1 = _first([r'r1\s*=\s*(\d+(?:\.\d+)?)', r'(\d+(?:\.\d+)?)\s*(?:kΩ|KΩ|k欧)'], q)
    r2 = _first([r'r2\s*=\s*(\d+(?:\.\d+)?)', r'(\d+(?:\.\d+)?)\s*(?:Ω|欧|ohm)'], q)
    if vin is None or r1 is None or r2 is None:
        return {"rows": [("提示", "分压需要：输入电压 + R1 + R2。如「5V 分压 R1=10k R2=10k 输出多少」")], "note": ""}
    vout = vin * r2 / (r1 + r2)
    rows = [
        ("计算目标", "电阻分压 Vout = Vin × R2 / (R1 + R2)"),
        ("输入 Vin", f"{vin} V"),
        ("R1 / R2", f"{r1} kΩ / {r2} kΩ"),
        ("输出 Vout", f"{_num(vout, 3)} V"),
        ("标准", "欧姆定律"),
    ]
    return {"rows": rows, "note": "注意：R1+R2 并联于负载会拉低输出，阻值需远小于负载阻抗"}


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
        ("计算目标", "RC 时间常数 / 截止频率"),
        ("电阻", f"{r} {'kΩ' if r_mult == 1e3 else 'MΩ'}"),
        ("电容", f"{c_val} {c_unit}"),
        ("时间常数 τ", f"{_num(tau * 1e3, 2)} ms（{_num(tau * 1e6, 1)} μs）"),
        ("截止频率 f", f"{_num(fc, 2)} Hz"),
        ("标准", "τ = RC，f = 1/(2πRC)"),
    ]
    return {"rows": rows, "note": "τ 为充放电到 63% 的时间；截止频率指增益 -3dB 处。去耦电容搭配常用此估算谐振频率"}


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


# ===== 计算类型路由 =====

def _detect_calc_type(q: str) -> str:
    """判断用户需要哪个子计算器"""
    if any(w in q for w in ["综合校验", "布局校验", "组合校验", "空间校验", "净空校验", "走线空间校验"]):
        return "layout"
    if _RE_2152.search(q):
        return "trace2152"
    if any(w in q for w in ["差分阻抗", "差分对", "差分", "ziff"]):
        return "differential"
    if any(w in q for w in ["爬电", "电气间隙", "creepage", "clearance", "安规间距", "绝缘间距"]):
        return "creepage"
    if any(w in q for w in ["延迟", "传播时延", "走线时延", "波长", "1/4", "四分之一波长"]):
        return "signal"
    if any(w in q for w in ["阻抗", "微带", "带状", "特征阻抗", "50欧", "50Ω", "欧姆匹配"]):
        return "impedance"
    if any(w in q for w in ["过孔", "via", "VIA", "孔径", "钻孔"]):
        return "via"
    if "趋肤" in q or "趋肤效应" in q:
        return "skin"
    if any(w in q for w in ["结温", "热阻", "θja", "θJA", "tj", "TJ", "散热"]):
        return "thermal"
    if "限流" in q or ("led" in q.lower()) or "发光二极管" in q:
        return "led"
    if "分压" in q:
        return "divider"
    if "时间常数" in q or ("rc" in q.lower() and "rc" in q):
        return "rc"
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
        add("推荐线宽", f"{_num(kw['width_mil'] * SAFETY_MARGIN)} mil（留 20% 裕量）")
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


def _extract_layer(query: str) -> str:
    return "internal" if "内层" in query.lower() else "external"


def _extract_width(query: str) -> tuple[float | None, str]:
    m = _RE_WIDTH.search(query)
    if not m:
        return None, "mm"
    return float(m.group(1)), ("mil" if m.group(2) == "mil" else "mm")


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

    def _to_meters(v, unit):
        return {"m": 1.0, "cm": 0.01, "厘米": 0.01,
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
                  width_mil=width_mil, width_mm=width_mil * MIL_TO_MM)
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

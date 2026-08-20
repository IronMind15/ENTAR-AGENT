"""
工具：calc_pcb_trace
计算 PCB 走线宽度、载流能力与压降（IPC-2221 标准）。

当用户涉及 PCB 设计中的走线宽度、电流载流能力、铜厚温升关系、
压降计算等问题时使用。纯公式计算，无需联网。

复用 skills.pcb_calc 的核心计算函数，保证与技能秒回通道结果一致。
"""

import json
import logging

from scripts.tools import register

logger = logging.getLogger("tool.pcb")

DEFINITION = {
    "name": "calc_pcb_trace",
    "description": (
        "计算 PCB 走线设计参数（IPC-2221）："
        "已知电流求最小线宽，已知线宽求最大载流，以及电阻/压降/功率损耗。"
        "当用户问 PCB 走线多宽、能过多少电流、铜厚与载流关系、压降多少时使用。"
        "注意：铜排/紫铜/母线/汇流条属于铜排载流计算，不属于走线，切勿用本工具。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "current_a": {
                "type": "number",
                "description": "电流（A）。已知电流求线宽时必填。",
            },
            "width_mil": {
                "type": "number",
                "description": "走线宽度（mil）。已知线宽求载流时填。1mm≈39.37mil",
            },
            "oz": {
                "type": "number",
                "description": "铜厚（oz），常见 1/2/3，默认 1。1oz≈35μm",
            },
            "temp_rise": {
                "type": "number",
                "description": "允许温升（℃），默认 10（保守），常规 20。",
            },
            "is_internal": {
                "type": "boolean",
                "description": "是否内层走线（内层散热差，载流减半），默认 false（外层）。",
            },
            "length_m": {
                "type": "number",
                "description": "走线长度（米），可选，用于计算压降和功率损耗。",
            },
        },
        "required": [],
    },
}


def _fmt(v: float, digits: int = 3) -> float:
    return round(v, digits)


def _validate_args(args: dict) -> str | None:
    """参数范围校验。LLM 提取参数可能幻觉（如把尺寸当 oz），非法时返回错误提示。"""
    try:
        oz = args.get("oz")
        if oz is not None and not (0.2 <= float(oz) <= 10):
            return (f"铜厚 oz 取值异常（{oz}），常见 0.5~3 oz。"
                    "请确认是否误把其他尺寸当成了铜厚")
        temp_rise = args.get("temp_rise")
        if temp_rise is not None and not (1 <= float(temp_rise) <= 200):
            return f"温升取值异常（{temp_rise}℃），应在 1~200℃ 之间"
        current_a = args.get("current_a")
        if current_a is not None and float(current_a) <= 0:
            return "电流应为正值（A）"
        width_mil = args.get("width_mil")
        if width_mil is not None and float(width_mil) <= 0:
            return "线宽应为正值（mil）"
    except (TypeError, ValueError):
        return "参数必须是数字，请检查后重试"
    return None


@register(
    DEFINITION,
    sector="calc",
    display="🔧 PCB 走线计算...",
    user_desc=(
        "PCB 走线设计计算（IPC-2221）。用户问：走线宽度、载流能力、铜厚与温升、压降、功率损耗。\n"
        "已知量不全先向用户确认（电流/线宽/铜厚/温升/内外层/线长），再按参数调用。\n"
        '示例：10A 电流要多宽的线 → calc_pcb_trace(current_a=10)'
    ),
)
def execute(args: dict) -> str:
    """执行 PCB 走线计算"""
    logger.info(f"  工具调用: calc_pcb_trace({args})")

    err = _validate_args(args)
    if err:
        return json.dumps({
            "error": err,
            "hint": "请核对参数后重试。铜排/紫铜/母线/汇流条是铜排载流计算，不要用走线工具。",
        }, ensure_ascii=False)

    from scripts.skills.pcb_calc import (
        calc_min_width, calc_max_current, calc_resistance_drop,
        MIL_TO_MM,
    )

    current_a = args.get("current_a")
    width_mil = args.get("width_mil")
    oz = float(args.get("oz") or 1)
    temp_rise = float(args.get("temp_rise") or 10)
    # is_internal 可能是 JSON 布尔或字符串，统一转 bool（防 "false" 字符串真值陷阱）
    is_internal = str(args.get("is_internal")).lower() in ("true", "1")
    length_m = args.get("length_m")

    try:
        result = {}
        if current_a is not None and width_mil is None:
            w = calc_min_width(float(current_a), oz, temp_rise, is_internal)
            result = {
                "mode": "width",
                "min_width_mil": _fmt(w),
                "min_width_mm": _fmt(w * MIL_TO_MM),
                "recommended_mil": _fmt(w * 1.2, 1),
                "note": "建议留 20% 裕量",
            }
            if length_m:
                r, v, p = calc_resistance_drop(
                    w, oz, float(length_m), float(current_a))
                result["resistance_ohm"] = _fmt(r, 5)
                result["voltage_drop_v"] = _fmt(v, 4)
                result["power_loss_w"] = _fmt(p, 3)
        elif width_mil is not None and current_a is None:
            max_cur = calc_max_current(
                float(width_mil), oz, temp_rise, is_internal)
            result = {
                "mode": "current",
                "max_current_a": _fmt(max_cur),
                "safe_current_a": _fmt(max_cur * 0.8),
                "note": "建议按 80% 裕量使用",
            }
        else:
            return json.dumps(
                {"error": "需提供 current_a 或 width_mil 之一"}, ensure_ascii=False
            )

        result["standard"] = "IPC-2221"
        result["is_internal"] = is_internal
        result["oz"] = oz
        result["temp_rise_c"] = temp_rise
        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        logger.exception(f"PCB 计算失败: {e}")
        return json.dumps({"error": f"PCB 计算失败: {e}"}, ensure_ascii=False)

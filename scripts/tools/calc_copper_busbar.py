"""
工具：calc_copper_busbar
铜排/母线载流计算：牛顿散热 + ASTM B187 实测表 + 电流密度反推。

当用户问铜排/紫铜/母线/汇流条能过多少电流、需要多大截面时使用。
（区别于 calc_pcb_trace：走线工具只算 PCB 走线，铜排是独立场景）
"""

import json
import logging

from tools import register

logger = logging.getLogger("tool.pcb")

DEFINITION = {
    "name": "calc_copper_busbar",
    "description": (
        "计算铜排/母线载流（牛顿散热 + ASTM B187 + 电流密度）："
        "已知铜排宽×厚求载流，或已知电流反推截面。"
        "当用户问铜排/紫铜/母线/汇流条能过多少电流、要多大的铜排时使用。"
        "注意：PCB 走线载流用 calc_pcb_trace，本工具只管铜排/母线，两者不要混用。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "material": {
                "type": "string",
                "description": "材料：紫铜（默认）/黄铜/铝",
            },
            "w_mm": {
                "type": "number",
                "description": "铜排宽度（mm），与 h_mm 一起用于求载流",
            },
            "h_mm": {
                "type": "number",
                "description": "铜排厚度（mm）",
            },
            "current_a": {
                "type": "number",
                "description": "电流（A），用于反推所需截面",
            },
            "temp_rise": {
                "type": "number",
                "description": "允许温升（℃），铜排惯例默认 65",
            },
            "ambient_temp": {
                "type": "number",
                "description": "环境温度（℃），可选",
            },
            "orient": {
                "type": "string",
                "description": "放置方向：平放（默认）/侧立",
            },
        },
        "required": [],
    },
}


@register(
    DEFINITION,
    sector="calc",
    display="🔧 铜排载流计算...",
    user_desc=(
        "铜排/母线载流计算（牛顿散热 + ASTM B187）。用户问：铜排/紫铜/母线/汇流条"
        "能过多少电流、要多大的铜排。\n"
        "注意：PCB 走线载流用 calc_pcb_trace，本工具只管铜排/母线，两者不要混用。"
    ),
)
def execute(args: dict) -> str:
    """执行铜排载流计算（复用 pcb_calc 的铜排技能）"""
    logger.info(f"  工具调用: calc_copper_busbar({args})")

    parts = ["铜排"]
    if args.get("material"):
        parts.append(args["material"])
    w, h = args.get("w_mm"), args.get("h_mm")
    if w and h:
        parts.append(f"{w}mm宽 {h}mm厚")
    if args.get("temp_rise"):
        parts.append(f"温升{args['temp_rise']}℃")
    if args.get("ambient_temp"):
        parts.append(f"环境温度{args['ambient_temp']}℃")
    if args.get("orient"):
        parts.append(args["orient"])
    if args.get("current_a"):
        parts.append(f"{args['current_a']}A")
    q = " ".join(parts)

    try:
        from skills.pcb_calc import _handle_impl
        r = _handle_impl(q)
        answer = r.get("answer", "")
        if not answer:
            return json.dumps({"error": "铜排计算无结果，请提供宽×厚 或 电流"}, ensure_ascii=False)
        return json.dumps({
            "answer": answer,
            "standard": "牛顿散热 + ASTM B187 + 电流密度",
        }, ensure_ascii=False)
    except Exception as e:
        logger.exception(f"铜排计算失败: {e}")
        return json.dumps({"error": f"铜排计算失败: {e}"}, ensure_ascii=False)

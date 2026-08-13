"""
判定层路由（v1.12.0 治本框架）

目标：判定层不再「正则要么接管要么放行」的二态，改为三态——
  确定性直行 / 歧义交还用户 / 放行。

提供两个通用机制（覆盖 bot 命令 / 技能层 / 工具 confirm 的判定互斥）：

1. `detect_domains(text)` —— 声明式领域检测（fault/standard/pcb/kanban）。
   bot 文件命令（删除/重学）在接管前先探测业务领域：命中看板 → 让位给看板技能
   （看板删除订阅自带确认机制）；空领域 → 照旧文件命令。这就是「判定互斥」，
   不再靠判定顺序巧合（v1.12.0 修复：bot 删除学习正则抢「删除看板订阅」）。

2. `ask_clarification` / `get_clarification` / `resolve_clarification` ——
   多领域歧义时反问用户，把决定权交还用户（不靠正则瞎猜）。澄清 pending
   按 user_id 隔离，600s 超时。

注意：模块顶层不 import 技能/看板模块（防循环依赖），仅在调用时按需导入。
"""

import logging
import re

logger = logging.getLogger("routing")

# 领域声明顺序：确定性高的在前（fault > standard > pcb > kanban），
# detect_domains 按此顺序返回命中列表。
_DOMAIN_KEYS = ("fault", "standard", "pcb", "kanban")


def _fault_pred(text: str) -> bool:
    from skills.error_query import FAULT_CODE_PATTERN
    return bool(FAULT_CODE_PATTERN.search(text))


def _standard_pred(text: str) -> bool:
    from skills.standards_query import STANDARD_ID_PATTERN
    return bool(STANDARD_ID_PATTERN.search(text))


def _pcb_pred(text: str) -> bool:
    from skills.pcb_calc import _is_pcb_calc_query
    return _is_pcb_calc_query(text)


def _kanban_pred(text: str) -> bool:
    from dashboard.subscription_commands import is_kanban_topic
    return is_kanban_topic(text)


# 声明式领域规则表：key → {label, pred}。pred 返回 bool 谓词。
# 新增业务领域时在此登记，detect_domains 自动纳入互斥判定。
DOMAINS = {
    "fault": {"label": "故障查询", "pred": _fault_pred},
    "standard": {"label": "标准查询", "pred": _standard_pred},
    "pcb": {"label": "PCB 计算", "pred": _pcb_pred},
    "kanban": {"label": "看板订阅", "pred": _kanban_pred},
}


def detect_domains(text: str) -> list[str]:
    """返回文本命中的业务领域 key 列表（按 DOMAINS 声明顺序）。

    - `["kanban"]`：看板操作，调用方应让位给看板技能（M1）。
    - `["kanban", "fault"]` 等多领域：操作歧义，调用方应反问澄清（M2）。
    - `[]`：非业务领域，照旧原判定。
    单领域检测失败静默跳过（不影响其他领域判定）。
    """
    t = (text or "").strip()
    if not t:
        return []
    hits = []
    for key in _DOMAIN_KEYS:
        try:
            if DOMAINS[key]["pred"](t):
                hits.append(key)
        except Exception as e:
            logger.warning(f"领域检测失败({key}): {e}")
            continue
    return hits


# ===== 操作歧义澄清（M2 / M3） =====
# v1.13.0（M3）：pending 统一收口到 pending_context（type=clarify），薄封装保留 API。
_CLARIFY_TIMEOUT = 600   # 澄清反问窗口（秒）

_CANCEL_CLARIFY_RE = re.compile(r"^(?:取消|算了|不要了|不执行|先不弄了)[。！!]?$")


def ask_clarification(user_id: str, options: list[dict],
                      ctx: dict | None = None) -> None:
    """存澄清上下文。options: [{"key", "label"}]；ctx: 触发句等透传信息。

    key 是路由目标（调用方用它决定进入哪个确认流程）；
    label 是展示给用户的可选项文案。
    """
    from pending_context import PT_CLARIFY, set as pc_set
    pc_set(user_id, PT_CLARIFY,
           {"options": list(options), "ctx": ctx or {}},
           ttl=_CLARIFY_TIMEOUT)


def get_clarification(user_id: str) -> dict | None:
    """取未过期的澄清上下文；过期/不存在返回 None。只读，不清除。"""
    from pending_context import PT_CLARIFY, get as pc_get
    entry = pc_get(user_id)
    if entry and entry["type"] == PT_CLARIFY:
        return dict(entry["payload"])
    return None


def clear_clarification(user_id: str) -> None:
    from pending_context import PT_CLARIFY, clear_type
    clear_type(user_id, PT_CLARIFY)


def render_clarification(user_id: str,
                         prompt: str = "这句话同时涉及几个操作，我不确定你想做哪一个：") -> str:
    """生成反问文案（编号 1..N + 取消提示）。无澄清上下文返回空串。"""
    entry = get_clarification(user_id)
    if not entry:
        return ""
    lines = [prompt]
    for i, opt in enumerate(entry["options"], 1):
        lines.append(f"  {i}. {opt.get('label', '')}")
    lines.append("回复编号或具体项即可；回复「取消」放弃。")
    return "\n".join(lines)


def resolve_clarification(user_id: str, text: str) -> dict | None:
    """解析澄清回复。

    Returns:
      - 选项 dict（含原 option 字段 + "_ctx"）= 用户选中某操作，调用方按 key 路由；
      - {"key": "cancel", "_ctx": ...} = 用户放弃；
      - None = 无法解析（保留澄清 pending，不阻塞后续判定）。
    """
    entry = get_clarification(user_id)
    if not entry:
        return None
    t = (text or "").strip().rstrip("。！!，,、 \t")
    if not t:
        return None

    # 取消
    if _CANCEL_CLARIFY_RE.fullmatch(t):
        clear_clarification(user_id)
        return {"key": "cancel", "_ctx": entry["ctx"]}

    # 编号：1..N
    if t.isdigit():
        n = int(t)
        if 1 <= n <= len(entry["options"]):
            clear_clarification(user_id)
            return {**entry["options"][n - 1], "_ctx": entry["ctx"]}

    # 具体项文本匹配（用户回复命中 label：回复子串含于 label 即算——
    # 「删除学习内容」命中「删除学习内容『1』」）
    for opt in entry["options"]:
        label = opt.get("label", "")
        if label and t in label:
            clear_clarification(user_id)
            return {**opt, "_ctx": entry["ctx"]}

    return None

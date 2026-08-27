"""看板实时查询工具（v1.11.0）— 实时采集组装，返回 Markdown 看板正文给 LLM 参考

v1.13.3（P4 口径统一）：查询时若当前用户仅有唯一启用看板任务，注入该任务
固定提示词快照，与定时推送同口径（同一数据源「问一次 vs 推一次」风格一致）；
多任务/无任务时退通用整理指令（dash_query 是全源合并总览，任务提示词属
任务维度无法合并）。
"""

import json

from scripts.tools import register

DEFINITION = {
    "name": "dash_query",
    "description": (
        "实时查询当前已登记的企业项目看板数据，自动适配新增、删除或更换的数据源，"
        "返回 Markdown 看板正文。用于回答『今天看板怎么样』『现在有哪些滞后项目/故障问题』等。"
        "正文末尾『## 数据来源』小节列出每个数据源及直达原文链接 [查看原文](url)——"
        "【回复用户时必须原样保留这些链接】，不得省略、不改为纯文字，否则用户无法点开原文核对"
        "（v1.11.10 审查发现转述丢链接）。注意：看板数据源 base_id 未配置时返回未配置提示。"
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


def _task_prompt_for_query(user_id: str) -> str:
    """实时查询口径与定时推送对齐：用户仅有唯一启用任务时注入其固定提示词快照。

    只读快照（不调 ensure_task_prompt，查询路径不触发写盘副作用）；多任务/
    无任务退通用指令。返回 '' 表示用 llm_pipeline 默认整理口径。
    """
    if not user_id:
        return ""
    try:
        from scripts.dashboard.subscription_store import get_subscription_store
        subs = [s for s in get_subscription_store().list_for_owner(user_id)
                if getattr(s, "enabled", 1)]
        if len(subs) != 1:
            return ""
        sub = subs[0]
        if getattr(sub, "task_prompt", "") and getattr(sub, "task_prompt_spec", None):
            return sub.task_prompt
    except Exception:
        pass
    return ""


@register(
    DEFINITION,
    sector="dash",
    display="📊 查询项目看板...",
    user_desc=(
        "实时查询项目看板。用户问：今天看板怎么样、现在有哪些滞后项目/故障问题、"
        "项目进展如何。\n"
        "调用后把返回的 Markdown 看板内容展示给用户。"
    ),
)
def execute(args: dict) -> str:
    from scripts.dashboard import service
    from scripts.tools import get_current_user_id
    user_id = get_current_user_id()
    sources = service.load_all_available_sources(user_id=user_id)
    if not sources:
        return json.dumps(
            {"error": "未配置可用的看板数据源（钉钉文档 base_id 未配置）。"
                     "请先发钉钉文档，再回复「按这几个文档做每日看板」登记数据源。"},
            ensure_ascii=False)

    from scripts.tools import get_current_staff_id
    parsed, errors = service.collect_and_parse(sources, staff_id=get_current_staff_id())
    if not parsed:
        return json.dumps(
            {"error": "看板暂无数据：" + ("；".join(errors[:3]) or "数据源未配置")},
            ensure_ascii=False)
    task_prompt = _task_prompt_for_query(user_id)
    try:
        from scripts.skills.agent import call_deepseek_json
        text = service.assemble(parsed, date_str=service.today_str(),
                                llm_func=call_deepseek_json,
                                evidence_pipeline=True, errors=errors,
                                task_prompt=task_prompt)
    except Exception:
        text = service.assemble(parsed, date_str=service.today_str(),
                                evidence_pipeline=True, errors=errors,
                                task_prompt=task_prompt)
    return json.dumps({"dashboard": text, "sources": len(parsed)}, ensure_ascii=False)

"""看板实时查询工具（v1.11.0）— 实时采集组装，返回 Markdown 看板正文给 LLM 参考"""

import json

from tools import register

DEFINITION = {
    "name": "query_dashboard",
    "description": (
        "实时查询当前已登记的企业项目看板数据，自动适配新增、删除或更换的数据源，"
        "返回 Markdown 看板正文。用于回答『今天看板怎么样』『现在有哪些滞后项目/故障问题』等。"
        "正文末尾『## 数据来源』小节列出每个数据源及直达原文链接 [查看原文](url)——"
        "【回复用户时必须原样保留这些链接】，不得省略、不改为纯文字，否则用户无法点开原文核对"
        "（v1.11.10 审查发现转述丢链接）。注意：看板数据源 base_id 未配置时返回未配置提示。"
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


@register("query_dashboard", DEFINITION)
def execute(args: dict) -> str:
    from dashboard import service
    from tools import get_current_user_id
    sources = service.load_all_available_sources(user_id=get_current_user_id())
    if not sources:
        return json.dumps(
            {"error": "未配置可用的看板数据源（钉钉文档 base_id 未配置）。"
                     "请先发钉钉文档，再回复「按这几个文档做每日看板」登记数据源。"},
            ensure_ascii=False)

    from tools import get_current_staff_id
    parsed, errors = service.collect_and_parse(sources, staff_id=get_current_staff_id())
    if not parsed:
        return json.dumps(
            {"error": "看板暂无数据：" + ("；".join(errors[:3]) or "数据源未配置")},
            ensure_ascii=False)
    try:
        from skills.agent import call_deepseek_json
        text = service.assemble(parsed, date_str=service.today_str(),
                                llm_func=call_deepseek_json,
                                evidence_pipeline=True, errors=errors)
    except Exception:
        text = service.assemble(parsed, date_str=service.today_str(),
                                evidence_pipeline=True, errors=errors)
    return json.dumps({"dashboard": text, "sources": len(parsed)}, ensure_ascii=False)

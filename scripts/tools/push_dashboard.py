"""看板主动推送工具（v1.11.0）— 立即采集组装并推送给所有启用订阅的接收人"""

import json

from tools import register

DEFINITION = {
    "name": "push_dashboard",
    "description": (
        "立即推送一次每日项目看板给所有启用订阅的接收人。"
        "用户明确要求『现在推看板』『推送看板』时使用；日常自动推送由定时任务负责。"
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


@register("push_dashboard", DEFINITION)
def execute(args: dict) -> str:
    from dashboard import service
    from dashboard.subscription_store import get_subscription_store

    sources = service.load_all_available_sources()
    if not sources:
        return json.dumps(
            {"error": "未配置可用的看板数据源（钉钉文档 base_id 未配置）。"
                     "请先发钉钉文档，再回复「按这几个文档做每日看板」登记数据源。"},
            ensure_ascii=False)

    subs = [s for s in get_subscription_store().list_enabled() if s.recipients]
    if not subs:
        return json.dumps({"error": "没有启用的看板订阅"}, ensure_ascii=False)

    # 用第一个订阅的操作人身份读取（同一应用权限一致）
    sub0 = subs[0]
    parsed, errors = service.collect_and_parse(
        sources, operator_id=sub0.owner_union_id, staff_id=sub0.owner_staff_id)
    if not parsed:
        return json.dumps(
            {"error": "看板暂无数据：" + ("；".join(errors[:3]) or "数据源未配置")},
            ensure_ascii=False)

    recipients = list({sid for s in subs for sid in s.recipients})
    text = service.assemble(parsed, title=sub0.title, date_str=service.today_str())
    ok, msg = service.push(recipients, sub0.title, text)
    if not ok:
        return json.dumps({"error": f"推送失败：{msg}"}, ensure_ascii=False)
    return json.dumps({"ok": True, "recipients": len(recipients), "sources": len(parsed)},
                      ensure_ascii=False)

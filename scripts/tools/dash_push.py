"""看板主动推送工具（v1.11.0）— 立即采集组装并推送当前用户启用的订阅"""

import json

from tools import register

DEFINITION = {
    "name": "dash_push",
    "description": (
        "立即推送一次每日项目看板。只操作当前用户自己的订阅与数据源，"
        "推送给该用户启用的订阅接收人。用户明确要求『现在推看板』『推送看板』时使用；"
        "日常自动推送由定时任务负责。"
    ),
    "parameters": {"type": "object", "properties": {}, "required": []},
}


@register(
    DEFINITION,
    policy={
        "confirm": True,
        "risk": "external_send",
        "summary": "立即向您启用的订阅接收人推送看板",
    },
    sector="dash",
    display="📊 推送项目看板...",
    user_desc=(
        "立即推送一次看板给订阅接收人。用户明确要求「现在推看板」「推送看板」时使用。\n"
        "看板订阅管理（帮我推个看板/改时间/停掉看板）由专门技能直接处理，不需要调用本工具。"
    ),
)
def execute(args: dict) -> str:
    from dashboard import service
    from dashboard.subscription_store import get_subscription_store
    from tools import get_current_user_id

    # v1.11.10：用户级隔离——只采集当前用户的动态数据源（配置源为系统级共享），
    # 只推当前用户自己的订阅；此前全量采集所有用户候选并推给所有订阅接收人，违反隔离。
    uid = get_current_user_id()
    sources = service.load_all_available_sources(user_id=uid)
    if not sources:
        return json.dumps(
            {"error": "您还没有可用的看板数据源（钉钉文档 base_id 未配置）。"
                     "请先发钉钉文档，再回复「按这几个文档做每日看板」登记数据源。"},
            ensure_ascii=False)

    subs = [s for s in get_subscription_store().list_enabled()
            if s.owner_user_id == uid and s.recipients]
    if not subs:
        return json.dumps({"error": "您还没有启用的看板订阅。说「帮我推个看板」先开通。"},
                          ensure_ascii=False)

    # 用第一个订阅的操作人身份读取（同一应用权限一致）
    sub0 = subs[0]
    parsed, errors = service.collect_and_parse(
        sources, operator_id=sub0.owner_union_id, staff_id=sub0.owner_staff_id)
    if not parsed:
        return json.dumps(
            {"error": "看板暂无数据：" + ("；".join(errors[:3]) or "数据源未配置")},
            ensure_ascii=False)

    recipients = list({sid for s in subs for sid in s.recipients})
    template = service.resolve_template(sub0)
    try:
        from skills.agent import call_deepseek_json
        report = service.assemble_report(
            parsed, title=sub0.title, date_str=service.today_str(),
            llm_func=call_deepseek_json, old_snapshot=sub0.last_snapshot,
            errors=errors, template=template)
    except Exception:
        report = service.assemble_report(
            parsed, title=sub0.title, date_str=service.today_str(),
            old_snapshot=sub0.last_snapshot, errors=errors, template=template)
    ok, msg = service.push_messages(recipients, sub0.title, report.messages)
    if not ok:
        return json.dumps({"error": f"推送失败：{msg}"}, ensure_ascii=False)
    return json.dumps({"ok": True, "recipients": len(recipients), "sources": len(parsed)},
                      ensure_ascii=False)

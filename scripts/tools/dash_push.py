"""看板主动推送工具（v1.11.0）— 立即采集组装并推送当前用户启用的订阅"""

import json
import logging

from scripts.tools import register


logger = logging.getLogger("tools.dash_push")

DEFINITION = {
    "name": "dash_push",
    "description": (
        "立即推送一次每日项目看板。只操作当前用户自己的订阅与数据源，"
        "推送给该用户启用的订阅接收人。用户明确要求『现在推看板』『推送看板』时使用；"
        "『模拟一份看板』『推给我一个演示』也属于立即推送请求；"
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
    from scripts.dashboard import service
    from scripts.dashboard.subscription_store import get_subscription_store
    from scripts.tools import get_current_user_id

    # v1.11.10：用户级隔离——只采集当前用户的动态数据源（配置源为系统级共享），
    # 只推当前用户自己的订阅；此前全量采集所有用户候选并推给所有订阅接收人，违反隔离。
    uid = get_current_user_id()
    subs = [s for s in get_subscription_store().list_enabled()
            if s.owner_user_id == uid and s.recipients]
    if not subs:
        return json.dumps({"error": "您还没有启用的看板订阅。说「帮我推个看板」先开通。"},
                          ensure_ascii=False)

    # 用当前用户第一个启用任务做即时演示；数据源、模板和提示词均取该任务自身
    # 的快照，保持与定时推送相同的“提示词 → 总结 → 来源”协议。
    sub0 = subs[0]
    task_prompt = service.ensure_task_prompt(sub0)
    sources = service.resolve_subscription_sources(sub0)
    if not sources:
        return json.dumps(
            {"error": "该看板任务还没有可用的数据源。请先配置或绑定钉钉文档。"},
            ensure_ascii=False)
    parsed, errors = service.collect_and_parse(
        sources, operator_id=sub0.owner_union_id, staff_id=sub0.owner_staff_id)
    if not parsed:
        return json.dumps(
            {"error": "看板暂无数据：" + ("；".join(errors[:3]) or "数据源未配置")},
            ensure_ascii=False)

    # 即时演示只发当前选中任务的接收人；不能借“现在推给我”把同一用户其他
    # 任务的接收人也带上。
    recipients = list(sub0.recipients)
    template = service.resolve_template(sub0)
    logger.info("[即时看板] 任务=%s 提示词=%s/%s，数据源=%s；开始调用看板执行模型",
                sub0.id, sub0.task_prompt_version, sub0.task_prompt_hash, len(sources))
    try:
        from scripts.skills.agent import call_dashboard_json
        if sub0.per_source:
            body_messages = service.assemble_per_source_messages(
                parsed, title=sub0.title, date_str=service.today_str(),
                llm_func=call_dashboard_json, old_snapshot=sub0.last_snapshot,
                errors=errors, template=template, task_prompt=task_prompt)
            verification = {"mode": "per_source", "sources": len(parsed)}
        else:
            report = service.assemble_report(
                parsed, title=sub0.title, date_str=service.today_str(),
                llm_func=call_dashboard_json, old_snapshot=sub0.last_snapshot,
                errors=errors, template=template, task_prompt=task_prompt)
            body_messages, verification = report.messages, report.verification
    except Exception:
        if sub0.per_source:
            body_messages = service.assemble_per_source_messages(
                parsed, title=sub0.title, date_str=service.today_str(),
                old_snapshot=sub0.last_snapshot, errors=errors, template=template,
                task_prompt=task_prompt)
            verification = {"mode": "per_source", "fallback": True}
        else:
            report = service.assemble_report(
                parsed, title=sub0.title, date_str=service.today_str(),
                old_snapshot=sub0.last_snapshot, errors=errors, template=template,
                task_prompt=task_prompt)
            body_messages, verification = report.messages, report.verification
    logger.info("[即时看板] 任务=%s 组装完成：%s", sub0.id, verification)
    messages = [service.render_task_prompt_message(sub0)] + body_messages
    source_links = service.render_source_links(sources)
    if source_links:
        messages.append(source_links)
    ok, msg = service.push_messages(recipients, sub0.title, messages)
    if not ok:
        return json.dumps({"error": f"推送失败：{msg}"}, ensure_ascii=False)
    # 手动演示和定时推送同样保留可回放证据，防用户只能从临时钉钉消息猜测
    # 本次到底采用了哪份任务提示词。
    from scripts.dashboard.push_history import get_push_history_store
    get_push_history_store().record(
        sub0.id, sub0.owner_user_id, sub0.title, messages,
        prompt_version=sub0.task_prompt_version,
        prompt_hash=sub0.task_prompt_hash)
    logger.info("[即时看板] 任务=%s 推送成功；已留档提示词=%s/%s",
                sub0.id, sub0.task_prompt_version, sub0.task_prompt_hash)
    return json.dumps({"ok": True, "recipients": len(recipients), "sources": len(parsed)},
                      ensure_ascii=False)

"""
看板定时推送调度器（v1.11.0）

独立 BackgroundScheduler，1 分钟 interval tick + 到点扫描：
不逐订阅建 cron（增删改订阅无需重注册调度），每个 tick 扫一遍启用订阅，
命中推送时间（hour/min 匹配 + weekday 过滤 + 当天不重复）就执行。

执行链：collect → parse → 快照/变化检测（changes_only 无变化静默）→
LLM 组装 → 规则兜底 → 推送 → 更新快照 + last_pushed_at。
"""

import datetime
import logging
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from apscheduler.schedulers.background import BackgroundScheduler  # noqa: E402

logger = logging.getLogger("dashboard_scheduler")

_scheduler: BackgroundScheduler | None = None

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"

# v1.12.5（消息流）：定时推送在汇总报告之外，把「记录变化最多」的 Top N 数据源
# 单独展开一条（每个关键源聚焦详情）。N 收敛在 2：变化源多时避免消息暴增。
KEY_SOURCE_EXPAND_N = 2


def _key_source_expansions(report, parsed, n: int = KEY_SOURCE_EXPAND_N):
    """从报告变化里挑出要单独展开的关键数据源。

    Returns:
        list[(parsed_item, source_name)]，按 record_changes 数量降序取前 n。
        record_changes 为空（baseline 首次 / source_added / snapshot_upgraded）
        没有可展开的变化，直接跳过。
    """
    by_key = {item["source_key"]: item for item in parsed}
    ranked = sorted(
        report.changes,
        key=lambda c: len(c.get("record_changes") or []), reverse=True)
    picked = []
    for change in ranked:
        if not change.get("record_changes"):
            continue
        item = by_key.get(change.get("source_key"))
        if item is not None:
            name = change.get("source_name") or item.get("name") or "数据源"
            picked.append((item, name))
        if len(picked) >= n:
            break
    return picked


def _admin_staff_ids() -> list[str]:
    """失败告警管理员名单（CONTACT_ADMIN_STAFF_IDS，逗号/空格/分号分隔）"""
    try:
        from scripts.config import CONTACT_ADMIN_STAFF_IDS
        ids = re.split(r"[,;，；\s]+", (CONTACT_ADMIN_STAFF_IDS or "").strip())
        return [i for i in ids if i]
    except Exception:
        return []


def _notify_failure(sub, reason: str) -> tuple[str, str]:
    """订阅执行失败告警（v1.11.6）：推送给创建人 + 管理员。

    静默失败（用户每天等不到看板却无人知）是最痛点——失败不丢日志，
    但主动消息才能让订阅人/管理员及时处理（文档被取消分享、权限被收等）。
    告警自身失败不影响主流程。
    """
    try:
        recipients = []
        if sub.owner_staff_id:
            recipients.append(sub.owner_staff_id)
        recipients.extend(_admin_staff_ids())
        recipients = list(dict.fromkeys(r for r in recipients if r))
        if not recipients:
            return "not_sent", "未配置订阅人或管理员的钉钉 staff_id"
        from scripts.dingtalk_notifier import DingTalkNotifier
        text = (
            f"⚠️ **每日看板推送失败**\n\n"
            f"订阅：{sub.title or '恩特能源每日项目看板'}\n"
            f"原因：{reason}\n\n"
            "请检查数据源文档是否仍可访问（分享/权限），或联系管理员。"
        )
        DingTalkNotifier().send_markdown_to_users(
            recipients, "看板推送失败提醒", text)
        logger.info(f"[看板] 订阅 {sub.id} 失败告警已发送（{len(recipients)} 人）")
        return "sent", ""
    except Exception as e:
        logger.warning(f"[看板] 订阅 {sub.id} 失败告警发送失败: {e}")
        return "failed", str(e)


# 到点扫描宽容窗口（分钟）：允许在预定推送时分附近 ±5 分钟内补推，
# 防单次 tick 因服务器繁忙/锁竞争错过精确分钟而漏推一整天（v1.12.x 审查 High 4）。
_DUE_WINDOW_MINUTES = 5


def is_due(now: datetime.datetime, sub) -> bool:
    """判断订阅是否到推送时间点

    - 分钟级宽容窗口：now 距 scheduled（hour:min）≤ _DUE_WINDOW_MINUTES 分钟即到期
      （interval tick 每分钟扫一次，单次 tick 卡顿/错过精确分钟时窗口内补推）
    - weekdays（""=每天；"1,5"=周一/周五，1-7 制与 isoweekday 对齐）
    - last_pushed_at 当天窗口内已推过 → 不重复（防 tick 重入 + 窗口内重复）
    """
    scheduled = now.replace(hour=sub.push_hour, minute=sub.push_minute,
                            second=0, microsecond=0)
    if abs((now - scheduled).total_seconds()) > _DUE_WINDOW_MINUTES * 60:
        return False
    weekdays = [d.strip() for d in (sub.weekdays or "").split(",") if d.strip()]
    if weekdays and str(now.isoweekday()) not in weekdays:
        return False
    if sub.last_pushed_at:
        try:
            last = datetime.datetime.strptime(sub.last_pushed_at, _TS_FORMAT)
            if (last.date() == now.date()
                    and abs((now - last).total_seconds())
                    <= _DUE_WINDOW_MINUTES * 60):
                return False
        except ValueError:
            pass
    return True


def _execute_subscription(sub) -> dict:
    """执行一次订阅推送，返回 {"ok": bool, "reason": str}

    v1.11.4：alert_mode 默认 always（每天必推）；顶部注入「📌 今日变化/今日无变化」。
    changes_only 保留：无变化静默。
    """
    from scripts.dashboard import service
    from scripts.dashboard.alerts import change_banner, has_changes, make_snapshot
    from scripts.dashboard.monitoring import evaluate, render_banner
    from scripts.dashboard.subscription_store import get_subscription_store

    store = get_subscription_store()

    # v1.13.1：旧任务首次运行时懒生成提示词快照；后续执行只读快照，
    # 不因系统模板代码或用户模板文件变化而悄悄改变总结行为。
    had_task_prompt = bool(getattr(sub, "task_prompt", "")
                           and getattr(sub, "task_prompt_spec", None))
    task_prompt = service.ensure_task_prompt(sub)
    if not had_task_prompt:
        store.update(sub)

    def _status(status: str, stage: str, reason: str = "", alert_status: str = ""):
        try:
            store.set_execution_status(
                sub.id, status=status, stage=stage, reason=reason,
                alert_status=alert_status)
        except Exception as exc:
            logger.warning("[看板] 订阅 %s 运行状态留档失败: %s", sub.id, exc)

    def _failed(stage: str, reason: str, result_code: str = "") -> dict:
        alert_status, alert_error = _notify_failure(sub, reason)
        full_reason = reason
        if alert_error:
            full_reason += f"；失败提醒未送达：{alert_error}"
        _status("failed", stage, full_reason, alert_status)
        # 保持旧调用方使用的稳定结果码；可读原因完整写入持久状态和失败提醒。
        return {"ok": False, "reason": result_code or reason}

    sources = service.resolve_subscription_sources(sub)
    if not sources:
        logger.warning(f"[看板] 订阅 {sub.id} 无可用数据源，跳过")
        return _failed("resolve_sources", "数据源不可用（无可用数据源）", "no_sources")

    parsed, errors = service.collect_and_parse(
        sources, operator_id=sub.owner_union_id, staff_id=sub.owner_staff_id)
    errors = service.source_resolution_warnings(sub, sources) + errors
    if not parsed:
        detail = "；".join(errors[:2]) or "采集结果为空"
        logger.warning(f"[看板] 订阅 {sub.id} 采集为空：{detail}")
        return _failed("collect", f"采集为空：{detail}", "no_data")

    snap = make_snapshot(parsed)
    changed = has_changes(sub.last_snapshot, snap)
    outcome = evaluate(sub.last_snapshot, snap, errors)

    if sub.alert_mode == "changes_only" and not changed:
        # 旧版去噪快照静默升级为完整记录快照，下一次即可检测业务字段变化。
        if sub.last_snapshot and any(
                "records" not in source for source in sub.last_snapshot):
            get_subscription_store().set_snapshot(sub.id, snap)
        logger.info(f"[看板] 订阅 {sub.id} 数据无变化，静默（changes_only）")
        _status("skipped", "change_check", "未发现重要变化，按 changes_only 设置静默", "not_needed")
        return {"ok": False, "reason": "no_changes"}
    if sub.alert_mode == "off":
        _status("skipped", "policy", "订阅提醒模式为 off，未执行推送", "not_needed")
        return {"ok": False, "reason": "off"}

    # LLM 组装 → 失败/超长规则兜底（service.assemble 内部处理）
    # v1.12.0：按订阅模板决定输出格式（daily 缺省=旧行为）
    # v1.12.5：per_source 订阅改用逐源组装——每个数据源单独一条（复用 v1.12.5
    # assemble_per_source_messages），不再合并成一份报告，也不再关键源展开
    # （已是逐源粒度）。
    template = service.resolve_template(sub)
    per_source = bool(getattr(sub, "per_source", False))
    try:
        from scripts.skills.agent import call_dashboard_json
        if per_source:
            report = None
            messages = list(service.assemble_per_source_messages(
                parsed, title=sub.title, date_str=service.today_str(),
                llm_func=call_dashboard_json, old_snapshot=sub.last_snapshot,
                errors=errors, template=template, task_prompt=task_prompt))
        else:
            report = service.assemble_report(
                parsed, title=sub.title, date_str=service.today_str(),
                llm_func=call_dashboard_json, old_snapshot=sub.last_snapshot,
                errors=errors, template=template, task_prompt=task_prompt)
            messages = list(report.messages)
    except Exception:
        if per_source:
            report = None
            messages = list(service.assemble_per_source_messages(
                parsed, title=sub.title, date_str=service.today_str(),
                old_snapshot=sub.last_snapshot, errors=errors, template=template,
                task_prompt=task_prompt))
        else:
            report = service.assemble_report(
                parsed, title=sub.title, date_str=service.today_str(),
                old_snapshot=sub.last_snapshot, errors=errors, template=template,
                task_prompt=task_prompt)
            messages = list(report.messages)

    # 监测模式：保留旧版首行作为兼容协议，再补充“检查结果/完整性”语义层。
    # 这样既不破坏已有钉钉消息解析和回归锚点，也不把部分失败伪装为完整成功。
    name_by_key = {s.key: s.name for s in sources}
    legacy_banner = change_banner(sub.last_snapshot, snap, name_by_key)
    messages[0] = (legacy_banner + "\n" + render_banner(outcome, name_by_key)
                   + "\n\n" + messages[0])

    # v1.12.5（消息流）：汇总报告之外，把「变化最多」的 Top N 关键数据源单独
    # 展开一条——每条聚焦详情（含完整证据），汇总报告仍给全局视角。复用同一套
    # LLM 组装（失败规则兜底），按订阅模板输出格式；标题带源名区分于汇总。
    # v1.12.5：per_source 逐源模式每条已是单源总结，跳过关键源展开。
    for key_item, key_name in (_key_source_expansions(report, parsed)
                               if not per_source and report else []):
        try:
            key_report = service.assemble_report(
                [key_item], title=f"🔍 {key_name} 详情",
                date_str=service.today_str(), llm_func=call_dashboard_json,
                old_snapshot=sub.last_snapshot, errors=errors, template=template,
                task_prompt=task_prompt)
        except Exception:
            key_report = service.assemble_report(
                [key_item], title=f"🔍 {key_name} 详情",
                date_str=service.today_str(),
                old_snapshot=sub.last_snapshot, errors=errors, template=template,
                task_prompt=task_prompt)
        messages.extend(key_report.messages)

    # 每次实际向钉钉发送看板时，先把本任务固定提示词交给用户核对；
    # 中间是本次监测/摘要，最后用独立消息收口全部数据源链接。
    prompt_message = service.render_task_prompt_message(sub)
    source_links = service.render_source_links(sources)
    messages = [prompt_message] + messages
    if source_links:
        messages.append(source_links)

    ok, msg = service.push_messages(sub.recipients, sub.title, messages)
    if not ok:
        logger.warning(f"[看板] 订阅 {sub.id} 推送失败：{msg}")
        return _failed("push", f"推送失败：{msg}")

    get_subscription_store().set_snapshot(
        sub.id, snap, last_pushed_at=service.now_str())

    # v1.12.7：推送成功写留档（每任务保留最近 30 次，可追溯历史看板）。
    # 留档失败不影响主流程（日志警告即可，不重复告警）。
    try:
        from scripts.dashboard.push_history import get_push_history_store
        get_push_history_store().record(
            sub.id, sub.owner_user_id, sub.title, messages,
            prompt_version=getattr(sub, "task_prompt_version", ""),
            prompt_hash=getattr(sub, "task_prompt_hash", ""))
    except Exception as e:
        logger.warning(f"[看板] 订阅 {sub.id} 留档写入失败: {e}")

    _status("success", "push", f"已成功推送 {len(messages)} 页", "not_needed")

    logger.info("[看板] 订阅 %s 已推送（%s 人，%s 页；核验=%s；per_source=%s）",
                sub.id, len(sub.recipients), len(messages),
                getattr(report, "verification", None) if report else "-",
                per_source)
    return {"ok": True, "reason": ""}


def _deduplicate_due_subscriptions(subs: list) -> tuple[list, list[tuple[int, int]]]:
    """保留来源覆盖最全的订阅，返回 (待执行, [(被抑制ID, 覆盖者ID)])。

    v1.12.7（D1）：来源集合按任务绑定解析 effective_source_keys（每个任务
    绑定自己的数据源，实时算以任务为边界）——同 owner 同时刻同接收人且
    解析来源被另一任务完整覆盖的订阅，只推覆盖最全的一条，防重复推送。
    """
    from scripts.dashboard import service

    def _keys(sub):
        return service.effective_source_keys(sub)

    ordered = sorted(subs, key=lambda sub: len(_keys(sub)), reverse=True)
    covered: list[tuple[tuple, set[str], int]] = []
    selected, suppressed = [], []
    for sub in ordered:
        identity = (sub.owner_user_id, sub.push_hour, sub.push_minute,
                    sub.weekdays or "", tuple(sorted(sub.recipients)))
        sources = _keys(sub)
        parent = next((sid for key, keys, sid in covered
                       if key == identity and sources <= keys), None)
        if parent is not None:
            suppressed.append((sub.id, parent))
            continue
        selected.append(sub)
        covered.append((identity, sources, sub.id))
    return selected, suppressed


def _tick():
    """1 分钟 tick：扫描到点的启用订阅"""
    from scripts.dashboard.subscription_store import get_subscription_store

    now = datetime.datetime.now()
    executed = 0
    try:
        subs = get_subscription_store().list_enabled()
    except Exception as e:
        logger.warning(f"[看板] 读取订阅列表失败：{e}")
        return
    due_subs = [sub for sub in subs if is_due(now, sub)]
    # 同一用户、时刻、接收人下，来源为另一条订阅子集时只执行覆盖最全的一条。
    # 配置不自动删除，用户查询状态时仍会收到“可能重复”提示并自行确认清理。
    due_subs, suppressed = _deduplicate_due_subscriptions(due_subs)
    for sub_id, parent_id in suppressed:
        logger.warning("[看板] 订阅 %s 被同批订阅 %s 完整覆盖，已抑制重复推送",
                       sub_id, parent_id)
    for sub in due_subs:
        try:
            result = _execute_subscription(sub)
            logger.info(f"[看板] tick 订阅 {sub.id} → {result}")
            executed += 1
        except Exception as e:
            logger.exception(f"[看板] 订阅 {sub.id} 执行异常: {e}")
            alert_status, alert_error = _notify_failure(sub, f"执行异常：{e}")
            try:
                get_subscription_store().set_execution_status(
                    sub.id, status="failed", stage="execution",
                    reason=(f"执行异常：{e}" +
                            (f"；失败提醒未送达：{alert_error}" if alert_error else "")),
                    alert_status=alert_status)
            except Exception as status_error:
                logger.warning("[看板] 订阅 %s 异常状态留档失败: %s", sub.id, status_error)
    if executed:
        logger.info(f"[看板] 本轮 tick 推送 {executed} 条订阅")


def start_dashboard_scheduler():
    """启动看板定时调度（main.py 调用）"""
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(daemon=True)
    _scheduler.add_job(
        _tick, "interval", minutes=1,
        id="dashboard_tick", name="看板 1 分钟到点扫描",
    )
    _scheduler.start()
    logger.info("[看板] 定时推送调度器已启动（每分钟扫描到点订阅）")


def stop_dashboard_scheduler():
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("[看板] 定时推送调度器已停止")

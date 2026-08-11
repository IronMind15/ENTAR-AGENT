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

_PARENT = os.path.dirname(os.path.abspath(__file__))  # scripts/
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from apscheduler.schedulers.background import BackgroundScheduler  # noqa: E402

logger = logging.getLogger("dashboard_scheduler")

_scheduler: BackgroundScheduler | None = None

_TS_FORMAT = "%Y-%m-%d %H:%M:%S"


def _admin_staff_ids() -> list[str]:
    """失败告警管理员名单（CONTACT_ADMIN_STAFF_IDS，逗号/空格/分号分隔）"""
    try:
        from config import CONTACT_ADMIN_STAFF_IDS
        ids = re.split(r"[,;，；\s]+", (CONTACT_ADMIN_STAFF_IDS or "").strip())
        return [i for i in ids if i]
    except Exception:
        return []


def _notify_failure(sub, reason: str):
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
            return
        from dingtalk_notifier import DingTalkNotifier
        text = (
            f"⚠️ **每日看板推送失败**\n\n"
            f"订阅：{sub.title or '恩特能源每日项目看板'}\n"
            f"原因：{reason}\n\n"
            "请检查数据源文档是否仍可访问（分享/权限），或联系管理员。"
        )
        DingTalkNotifier().send_markdown_to_users(
            recipients, "看板推送失败提醒", text)
        logger.info(f"[看板] 订阅 {sub.id} 失败告警已发送（{len(recipients)} 人）")
    except Exception as e:
        logger.warning(f"[看板] 订阅 {sub.id} 失败告警发送失败: {e}")


def is_due(now: datetime.datetime, sub) -> bool:
    """判断订阅是否到推送时间点

    - hour/min 精确匹配（interval tick 每分钟扫一次）
    - weekdays（""=每天；"1,5"=周一/周五，1-7 制与 isoweekday 对齐）
    - last_pushed_at 当天同一时分已推过 → 不重复（防 tick 重入）
    """
    if now.hour != sub.push_hour or now.minute != sub.push_minute:
        return False
    weekdays = [d.strip() for d in (sub.weekdays or "").split(",") if d.strip()]
    if weekdays and str(now.isoweekday()) not in weekdays:
        return False
    if sub.last_pushed_at:
        try:
            last = datetime.datetime.strptime(sub.last_pushed_at, _TS_FORMAT)
            if (last.date() == now.date()
                    and last.hour == now.hour and last.minute == now.minute):
                return False
        except ValueError:
            pass
    return True


def _execute_subscription(sub) -> dict:
    """执行一次订阅推送，返回 {"ok": bool, "reason": str}

    v1.11.4：alert_mode 默认 always（每天必推）；顶部注入「📌 今日变化/今日无变化」。
    changes_only 保留：无变化静默。
    """
    from dashboard import service
    from dashboard.alerts import change_banner, has_changes, make_snapshot
    from dashboard.subscription_store import get_subscription_store

    sources = service.resolve_subscription_sources(sub)
    if not sources:
        logger.warning(f"[看板] 订阅 {sub.id} 无可用数据源，跳过")
        _notify_failure(sub, "数据源不可用（无可用数据源）")
        return {"ok": False, "reason": "no_sources"}

    parsed, errors = service.collect_and_parse(
        sources, operator_id=sub.owner_union_id, staff_id=sub.owner_staff_id)
    errors = service.source_resolution_warnings(sub, sources) + errors
    if not parsed:
        detail = "；".join(errors[:2]) or "采集结果为空"
        logger.warning(f"[看板] 订阅 {sub.id} 采集为空：{detail}")
        _notify_failure(sub, f"采集为空：{detail}")
        return {"ok": False, "reason": "no_data"}

    snap = make_snapshot(parsed)
    changed = has_changes(sub.last_snapshot, snap)

    if sub.alert_mode == "changes_only" and not changed:
        # 旧版去噪快照静默升级为完整记录快照，下一次即可检测业务字段变化。
        if sub.last_snapshot and any(
                "records" not in source for source in sub.last_snapshot):
            get_subscription_store().set_snapshot(sub.id, snap)
        logger.info(f"[看板] 订阅 {sub.id} 数据无变化，静默（changes_only）")
        return {"ok": False, "reason": "no_changes"}
    if sub.alert_mode == "off":
        return {"ok": False, "reason": "off"}

    # LLM 组装 → 失败/超长规则兜底（service.assemble 内部处理）
    try:
        from skills.agent import call_deepseek_json
        text = service.assemble(parsed, title=sub.title, date_str=service.today_str(),
                                llm_func=call_deepseek_json,
                                old_snapshot=sub.last_snapshot,
                                evidence_pipeline=True, errors=errors)
    except Exception:
        text = service.assemble(
            parsed, title=sub.title, date_str=service.today_str(),
            old_snapshot=sub.last_snapshot, evidence_pipeline=True, errors=errors)

    # 每日必推：顶部注入变化标注（有变化列变更、无变化显式说明）
    if sub.alert_mode == "always":
        name_by_key = {s.key: s.name for s in sources}
        text = change_banner(sub.last_snapshot, snap, name_by_key) + "\n\n" + text

    ok, msg = service.push(sub.recipients, sub.title, text)
    if not ok:
        logger.warning(f"[看板] 订阅 {sub.id} 推送失败：{msg}")
        _notify_failure(sub, f"推送失败：{msg}")
        return {"ok": False, "reason": msg}

    get_subscription_store().set_snapshot(
        sub.id, snap, last_pushed_at=service.now_str())
    logger.info(f"[看板] 订阅 {sub.id} 已推送（{len(sub.recipients)} 人）")
    return {"ok": True, "reason": ""}


def _tick():
    """1 分钟 tick：扫描到点的启用订阅"""
    from dashboard.subscription_store import get_subscription_store

    now = datetime.datetime.now()
    executed = 0
    try:
        subs = get_subscription_store().list_enabled()
    except Exception as e:
        logger.warning(f"[看板] 读取订阅列表失败：{e}")
        return
    for sub in subs:
        try:
            if is_due(now, sub):
                result = _execute_subscription(sub)
                logger.info(f"[看板] tick 订阅 {sub.id} → {result}")
                executed += 1
        except Exception as e:
            logger.exception(f"[看板] 订阅 {sub.id} 执行异常: {e}")
            _notify_failure(sub, f"执行异常：{e}")
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

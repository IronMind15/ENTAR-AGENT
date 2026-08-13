"""
看板技能（v1.11.0）— priority=80，位于 pcb_calc(90) 之后、agent(50) 之前

职责：看板订阅管理对话（创建/改时间/改频率/改接收人/停止/查询）。
流程：
  管理指令 → parse_subscription_command → 存 pending（内存态）→ 反问确认
  「确认」回复 → _execute_pending 落地（create 注册订阅后立即推样例看板）
不拦截：文档/方案/方法论/学习入库等话题（subscription_commands.is_kanban_topic 判定）。
"""

import logging
import re

from dashboard import service
from dashboard import subscription_commands as sub_cmd
from dashboard.config_model import load_sources
from dashboard.subscription_store import Subscription, get_subscription_store
from skills import BaseSkill, register

logger = logging.getLogger("dashboard.skill")

# 确认词由 subscription_commands 统一识别，支持“是的，全部删除”等动作复述。

_HELP_TEXT = (
    "我可以帮您开通「每日项目看板」自动推送，也可以查实时看板。试试说：\n"
    "· 「帮我推个看板」— 开通每日自动推送\n"
    "· 「看板今天怎么样」— 查当前看板\n"
    "· 「改看板时间到10点」「每周一和周五」「也推给张工」「停掉看板」\n"
    "· 「看板模板」— 查看模板；「用周报模板」— 切换输出格式；\n"
    "  「按这个格式做看板：负责人/今日进展/明日计划」— 自定义模板"
)


@register
class DashboardSkill(BaseSkill):
    name: str = "dashboard"
    description: str = "每日项目看板：订阅管理 + 实时查看"
    priority: int = 80

    # ===== 匹配 =====
    @classmethod
    def match(cls, query: str, user_id: str = "") -> bool:
        q = (query or "").strip()
        if not q:
            return False
        # v1.12.0：创建订阅后的「选模板」反问回复（1/2/3/模板名/不用了）。
        # 须放最前——数字「1」不含「看板」，其他判定都接不住；且 parse_template_choice
        # 内部限定 pending choose_template + 反问窗口，普通聊天不会误拦。
        if sub_cmd.parse_template_choice(q, user_id) is not None:
            return True
        # 第 0 步：「按这几个文档做每日看板」（_NEGATIVE_RE 含「文档」会拦常规路径，需提前）
        if sub_cmd.parse_doc_dashboard_intent(q) is not None:
            return True
        if sub_cmd.is_kanban_topic(q):
            # v1.13.0：概念疑问句（「看板数据源和看板任务是分开的吗」）→ LLM 判歧义，
            # 判定非订阅管理 → 放行给 Agent 正常问答（修复 query 意图吞问题）。
            # 明确管理动词/明确查询无疑问词 → 走正则快速路径，不付 LLM 成本。
            if sub_cmd.needs_kanban_ambiguity_check(q):
                verified = sub_cmd._llm_verify_subscription(
                    q, fallback_intent="query")
                if verified is None:
                    return False
            return True
        # 订阅管理指令（含"也推给张工"这类不含"看板"的追加指令）
        if sub_cmd.parse_subscription_command(q) is not None:
            return True
        # 简短确认词：仅当该用户最近有看板活动（有 pending 待确认）才拦截。
        # 必须按 user_id 隔离——此前用全局时间戳，A 聊完看板 10 分钟内 B 说
        # 「确认/好的」会被误拦截（审查 Critical 1）。
        if sub_cmd.is_confirmation_text(q):
            return sub_cmd.has_recent_kanban_activity(user_id=user_id)
        if sub_cmd.is_cancel_text(q):
            return sub_cmd.has_recent_kanban_activity(user_id=user_id)
        if sub_cmd.is_contextual_delete(q):
            return sub_cmd.has_recent_kanban_activity(user_id=user_id)
        return False

    # ===== 处理 =====
    @classmethod
    def handle(cls, query: str, user_id: str = "", on_chunk=None) -> dict:
        q = (query or "").strip()
        uid = user_id or ""
        had_recent_activity = sub_cmd.has_recent_kanban_activity(user_id=uid)

        # 确认/继续分支
        if sub_cmd.is_confirmation_text(q):
            pending = sub_cmd.get_pending(uid)
            if not pending:
                return {"answer": "您还没有待确认的看板操作。说「帮我推个看板」即可开通。",
                        "source": "dashboard"}
            if pending.get("intent") == "choose_template":
                # v1.12.0：反问「选模板」中用户回「好的/确认」→ 保持当前模板（默认每日）
                sub_cmd.clear_pending(uid)
                return {"answer":
                        "好的，保持当前模板。想换格式随时说「用周报模板」「用项目模板」等。",
                        "source": "dashboard"}
            return cls._execute_pending(pending, uid)
        if sub_cmd.is_cancel_text(q) and sub_cmd.get_pending(uid):
            sub_cmd.clear_pending(uid)
            return {"answer": "已取消，刚才的看板操作没有执行。", "source": "dashboard"}

        # v1.12.0：创建订阅后的「选模板」反问回复（1/2/3/模板名/不用了）
        choice = sub_cmd.parse_template_choice(q, uid)
        if choice:
            return cls._execute_template_choice(choice, uid)

        # “现在帮我全部删除”只有在该用户刚查看/管理过看板时才承接，避免误删。
        if sub_cmd.is_contextual_delete(q) and had_recent_activity:
            store = get_subscription_store()
            subs = store.list_for_owner(uid)
            if not subs:
                return {"answer": "您还没有订阅看板，无需删除。", "source": "dashboard"}
            pending = {"intent": "delete", "sub_ids": [s.id for s in subs]}
            sub_cmd.set_pending(uid, pending)
            return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

        # 「按这几个文档做每日看板」→ 动态数据源创建
        if sub_cmd.parse_doc_dashboard_intent(q) is not None:
            return cls._handle_doc_create(uid)

        parsed = sub_cmd.parse_subscription_command(q)
        if not parsed:
            return {"answer": _HELP_TEXT, "source": "dashboard"}
        intent = parsed["intent"]

        if intent == "create":
            pending = cls._build_create_pending(uid)
            sub_cmd.set_pending(uid, pending)
            return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

        if intent == "query":
            sub_cmd.touch_activity(uid)
            return {"answer": cls._render_subscription_status(uid), "source": "dashboard"}

        if intent == "template":
            # v1.12.0：查看模板列表（直接答复，非写操作）
            sub_cmd.touch_activity(uid)
            return {"answer": cls._render_template_list(uid), "source": "dashboard"}

        if intent == "set_template":
            # v1.12.0：切换模板（写操作，确认后落地）
            store = get_subscription_store()
            subs = store.list_for_owner(uid)
            if not subs:
                return {"answer": "您还没有订阅看板。说「帮我推个看板」先开通，再切换模板。",
                        "source": "dashboard"}
            tpl = cls._match_template(q, uid)
            if not tpl:
                return {"answer": cls._render_template_list(uid, hint=True),
                        "source": "dashboard"}
            pending = {"intent": "set_template", "template_key": tpl.key,
                       "template_name": tpl.name}
            sub_cmd.set_pending(uid, pending)
            return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

        if intent == "describe_template":
            # v1.12.0：描述成模板（LLM 生成 → 确认 → 保存并应用）
            from dashboard.template_builder import describe_to_spec
            tdef = describe_to_spec(parsed.get("description") or "")
            if not tdef.get("ok"):
                return {"answer": tdef.get("message", "生成模板失败，请换个描述试试。"),
                        "source": "dashboard"}
            pending = {"intent": "describe_template",
                       "template_name": tdef.get("name", "自定义模板"),
                       "section_spec": tdef.get("section_spec"),
                       "template_def": tdef}
            sub_cmd.set_pending(uid, pending)
            return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

        if intent == "submit_template":
            # v1.12.0：提交模板（解析最近上传文件 → 确认 → 保存并应用）
            from knowledge_review import get_pending_learn
            uploaded = get_pending_learn(uid)
            if not uploaded or not uploaded.get("file_path"):
                return {"answer":
                        "请先上传一个 Excel 或 Markdown 文件（Excel 用第一行表头、"
                        "Markdown 用 # 标题定义章节），再回复「把这个当看板模板」。",
                        "source": "dashboard"}
            from dashboard.template_builder import parse_template_file
            tdef = parse_template_file(uploaded["file_path"],
                                       uploaded.get("file_name") or "")
            if not tdef.get("ok"):
                return {"answer": tdef.get("message", "解析模板失败，请换一个文件试试。"),
                        "source": "dashboard"}
            pending = {"intent": "submit_template",
                       "template_name": tdef.get("name", "自定义模板"),
                       "file_name": uploaded.get("file_name") or "",
                       "section_spec": tdef.get("section_spec"),
                       "template_def": tdef}
            sub_cmd.set_pending(uid, pending)
            return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

        if intent == "edit_template":
            # v1.13.0：编辑模板内容（整体重述覆盖 → 确认 → 覆盖保存）
            sub_cmd.touch_activity(uid)
            return cls._handle_edit_template(q, uid, parsed)

        if intent == "resume":
            store = get_subscription_store()
            paused = [s for s in store.list_for_owner(uid) if not s.enabled]
            if not paused:
                return {"answer": cls._resume_subscription(uid), "source": "dashboard"}
            pending = {"intent": "resume", "sub_ids": [s.id for s in paused]}
            sub_cmd.set_pending(uid, pending)
            return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

        if intent == "delete":
            store = get_subscription_store()
            subs = store.list_for_owner(uid)
            if not subs:
                return {"answer": "您还没有订阅看板，无需删除。说「帮我推个看板」即可开通。",
                        "source": "dashboard"}
            pending = {"intent": "delete", "sub_ids": [s.id for s in subs]}
            sub_cmd.set_pending(uid, pending)
            return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

        if intent == "set_recipient_self":
            return {"answer": sub_cmd.render_confirmation({"intent": "set_recipient_self"}),
                    "source": "dashboard"}

        if intent == "stop":
            store = get_subscription_store()
            active = [s for s in store.list_for_owner(uid) if s.enabled]
            if not active:
                return {"answer": cls._stop_subscription(uid), "source": "dashboard"}
            pending = {"intent": "stop", "sub_ids": [s.id for s in active]}
            sub_cmd.set_pending(uid, pending)
            return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

        if intent in ("change_time", "change_freq", "change_recipients"):
            store = get_subscription_store()
            subs = store.list_for_owner(uid)
            if not subs:
                return {"answer": "您还没有订阅看板。说「帮我推个看板」先开通，再调整。",
                        "source": "dashboard"}
            pending = dict(parsed)
            pending["sub_id"] = subs[0].id
            sub_cmd.set_pending(uid, pending)
            return {"answer": sub_cmd.render_confirmation(pending, current=subs[0]),
                    "source": "dashboard"}

        return {"answer": _HELP_TEXT, "source": "dashboard"}

    # ===== 辅助 =====
    @classmethod
    def _handle_doc_create(cls, user_id: str,
                           source_candidate_ids: list[int] | None = None) -> dict:
        """「按这几个文档做每日看板」：取文档候选 → 反问确认

        v1.11.4：`source_candidate_ids` 为本次消息识别的候选 id 时只取这些
        （避免历史候选混入）；缺省时兜底取全部可看板候选（如纯「帮我推个看板」）。
        """
        try:
            from dashboard.doc_candidates import get_candidate_store
            store = get_candidate_store()
            if source_candidate_ids:
                cands = [store.get(cid) for cid in source_candidate_ids]
                cands = [c for c in cands if c and c.enabled
                         and c.kind in ("notable", "workbook", "doc", "folder")]
            else:
                cands = store.list_dashboard_ready(user_id)
        except Exception as e:
            logger.warning(f"取文档候选失败: {e}")
            cands = []
        if not cands:
            return {"answer":
                    "请先在钉钉发送要作为看板的文档链接（AI表格/在线表格），"
                    "我识别后就可以按它做每日看板。",
                    "source": "dashboard"}
        # v1.11.6：创建前权限预检——概要模式探测可读性，不可读的剔除并提示，
        # 避免「订阅建好但每天推送失败」
        cands, blocked = cls._precheck_doc_candidates(cands)
        if not cands:
            reasons = "；".join(
                f"《{b['name']}》{b['reason']}" for b in blocked[:3])
            return {"answer":
                    f"这些文档当前无法读取，无法做看板数据源：{reasons}。\n"
                    "请确认文档已分享给机器人/您本人，且应用已开通文档读取权限。",
                    "source": "dashboard"}
        staff_id = cls._staff_id_of(user_id)
        pending = {
            "intent": "doc_create",
            "data_sources": [f"doc_{c.id}" for c in cands],
            "push_hour": 9, "push_minute": 0,
            "weekdays": "", "alert_mode": "always",
            "title": "恩特能源每日项目看板",
            "owner_user_id": user_id,
            "owner_staff_id": staff_id,
            "recipients": [staff_id] if staff_id else [],
        }
        sub_cmd.set_pending(user_id, pending)
        return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

    @classmethod
    def _build_create_pending(cls, user_id: str) -> dict:
        staff_id = cls._staff_id_of(user_id)
        sources = [s.key for s in load_sources() if s.enabled]
        return {
            "intent": "create",
            "data_sources": sources or [],
            "push_hour": 9, "push_minute": 0,
            "weekdays": "", "alert_mode": "always",
            "title": "恩特能源每日项目看板",
            "owner_user_id": user_id,
            "owner_staff_id": staff_id,
            "recipients": [staff_id] if staff_id else [],
        }

    @classmethod
    def _execute_template_choice(cls, choice: dict, user_id: str) -> dict:
        """v1.12.0：应用创建订阅反问时用户选择的模板；none=保持默认。

        模板切换应用到该用户所有订阅（与 set_template 一致）；按真实 update 结果回报。
        """
        sub_cmd.clear_pending(user_id)
        if choice.get("choice") == "none":
            return {"answer": "好的，保持当前模板。想换格式随时说「用周报模板」等。",
                    "source": "dashboard"}
        key = choice.get("choice")
        try:
            from dashboard.template_store import get_template_store
            tpl = get_template_store().get(key, user_id=user_id)
        except Exception:
            tpl = None
        if not tpl:
            return {"answer": "该模板不存在，保持当前模板。", "source": "dashboard"}
        store = get_subscription_store()
        owned = store.list_for_owner(user_id)
        if not owned:
            return {"answer": f"已记住模板「{tpl.name}」（您还没有订阅，开通后即生效）。",
                    "source": "dashboard"}
        for s in owned:
            s.template_id = tpl.key
            store.update(s)
        note = cls._push_sample(owned[0])
        return {"answer": f"✅ 已将看板切换为「{tpl.name}」模板。{note}",
                "source": "dashboard"}

    @classmethod
    def _execute_pending(cls, pending: dict, user_id: str) -> dict:
        store = get_subscription_store()
        intent = pending.get("intent")

        if intent in ("create", "doc_create"):
            sub = Subscription(
                owner_user_id=user_id,
                owner_staff_id=pending.get("owner_staff_id", ""),
                owner_union_id=user_id,   # 钉钉 sender_id 即 unionId
                data_sources=pending.get("data_sources") or [],
                push_hour=pending.get("push_hour", 9),
                push_minute=pending.get("push_minute", 0),
                weekdays=pending.get("weekdays", ""),
                alert_mode=pending.get("alert_mode", "always"),
                recipients=pending.get("recipients") or [],
                title=pending.get("title", "恩特能源每日项目看板"),
            )
            duplicate = store.find_exact_duplicate(sub)
            if duplicate:
                if not duplicate.enabled:
                    store.set_enabled(duplicate.id, True)
                    state = "已恢复原订阅"
                else:
                    state = "未重复创建"
                sub_cmd.clear_pending(user_id)
                return {
                    "answer": f"✅ 已有相同看板订阅（编号 {duplicate.id}），{state}。",
                    "source": "dashboard",
                }
            store.create(sub)
            sub_cmd.clear_pending(user_id)
            note = cls._push_sample(sub)
            # v1.12.0：创建后主动反问选模板（用户可回复 1/2/3/模板名/不用了，
            # 或不理会保持默认每日；pending 供 parse_template_choice 识别选择回复）
            # v1.12.x：提醒可自定义格式；有私有模板时列出提醒复用
            my_templates = cls._user_template_hint(user_id)
            sub_cmd.set_pending(user_id, {"intent": "choose_template", "sub_id": sub.id})
            return {"answer": f"✅ 已为您开通每日看板推送！{note}\n"
                              f"🎨 当前模板：每日简报。要不要换个格式？回复：\n"
                              f"  1 每日简报\n  2 周报总结\n  3 项目看板\n"
                              f"✏️ 也可以直接说「按这个格式做看板：先写总体结论，"
                              f"再按文档分块总结，最后放原文链接」自定义格式"
                              f"{my_templates}"
                              f"\n（回复「不用了」保持默认；随时也能说「用周报模板」切换）",
                    "source": "dashboard"}

        if intent == "delete":
            # 只删除当前用户真实拥有的订阅，并依据 rowcount 回报，禁止吞错后谎报成功。
            owned_ids = {s.id for s in store.list_for_owner(user_id)}
            requested_ids = [int(sid) for sid in (pending.get("sub_ids") or [])]
            deleted = 0
            for sid in (pending.get("sub_ids") or []):
                if int(sid) in owned_ids and store.delete(int(sid)):
                    deleted += 1
            sub_cmd.clear_pending(user_id)
            remaining = [sid for sid in requested_ids if store.get(sid) is not None]
            if remaining:
                return {"answer": f"⚠️ 已删除 {deleted} 个，但仍有 {len(remaining)} 个未删除，请联系管理员排查。",
                        "source": "dashboard"}
            return {"answer": f"✅ 已删除 {deleted} 个看板订阅。需要的话说「帮我推个看板」重新开通。",
                    "source": "dashboard"}

        if intent in ("stop", "resume"):
            desired = intent == "resume"
            owned_ids = {s.id for s in store.list_for_owner(user_id)}
            changed = 0
            for sid in (pending.get("sub_ids") or []):
                sid = int(sid)
                if sid in owned_ids:
                    store.set_enabled(sid, desired)
                    current = store.get(sid)
                    if current and current.enabled == desired:
                        changed += 1
            sub_cmd.clear_pending(user_id)
            action = "恢复" if desired else "停止"
            return {"answer": f"✅ 已{action} {changed} 个看板订阅。", "source": "dashboard"}

        if intent == "set_template":
            # v1.12.0：切换模板到订阅（按真实 update 结果回报）
            tpl = cls._match_template_key(pending.get("template_key"), user_id)
            if not tpl:
                sub_cmd.clear_pending(user_id)
                return {"answer": "模板不存在，可能已被删除。说「看板模板」查看可用模板。",
                        "source": "dashboard"}
            owned = store.list_for_owner(user_id)
            if not owned:
                sub_cmd.clear_pending(user_id)
                return {"answer": "您还没有订阅看板。说「帮我推个看板」先开通。",
                        "source": "dashboard"}
            for s in owned:
                s.template_id = tpl.key
                store.update(s)
            sub_cmd.clear_pending(user_id)
            note = cls._push_sample(owned[0])
            return {"answer": f"✅ 已将 {len(owned)} 个看板订阅切换为「{tpl.name}」模板。"
                              f"{note}",
                    "source": "dashboard"}

        if intent in ("describe_template", "submit_template"):
            # v1.12.0：保存用户模板并应用到订阅
            tdef = pending.get("template_def") or {}
            ok, tpl, message = cls._save_user_template(user_id, tdef)
            if not ok:
                sub_cmd.clear_pending(user_id)
                return {"answer": message, "source": "dashboard"}
            owned = store.list_for_owner(user_id)
            note = "（还没有看板订阅，开通后说「用这个模板」即可切换）"
            if owned:
                for s in owned:
                    s.template_id = tpl.key
                    store.update(s)
                note = cls._push_sample(owned[0])
            sub_cmd.clear_pending(user_id)
            return {"answer":
                    f"✅ 已保存模板「{tpl.name}」并应用到您的看板。{note}",
                    "source": "dashboard"}

        if intent == "edit_template":
            # v1.13.0：编辑已有用户模板内容（精确 key 更新，不靠同名匹配）
            key = pending.get("template_key")
            tpl = cls._match_template_key(key, user_id)
            if not tpl or tpl.scope != "user":
                sub_cmd.clear_pending(user_id)
                return {"answer": "模板不存在或已不是您的私有模板，请重新「看板模板」查看。",
                        "source": "dashboard"}
            tdef = pending.get("template_def") or {}
            from dashboard.template_store import get_template_store
            updated = get_template_store().update_user_template(
                key, user_id,
                description=tdef.get("description", ""),
                map_instructions=tdef.get("map_instructions", ""),
                reduce_instructions=tdef.get("reduce_instructions", ""),
                section_spec=tdef.get("section_spec"))
            if not updated:
                sub_cmd.clear_pending(user_id)
                return {"answer": "模板更新失败，请稍后重试或换一个模板名。",
                        "source": "dashboard"}
            cls._sync_template_file(updated, user_id)
            sub_cmd.clear_pending(user_id)
            owned = store.list_for_owner(user_id)
            note = "（还没有看板订阅，开通后说「用这个模板」即可生效）"
            if owned:
                note = cls._push_sample(owned[0])
            return {"answer":
                    f"✅ 已按新格式覆盖「{updated.name}」模板，引用它的订阅将按新结构出板。{note}",
                    "source": "dashboard"}

        sub = store.get(pending.get("sub_id") or 0)
        if not sub:
            sub_cmd.clear_pending(user_id)
            return {"answer": "订阅不存在，可能是已删除。说「帮我推个看板」重新开通。",
                    "source": "dashboard"}
        if intent == "change_time":
            sub.push_hour = pending.get("push_hour", sub.push_hour)
            sub.push_minute = pending.get("push_minute", sub.push_minute)
        elif intent == "change_freq":
            sub.weekdays = pending.get("weekdays", sub.weekdays)
        elif intent == "change_recipients":
            names = pending.get("recipient_names") or []
            for name in names:
                sid = sub_cmd.resolve_recipient(name)
                if not sid:
                    continue
                if pending.get("add") and sid not in sub.recipients:
                    sub.recipients.append(sid)
                elif not pending.get("add") and sid in sub.recipients:
                    sub.recipients.remove(sid)
        store.update(sub)
        sub_cmd.clear_pending(user_id)
        return {"answer":
                f"✅ 已更新：{sub_cmd.format_weekdays(sub.weekdays)} "
                f"{sub.push_hour:02d}:{sub.push_minute:02d}，接收 {len(sub.recipients)} 人。",
                "source": "dashboard"}

    @classmethod
    def _push_sample(cls, sub: Subscription) -> str:
        """订阅成功后立即推一条样例（数据源 base_id 未配则提示，不影响订阅）"""
        sources = service.resolve_subscription_sources(sub)
        if not sources:
            return "（数据源未配置，配置后每日自动推送）"
        parsed, errors = service.collect_and_parse(
            sources, operator_id=sub.owner_union_id, staff_id=sub.owner_staff_id)
        errors = service.source_resolution_warnings(sub, sources) + errors
        if not parsed:
            msg = "；".join(errors[:2]) or "无数据"
            return f"（推送前准备失败：{msg}，配置好数据源后每日自动推送）"
        # 样例推送与定时推送统一走 LLM 组装（失败规则兜底）；按订阅模板输出格式
        template = service.resolve_template(sub)
        try:
            from skills.agent import call_deepseek_json
            report = service.assemble_report(
                parsed, title=sub.title, date_str=service.today_str(),
                llm_func=call_deepseek_json, old_snapshot=sub.last_snapshot,
                errors=errors, template=template)
        except Exception:
            report = service.assemble_report(
                parsed, title=sub.title, date_str=service.today_str(),
                old_snapshot=sub.last_snapshot, errors=errors, template=template)
        ok, msg = service.push_messages(sub.recipients, sub.title, report.messages)
        if ok:
            return "已推送示例看板给您，可先查看效果！"
        return f"（订阅已开通，但示例推送失败：{msg}）"

    @classmethod
    def _render_subscription_status(cls, user_id: str) -> str:
        store = get_subscription_store()
        subs = store.list_for_owner(user_id)
        if not subs:
            return "您还没有订阅每日看板。说「帮我推个看板」即可开通。"
        lines = ["您的看板订阅："]
        for s in subs:
            status = "✅ 运行中" if s.enabled else "⏸️ 已暂停"
            lines.append(f"- {s.title}（{status}）")
            lines.append(f"  · 时间：{sub_cmd.format_weekdays(s.weekdays)} "
                         f"{s.push_hour:02d}:{s.push_minute:02d}")
            try:
                src_names = "、".join(x.name for x in service.resolve_subscription_sources(s))
            except Exception:
                src_names = ""
            lines.append(f"  · 数据源：{src_names or '（未配置）'}")
            lines.append(f"  · 接收人：{len(s.recipients)} 人")
            lines.append(f"  · 模板：{cls._template_label(s)}")
            lines.append(f"  · 上次推送：{s.last_pushed_at or '尚无'}")
        if subs:
            lines.append("")
            lines.append("说「看板模板」查看可用模板，或「用周报模板」切换输出格式。")
        overlaps = []
        for index, left in enumerate(subs):
            if not left.enabled:
                continue
            left_sources = set(left.data_sources)
            for right in subs[index + 1:]:
                if not right.enabled:
                    continue
                if (left.push_hour, left.push_minute, left.weekdays or "") != (
                        right.push_hour, right.push_minute, right.weekdays or ""):
                    continue
                union = left_sources | set(right.data_sources)
                score = len(left_sources & set(right.data_sources)) / len(union) if union else 1.0
                if score >= 0.5:
                    overlaps.append((left.id, right.id, round(score * 100)))
        if overlaps:
            lines.extend(["", "⚠️ 发现可能重复的订阅："])
            lines.extend(f"- 编号 {a} 与 {b}：同一时间、来源重合 {score}%"
                         for a, b, score in overlaps)
            lines.append("如需清理，可说「删除看板」，我会列出数量并再次确认。")
        return "\n".join(lines)

    @classmethod
    def _stop_subscription(cls, user_id: str) -> str:
        store = get_subscription_store()
        subs = store.list_for_owner(user_id)
        if not subs:
            return "您还没有订阅看板，无需停止。说「帮我推个看板」即可开通。"
        for s in subs:
            store.set_enabled(s.id, False)
        return "已停止您的每日看板推送。想恢复说「重新开通看板」，或直接告诉我调整。"

    @classmethod
    def _resume_subscription(cls, user_id: str) -> str:
        """恢复订阅（v1.11.6）：重新启用已停用订阅，不新建重复订阅"""
        store = get_subscription_store()
        subs = store.list_for_owner(user_id)
        paused = [s for s in subs if not s.enabled]
        if not paused:
            if not subs:
                return "您还没有订阅看板。说「帮我推个看板」即可开通。"
            return "您的看板订阅都在运行中，无需恢复。"
        for s in paused:
            store.set_enabled(s.id, True)
        return f"✅ 已恢复 {len(paused)} 条看板订阅，将按原配置每日推送。"

    @classmethod
    def _precheck_doc_candidates(cls, cands: list) -> tuple[list, list]:
        """创建前权限预检（v1.11.6）：概要模式探测文档可读性

        Returns:
            (usable, blocked) — usable: 可读候选；blocked: [{"name", "reason"}]
        - 明确返回 ok=False（权限/类型不支持/无内容）→ 剔除并记录原因
        - 探测抛权限错（403）→ 剔除
        - 其他异常（网络瞬断/限流）不确定不可读 → 保守放行，避免误杀
        """
        if not cands:
            return [], []
        try:
            from dingtalk_doc_client import (DingTalkDocPermissionError,
                                             get_doc_client)
            client = get_doc_client()
        except Exception:
            return list(cands), []
        usable, blocked = [], []
        for c in cands:
            try:
                result = client.read_document(
                    c.url, operator_id=c.operator_union or "",
                    staff_id="", summary=True)
                if result.get("ok"):
                    real_name = result.get("document_name") or result.get("name") or ""
                    if real_name and real_name != c.name:
                        c.name = real_name
                        try:
                            from dashboard.doc_candidates import get_candidate_store
                            get_candidate_store().update_name(c.id, real_name)
                        except Exception as exc:
                            logger.debug("回填文档真实标题失败(cand=%s): %s", c.id, exc)
                    usable.append(c)
                else:
                    blocked.append({
                        "name": c.name or f"文档{c.node_id[:8]}",
                        "reason": str(result.get("message", "读取失败"))[:80],
                    })
            except DingTalkDocPermissionError:
                blocked.append({
                    "name": c.name or f"文档{c.node_id[:8]}",
                    "reason": "权限不足（应用未授权或文档未分享）",
                })
            except Exception as e:
                logger.warning(f"看板数据源预检异常（放行）{c.node_id[:8]}: {e}")
                usable.append(c)
        return usable, blocked

    @staticmethod
    def _staff_id_of(user_id: str) -> str:
        """user_id(sender_id/unionId) → staff_id（钉钉入口已同步，取存储）"""
        if not user_id:
            return ""
        try:
            from user_store import get_store
            return (get_store().get_user(user_id) or {}).get("staff_id", "") or ""
        except Exception:
            return ""

    # ===== v1.12.0 看板模板辅助 =====
    @classmethod
    def _render_template_list(cls, user_id: str, hint: bool = False) -> str:
        """列出用户可见模板；hint=True 时追加使用提示（切换指令模板名没对上时）"""
        try:
            from dashboard.template_store import get_template_store
            templates = get_template_store().list_visible(user_id)
        except Exception as e:
            logger.warning(f"取模板列表失败: {e}")
            templates = []
        current_key = cls._current_template_key(user_id)
        lines = ["可用的看板模板："]
        if not templates:
            lines.append("-（暂无可用模板）")
        for tpl in templates:
            mark = " ← 当前" if tpl.key == current_key else ""
            tag = "（系统）" if tpl.scope == "system" else "（我的）"
            lines.append(f"- {tpl.name}：{tpl.description}{tag}{mark}")
        if hint:
            lines.extend([
                "",
                "说「用周报模板」「用项目看板」切换；或「按这个格式做看板：负责人/"
                "今日进展/明日计划」自定义模板。",
            ])
        lines.append("（带「我的」标记的模板可编辑：说「编辑<模板名>改成：新格式」覆盖内容）")
        return "\n".join(lines)

    @staticmethod
    def _current_template_key(user_id: str) -> str:
        try:
            subs = get_subscription_store().list_for_owner(user_id)
            if subs:
                return subs[0].template_id or "daily"
        except Exception:
            pass
        return ""

    @classmethod
    def _match_template(cls, text: str, user_id: str):
        """按名称/别名解析模板（TemplateStore.resolve）；返回模板或 None"""
        try:
            from dashboard.template_store import get_template_store
            return get_template_store().resolve(text, user_id)
        except Exception as e:
            logger.warning(f"解析看板模板失败({text}): {e}")
            return None

    @staticmethod
    def _match_template_key(key: str, user_id: str):
        """确认落地时校验模板键仍存在（防止 pending 期间模板被删）"""
        if not key:
            return None
        try:
            from dashboard.template_store import get_template_store
            return get_template_store().get(key, user_id)
        except Exception:
            return None

    @classmethod
    def _save_user_template(cls, user_id: str, tdef: dict) -> tuple:
        """创建/调节用户模板；同名模板更新内容（调节），并同步本地文件。

        v1.12.x：同名再次提交 = 调节模板要求（覆盖 map/reduce/spec），
        不再报「已存在」或加序号后缀；key/name 与系统模板冲突自动避让。
        保存后同步 data/dashboard_templates/{staff_id}/{key}.json。

        Returns: (ok, template_or_None, message)
        """
        from dashboard.template_store import get_template_store
        store_t = get_template_store()
        name = (tdef.get("name") or "").strip() or "自定义模板"
        base_key = "".join((name or "模板").split()) or "模板"
        if base_key in ("daily", "weekly", "project"):
            base_key = base_key + "_u"
        fields = {
            "name": name,
            "description": tdef.get("description", ""),
            "map_instructions": tdef.get("map_instructions", ""),
            "reduce_instructions": tdef.get("reduce_instructions", ""),
            "section_spec": tdef.get("section_spec"),
        }
        # 同名调节：本人已有同名模板 → 更新内容（保留原 key）
        for tpl in store_t.list_visible(user_id):
            if tpl.scope == "user" and tpl.name == name:
                updated = store_t.update_user_template(tpl.key, user_id, **fields)
                if updated:
                    cls._sync_template_file(updated, user_id)
                    return True, updated, ""
        # 新建：key 冲突自动加序号后缀
        for attempt in range(5):
            suffix = "" if attempt == 0 else f"_{attempt + 1}"
            name_candidate = name if attempt == 0 else f"{name}{attempt + 1}"
            res = store_t.create_user_template(
                key=base_key + suffix, name=name_candidate, user_id=user_id,
                description=tdef.get("description", ""),
                map_instructions=tdef.get("map_instructions", ""),
                reduce_instructions=tdef.get("reduce_instructions", ""),
                section_spec=tdef.get("section_spec"))
            if res.get("ok"):
                tpl = res.get("template")
                cls._sync_template_file(tpl, user_id)
                return True, tpl, ""
        return False, None, "模板保存失败（名称占用较多），请换一个模板名再试。"

    @staticmethod
    def _sync_template_file(tpl, user_id: str) -> None:
        """用户模板 → 本地用户文件夹 JSON（失败不影响主流程）"""
        if tpl is None or tpl.scope != "user":
            return
        try:
            from dashboard.template_store import TemplateStore
            TemplateStore.sync_user_template_file(
                tpl, DashboardSkill._staff_id_of(user_id))
        except Exception as e:
            logger.warning(f"看板模板文件同步失败({tpl.key}): {e}")

    @classmethod
    def _user_template_hint(cls, user_id: str) -> str:
        """创建订阅时提示已保存的私有模板（最多列 3 个，回复名字即可复用）

        v1.12.x：用户此前自定义过格式时提醒复用，避免每次重新描述。
        """
        try:
            from dashboard.template_store import get_template_store
            mine = [t for t in get_template_store().list_visible(user_id)
                    if t.scope == "user"]
            if mine:
                names = "、".join(t.name for t in mine[:3])
                return f"\n📂 您保存过模板：{names}，回复模板名即可复用；也可「编辑{names.split('、')[0]}改成：新格式」更新它"
        except Exception:
            pass
        return ""

    # ===== v1.13.0 编辑模板内容（整体重述覆盖） =====
    @classmethod
    def _handle_edit_template(cls, text: str, user_id: str, parsed: dict) -> dict:
        """编辑已有用户模板：目标解析 → 有描述生成 spec 入 pending，无描述展示当前内容引导"""
        try:
            from dashboard.template_store import get_template_store
            store_t = get_template_store()
        except Exception as e:
            logger.warning(f"取模板存储失败: {e}")
            return {"answer": "模板服务暂不可用，请稍后再试。", "source": "dashboard"}

        desc = (parsed.get("description") or "").strip()
        # 目标模板：resolve 整句（限定可见模板），再核对是本人私有模板
        tpl = store_t.resolve(text, user_id)
        if tpl and tpl.scope != "user":
            return {"answer":
                    f"「{tpl.name}」是系统自带模板，不能直接修改。\n"
                    f"可以另建一个类似格式的：回复「按这个格式做看板：{tpl.description}」。",
                    "source": "dashboard"}
        if not tpl:
            return {"answer":
                    "没找到您要编辑的模板。说「看板模板」查看可选模板；"
                    "或回复「编辑<模板名>改成：先写总体结论」描述新格式覆盖它。",
                    "source": "dashboard"}

        if not desc:
            # 无格式描述 → 展示当前内容 + 引导，不 set pending
            return {"answer":
                    cls._render_template_detail(tpl)
                    + "\n\n回复「编辑" + tpl.name
                    + "改成：先写总体结论」描述新格式，确认后覆盖保存。",
                    "source": "dashboard"}

        # 有描述 → LLM 生成新 spec → pending → 确认
        from dashboard.template_builder import describe_to_spec
        tdef = describe_to_spec(desc)
        if not tdef.get("ok"):
            return {"answer": tdef.get("message", "生成模板失败，请换个描述试试。"),
                    "source": "dashboard"}
        # 编辑不换名：保留原 key/name，覆盖其余字段
        tdef["key"] = tpl.key
        tdef["name"] = tpl.name
        pending = {"intent": "edit_template", "template_key": tpl.key,
                   "template_name": tpl.name,
                   "section_spec": tdef.get("section_spec"),
                   "template_def": tdef}
        sub_cmd.set_pending(user_id, pending)
        return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

    @classmethod
    def _render_template_detail(cls, tpl) -> str:
        """展示单个模板当前内容（编辑引导/确认前用）"""
        from dashboard.subscription_commands import format_spec_summary
        lines = [f"📄 模板「{tpl.name}」（{tpl.key}）当前结构：",
                 f"📐 {format_spec_summary(tpl.section_spec)}",
                 f"📝 {tpl.description or '（无描述）'}"]
        return "\n".join(lines)

    @staticmethod
    def _template_label(sub) -> str:
        """订阅 → 模板展示名（缺失回落「每日简报」）"""
        try:
            tpl = service.resolve_template(sub)
            if tpl:
                return f"{tpl.name}（{tpl.key}）"
        except Exception:
            pass
        return "每日简报（daily）"

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

from scripts.dashboard import service
from scripts.dashboard import subscription_commands as sub_cmd
from scripts.dashboard.config_model import KIND_LABELS
from scripts.dashboard.subscription_store import Subscription, get_subscription_store
from scripts.skills import BaseSkill, register

logger = logging.getLogger("dashboard.skill")

# 确认词由 subscription_commands 统一识别，支持“是的，全部删除”等动作复述。

_HELP_TEXT = (
    "我可以帮您开通「每日项目看板」自动推送，也可以查实时看板。试试说：\n"
    "· 「帮我推个看板」— 开通每日自动推送\n"
    "· 「看板今天怎么样」— 查当前看板\n"
    "· 「改看板时间到10点」「每周一和周五」「也推给张工」「停掉看板」\n"
    "· 「看板模板」— 查看模板；「用周报模板」— 切换输出格式；\n"
    "  「按这个格式做看板：负责人/今日进展/明日计划」— 自定义模板"
    "\n· 「查看看板任务提示词」— 查看每个任务实际使用的固定总结指令"
    "\n· 「编辑看板任务提示词改成：……」— 确认后覆盖该任务的总结指令"
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
        # v1.12.6（B5）：口语序数选择（「我说选第一个」）。同样不含「看板」，且绑
        # kanban pending + 反问窗口——否则落 Agent 无工具可用，实测编造「工单转达」幻觉。
        if sub_cmd.parse_spoken_choice(q, user_id) is not None:
            return True
        # 第 0 步：「按这几个文档做每日看板」（_NEGATIVE_RE 含「文档」会拦常规路径，需提前）
        if sub_cmd.parse_doc_dashboard_intent(q) is not None:
            return True
        # v1.12.7：任务级源增删（「把部门周报加进看板」「从看板去掉」同样含「文档/文件」词，
        # 必须在 _NEGATIVE_RE 拦进 Agent 前接住）
        if sub_cmd.parse_source_edit_intent(q) is not None:
            return True
        # v1.12.7：历史留档回放（看上次的看板/查看板历史）——只读查询，同样
        # 须在 is_kanban_topic 的歧义 LLM 判定前接住，避免付无谓的 LLM 成本
        if sub_cmd.parse_history_intent(q) is not None:
            return True
        # 「现在推/模拟/演示」是一次性外发请求，交 Agent 的 dash_push 工具做
        # 统一二次确认；不能被后续「看板」话题或「推给」接收人规则劫持。
        if sub_cmd.is_immediate_push_request(q):
            return False
        if sub_cmd.parse_edit_task_prompt(q) is not None:
            return True
        if sub_cmd.parse_prompt_intent(q) is not None:
            return True
        if sub_cmd.is_kanban_topic(q):
            # v1.12.3：概念疑问句（「看板数据源和看板任务是分开的吗」）→ LLM 判歧义，
            # 判定非订阅管理 → 放行给 Agent 正常问答（修复 query 意图吞问题）。
            # 明确管理动词/明确查询无疑问词 → 走正则快速路径，不付 LLM 成本。
            if sub_cmd.needs_kanban_ambiguity_check(q):
                verified = sub_cmd._llm_verify_subscription(
                    q, fallback_intent="query")
                if verified is None:
                    return False
            return True
        # 订阅管理指令（含"也推给张工"这类不含"看板"的追加指令）
        # v1.12.9：传 user_id 供 is_kanban_context 判定——口语操作句（「帮我停掉这个」
        # 「删除」）在看板对话中由 parse 内 LLM 兜底承接；非看板上下文照旧放行。
        if sub_cmd.parse_subscription_command(q, {"user_id": user_id}) is not None:
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
        # v1.12.6（B5）：口语序数选择（「我说选第一个」等），防落 Agent 幻觉
        spoken = sub_cmd.parse_spoken_choice(q, uid)
        if spoken:
            return cls._handle_spoken_choice(spoken, uid)

        # “现在帮我全部删除”只有在该用户刚查看/管理过看板时才承接，避免误删。
        if sub_cmd.is_contextual_delete(q) and had_recent_activity:
            store = get_subscription_store()
            subs = store.list_for_owner(uid)
            if not subs:
                return {"answer": "您还没有订阅看板，无需删除。", "source": "dashboard"}
            pending = {"intent": "delete", "sub_ids": [s.id for s in subs],
                       "titles": [s.title for s in subs]}
            sub_cmd.set_pending(uid, pending)
            return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

        # 「按这几个文档做每日看板」→ 动态数据源创建
        if sub_cmd.parse_doc_dashboard_intent(q) is not None:
            return cls._handle_doc_create(uid)
        # v1.12.7：任务级源增删（把XX加进看板 / 从看板去掉）
        source_edit = sub_cmd.parse_source_edit_intent(q)
        if source_edit:
            return cls._handle_change_sources(uid, source_edit)
        # v1.12.7：历史留档回放（只读，回放最近一次推送内容）——独立于
        # parse_subscription_command（那是查订阅配置，历史查询语义不同）
        if sub_cmd.parse_history_intent(q) is not None:
            sub_cmd.touch_activity(uid)
            return {"answer": cls._render_push_history(uid), "source": "dashboard"}
        prompt_edit = sub_cmd.parse_edit_task_prompt(q)
        if prompt_edit is not None:
            sub_cmd.touch_activity(uid)
            return cls._handle_edit_task_prompt(uid, prompt_edit)
        if sub_cmd.parse_prompt_intent(q) is not None:
            sub_cmd.touch_activity(uid)
            return {"answer": cls._render_task_prompt(uid), "source": "dashboard"}

        parsed = sub_cmd.parse_subscription_command(q, {"user_id": uid})
        # 防御：match 层已用同参数先判过（LLM 兜底返回 None → match False → 不接），
        # handle 不会出现「match 接了但 parse 返回 None」；此处兜底只是防 handle 被
        # 外部直接调用（测试/其他入口）时误弹帮助文案。
        if not parsed:
            return {"answer": _HELP_TEXT, "source": "dashboard"}
        intent = parsed["intent"]

        # 创建草稿尚未确认时，用户说「改到八点半」是在补充同一个任务，不应
        # 因为数据库里还没有订阅而回答“先开通”。直接更新草稿并重新完整复述。
        pending = sub_cmd.get_pending(uid)
        if (pending and pending.get("intent") in ("create", "doc_create")
                and intent == "change_time"):
            pending["push_hour"] = parsed["push_hour"]
            pending["push_minute"] = parsed["push_minute"]
            sub_cmd.set_pending(uid, pending)
            return {"answer": "已更新这份待确认任务的推送时间。\n\n" +
                    sub_cmd.render_confirmation(pending), "source": "dashboard"}
        if (pending and pending.get("intent") in ("create", "doc_create")
                and intent == "change_freq"):
            pending["weekdays"] = parsed["weekdays"]
            sub_cmd.set_pending(uid, pending)
            return {"answer": "已更新这份待确认任务的推送频率。\n\n" +
                    sub_cmd.render_confirmation(pending), "source": "dashboard"}

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
            from scripts.dashboard.template_builder import describe_to_spec
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
            from scripts.knowledge_review import get_pending_learn
            uploaded = get_pending_learn(uid)
            if not uploaded or not uploaded.get("file_path"):
                return {"answer":
                        "请先上传一个 Excel 或 Markdown 文件（Excel 用第一行表头、"
                        "Markdown 用 # 标题定义章节），再回复「把这个当看板模板」。",
                        "source": "dashboard"}
            from scripts.dashboard.template_builder import parse_template_file
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
            # v1.12.1：编辑模板内容（整体重述覆盖 → 确认 → 覆盖保存）
            sub_cmd.touch_activity(uid)
            return cls._handle_edit_template(q, uid, parsed)

        if intent == "edit_task_prompt":
            sub_cmd.touch_activity(uid)
            return cls._handle_edit_task_prompt(uid, parsed)

        if intent == "resume":
            store = get_subscription_store()
            paused = [s for s in store.list_for_owner(uid) if not s.enabled]
            if not paused:
                return {"answer": cls._resume_subscription(uid), "source": "dashboard"}
            pending = {"intent": "resume", "sub_ids": [s.id for s in paused],
                       "titles": [s.title for s in paused]}
            sub_cmd.set_pending(uid, pending)
            return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

        if intent == "delete":
            store = get_subscription_store()
            subs = store.list_for_owner(uid)
            if not subs:
                return {"answer": "您还没有订阅看板，无需删除。说「帮我推个看板」即可开通。",
                        "source": "dashboard"}
            pending = {"intent": "delete", "sub_ids": [s.id for s in subs],
                       "titles": [s.title for s in subs]}
            sub_cmd.set_pending(uid, pending)
            return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

        if intent == "set_per_source":
            # v1.12.5：每源独立总结（开启/关闭 → 确认 → 落地）
            return cls._handle_set_per_source(q, uid, parsed)

        if intent == "change_sources":
            # v1.12.9：LLM 兜底分类出的源增删——正则 parse_source_edit_intent 已在
            # match 前段接住精确指令（带 doc_name/action），这里只剩口语操作句
            # （无「看板」词、LLM 只判意图不给参数），action 从文本规则推断、
            # doc_name 空走最近候选兜底，写操作仍反问确认后才落地。
            return cls._handle_llm_change_sources(q, uid)

        if intent == "set_recipient_self":
            return {"answer": sub_cmd.render_confirmation({"intent": "set_recipient_self"}),
                    "source": "dashboard"}

        if intent == "stop":
            store = get_subscription_store()
            active = [s for s in store.list_for_owner(uid) if s.enabled]
            if not active:
                return {"answer": cls._stop_subscription(uid), "source": "dashboard"}
            pending = {"intent": "stop", "sub_ids": [s.id for s in active],
                       "titles": [s.title for s in active]}
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
    def _handle_set_per_source(cls, text: str, user_id: str, parsed: dict) -> dict:
        """v1.12.5：每源独立总结（开启/关闭 → 确认 → 落地）

        用户说「四份文件各自独立总结」「合并成一份」——不带「看板」也能进
        （parse 门槛已放宽）。输出模式是订阅级配置，确认后按真实 update 结果回报，
        不编造「已生效」。
        """
        store = get_subscription_store()
        subs = store.list_for_owner(user_id)
        if not subs:
            return {"answer": "您还没有订阅看板。说「帮我推个看板」先开通，再调整输出方式。",
                    "source": "dashboard"}
        pending = {"intent": "set_per_source", "sub_id": subs[0].id,
                   "per_source": bool(parsed.get("per_source"))}
        sub_cmd.set_pending(user_id, pending)
        return {"answer": sub_cmd.render_confirmation(pending, current=subs[0]),
                "source": "dashboard"}

    @classmethod
    def _handle_doc_create(cls, user_id: str,
                           source_candidate_ids: list[int] | None = None,
                           request_text: str = "") -> dict:
        """「按这几个文档做每日看板」：取文档候选 → 反问确认

        v1.11.4：`source_candidate_ids` 为本次消息识别的候选 id 时只取这些
        （避免历史候选混入）；缺省时兜底取全部可看板候选（如纯「帮我推个看板」）。
        """
        try:
            from scripts.dashboard.doc_candidates import get_candidate_store
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
        requested_time = sub_cmd._parse_time(request_text) if request_text else None
        needs_coordination = bool(re.search(
            r"(?:结论|关注(?:的问题|事项)?|协调(?:的事情|事项)?).{0,30}(?:结论|关注|协调)|"
            r"(?:结论|关注|协调).{0,30}(?:结论|关注|协调)", request_text or ""))
        pending = {
            "intent": "doc_create",
            "data_sources": [f"doc_{c.id}" for c in cands],
            "push_hour": requested_time[0] if requested_time else 9,
            "push_minute": requested_time[1] if requested_time else 0,
            "weekdays": "", "alert_mode": "always",
            "title": "恩特能源每日项目看板",
            "owner_user_id": user_id,
            "owner_staff_id": staff_id,
            "recipients": [staff_id] if staff_id else [],
        }
        if needs_coordination:
            pending["template_id"] = "coordination"
            pending["output_summary"] = "精炼结论、需要关注的问题、需要协调的事情"
        sub_cmd.set_pending(user_id, pending)
        return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

    @classmethod
    def _handle_change_sources(cls, user_id: str, parsed: dict) -> dict:
        """v1.12.7：任务级源增删（把XX加进看板 / 从看板去掉 → 确认 → 落地）

        目标任务 = 该用户最近一个订阅（list_for_owner 升序，最近创建在最后）。
        文档名匹配候选：精确 → 模糊包含 → 未知名兜底最近候选；匹配不到列出
        当前任务源 + 可加候选引导，不编造「已加入」。
        """
        store = get_subscription_store()
        subs = store.list_for_owner(user_id)
        if not subs:
            return {"answer":
                    "您还没有看板任务。说「帮我推个看板」先开通，再增减数据源。",
                    "source": "dashboard"}
        target = subs[-1]
        doc = cls._match_source_doc(user_id, parsed.get("doc_name") or "")
        if doc is None:
            return {"answer": cls._source_candidates_hint(user_id, target),
                    "source": "dashboard"}
        action = parsed.get("action") or "add"
        pending = {"intent": "change_sources", "sub_id": target.id,
                   "action": action,
                   "doc_name": doc.name or f"文档{doc.node_id[:8]}",
                   "doc_key": f"doc_{doc.id}"}
        sub_cmd.set_pending(user_id, pending)
        return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

    @classmethod
    def _handle_llm_change_sources(cls, text: str, user_id: str) -> dict:
        """v1.12.9：LLM 兜底分类出的源增删（口语操作句，无「看板」词）。

        正则 source_edit 路径（parse_source_edit_intent）能给出精确 doc_name/action；
        LLM 兜底只给意图名不给参数，这里 action 从文本规则推断：
          「全部/所有/清空」→ remove_all（清空任务全部数据源）
          「删/去/移/拿掉/取消/清」→ remove（移除单个源）
          否则 → add
        doc_name 空则走最近候选兜底（_match_source_doc(uid, "")），匹配不到列出
        候选引导；写操作一律反问确认后才落地，不编造「已生效」。
        """
        t = (text or "").strip()
        if any(w in t for w in ("全部", "所有", "清空")):
            action = "remove_all"
        elif re.search(r"删|去|移|拿掉|取消|清", t):
            action = "remove"
        else:
            action = "add"
        store = get_subscription_store()
        subs = store.list_for_owner(user_id)
        if not subs:
            return {"answer":
                    "您还没有看板任务。说「帮我推个看板」先开通，再增减数据源。",
                    "source": "dashboard"}
        target = subs[-1]
        if action == "remove_all":
            if not (target.data_sources or []):
                return {"answer": "当前看板任务没有配置数据源，无需清空。",
                        "source": "dashboard"}
            pending = {"intent": "change_sources", "sub_id": target.id,
                       "action": "remove_all", "doc_name": "全部数据源", "doc_key": ""}
            sub_cmd.set_pending(user_id, pending)
            return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}
        doc = cls._match_source_doc(user_id, "")
        if doc is None:
            return {"answer": cls._source_candidates_hint(user_id, target),
                    "source": "dashboard"}
        pending = {"intent": "change_sources", "sub_id": target.id,
                   "action": action,
                   "doc_name": doc.name or f"文档{doc.node_id[:8]}",
                   "doc_key": f"doc_{doc.id}"}
        sub_cmd.set_pending(user_id, pending)
        return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

    @classmethod
    def _match_source_doc(cls, user_id: str, name: str):
        """按文档名匹配候选：精确 → 模糊包含 → 未知名兜底最近候选。

        find_by_name 是精确匹配；补一层包含匹配容错（如「周报」命中
        「33周部门周报」）。返回 DocCandidate 或 None（可加源且 enabled）。
        """
        try:
            from scripts.dashboard.doc_candidates import get_candidate_store
            store = get_candidate_store()
            if not name:
                cand = store.get_pending(user_id)
                return cand if cand and cand.enabled else None
            cand = store.find_by_name(user_id, name)
            if cand and cand.enabled:
                return cand
            for c in store.list_dashboard_ready(user_id):
                cname = c.name or ""
                if cname and (name in cname or cname in name):
                    return c
        except Exception as e:
            logger.warning(f"匹配看板源文档失败({name}): {e}")
        return None

    @classmethod
    def _source_candidates_hint(cls, user_id: str, target) -> str:
        """匹配不到文档时：列出当前任务绑定源 + 可加候选，引导用户说清名字。"""
        lines = ["没认出您说的文档。当前看板任务的数据源："]
        try:
            src_names = "、".join(
                x.name for x in service.resolve_subscription_sources(target))
        except Exception:
            src_names = ""
        lines.append(f"· {src_names or '（未配置）'}")
        lines.append("")
        lines.append("您登记过的可加文档：")
        try:
            from scripts.dashboard.doc_candidates import get_candidate_store
            cands = get_candidate_store().list_dashboard_ready(user_id)
        except Exception:
            cands = []
        if cands:
            lines.extend(f"· {c.name or f'文档{c.node_id[:8]}'}" for c in cands)
        else:
            lines.append("·（暂无登记文档，先发文档链接给机器人）")
        lines.append("")
        lines.append("回复「把<文档名>加进看板」或「把<文档名>从看板去掉」即可调整。")
        return "\n".join(lines)

    @classmethod
    def _build_create_pending(cls, user_id: str) -> dict:
        staff_id = cls._staff_id_of(user_id)
        # v1.12.7（D1）：任务级绑定——创建时快照 owner 当前 enabled 候选做
        # 任务唯一事实源；之后新发布的文档默认不进此任务，需主动说
        # 「把这个文档加进看板」（change_sources 意图）。
        sources = []
        try:
            from scripts.dashboard.doc_candidates import get_candidate_store
            cands = get_candidate_store().list_dashboard_ready(user_id)
            sources = [f"doc_{c.id}" for c in cands]
        except Exception as e:
            logger.warning(f"拉取创建看板候选失败: {e}")
        return {
            "intent": "create",
            "data_sources": sources,
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
            from scripts.dashboard.template_store import get_template_store
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
            service.ensure_task_prompt(s, force=True)
            store.update(s)
        note = cls._push_sample(owned[0])
        return {"answer": f"✅ 已将看板切换为「{tpl.name}」模板。{note}",
                "source": "dashboard"}

    @classmethod
    def _handle_spoken_choice(cls, choice: dict, user_id: str) -> dict:
        """口语序数选择（v1.12.6，B5）——接住「我说选第一个」防落 Agent 幻觉。

        仅 choose_template 反问有编号候选项可映射（第 1→每日、第 2→周报、第 3→
        项目，与数字 1/2/3 同义）；其余 pending 的确认流程没有「第 N 项」候选项，
        如实说明并重申确认入口——不编造能力（工单幻觉根因之一，袁会荧 2026-08-14）。
        """
        uid = user_id or ""
        pending = sub_cmd.get_pending(uid)
        index = choice.get("index") or 0
        if pending and pending.get("intent") == "choose_template":
            if 1 <= index <= 3:
                return cls._execute_template_choice(
                    {"intent": "choose_template",
                     "choice": ("daily", "weekly", "project")[index - 1]}, uid)
            # 超出候选项 → 重列模板（不编造），pending 保留待用户再选
            return {"answer":
                    "当前只有 3 个模板可选：\n"
                    "1 每日简报\n2 周报总结\n3 项目看板\n"
                    "回复数字或模板名即可。",
                    "source": "dashboard"}
        # 其余 pending：确认流程没有分项候选项，如实说明并重申确认入口
        if pending:
            base = sub_cmd.render_confirmation(pending)
            return {"answer":
                    f"收到，您说的是第 {index} 项。不过我正等您确认的看板操作没有分项"
                    f"候选项——请回复「确认」执行，或直接告诉我调整（如「改到10点」"
                    f"「每周一和周五」「停掉看板」）。\n"
                    f"（说明：我没有「工单/转达」这类功能，如需调整看板请直接说具体指令。）\n\n"
                    f"{base}",
                    "source": "dashboard"}
        return {"answer": "我正等您确认看板操作，但还没收到待确认内容。"
                          "说「帮我推个看板」即可开通。",
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
                template_id=pending.get("template_id", "daily"),
            )
            # v1.13.1：创建时固化任务提示词和模板结构快照；后续每次执行
            # 只读取这份快照，不让模板代码的动态变化成为隐藏行为变化。
            service.ensure_task_prompt(sub)
            duplicate = store.find_exact_duplicate(sub)
            if duplicate:
                if not duplicate.enabled:
                    store.set_enabled(duplicate.id, True)
                    state = "已恢复原订阅"
                else:
                    state = "未重复创建"
                sub_cmd.clear_pending(user_id)
                # v1.13.3：不再展示数据库自增 id（删任务后留洞会误导）；如需
                # 定位，用户可「查看看板」看动态序号。
                return {
                    "answer": f"✅ 已有相同的看板订阅，{state}。",
                    "source": "dashboard",
                }
            sub.id = store.create(sub)
            service.sync_task_prompt_file(sub)
            sub_cmd.clear_pending(user_id)
            note = cls._push_sample(sub)
            # v1.12.0：创建后主动反问选模板（用户可回复 1/2/3/模板名/不用了，
            # 或不理会保持默认每日；pending 供 parse_template_choice 识别选择回复）
            # v1.12.x：提醒可自定义格式；有私有模板时列出提醒复用
            my_templates = cls._user_template_hint(user_id)
            # 用户已经在原始请求中明确了三段式输出要求时，直接应用对应系统模板，
            # 不再反问一次模板选择，避免“我明明说了要什么格式”却被机械追问。
            if pending.get("template_id") == "coordination":
                return {"answer": f"✅ 已为您开通每日看板推送（管理晨报模板）！{note}\n"
                                   "后续将按「精炼结论 / 需要关注的问题 / 需要协调的事情」输出。",
                        "source": "dashboard"}
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

        if intent == "set_per_source":
            # v1.12.5：每源独立总结（按真实 update 结果回报，禁止吞错谎报）
            sub = store.get(pending.get("sub_id") or 0)
            if not sub:
                sub_cmd.clear_pending(user_id)
                return {"answer": "订阅不存在，可能是已删除。说「帮我推个看板」重新开通。",
                        "source": "dashboard"}
            owned_ids = {s.id for s in store.list_for_owner(user_id)}
            if sub.id not in owned_ids:
                sub_cmd.clear_pending(user_id)
                return {"answer": "该订阅不属于您，无法调整输出方式。", "source": "dashboard"}
            per_source = bool(pending.get("per_source"))
            sub.per_source = per_source
            store.update(sub)
            sub_cmd.clear_pending(user_id)
            mode = "每个数据源单独总结一条" if per_source else "合并成一份看板报告"
            return {"answer":
                    f"✅ 已切换订阅输出方式：{mode}。下次推送即按新方式出板。",
                    "source": "dashboard"}

        if intent == "change_sources":
            # v1.12.7（D1）：任务级源增删落地（按真实 update 结果回报）
            sub = store.get(pending.get("sub_id") or 0)
            if not sub:
                sub_cmd.clear_pending(user_id)
                return {"answer": "订阅不存在，可能是已删除。说「帮我推个看板」重新开通。",
                        "source": "dashboard"}
            owned_ids = {s.id for s in store.list_for_owner(user_id)}
            if sub.id not in owned_ids:
                sub_cmd.clear_pending(user_id)
                return {"answer": "该任务不属于您，无法修改数据源。", "source": "dashboard"}
            action = pending.get("action")
            key = str(pending.get("doc_key") or "")
            name = pending.get("doc_name") or ""
            current = [str(x) for x in (sub.data_sources or [])]
            if action == "remove_all":
                # v1.12.9：清空任务全部数据源（「删除全部数据源」口语操作，
                # LLM 兜底分类 change_sources → 确认后落地，按真实 update 回报）
                if not current:
                    sub_cmd.clear_pending(user_id)
                    return {"answer": "当前看板任务没有配置数据源，无需清空。",
                            "source": "dashboard"}
                sub.data_sources = []
                store.update(sub)
                sub_cmd.clear_pending(user_id)
                return {"answer":
                        "✅ 已清空看板任务的全部数据源，下次推送将无数据可出。"
                        "需要恢复可说「把XX加进看板」。",
                        "source": "dashboard"}
            if action == "add":
                if not key:
                    sub_cmd.clear_pending(user_id)
                    return {"answer": "未找到要加入的文档，请重发文档链接后再试。",
                            "source": "dashboard"}
                if key in current:
                    sub_cmd.clear_pending(user_id)
                    return {"answer": f"《{name}》已在任务中，无需重复添加。",
                            "source": "dashboard"}
                current.append(key)
                sub.data_sources = current
                store.update(sub)
                sub_cmd.clear_pending(user_id)
                return {"answer": f"✅ 已将《{name}》加入看板任务，下次推送即包含该文件。",
                        "source": "dashboard"}
            if key in current:
                current.remove(key)
                sub.data_sources = current
                store.update(sub)
                sub_cmd.clear_pending(user_id)
                return {"answer": f"✅ 已将《{name}》从看板任务移除，下次推送不再包含该文件。",
                        "source": "dashboard"}
            sub_cmd.clear_pending(user_id)
            return {"answer": f"《{name}》不在当前任务中，无需移除。", "source": "dashboard"}

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
                service.ensure_task_prompt(s, force=True)
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
                    service.ensure_task_prompt(s, force=True)
                    store.update(s)
                note = cls._push_sample(owned[0])
            sub_cmd.clear_pending(user_id)
            return {"answer":
                    f"✅ 已保存模板「{tpl.name}」并应用到您的看板。{note}",
                    "source": "dashboard"}

        if intent == "edit_template":
            # v1.12.1：编辑已有用户模板内容（精确 key 更新，不靠同名匹配）
            key = pending.get("template_key")
            tpl = cls._match_template_key(key, user_id)
            if not tpl or tpl.scope != "user":
                sub_cmd.clear_pending(user_id)
                return {"answer": "模板不存在或已不是您的私有模板，请重新「看板模板」查看。",
                        "source": "dashboard"}
            tdef = pending.get("template_def") or {}
            from scripts.dashboard.template_store import get_template_store
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
                for s in owned:
                    service.ensure_task_prompt(s, force=True)
                    store.update(s)
                note = cls._push_sample(owned[0])
            return {"answer":
                    f"✅ 已按新格式覆盖「{updated.name}」模板，引用它的订阅将按新结构出板。{note}",
                    "source": "dashboard"}

        if intent == "edit_task_prompt":
            sub = store.get(int(pending.get("sub_id") or 0))
            if not sub or sub.owner_user_id != user_id:
                sub_cmd.clear_pending(user_id)
                return {"answer": "任务不存在、已删除，或不属于您，未修改提示词。",
                        "source": "dashboard"}
            try:
                from scripts.dashboard.task_prompt import set_custom_prompt
                set_custom_prompt(sub, pending.get("task_prompt") or "")
            except ValueError as exc:
                sub_cmd.clear_pending(user_id)
                return {"answer": f"提示词未保存：{exc}", "source": "dashboard"}
            store.update(sub)
            service.sync_task_prompt_file(sub)
            sub_cmd.clear_pending(user_id)
            # v1.13.3：动态位置序号回报，不展示数据库自增 id
            pos = pending.get("sub_position")
            target = f"第 {pos} 个任务" if pos else "该任务"
            return {"answer":
                    f"✅ 已更新{target}的固定提示词（版本 {sub.task_prompt_version}，"
                    f"哈希 {sub.task_prompt_hash}）。下一次看板推送会先原样发出这份提示词，"
                    "再发送本次文档总结和最终来源链接。",
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
        had_task_prompt = bool(getattr(sub, "task_prompt", "")
                               and getattr(sub, "task_prompt_spec", None))
        task_prompt = service.ensure_task_prompt(sub)
        if not had_task_prompt and sub.id:
            get_subscription_store().update(sub)
        sources = service.resolve_subscription_sources(sub)
        if not sources:
            return "（数据源未配置，配置后每日自动推送）"
        parsed, errors = service.collect_and_parse(
            sources, operator_id=sub.owner_union_id, staff_id=sub.owner_staff_id)
        errors = service.source_resolution_warnings(sub, sources) + errors
        if not parsed:
            msg = "；".join(errors[:2]) or "无数据"
            return f"（推送前准备失败：{msg}，配置好数据源后每日自动推送）"
        # 样例推送（识别/预览场景）v1.12.5 改逐源多条：每个数据源单独 LLM 总结
        # 发一条，逐源聚焦、全面识别不被单条字数限制压缩；而不是一份报告切页。
        # 复用 LLM 组装（失败规则兜底）+ 订阅模板输出格式。
        template = service.resolve_template(sub)
        try:
            from scripts.skills.agent import call_dashboard_json
            msgs = service.assemble_per_source_messages(
                parsed, title=sub.title, date_str=service.today_str(),
                llm_func=call_dashboard_json, old_snapshot=sub.last_snapshot,
                errors=errors, template=template, task_prompt=task_prompt)
        except Exception:
            msgs = service.assemble_per_source_messages(
                parsed, title=sub.title, date_str=service.today_str(),
                old_snapshot=sub.last_snapshot, errors=errors, template=template,
                task_prompt=task_prompt)
        prompt_message = service.render_task_prompt_message(sub)
        source_links = service.render_source_links(sources)
        msgs = [prompt_message] + msgs + ([source_links] if source_links else [])
        ok, msg = service.push_messages(sub.recipients, sub.title, msgs)
        if ok:
            return "已推送示例看板给您（每个数据源一条），可先查看效果！"
        return f"（订阅已开通，但示例推送失败：{msg}）"

    @classmethod
    def _render_subscription_status(cls, user_id: str) -> str:
        store = get_subscription_store()
        subs = store.list_for_owner(user_id)
        if not subs:
            return "您还没有订阅每日看板。说「帮我推个看板」即可开通。"
        lines = ["您的看板订阅："]
        # v1.13.3：任务序号 = 动态位置（删除任务后自动重排），与「编辑第 N 个
        # 任务提示词」「第 N 个订阅」的解析序号一致，不再是数据库自增 id。
        for index, s in enumerate(subs, 1):
            status = "✅ 运行中" if s.enabled else "⏸️ 已暂停"
            lines.append(f"{index}. {s.title}（{status}）")
            lines.append(f"  · 时间：{sub_cmd.format_weekdays(s.weekdays)} "
                         f"{s.push_hour:02d}:{s.push_minute:02d}")
            try:
                resolved = service.resolve_subscription_sources(s)
            except Exception:
                resolved = []
            # v1.13.3：逐条列源（名称 + 类型），让「任务绑了哪几个源」一眼可辨
            if resolved:
                lines.append(f"  · 绑定 {len(resolved)} 个数据源：")
                for i, src in enumerate(resolved, 1):
                    label = KIND_LABELS.get(getattr(src, "kind", ""), "")
                    name = getattr(src, "name", "") or "数据源"
                    lines.append(f"    {i}. {name}"
                                 + (f"（{label}）" if label else ""))
            else:
                lines.append("  · 数据源：（未配置）")
            lines.append(f"  · 接收人：{len(s.recipients)} 人")
            lines.append(f"  · 模板：{cls._template_label(s)}")
            lines.append(f"  · 任务提示词：{s.task_prompt_hash or '待首次执行固化'}")
            lines.append(f"  · 上次推送：{s.last_pushed_at or '尚无'}")
            # 调度失败不能只留在服务端日志：订阅所有者查询时要能分辨成功、
            # 静默跳过和实际失败，也要知道失败提醒有没有送达。
            if s.last_run_status:
                labels = {
                    "success": "✅ 成功",
                    "skipped": "⏭️ 已跳过",
                    "failed": "❌ 失败",
                }
                run_label = labels.get(s.last_run_status, s.last_run_status)
                lines.append(f"  · 最近执行：{run_label}（{s.last_run_at or '时间未记录'}）")
                if s.last_run_reason:
                    lines.append(f"    原因：{s.last_run_reason}")
                if s.last_alert_status in {"sent", "failed", "not_sent"}:
                    alert_labels = {
                        "sent": "已向管理员发送失败提醒",
                        "failed": "失败提醒发送失败，请联系管理员查看服务日志",
                        "not_sent": "未配置可接收失败提醒的人员",
                    }
                    lines.append(f"    提醒：{alert_labels[s.last_alert_status]}")
        if subs:
            lines.append("")
            lines.append("说「看板模板」查看可用模板，或「用周报模板」切换输出格式。")
        overlaps = []
        for index, left in enumerate(subs, 1):
            if not left.enabled:
                continue
            # v1.12.7（D1）：重合度按任务绑定解析的有效来源集合判断（每个
            # 任务绑定自己的源，实时算以任务为边界）。
            left_sources = service.effective_source_keys(left)
            for j, right in enumerate(subs[index:], index + 1):
                if not right.enabled:
                    continue
                if (left.push_hour, left.push_minute, left.weekdays or "") != (
                        right.push_hour, right.push_minute, right.weekdays or ""):
                    continue
                right_sources = service.effective_source_keys(right)
                union = left_sources | right_sources
                score = len(left_sources & right_sources) / len(union) if union else 1.0
                if score >= 0.5:
                    overlaps.append((index, j, round(score * 100)))
        if overlaps:
            lines.extend(["", "⚠️ 发现可能重复的订阅："])
            # v1.13.3：用动态位置序号（第 N 个），不展示数据库自增 id
            lines.extend(f"- 第 {a} 个与第 {b} 个：同一时间、来源重合 {score}%"
                         for a, b, score in overlaps)
            lines.append("如需清理，可说「删除看板」，我会列出数量并再次确认。")
        return "\n".join(lines)

    @classmethod
    def _render_push_history(cls, user_id: str) -> str:
        """v1.12.7：回放最近一次推送留档（只读）。

        用户拍板「留档默认保留最近 30 次，每个任务 30 次」——这里取该用户
        最近一个订阅（list_for_owner 升序，最近创建在最后）的最近一次留档。
        无订阅/无留档分别引导；内容超长截断到钉钉安全长度，避免整条发不出。
        """
        store = get_subscription_store()
        subs = store.list_for_owner(user_id)
        if not subs:
            return "您还没有订阅看板。说「帮我推个看板」开通后，每次推送都会留档，随时可回看。"
        target = subs[-1]
        try:
            from scripts.dashboard.push_history import get_push_history_store
            latest = get_push_history_store().latest(target.id)
        except Exception as e:
            logger.warning(f"回放看板留档失败({target.id}): {e}")
            latest = None
        if latest is None:
            return ("该任务还没有推送留档——第一次推送成功后会存档，"
                    "届时说「看上次的看板」即可回放。")
        title = latest.title or "恩特能源每日项目看板"
        body = latest.content or ""
        if len(body) > 4500:
            body = body[:4500] + "\n\n…（内容较长已截断，可查看留档原文）"
        lines = [f"📚 {title}（{latest.pushed_at}）"]
        if latest.prompt_hash:
            lines.append(f"任务提示词：{latest.prompt_version or 'task-prompt-v1'} / {latest.prompt_hash}")
        lines.extend(["", body])
        lines.extend(["", f"（该任务最近 {cls._history_count(target.id)} 次推送已留档，"
                          "可追溯）"])
        return "\n".join(lines)

    @classmethod
    def _render_task_prompt(cls, user_id: str) -> str:
        """展示任务实际采用的固定提示词，解除总结黑箱。"""
        store = get_subscription_store()
        subs = store.list_for_owner(user_id)
        if not subs:
            return "您还没有看板任务。开通后可以说「查看看板任务提示词」查看固定指令。"
        lines = ["🧾 看板任务固定提示词（只读）"]
        for index, sub in enumerate(subs, 1):
            if not sub.task_prompt:
                service.ensure_task_prompt(sub)
                store.update(sub)
            lines.extend([
                "", f"任务 {index}：{sub.title}",
                f"提示词版本：{sub.task_prompt_version or 'task-prompt-v1'}",
                f"提示词哈希：{sub.task_prompt_hash or '未生成'}",
                "执行方式：每次运行临时读取本提示词和本次文档数据；本次完成后丢弃临时上下文。",
                "---",
                sub.task_prompt or "（尚未生成，下一次执行时会固化）",
            ])
        lines.append("")
        # v1.13.3：任务序号是动态位置（删除任务后自动重排），编辑用「编辑第 N 个任务…」
        lines.append("任务序号按当前顺序动态排列，删除任务后自动重排，不占用编号。"
                     "编辑用「编辑第 N 个任务提示词改成：……」")
        return "\n".join(lines)

    @classmethod
    def _handle_edit_task_prompt(cls, user_id: str, parsed: dict) -> dict:
        """编辑单个任务的固定提示词：明确任务、展示覆盖内容、二次确认。"""
        store = get_subscription_store()
        subs = store.list_for_owner(user_id)
        if not subs:
            return {"answer": "您还没有看板任务，无法编辑提示词。", "source": "dashboard"}
        requested = int(parsed.get("task_id") or 0)
        # v1.13.3：编号 = 动态位置序号（list_for_owner 顺序 1..N），删除任务
        # 后自动重排，已删除任务不占用编号——不再拿序号直接当数据库 id（删过
        # 任务后 id 留洞会指错任务）。
        if requested:
            if 1 <= requested <= len(subs):
                sub = subs[requested - 1]
            else:
                return {"answer": f"未找到第 {requested} 个您的看板任务。"
                                  "先说「查看看板任务提示词」查看当前任务序号。",
                        "source": "dashboard"}
        elif len(subs) == 1:
            sub = subs[0]
        else:
            choices = "；".join(f"第{i}个：{item.title}"
                                for i, item in enumerate(subs, 1))
            return {"answer": "您有多个看板任务，请指定要改哪一个：" + choices + "。\n"
                              "例如：编辑第 3 个任务提示词改成：……",
                    "source": "dashboard"}
        if not sub.task_prompt:
            service.ensure_task_prompt(sub)
            store.update(sub)
            service.sync_task_prompt_file(sub)
        position = subs.index(sub) + 1
        new_prompt = (parsed.get("task_prompt") or "").strip()
        if not new_prompt:
            return {"answer": f"第 {position} 个任务（{sub.title}）的固定提示词如下：\n\n"
                              f"{sub.task_prompt}\n\n"
                              "请使用「编辑看板任务提示词改成：完整新提示词」提交新版本。"
                              "若有多个任务，请加“编辑第 N 个的…”。",
                    "source": "dashboard"}
        if len(new_prompt) < 20:
            return {"answer": "新的提示词太短。请至少写清楚要关注什么、如何排序或输出什么，"
                              "不少于 20 个字符。", "source": "dashboard"}
        if len(new_prompt) > 4000:
            return {"answer": "新的提示词超过 4000 个字符，请精简后再提交。", "source": "dashboard"}
        pending = {"intent": "edit_task_prompt", "sub_id": sub.id,
                   "sub_position": position, "task_prompt": new_prompt}
        sub_cmd.set_pending(user_id, pending)
        return {"answer": sub_cmd.render_confirmation(pending), "source": "dashboard"}

    @staticmethod
    def _history_count(sub_id: int) -> int:
        try:
            from scripts.dashboard.push_history import get_push_history_store
            return get_push_history_store().count(sub_id)
        except Exception:
            return 0

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
            from scripts.dingtalk_doc_client import (DingTalkDocPermissionError,
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
                            from scripts.dashboard.doc_candidates import get_candidate_store
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
            from scripts.user_store import get_store
            return (get_store().get_user(user_id) or {}).get("staff_id", "") or ""
        except Exception:
            return ""

    # ===== v1.12.0 看板模板辅助 =====
    @classmethod
    def _render_template_list(cls, user_id: str, hint: bool = False) -> str:
        """列出用户可见模板；hint=True 时追加使用提示（切换指令模板名没对上时）"""
        try:
            from scripts.dashboard.template_store import get_template_store
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
            from scripts.dashboard.template_store import get_template_store
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
            from scripts.dashboard.template_store import get_template_store
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
        from scripts.dashboard.template_store import get_template_store
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
            from scripts.dashboard.template_store import TemplateStore
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
            from scripts.dashboard.template_store import get_template_store
            mine = [t for t in get_template_store().list_visible(user_id)
                    if t.scope == "user"]
            if mine:
                names = "、".join(t.name for t in mine[:3])
                return f"\n📂 您保存过模板：{names}，回复模板名即可复用；也可「编辑{names.split('、')[0]}改成：新格式」更新它"
        except Exception:
            pass
        return ""

    # ===== v1.12.1 编辑模板内容（整体重述覆盖） =====
    @classmethod
    def _handle_edit_template(cls, text: str, user_id: str, parsed: dict) -> dict:
        """编辑已有用户模板：目标解析 → 有描述生成 spec 入 pending，无描述展示当前内容引导"""
        try:
            from scripts.dashboard.template_store import get_template_store
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
        from scripts.dashboard.template_builder import describe_to_spec
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
        from scripts.dashboard.subscription_commands import format_spec_summary
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

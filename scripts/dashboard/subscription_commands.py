"""
看板订阅管理指令解析（v1.11.0）

用户用自然语言管理看板订阅。保守触发词 + 否定词防误触
（文档/方案/方法论/学习/入库等与"操作订阅"无关）。

流程：识别意图 → 存 pending（内存态）→ 反问确认 → 用户确认后落地（阶段 5 技能层）。

- parse_subscription_command(text, ctx) -> None | dict
    识别出看板管理意图返回 pending dict；非看板指令返回 None（不拦截，走 Agent 循环）。
- render_confirmation(pending, current=None) -> str   反问确认文案
- set_pending / get_pending / clear_pending           内存 pending（重启失效，重说即可）
- resolve_recipient(name) -> staff_id                 姓名 → userId（复用 contact_api.search）
"""

import json
import logging
import re
import threading
import time
from typing import Optional

logger = logging.getLogger("dashboard.subscription_commands")

# 与"操作订阅"无关的看板话题（不拦截，放行给 Agent 正常聊/调 query_dashboard 工具）
# 「怎么用/怎么样/什么情况」等是求助或查询实时看板，不是订阅管理
# v1.11.5 追加「功能/关系/干嘛/为什么」等反向词：质疑反问「这跟看板功能有什么关系」
# 不是开通订阅，应放行给 Agent；「看看/想看看/看一下」是实时查询（Agent→query_dashboard）
_NEGATIVE_RE = re.compile(
    r"文档|方案|方法论|学习|入库|设计|功能说明|怎么用|怎么配|怎么设|怎么操作|"
    r"怎么看|怎么查|怎么样|什么情况|什么进展|是什么|介绍|"
    r"看看|想看看|看一下|"
    r"功能|关系|干嘛|为什么|为啥|这跟|有什么用|干嘛的|干嘛用|与.*有关")
# 看板话题门槛：必须出现「看板」
_HAS_KANBAN_RE = re.compile(r"看板")

# 「按这几个文档做每日看板」：文档引用 + 动作 + 看板（注意 _NEGATIVE_RE 含「文档」，
# 常规 is_kanban_topic 会拦下，本意图判定放最前单独处理）
_DOC_REF_RE = re.compile(r"文档|表格|文件|共享表")
_DOC_ACTION_RE = re.compile(r"做|建|开通|开|设置|生成|推送|推|发")
_LEARN_ONLY_RE = re.compile(r"学习|入库|帮我学")

# 意图触发词（用 .* 连接「停掉」与「看板」，兼容口语插字：停掉前面的看板 / 把看板停掉）
_STOP_RE = re.compile(
    r"停掉.*看板|停用.*看板|停止.*看板|取消.*看板|关掉.*看板|"
    r"看板.*停掉|看板.*停用|看板.*停止|看板.*取消|看板.*关掉|"
    r"不要.*看板|别再.*看板|退订")
# 查询类保守匹配（不含「时间」——"改看板时间到10点"是改时间不是查设置）
_QUERY_RE = re.compile(r"看板.*(几点|什么时候|设置|在哪|是谁)|我的看板|看板状态|看板.*订阅|订阅.*看板")
_RECIPIENT_RE = re.compile(r"推给|发给|也推|推送给|加上|捎上")
_ADD_RECIPIENT_RE = re.compile(r"推给|发给|也推|推送给|加上|捎上")
_REMOVE_RECIPIENT_RE = re.compile(r"不要推给|不发给|去掉|移除|别推给")
# v1.11.5：「只推给我自己/只发给我」→ 接收人本就是自己，不当作加人
_SET_SELF_RE = re.compile(r"只?\s*(?:推给|发给|推送|发)\s*我(?:自己)?")
# v1.11.6：恢复订阅——复用已停用订阅重新启用（不新建重复）；触发词须在 _CREATE_RE
# 的「开通」之前判定（「重新开通看板」= 恢复，不是新建）
_RESUME_RE = re.compile(
    r"恢复.*看板|重新开通.*看板|重新打开.*看板|重新开启.*看板|"
    r"启用.*看板|重启.*看板|"
    r"看板.*恢复|看板.*重新开通|看板.*重新打开|"
    r"继续.*看板推送|接着.*看板推送")
# v1.11.6：删除订阅——确认后彻底移除（不可恢复）；须在 _QUERY_RE 之前判定
# （「删除看板订阅」会命中 _QUERY_RE 的「看板.*订阅」被误判成查看设置）
_DELETE_RE = re.compile(
    r"删除.*看板|删掉.*看板|解绑.*看板|注销.*看板|移除订阅.*看板|"
    r"看板.*删除|看板.*删掉|看板.*解绑|看板.*注销")
_TIME_RE = re.compile(r"\d{1,2}[点时:：]|半")
_FREQ_RE = re.compile(r"每周|周一到|周[一二三四五六日天]|星期|周末|每隔|隔天|频率")
# v1.11.5：明确的创建/开通意图才建订阅；否则含「看板」文本放行给 Agent
# 「帮我推个看板/给我推个看板」= 开通订阅（前缀 帮/给/替 必填）；
# 「现在推看板/马上推看板」无前缀 → 放行给 Agent 调 push_dashboard 工具（立即推送），不拦截。
_CREATE_RE = re.compile(
    r"(?:帮我|给我|替我)\s*推.{0,6}看板|"
    r"做.{0,6}看板|建.{0,6}看板|开通.{0,6}看板|开.{0,6}看板|"
    r"设置.{0,6}看板|设.{0,6}看板|生成.{0,6}看板|"
    r"要.{0,6}看板|需要.{0,6}看板|"
    r"每日.{0,4}看板|每天.{0,4}看板|"
    r"看板.{0,4}(推送|提醒|订阅)")

# 周次用 1-7（周一=1 … 周日=7），与 datetime.isoweekday() 对齐
_WEEKDAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_CN_WEEK = ("", "周一", "周二", "周三", "周四", "周五", "周六", "周日")


def parse_doc_dashboard_intent(text: str,
                               ctx: Optional[dict] = None) -> Optional[dict]:
    """识别「按这几个文档做每日看板」→ 动态文档看板创建意图

    返回 {"intent": "doc_create", **ctx}；非该意图返回 None。
    注意：_NEGATIVE_RE 含「文档」会拦截常规 is_kanban_topic，本判定放最前。
    """
    t = (text or "").strip()
    if not t or "看板" not in t:
        return None
    if not _DOC_REF_RE.search(t):
        return None
    if not _DOC_ACTION_RE.search(t):
        return None
    if _LEARN_ONLY_RE.search(t):
        return None  # 「把文档学习入库」是学习，不是做看板
    return {"intent": "doc_create", **(ctx or {})}


def parse_subscription_command(text: str, ctx: Optional[dict] = None) -> Optional[dict]:
    """解析订阅管理指令；非看板管理指令返回 None

    ctx: {user_id, staff_id, union_id}，透传给 pending 供技能层注册用。
    """
    text = (text or "").strip()
    if not text:
        return None
    if _NEGATIVE_RE.search(text):
        return None  # 讨论文档/方案等，不是操作订阅
    # 门槛：含「看板」或明确的接收人指令（"也推给张工"是订阅上下文内的追加指令）
    if not (_HAS_KANBAN_RE.search(text) or _RECIPIENT_RE.search(text)):
        return None

    if _STOP_RE.search(text):
        return {"intent": "stop", **(ctx or {})}
    if _RESUME_RE.search(text):
        # v1.11.6：恢复已停用订阅（复用，不新建重复）
        return {"intent": "resume", **(ctx or {})}
    if _DELETE_RE.search(text):
        # v1.11.6：删除订阅（确认后彻底移除）
        return {"intent": "delete", **(ctx or {})}
    if _QUERY_RE.search(text):
        return {"intent": "query", **(ctx or {})}
    if _SET_SELF_RE.search(text):
        # v1.11.5：「只推给我自己」接收人本就是自己，不做加人操作
        return {"intent": "set_recipient_self", **(ctx or {})}
    if _RECIPIENT_RE.search(text):
        names = _extract_names(text)
        if names:
            # v1.11.6：捕获的是指代词（"别人/他们"）或整句有疑问歧义 → LLM 复核，
            # 防「不要推给别人」把「别人」当人名、防疑问句误加接收人
            intent = _maybe_llm_verify(text, "change_recipients")
            if intent is None:
                return None
            if intent != "change_recipients":
                return {"intent": intent, **(ctx or {})}
            return {"intent": intent, "recipient_names": names,
                    "add": not bool(_REMOVE_RECIPIENT_RE.search(text)),
                    **(ctx or {})}
        # v1.11.5：捕获到的人名全是代词/助词（你了、我自己）→ 不是接收人操作，
        # 继续下一个意图，避免误判
    if _TIME_RE.search(text):
        hour, minute = _parse_time(text)
        return {"intent": "change_time", "push_hour": hour, "push_minute": minute,
                **(ctx or {})}
    if _FREQ_RE.search(text):
        return {"intent": "change_freq", "weekdays": _parse_weekdays(text),
                **(ctx or {})}
    # 兜底：v1.11.5 收紧——明确的创建/开通意图才建订阅，
    # 其余含「看板」文本（质疑/反问/求助）放行给 Agent 正常聊
    if _CREATE_RE.search(text):
        # v1.11.6：create 是重操作，疑问/反问（"看板推送是不是要收费"）先 LLM 复核防误建
        intent = _maybe_llm_verify(text, "create")
        if intent is None:
            return None
        return {"intent": intent, **(ctx or {})}
    return None


# ===== 时间解析 =====
def _parse_time(text: str) -> tuple[int, int]:
    """从文本提取 (小时, 分钟)。支持 '10点' '9点半' '10:30' '每天10:15'"""
    m = re.search(r"(\d{1,2})[点时:：](半|(\d{1,2}))?", text)
    if not m:
        return 9, 0
    hour = min(23, max(0, int(m.group(1))))
    if m.group(2) == "半":
        minute = 30
    elif m.group(3):
        minute = min(59, max(0, int(m.group(3))))
    else:
        minute = 0
    return hour, minute


def _parse_weekdays(text: str) -> str:
    """频率 → 周次字符串（1-7，周一=1）。'每天'→''；'周末'→'6,7'；'每周一和周五'→'1,5'"""
    t = text
    if re.search(r"每天|每日|全部", t):
        return ""
    if "周末" in t:
        return "6,7"
    if re.search(r"一到五|周一到周五|周一至周五|工作日", t):
        return "1,2,3,4,5"
    days = {_WEEKDAY_MAP[ch] for ch in "一二三四五六日天" if ch in t}
    if re.search(r"\d", t):
        for d in re.findall(r"\d", t):
            n = int(d)
            days.add(7 if n == 0 else min(7, max(1, n)))
    if days:
        return ",".join(str(d) for d in sorted(days))
    return ""


# v1.11.5：接收人必须是「人名」——含代词/助词（你我他她它自己大家们了的地得啥）
# 的捕获块（如「我自己」「你了」）不是人名，丢弃，防止「只推给我自己」/「发给了你」
# 被误判成加入接收人。
_INVALID_NAME_RE = re.compile(r"[你我他她它自大家们的地得啥]")
# 捕获块句末语气词（「李四了」「王工啦」的「了/啦」是句末助词，不是名字一部分）
# 先剥掉再判人名
_TRAILING_PARTICLE_RE = re.compile(r"[了啊吧呀呢嘛啦哦噢么呗嘞]+$")


def _extract_names(text: str) -> list[str]:
    """提取接收人姓名（2~4 个汉字，跟在 推给/发给 等词后，排除代词/助词）"""
    names = []
    for m in re.finditer(r"(?:推给|发给|也推给|推送给|加上|捎上)\s*([一-龥]{2,4})", text):
        name = _TRAILING_PARTICLE_RE.sub("", m.group(1))
        if not name or _INVALID_NAME_RE.search(name):
            continue
        if name not in names:
            names.append(name)
    return names


# ===== LLM 意图复核（v1.11.6）=====
# 正则做快闸，LLM 兜住枚举盲区。正则命中 create/change_recipients 这类「重操作」，
# 但文本带疑问/反问（"看板推送是不是要收费"）或捕获的是指代词（"别人"）时，
# 调一次 LLM 确认是不是真的订阅管理操作。仅可疑才调，LLM 只用在刀刃上。
_ALL_INTENTS = {"create", "stop", "delete", "query", "change_time",
                "change_freq", "change_recipients", "set_recipient_self"}
_SUSPECT_QUESTION_RE = re.compile(
    r"是不是|是否|吗|？|\?|要不要|要收费|免费|多少钱|为啥|为什么|怎么|啥|干嘛|有没有")
_SUSPECT_REFER_RE = re.compile(r"别人|他们|她们|大家|某人|任何人")


def _needs_llm_verify(text: str, intent: str) -> bool:
    """重操作 + 可疑文本 → 需要 LLM 复核；普通指令直接走正则，零成本"""
    if _SUSPECT_QUESTION_RE.search(text):
        return intent in ("create", "change_recipients", "doc_create")
    if intent == "change_recipients" and _SUSPECT_REFER_RE.search(text):
        return True
    return False


def _llm_verify_subscription(text: str, fallback_intent: str,
                             llm_fn=None) -> Optional[str]:
    """调 DeepSeek 判断是否为订阅管理意图。

    返回：None=放行给 Agent（LLM 判断不是订阅操作）；
         str=最终意图（LLM 可能修正，如 change_recipients→set_recipient_self）；
    任何失败回退 fallback_intent（保持正则原行为，不崩）。
    """
    try:
        if llm_fn is None:
            from skills.agent import call_deepseek as llm_fn
        prompt = (
            "你是恩特小助手『每日项目看板』订阅意图分类器。判断下面这条钉钉消息"
            "是否在管理看板订阅（开通/停用/删除/改推送时间/改频率/改接收人/查配置）。\n"
            "只返回一行 JSON，不要任何其他文字：\n"
            '{"is_subscription": true或false, "intent": "create或stop或delete或query或'
            'change_time或change_freq或change_recipients或set_recipient_self或null"}\n'
            f"消息：{text[:200]}"
        )
        raw = llm_fn(prompt, max_tokens=120)
        data = json.loads(raw.strip())
        if not data.get("is_subscription"):
            return None
        intent = data.get("intent")
        return intent if intent in _ALL_INTENTS else fallback_intent
    except Exception as e:
        logger.warning(f"看板意图 LLM 复核失败，回退正则({fallback_intent}): {e}")
        return fallback_intent


def _maybe_llm_verify(text: str, intent: str) -> Optional[str]:
    """正则命中后统一入口：可疑才复核；返回 None=放行，str=最终意图"""
    if not _needs_llm_verify(text, intent):
        return intent
    return _llm_verify_subscription(text, fallback_intent=intent)


# ===== 接收人解析 =====
def resolve_recipient(name: str) -> str:
    """姓名 → staff_id（userId）。查不到返回 ""（不抛异常）"""
    if not name:
        return ""
    try:
        from contact_api import get_contact_client
        res = get_contact_client().search(keyword=name, limit=1)
        if res.get("found") and res.get("results"):
            return res["results"][0].get("userId", "") or ""
    except Exception as e:
        logger.warning(f"订阅解析接收人失败({name}): {e}")
    return ""


# ===== 格式展示 =====
def format_weekdays(weekdays: str) -> str:
    """'1,5' → '每周周一、周五'；'' → '每天'（周次 1-7，周一=1）"""
    if not weekdays:
        return "每天"
    days = []
    for d in weekdays.split(","):
        d = d.strip()
        if d.isdigit():
            idx = int(d)
            days.append(_CN_WEEK[idx] if 0 < idx < len(_CN_WEEK) else f"周{d}")
    return "每周" + "、".join(days) if days else weekdays


def _format_sources(data_sources: list) -> str:
    try:
        from .config_model import load_sources
        from .doc_candidates import get_candidate_store
        by_key = {s.key: s.name for s in load_sources()}
        store = get_candidate_store()
        names = []
        for k in data_sources:
            if isinstance(k, str) and k.startswith("doc_"):
                try:
                    cand = store.get(int(k[len("doc_"):]))
                    names.append(cand.name or f"文档{cand.node_id[:8]}"
                                 if cand else k)
                except Exception:
                    names.append(k)
            else:
                names.append(by_key.get(k, k))
        return "、".join(names) if names else "（未配置数据源）"
    except Exception:
        return "、".join(str(k) for k in data_sources)


def render_confirmation(pending: dict, current=None) -> str:
    """根据 pending 生成反问确认文案；current 为当前订阅（改配置时对比展示）"""
    intent = pending.get("intent", "create")
    lines = []

    if intent == "create":
        sources = pending.get("data_sources") or []
        lines.append("好的，我可以为您开通每日项目看板推送，请确认：")
        lines.append("")
        lines.append(f"📋 数据板块：{_format_sources(sources)}")
        lines.append(f"⏰ 推送时间：{format_weekdays(pending.get('weekdays', ''))} "
                     f"{pending.get('push_hour', 9):02d}:{pending.get('push_minute', 0):02d}")
        lines.append(f"🔔 提醒模式：{'仅数据有变化时推送' if pending.get('alert_mode', 'always') == 'changes_only' else '每天固定推送（附今日变化）'}")
        lines.append(f"👤 接收人：您自己")
        lines.append("")
        lines.append("回复「确认」即可订阅；或直接告诉我调整（如「改到10点」「每周一和周五」「也推给张工」）。")

    elif intent == "doc_create":
        sources = pending.get("data_sources") or []
        lines.append("好的，将按以下文档做每日看板，请确认：")
        lines.append("")
        lines.append(f"📋 数据板块：{_format_sources(sources)}")
        lines.append(f"⏰ 推送时间：{format_weekdays(pending.get('weekdays', ''))} "
                     f"{pending.get('push_hour', 9):02d}:{pending.get('push_minute', 0):02d}")
        lines.append(f"🔔 提醒模式：{'仅数据有变化时推送' if pending.get('alert_mode', 'always') == 'changes_only' else '每天固定推送（附今日变化）'}")
        lines.append(f"👤 接收人：您自己")
        lines.append("")
        lines.append("回复「确认」即可订阅；或直接告诉我调整（如「改到10点」「每周一和周五」「也推给张工」）。")

    elif intent == "change_time":
        lines.append(f"好的，看板推送时间将调整为 "
                     f"{format_weekdays(pending.get('weekdays', ''))} "
                     f"{pending.get('push_hour'):02d}:{pending.get('push_minute'):02d}。")
        lines.append("回复「确认」生效，或告诉我要改的其他时间。")

    elif intent == "change_freq":
        lines.append(f"好的，看板推送频率将调整为 "
                     f"{format_weekdays(pending.get('weekdays', ''))}。")
        lines.append("回复「确认」生效，或告诉我要改的周期。")

    elif intent == "change_recipients":
        names = pending.get("recipient_names") or []
        action = "加入接收人" if pending.get("add") else "移除接收人"
        lines.append(f"好的，看板将{action}：{'、'.join(names)}。")
        lines.append("回复「确认」生效，或直接告诉我接收人姓名。")

    elif intent == "set_recipient_self":
        lines.append("看板推送默认就是只推送给您自己的，无需调整。")
        lines.append("如需增加其他接收人，直接告诉我姓名（如「也推给张工」）。")

    elif intent == "delete":
        lines.append("好的，将删除您的看板订阅（此操作不可恢复，历史推送配置一并清除）。")
        lines.append("回复「确认」删除；如果只是想暂停，说「停掉看板」即可。")

    elif intent == "stop":
        lines.append("好的，将停止您的每日看板推送。")
        lines.append("回复「确认」停止；如果只是想调整，直接告诉我要改什么（如「改到10点」）。")

    else:
        lines.append("收到，请问您想对看板做什么调整？")

    return "\n".join(lines)


# ===== 话题判定 =====
def is_kanban_topic(text: str) -> bool:
    """是否看板管理话题：含「看板」且未被否定词标记为文档/方案等"""
    t = (text or "").strip()
    if not t or not _HAS_KANBAN_RE.search(t):
        return False
    return not bool(_NEGATIVE_RE.search(t))


# ===== pending（内存态，重启失效可接受，重说即可） =====
_ACTIVITY_TIMEOUT = 600   # 最近看板活动（秒），用于确认词防误触
_pending: dict[str, dict] = {}
_pending_lock = threading.Lock()
_last_activity_ts: float = 0.0


def set_pending(user_id: str, pending: dict):
    global _last_activity_ts
    with _pending_lock:
        _pending[user_id] = pending
        _last_activity_ts = time.time()


def get_pending(user_id: str) -> Optional[dict]:
    with _pending_lock:
        return _pending.get(user_id)


def clear_pending(user_id: str):
    with _pending_lock:
        _pending.pop(user_id, None)


def has_recent_kanban_activity(timeout: float = _ACTIVITY_TIMEOUT) -> bool:
    """最近 timeout 秒内是否有过看板对话（技能 match 用：简短「确认」只在
    刚聊过看板时拦截，避免抢普通对话）"""
    return (time.time() - _last_activity_ts) <= timeout

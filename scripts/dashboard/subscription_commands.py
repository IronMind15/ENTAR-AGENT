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

# 与"操作订阅"无关的看板话题（不拦截，放行给 Agent 正常聊/调 dash_query 工具）
# 「怎么用/怎么样/什么情况」等是求助或查询实时看板，不是订阅管理
# v1.11.5 追加「功能/关系/干嘛/为什么」等反向词：质疑反问「这跟看板功能有什么关系」
# 不是开通订阅，应放行给 Agent；「看看/想看看/看一下」是实时查询（Agent→dash_query）
_NEGATIVE_RE = re.compile(
    r"文档|方案|方法论|学习|入库|设计|功能说明|怎么用|怎么配|怎么设|怎么操作|"
    r"怎么看|怎么查|怎么样|什么情况|什么进展|是什么|介绍|"
    r"看看|想看看|(?<!查)看一下|"
    r"功能|关系|干嘛|为什么|为啥|这跟|有什么用|干嘛的|干嘛用|与.*有关")
# 看板话题门槛：必须出现「看板」
_HAS_KANBAN_RE = re.compile(r"看板")

# 「按这几个文档做每日看板」：动态文档看板创建意图（注意 _NEGATIVE_RE 含「文档」，
# 常规 is_kanban_topic 会拦下，本意图判定放最前单独处理）。
# v1.11.10：默认仍要求文档引用词（区分「动态文档看板」与「普通订阅」）；
# bot 带链接路径用 require_doc_ref=False 放宽——同事发完链接补一句创建意图不带文档词。
_DOC_REF_RE = re.compile(r"文档|表格|文件|共享表")
_LEARN_ONLY_RE = re.compile(r"学习|入库|帮我学")

# 意图触发词（用 .* 连接「停掉」与「看板」，兼容口语插字：停掉前面的看板 / 把看板停掉）
_STOP_RE = re.compile(
    r"停掉.*看板|停用.*看板|停止.*看板|取消.*看板|关掉.*看板|"
    r"看板.*停掉|看板.*停用|看板.*停止|看板.*取消|看板.*关掉|"
    r"不要.*看板|别再.*看板|退订")
# 查询类保守匹配（不含「时间」——"改看板时间到10点"是改时间不是查设置）
_QUERY_RE = re.compile(
    r"看板.*(几点|什么时候|设置|在哪|是谁|订阅)|"
    r"看板.*任务|任务.*看板|看板.*列表|"
    r"我的看板|看板状态|订阅.*看板")
# 提示词查看必须有明确的查看动作/问法。此前第二段只要出现「看板…提示词」
# 就命中，导致「刚改完提示词，模拟一份看板」等执行请求被只读查看劫持。
_PROMPT_RE = re.compile(
    r"(?:查看|看看|显示|告诉我|列出).{0,8}(?:看板|任务).{0,8}提示词|"
    r"(?:看板|任务).{0,6}提示词\s*(?:是什么|内容|怎么写|长什么样)\s*[？?]?$"
)
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
# v1.12.5：每源独立总结（per_source）。袁会荧实测需求「四份文件各自独立总结」——
# 常不带「看板」也不带「数据源」，属订阅输出模式调整（防漏进 Agent 幻觉）。
# 开启：各自/每个/单独/分开/独立 + 总结/出板/推送/汇报；关闭：否定/合并语。
# 关闭须在开启之前判定（「不要分开总结」的「分开总结」会命中开启分支）。
_PER_SOURCE_OFF_RE = re.compile(
    r"(?:不|别|不要|取消|撤销)[^，。！？\n]{0,8}?"
    r"(?:分开|单独|独立|各自)[^，。！？\n]{0,6}?(?:总结|出板|汇报|推送|发|来)"
    r"|(?:合并|合在一起|放一起|整一份|汇总一条|合成一份)")
_PER_SOURCE_ON_RE = re.compile(
    r"(?:各自|每[个份]|单独|分开|独立)[^，。！？\n]{0,10}?"
    r"(?:总结|出板|汇报|推送|分析|说明)"
    r"|(?:总结|出板|汇报|推送)[^，。！？\n]{0,8}?"
    r"(?:分开|单独|独立|各自)"
    r"|每[个份][^，。！？\n]{0,6}?(?:数据源|文档|文件|表格)[^，。！？\n]{0,6}?"
    r"(?:各自|单独|独立)")
# v1.12.0：模板意图。顺序：describe > submit > set > list（「看板用周报模板」含
# 「看板…模板」不能误判成列表查询；「按这个格式做看板：…」含「做看板」须在
# _CREATE_RE 之前判定）。
_TEMPLATE_DESCRIBE_RE = re.compile(
    r"(?:按|照着|根据|自定义|模板描述|描述格式)[^，。！？\n]{0,30}?"
    r"(?:格式|方式|样式|模板)[^，。！？\n：:，,]{0,12}?[：:，,]\s*(?P<desc>\S.{0,160})|"
    r"(?:模板描述|描述格式|自定义格式)[：:，,]\s*(?P<desc2>\S.{0,160})")
_TEMPLATE_SUBMIT_RE = re.compile(
    r"(?:当|作为|做成|提交|上传|换成)\s*(?:看板)?\s*模板|"
    r"模板.{0,4}(?:提交|上传|当|做成)|"
    r"看板.{0,4}(?:当|作为|做成|提交|上传)")
_TEMPLATE_SET_RE = re.compile(
    r"(?:按|根据|用|换|切|改成|改为|设置|设|变成|换成)\s*(?:[^，。！？\s]{0,10}?)?\s*(?:模板|模式)|"
    r"(?:模板|模式)\s*(?:用|换|切|改成|改为|设置|设|变成)")
# v1.12.1：编辑模板内容（整体重述覆盖）。与 set_template 区分：
#   set  = 切到已存在模板（用/换/改成+模板名，无格式冒号）
#   edit = 重述格式覆盖模板内容（显式编辑动词，或「…模板改成/改为：<新格式>」带冒号）
# 注意：须在 _TEMPLATE_SET_RE 之前判定——否则「把周报模板改成：先写总体结论」
# 会被 set 的「改成」抢成切换（v1.12.1 修复该误抢）。
# 意图判定与 desc 提取拆开：目标模板名/desc 都不靠位置捕获（非贪婪会吞/丢目标，
# v1.12.1 实测），统一交给独立 _EDIT_DESC_RE（整句找「改成…：」）与技能层
# TemplateStore.resolve(text) 解析，更稳健。
_EDIT_TEMPLATE_RE = re.compile(
    r"(?:编辑|修改|调整|改改|重做)"                       # 门槛 A：显式编辑动词
    r"[^，。！？：:]{0,12}?(?:看板)?\s*(?:模板|样式|格式)"   # + 模板词（编辑周报…模板 / 编辑…模板周报）
    r"|"
    r"(?:把|将)?[^，。！？：:]{0,12}?(?:看板)?\s*(?:模板|样式|格式)"  # 门槛 B：…模板改成：<格式>
    r"[^，。！？：:]{0,12}?(?:改成|改为|换成)[：:]"
    r"|"
    r"(?:编辑|修改|调整|改改|重做)"                       # 门槛 C：编辑<名>改成：<格式>
    r"[^，。！？：:]{0,12}?(?:看板)?\s*(?:模板|样式|格式)?"  # 模板词可省（编辑自定义周报改成：…）
    r"[^，。！？：:]{0,12}?(?:改成|改为|换成)[：:]")
# 编辑固定提示词只能采用明确的覆盖形式「编辑…提示词改成：新内容」。
# 不能因一句末尾的「按照这个要求重写提示词」就截走前面本应作为新内容的需求。
_EDIT_TASK_PROMPT_RE = re.compile(
    r"(?:编辑|修改|调整|改改|重写)[^，。！？：:]{0,16}?"
    r"(?:看板)?\s*(?:任务)?\s*(?:提示词|任务指令|总结指令)"
    r"[^，。！？：:]{0,16}?(?:改成|改为|换成)[：:]", re.S)
_EDIT_TASK_PROMPT_DESC_RE = re.compile(
    r"(?:改成|改为|换成)[：:]\s*(?P<desc>[\s\S]{1,4000})")
_EDIT_TASK_PROMPT_SUFFIX_RE = re.compile(
    r"^(?P<desc>[\s\S]{1,4000}?)[：:，,\s]*"
    r"(?:按照|按|根据)(?:这个|上述|以上|前面)?(?:要求|内容|格式)?\s*"
    r"(?:重写|编辑|修改|调整)(?:看板)?(?:任务)?(?:提示词|任务指令|总结指令)\s*$",
    re.S)
_EDIT_TASK_PROMPT_REQUEST_RE = re.compile(
    r"^(?:我想|我要|帮我|请)?\s*(?:编辑|修改|调整|重写)"
    r"(?:看板)?(?:任务)?(?:提示词|任务指令|总结指令)\s*$")
_TASK_ID_RE = re.compile(r"(?:任务|编号)\s*#?\s*(?P<id>\d+)")
# 明确的即时演示/模拟推送由 Agent 的 dash_push 工具承接（工具会二次确认外发）。
# 必须早于「推给」接收人正则，防「现在推给我一个演示」被误改为接收人设置。
_IMMEDIATE_PUSH_RE = re.compile(
    r"(?:现在|马上|立刻|立即).{0,8}?(?:推送?|发).{0,12}?(?:看板|演示|示例|模拟)|"
    r"(?:模拟|演示|示例).{0,12}?(?:看板|推送?|发)|"
    r"看板.{0,12}?(?:现在|马上|立刻|立即).{0,8}?(?:推送?|发)", re.S)
# desc 提取：整句任意位置「改成/改为/换成：<内容>」即新格式描述（须带冒号，
# 防「改成周报模板」无冒号的切换被误当格式）。
_EDIT_DESC_RE = re.compile(r"(?:改成|改为|换成)[：:]\s*(?P<desc>\S.{0,160})")
# 模板列表/查询（「看板模板」后必须紧跟 模板/模式/样式，避开「看板用周报模板」这类切换）
_TEMPLATE_LIST_RE = re.compile(
    r"(?:有哪|有哪些|哪些|几种|什么|列一下|介绍一下?|看看|查询|有什么)\s*看板\s*(?:模板|模式|样式)|"
    r"看板\s*(?:模板|模式|样式)\s*(?:有|有哪|哪些|几种|列表|是什么|怎么|介绍)?")
# v1.12.0：创建订阅后的「选模板」反问回复。数字 1/2/3 太泛，必须绑死「刚创建完
# 正被反问」的上下文（pending choose_template + 600s 反问窗口），超时/非选择回复
# 放行给 Agent/主流程，防「项目进展怎么样」这类长句误判成选模板。
_TEMPLATE_CHOICE_NONE_RE = re.compile(
    r"^(?:不用了?|不要了?|就这个|默认|保持|不换|就这样|都可以|随便)[。！!]?$")
_TEMPLATE_CHOICE_NUM_RE = re.compile(
    r"^([1-3])\s*(?:每日|日报|周报|每周|项目|里程碑|简报|总结)?[。！!]?$")
_TEMPLATE_CHOICE_OPTIONS = [
    ("daily", ("每日", "日报", "每日简报", "日更")),
    ("weekly", ("周报", "每周", "周总结", "周更")),
    ("project", ("项目看板", "项目模板", "里程碑")),
]
# 时间表达必须带明确的小时，不能因一句无关的「半」误入时间修改后又静默
# 回退 09:00。覆盖阿拉伯数字和常见中文数字：八点半 / 早晨八点半 / 8:30。
_CN_TIME_NUM = "零〇一二两三四五六七八九十"
_TIME_RE = re.compile(
    rf"(?:\d{{1,2}}|[{_CN_TIME_NUM}]{{1,3}})\s*[点时:：](?:\s*(?:半|\d{{1,2}}))?")
_CONTEXTUAL_TASK_RE = re.compile(
    r"(?:这个|该|当前|刚才的?|前面的?)(?:看板|任务|订阅|推送)?|(?:看板)?(?:任务|订阅|推送)"
)
_FREQ_RE = re.compile(r"每周|周一到|周[一二三四五六日天]|星期|周末|每隔|隔天|频率")
# v1.11.5：明确的创建/开通意图才建订阅；否则含「看板」文本放行给 Agent
# 「帮我推个看板/给我推个看板」= 开通订阅（前缀 帮/给/替 必填）；
# 「现在推看板/马上推看板」无前缀 → 放行给 Agent 调 dash_push 工具（立即推送），不拦截。
_CREATE_RE = re.compile(
    r"(?:帮我|给我|替我)\s*推.{0,6}看板|"
    r"做.{0,6}看板|建.{0,6}看板|开通.{0,6}看板|开.{0,6}看板|"
    r"设置.{0,6}看板|设.{0,6}看板|生成.{0,6}看板|"
    r"要.{0,6}看板|需要.{0,6}看板|"
    r"每日.{0,4}看板|每天.{0,4}看板|"
    r"看板.{0,4}(推送|提醒|订阅)")

_CONTEXT_DELETE_RE = re.compile(
    r"(?:现在)?(?:帮我)?(?:把)?(?:这些|以上|前面|现有|我的)?(?:全部|都)?"
    r"(?:删除|删掉|移除|清除)(?:掉|了)?$")
# v1.12.4：_CONFIRM_TEXT_RE 已于 v1.12.1（M3）收口到 pending_context（确认词唯一
# 事实源，is_confirmation_text 委托其 is_confirm_text），此处旧定义已无任何引用，
# 删除防两表漂移——修改确认词只改 pending_context 一处。
_CANCEL_TEXT_RE = re.compile(r"^(?:取消|算了|不要了|不执行|先不弄了)[。！!]?$")
# v1.12.7：任务级源增删（把XX加进看板 / 把XX从看板去掉）。含「文档/文件」词，
# 必须在 _NEGATIVE_RE（160 行）之前判定——match 层单独提前接（parse_source_edit_intent）。
# 触发门槛：必须含「看板」+ 明确的加/去动词，防把普通聊天误判成源操作。
_SOURCE_EDIT_RE = re.compile(
    r"(?:把|将)?[^，。！？\n]{0,16}?(?:加进|加入|加到|纳入|放进|加上)[^，。！？\n]{0,8}?看板"
    r"|看板[^，。！？\n]{0,8}?(?:加上|加入|纳入)[^，。！？\n]{0,16}?"
    r"|(?:把|将)[^，。！？\n]{0,16}?从[^，。！？\n]{0,8}?看板[^，。！？\n]{0,8}?(?:去掉|移除|删掉|剔除|解除)"
    r"|看板[^，。！？\n]{0,8}?(?:去掉|移除|删掉|剔除)[^，。！？\n]{0,16}?"
    r"|(?:把|将)[^，。！？\n]{0,16}?(?:从看板|看板里?|看板中)?(?:去掉|移除|删掉|剔除|不要了|不看了)")
_SOURCE_ADD_NAME_RE = re.compile(
    r"(?:把|将)?(?P<n1>[^，。！？\n:：]{1,20}?)(?:这个)?(?:加进|加入|加到|纳入|放进|加上)[^，。！？\n]{0,8}?看板"
    r"|看板[^，。！？\n]{0,8}?(?:加上|加入|纳入)(?P<n2>[^，。！？\n]{1,20}?)"
    r"|(?:把|将)(?P<n3>[^，。！？\n]{1,20}?)(?:加进|加入|加到|纳入|放进)看板")
_SOURCE_REMOVE_NAME_RE = re.compile(
    r"(?:把|将)?(?P<n1>[^，。！？\n:：]{1,20}?)从[^，。！？\n]{0,8}?看板[^，。！？\n]{0,8}?(?:去掉|移除|删掉|剔除)"
    r"|看板[^，。！？\n]{0,8}?(?:去掉|移除|删掉|剔除)(?P<n2>[^，。！？\n]{1,20}?)"
    r"|(?:把|将)(?P<n3>[^，。！？\n]{1,20}?)(?:从看板|看板里?|看板中)?(?:去掉|移除|删掉|剔除|不要了|不看了)")
# 名称清洗：剥句首指代词（这个/这份/刚发的...）与句尾泛指载体词（文件/文档/表格）；
# 「周报」等可能是文档名一部分，不剥。清洗后为空 → 表示「最近发的那个文档」。
_SOURCE_NAME_CLEAN_RE = re.compile(
    r"^(?:这个|这份|那个|刚发的?|刚传的?|新发的?|新传的?|刚才|之前|前面|上面|上述|"
    r"我(?:刚)?(?:发|传)(?:的)?|我发的?|我传的?)+(.*)$")

# v1.12.7：历史留档回放（看上次的看板 / 查看板历史 / 留档）。只读查询，
# 不进 _NEGATIVE_RE 判定（不含文档/文件词），独立于 _QUERY_RE（后者是查配置）。
_HISTORY_RE = re.compile(
    r"(?:历史|留档|上次|前几期|上几次|以前的?|之前的?)(?:的)?(?:看板|推送|报告|内容)|"
    r"(?:看板|每日看板)(?:的)?(?:历史|留档|上次|之前|前几期)")

# 周次用 1-7（周一=1 … 周日=7），与 datetime.isoweekday() 对齐
_WEEKDAY_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
_CN_WEEK = ("", "周一", "周二", "周三", "周四", "周五", "周六", "周日")


def parse_doc_dashboard_intent(text: str,
                               ctx: Optional[dict] = None,
                               require_doc_ref: bool = True) -> Optional[dict]:
    """识别「按这几个文档做每日看板」→ 动态文档看板创建意图

    返回 {"intent": "doc_create", **ctx}；非该意图返回 None。
    注意：_NEGATIVE_RE 含「文档」会拦截常规 is_kanban_topic，本判定放最前。

    v1.11.10：require_doc_ref=False 时不再强制「文档/表格/文件」引用词——
    bot 带链接路径（本次文档已识别）只需判断创建意图，同事发完链接补一句
    「我想做一个每日看板」不会重复文档词。技能路径保持默认严格，防止
    「帮我推个看板」这类普通订阅被 doc_create 误抢。
    """
    t = (text or "").strip()
    if not t or "看板" not in t:
        return None
    if require_doc_ref and not _DOC_REF_RE.search(t):
        return None
    if not _CREATE_RE.search(t):
        return None
    if _LEARN_ONLY_RE.search(t):
        return None  # 「把文档学习入库」是学习，不是做看板
    return {"intent": "doc_create", **(ctx or {})}


def parse_source_edit_intent(text: str) -> Optional[dict]:
    """识别任务级源增删（v1.12.7）：把XX加进看板 / 把XX从看板去掉。

    含「文档/文件」词，独立于 _NEGATIVE_RE 判定（match 层提前接，防被
    「文档」否定词拦进 Agent）。返回 {"intent": "change_sources",
    "action": "add|remove", "doc_name": str}；非源操作返回 None。

    名称可能抓不到（如「把这个加进看板」）——doc_name 为空交给技能层
    用「最近候选」兜底；动词判不准则默认 remove（去掉类动词更常见）。
    """
    t = (text or "").strip()
    if not t or "看板" not in t:
        return None
    if not _SOURCE_EDIT_RE.search(t):
        return None
    action, raw = None, ""
    for pat, a in ((_SOURCE_REMOVE_NAME_RE, "remove"),
                   (_SOURCE_ADD_NAME_RE, "add")):
        m = pat.search(t)
        if m:
            action, raw = a, (next((g for g in m.groups() if g), "") or "")
            break
    if action is None:
        action = ("remove" if re.search(r"(?:去掉|移除|删掉|剔除|解除|不要了|不看了)", t)
                  else "add")
    return {"intent": "change_sources", "action": action,
            "doc_name": _clean_source_name(raw)}


def _clean_source_name(raw: str) -> str:
    """清洗源编辑意图里提取的文档名：剥句首指代词 + 句尾泛指载体词。"""
    name = (raw or "").strip()
    m = _SOURCE_NAME_CLEAN_RE.match(name)
    if m:
        name = m.group(1).strip()
    name = re.sub(r"(?:文件|文档|表格)$", "", name).strip()
    return name


def parse_history_intent(text: str) -> Optional[dict]:
    """识别历史留档回放（v1.12.7）：看上次的看板 / 查看板历史 / 看板留档。

    返回 {"intent": "history"}；非历史查询返回 None。只读查询，技能层直接
    回放该任务最近一次留档（push_history.latest），不设 pending 确认。
    """
    t = (text or "").strip()
    if not t:
        return None
    if not _HISTORY_RE.search(t):
        return None
    return {"intent": "history"}


def parse_prompt_intent(text: str) -> Optional[dict]:
    """查看任务固定提示词（只读，不触发模型、不修改任务）。"""
    if _EDIT_TASK_PROMPT_RE.search((text or "").strip()):
        return None
    return {"intent": "prompt"} if _PROMPT_RE.search((text or "").strip()) else None


def parse_edit_task_prompt(text: str) -> Optional[dict]:
    """识别用户对单个看板任务固定提示词的整体覆盖请求。"""
    raw = (text or "").strip()
    desc = _EDIT_TASK_PROMPT_DESC_RE.search(raw) if _EDIT_TASK_PROMPT_RE.search(raw) else None
    suffix = _EDIT_TASK_PROMPT_SUFFIX_RE.match(raw)
    request_only = _EDIT_TASK_PROMPT_REQUEST_RE.match(raw)
    if not desc and not suffix and not request_only:
        return None
    task_id = _TASK_ID_RE.search(raw)
    return {
        "intent": "edit_task_prompt",
        "task_id": int(task_id.group("id")) if task_id else 0,
        "task_prompt": ((desc.group("desc") if desc else suffix.group("desc")) or "")
        .strip(" ：:，,") if (desc or suffix) else "",
    }


def is_immediate_push_request(text: str) -> bool:
    """是否是一次性看板演示/立即推送，而非订阅配置修改。"""
    return bool(_IMMEDIATE_PUSH_RE.search((text or "").strip()))


def parse_subscription_command(text: str, ctx: Optional[dict] = None) -> Optional[dict]:
    """解析订阅管理指令；非看板管理指令返回 None

    ctx: {user_id, staff_id, union_id}，透传给 pending 供技能层注册用。
    """
    text = (text or "").strip()
    if not text:
        return None
    if is_immediate_push_request(text):
        return None  # 放行 Agent 走受治理的 dash_push（二次确认外发）
    if _NEGATIVE_RE.search(text):
        return None  # 讨论文档/方案等，不是操作订阅
    # 门槛：含「看板」或明确的接收人指令（"也推给张工"是订阅上下文内的追加指令），
    # 或编辑模板意图（「编辑周报模板」可不含「看板」，v1.12.1 放宽），
    # 或用户在看板上下文（口语指代操作「帮我停掉这个」无「看板」词，v1.12.9）。
    # 安全：_NEGATIVE_RE（160 行）已含「文档/设计/方案/学习/入库」等，误入只到引导，无写副作用。
    ctx_user_id = (ctx or {}).get("user_id", "")
    if not (_HAS_KANBAN_RE.search(text) or _RECIPIENT_RE.search(text)
            or _EDIT_TEMPLATE_RE.search(text)
            or parse_edit_task_prompt(text) is not None
            or _PER_SOURCE_ON_RE.search(text) or _PER_SOURCE_OFF_RE.search(text)
            or is_kanban_context(text, ctx_user_id)):
        return None

    # 用户刚查看、创建或调整过看板时，“这个任务”就是当前看板任务。先以
    # 确定性规则承接，避免落 Agent 后出现“我没有删除功能”的机械答复。
    # 仅在已有看板上下文且含明确任务指代时生效，普通聊天不受影响。
    if (ctx_user_id and is_kanban_context(text, ctx_user_id)
            and _CONTEXTUAL_TASK_RE.search(text)):
        if re.search(r"删除|删掉|移除|清除", text):
            return {"intent": "delete", **(ctx or {})}
        if re.search(r"停掉|暂停|关闭|关掉", text):
            return {"intent": "stop", **(ctx or {})}

    if _STOP_RE.search(text):
        return {"intent": "stop", **(ctx or {})}
    if _RESUME_RE.search(text):
        # v1.11.6：恢复已停用订阅（复用，不新建重复）
        return {"intent": "resume", **(ctx or {})}
    if _DELETE_RE.search(text):
        # v1.11.6：删除订阅（确认后彻底移除）
        return {"intent": "delete", **(ctx or {})}
    if _PER_SOURCE_OFF_RE.search(text):
        # v1.12.5：每源独立总结关闭。OFF 独立判定并优先——否定句（「不要分开总结」
        # 「合并成一份」）里的「分开总结」同时命中开启正则，须直接取 False 不再看 ON。
        return {"intent": "set_per_source", "per_source": False, **(ctx or {})}
    if _PER_SOURCE_ON_RE.search(text):
        # v1.12.5：每源独立总结开启（确认后落地）
        return {"intent": "set_per_source", "per_source": True, **(ctx or {})}
    # v1.12.0：模板意图（describe > submit > set > list）
    if _TEMPLATE_DESCRIBE_RE.search(text):
        desc = _extract_template_desc(text)
        if desc:
            return {"intent": "describe_template", "description": desc, **(ctx or {})}
    if _TEMPLATE_SUBMIT_RE.search(text):
        return {"intent": "submit_template", **(ctx or {})}
    task_prompt_edit = parse_edit_task_prompt(text)
    if task_prompt_edit is not None:
        return {**task_prompt_edit, **(ctx or {})}
    # v1.12.1：编辑模板（在 set 之前——「把XX模板改成：<格式>」是编辑不是切换）
    # 目标模板名不在解析层捕获，由技能层 TemplateStore.resolve(text) 从整句解析；
    # desc 可为 ""（「编辑看板模板」无格式描述 → 技能层引导）。
    edit_desc = _extract_edit_template(text)
    if edit_desc is not None:
        return {"intent": "edit_template", "description": edit_desc, **(ctx or {})}
    if _TEMPLATE_SET_RE.search(text):
        return {"intent": "set_template", **(ctx or {})}
    if _TEMPLATE_LIST_RE.search(text):
        return {"intent": "template", **(ctx or {})}
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
        parsed_time = _parse_time(text)
        if parsed_time is None:
            return None
        hour, minute = parsed_time
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
    # v1.12.9：正则全部未命中 → 只在「无看板词 + 用户在看板上下文」时 LLM 分类承接
    # 口语操作（「帮我停掉这个」「删除」「删除全部数据源」等无「看板」词的操作句），
    # 防落 Agent 被 LLM 无工具编造「已执行」。LLM 判非订阅操作/失败 → None →
    # 放行 Agent 正常聊，不拦截普通聊天、不产生写副作用（写操作仍走 handle 二次确认）。
    # 确认词/取消词/承接式全部删除先于 LLM 分类排除——它们归 handle 的确认分支处理。
    # 含「看板」词的消息不由这里兜底：match 层的 is_kanban_topic + needs_kanban_ambiguity_check
    # 已处理其歧义（「看看今天的看板」等实时查询/概念句放行 Agent），此处再抢会把
    # 正则未命中的看板词句（如「我想看下看板配置」）误送 LLM 分类（回归 v1.12.6 修复）。
    if not is_kanban_topic(text) and is_kanban_context(text, ctx_user_id):
        if is_confirmation_text(text) or is_cancel_text(text) or is_contextual_delete(text):
            return None
        intent = _cached_llm_classify(text, ctx_user_id)
        if intent is not None:
            return {"intent": intent, **(ctx or {})}
    return None


def parse_template_choice(text: str, user_id: str) -> Optional[dict]:
    """识别「创建订阅后被反问选模板」的回复（1/2/3/模板名/不用了）

    仅当该用户有 choose_template pending 且处于反问窗口（600s）内才识别——
    数字「1」等太泛，必须绑死「刚创建完正被反问」的上下文；超时后用户再说
    「周报」走 set_template 主流程，不会误拦普通聊天。

    返回 {"intent": "choose_template", "choice": "daily|weekly|project|none"}；
    非选择回复返回 None（放行给 Agent/主流程）。
    """
    pending = get_pending(user_id)
    if not pending or pending.get("intent") != "choose_template":
        return None
    if not has_recent_kanban_activity(user_id=user_id):
        return None  # 反问窗口过期
    t = (text or "").strip()
    if not t:
        return None
    if _TEMPLATE_CHOICE_NONE_RE.fullmatch(t):
        return {"intent": "choose_template", "choice": "none"}
    m = _TEMPLATE_CHOICE_NUM_RE.fullmatch(t)
    if m:
        return {"intent": "choose_template",
                "choice": ("daily", "weekly", "project")[int(m.group(1)) - 1]}
    # 裸「项目」是选择回复；「项目进展怎么样」这类长句不匹配
    if re.fullmatch(r"项目[。！!]?", t):
        return {"intent": "choose_template", "choice": "project"}
    # 其余模板名匹配要求「去掉模板名后剩余字符为空」——纯模板名才是选择回复，
    # 「帮我查下周报数据」「项目进展怎么样」这类带动作/上下文的文本放行不误判
    for key, aliases in _TEMPLATE_CHOICE_OPTIONS:
        for alias in aliases:
            if alias in t and not t.replace(alias, "").strip(" 。！!，,、\t"):
                return {"intent": "choose_template", "choice": key}
    return None


# ===== 口语序数选择（v1.12.6，B5）=====
# 数字「1/2/3」等已由 parse_template_choice 承接（choose_template 反问），这里补
# 口语序数形式（「我说选第一个」「选第二个」「就第一个吧」）——否则落 Agent 无工具
# 可用，实测编造「看板维护转达工单」幻觉（袁会荧 2026-08-14：回「我说选第一个」→
# 落 Agent → contact_find 查通讯录 → 编造工单）。同样必须绑死「有 kanban pending
# 且处于反问窗口」，否则「第一个」等短语会误拦普通聊天。
_SPOKEN_CHOICE_PREFIX_RE = re.compile(r"^(?:我(?:是)?说|我|请|麻烦)?(?:就|要|选|挑)*")
_SPOKEN_CHOICE_RE = re.compile(
    r"^第([一二三四五六七八九十]|\d{1,2})(?:个)?(?:吧|哈|呀|哦|的|了)?[。！!]?$")
_CN_ORDINAL_MAP = {ch: i + 1 for i, ch in enumerate("一二三四五六七八九十")}


def parse_spoken_choice(text: str, user_id: str) -> Optional[dict]:
    """识别看板反问中的口语序数选择（「我说选第一个」→ index=1）

    仅当该用户有 kanban pending 且处于反问窗口（600s）内才识别——
    「选第一个」不含「看板」，必须绑死「刚在看板对话中被反问」的上下文。

    返回 {"intent": "choose_ordinal", "index": n}；非选择回复返回 None
    （放行给 Agent/主流程）。
    """
    pending = get_pending(user_id)
    if not pending:
        return None
    if not has_recent_kanban_activity(user_id=user_id):
        return None  # 反问窗口过期
    t = (text or "").strip()
    if not t:
        return None
    stripped = _SPOKEN_CHOICE_PREFIX_RE.sub("", t)
    m = _SPOKEN_CHOICE_RE.fullmatch(stripped)
    if not m:
        return None
    raw = m.group(1)
    index = int(raw) if raw.isdigit() else _CN_ORDINAL_MAP.get(raw, 0)
    if index <= 0 or index > 20:
        return None
    return {"intent": "choose_ordinal", "index": index}


def is_contextual_delete(text: str) -> bool:
    """识别承接上一轮看板上下文的“全部删除”，不单独作为全局意图。"""
    return bool(_CONTEXT_DELETE_RE.fullmatch((text or "").strip()))


def is_confirmation_text(text: str) -> bool:
    """允许带动作复述的自然确认，例如“是的，全部删除”。

    v1.12.1（M3）：确认词委托 pending_context（类型作用域，kanban 用通用词）。
    """
    from pending_context import PT_KANBAN, is_confirm_text
    return is_confirm_text(text, PT_KANBAN)


def is_cancel_text(text: str) -> bool:
    """v1.12.1（M3）：取消词委托 pending_context"""
    from pending_context import is_cancel_text as pc_is_cancel
    return pc_is_cancel(text)


# ===== 时间解析 =====
def _cn_number_to_int(value: str) -> int | None:
    """解析 0~23 范围内常见中文数字（八、十、十二、二十三）。"""
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
              "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if value == "十":
        return 10
    if "十" in value:
        left, right = value.split("十", 1)
        tens = digits.get(left, 1) if left else 1
        ones = digits.get(right, 0) if right else 0
        return tens * 10 + ones
    return digits.get(value)


def _parse_time(text: str) -> tuple[int, int] | None:
    """提取时间；支持 10点、9点半、10:30、早晨八点半、下午三点。

    无法确定小时返回 None，调用方必须要求澄清，禁止把用户意图悄悄变为
    默认的 09:00。
    """
    m = re.search(rf"(\d{{1,2}}|[{_CN_TIME_NUM}]{{1,3}})\s*[点时:：]"
                  r"\s*(半|(\d{1,2}))?", text)
    if not m:
        return None
    raw_hour = _cn_number_to_int(m.group(1))
    if raw_hour is None:
        return None
    hour = min(23, max(0, raw_hour))
    if m.group(2) == "半":
        minute = 30
    elif m.group(3):
        minute = min(59, max(0, int(m.group(3))))
    else:
        minute = 0
    # 「下午三点 / 晚上七点」按自然语言转为 24 小时制；12 点保持 12。
    prefix = text[:m.start()]
    if re.search(r"下午|晚上|傍晚", prefix) and 1 <= hour < 12:
        hour += 12
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


def _extract_template_desc(text: str) -> Optional[str]:
    """从描述意图文本中提取格式描述（「按…格式：内容」或「模板描述：内容」）"""
    m = _TEMPLATE_DESCRIBE_RE.search(text or "")
    if not m:
        return None
    desc = (m.group("desc") or m.group("desc2") or "").strip()
    return desc or None


def _extract_edit_template(text: str) -> Optional[str]:
    """从编辑意图文本提取新格式描述（desc）。

    desc 只认「改成/改为/换成：<内容>」带冒号（防把「改成周报模板」无冒号的
    切换当格式）；intent 命中但无 desc 返回 ""（引导用户描述）。
    返回 None=非编辑意图；""=编辑意图但无描述；str=格式描述。
    目标模板名不在本函数解析，由技能层 resolve(text) 从整句匹配。
    """
    if not _EDIT_TEMPLATE_RE.search(text or ""):
        return None
    m = _EDIT_DESC_RE.search(text or "")
    return (m.group("desc") or "").strip() if m else ""


# ===== 看板意图登记表（v1.12.0：能力清单/文档单一事实源） =====
# 每个 {id, name, trigger, regex, desc, status}。regex 引用上面定义的正则对象
# （.pattern 复用，避免登记表与判定正则各自维护触发词导致漂移）。
# 注：doc_create 由 parse_doc_dashboard_intent 独立判定（发文档动态做看板），
# 不进 parse_subscription_command 的主意图链，但仍登记于此供能力清单展示。
_INTENT_DEFS: list[dict] = [
    {"id": "doc_create", "name": "文档动态看板", "trigger": "按这几个文档做每日看板",
     "regex": _DOC_REF_RE, "desc": "发钉钉文档动态做看板（bot 带链接路径放宽引用词）",
     "status": "enabled"},
    {"id": "change_sources", "name": "增减任务数据源", "trigger": "把部门周报加进看板 / 从看板去掉",
     "regex": _SOURCE_EDIT_RE,
     "desc": "任务级源增删（含文档/文件词，在否定词前判定；名称匹配最近候选兜底）",
     "status": "enabled"},
    {"id": "create", "name": "开通订阅", "trigger": "帮我推个看板 / 做每日看板",
     "regex": _CREATE_RE, "desc": "创建订阅（须明确创建意图，防误建）", "status": "enabled"},
    {"id": "stop", "name": "停用订阅", "trigger": "停掉看板",
     "regex": _STOP_RE, "desc": "暂停订阅推送", "status": "enabled"},
    {"id": "resume", "name": "恢复订阅", "trigger": "恢复看板 / 重新开通看板",
     "regex": _RESUME_RE, "desc": "复用已停用订阅重新启用（不新建重复）", "status": "enabled"},
    {"id": "delete", "name": "删除订阅", "trigger": "删除看板订阅",
     "regex": _DELETE_RE, "desc": "彻底移除订阅（确认后，不可恢复）", "status": "enabled"},
    {"id": "set_per_source", "name": "每源独立总结", "trigger": "四份文件各自独立总结 / 合并成一份",
     "regex": _PER_SOURCE_ON_RE,
     "desc": "订阅输出模式：每个数据源单独一条 vs 合并成一份报告（确认后落地）",
     "status": "enabled"},
    {"id": "query", "name": "查询配置", "trigger": "看板几点推送 / 我的看板设置",
     "regex": _QUERY_RE, "desc": "查订阅配置（不含「时间」防改时间被误判）", "status": "enabled"},
    {"id": "history", "name": "历史看板回放", "trigger": "看上次的看板 / 查看板历史 / 看板留档",
     "regex": _HISTORY_RE, "desc": "回放最近一次推送留档（每任务保留 30 次，只读不确认）",
     "status": "enabled"},
    {"id": "edit_task_prompt", "name": "编辑任务提示词", "trigger": "编辑看板任务提示词改成：…",
     "regex": _EDIT_TASK_PROMPT_RE,
     "desc": "确认后覆盖指定任务的固定总结指令，保留证据和来源校验硬约束", "status": "enabled"},
    {"id": "change_time", "name": "改推送时间", "trigger": "改看板时间到10点",
     "regex": _TIME_RE, "desc": "修改推送时间点", "status": "enabled"},
    {"id": "change_freq", "name": "改推送频率", "trigger": "每周一和周五",
     "regex": _FREQ_RE, "desc": "修改推送频率（周几/每天）", "status": "enabled"},
    {"id": "change_recipients", "name": "增删接收人", "trigger": "也推给张工 / 不要推给李四",
     "regex": _RECIPIENT_RE, "desc": "增加或移除订阅接收人（指代词/疑问句走 LLM 复核）",
     "status": "enabled"},
    {"id": "set_recipient_self", "name": "接收人设为自己", "trigger": "只推给我自己",
     "regex": _SET_SELF_RE, "desc": "接收人本就是自己，不做加人操作", "status": "enabled"},
    {"id": "describe_template", "name": "描述自定义模板", "trigger": "按这个格式做看板：…",
     "regex": _TEMPLATE_DESCRIBE_RE, "desc": "用自然语言描述模板格式（LLM 生成模板）",
     "status": "enabled"},
    {"id": "submit_template", "name": "上传表头模板", "trigger": "上传/提交模板",
     "regex": _TEMPLATE_SUBMIT_RE, "desc": "按表头提交确定性模板", "status": "enabled"},
    {"id": "set_template", "name": "切换模板", "trigger": "看板用周报模板",
     "regex": _TEMPLATE_SET_RE, "desc": "切换每日/周报/项目/自定义模板", "status": "enabled"},
    {"id": "template", "name": "模板列表查询", "trigger": "有哪些看板模板",
     "regex": _TEMPLATE_LIST_RE, "desc": "列出可选模板（避开「用周报模板」切换）", "status": "enabled"},
    {"id": "edit_template", "name": "编辑模板内容", "trigger": "编辑看板模板周报改成：…",
     "regex": _EDIT_TEMPLATE_RE,
     "desc": "重述格式覆盖更新已有用户模板（系统模板不可编辑）", "status": "enabled"},
    {"id": "choose_template", "name": "反问选模板", "trigger": "创建后回复 1/2/3 或模板名",
     "regex": _TEMPLATE_CHOICE_NUM_RE, "desc": "创建订阅后的反问窗口回复（600s，绑上下文防误判）",
     "status": "enabled"},
]


# ===== LLM 意图复核（v1.11.6）=====
# 正则做快闸，LLM 兜住枚举盲区。正则命中 create/change_recipients 这类「重操作」，
# 但文本带疑问/反问（"看板推送是不是要收费"）或捕获的是指代词（"别人"）时，
# 调一次 LLM 确认是不是真的订阅管理操作。仅可疑才调，LLM 只用在刀刃上。
# _ALL_INTENTS 从 _INTENT_DEFS 派生；doc_create/resume/模板意图虽在登记表内，
# 但 _llm_verify_subscription 的复核 prompt 只列出 8 个核心意图（create/stop/delete/
# query/change_time/change_freq/change_recipients/set_recipient_self），LLM 不会返回
# doc_create/resume/模板意图——派生全量只用于校验白名单，不影响复核语义。
_ALL_INTENTS = {d["id"] for d in _INTENT_DEFS}
_SUSPECT_QUESTION_RE = re.compile(
    r"是不是|是否|吗|？|\?|要不要|要收费|免费|多少钱|为啥|为什么|怎么|啥|干嘛|有没有")
_SUSPECT_REFER_RE = re.compile(r"别人|他们|她们|大家|某人|任何人")


def _needs_llm_verify(text: str, intent: str) -> bool:
    """重操作 + 可疑文本 → 需要 LLM 复核；普通指令直接走正则，零成本"""
    if _SUSPECT_QUESTION_RE.search(text):
        # v1.12.3：query 加入——「看板数据源和看板任务是分开的吗」命中 _QUERY_RE
        # 的「看板.*任务」被吞成查订阅状态，概念疑问句须 LLM 判歧义后放行 Agent。
        return intent in ("create", "change_recipients", "doc_create", "query")
    if intent == "change_recipients" and _SUSPECT_REFER_RE.search(text):
        return True
    return False


def needs_kanban_ambiguity_check(text: str) -> bool:
    """看板话题 + 概念疑问句 → match 层需 LLM 判歧义（v1.12.3）

    明确管理动词/明确查询（无「吗/是不是/为什么/怎么」等疑问词）→ False，
    走正则快速路径秒回；只有含疑问词的看板话题才付一次 LLM 判歧义成本。
    """
    if not is_kanban_topic(text):
        return False
    return bool(_SUSPECT_QUESTION_RE.search(text))


def _llm_verify_subscription(text: str, fallback_intent: Optional[str] = None,
                             llm_fn=None) -> Optional[str]:
    """调 DeepSeek 判断是否为订阅管理意图。

    返回：None=放行给 Agent（LLM 判断不是订阅操作 / 失败且无 fallback）；
         str=最终意图（LLM 可能修正，如 change_recipients→set_recipient_self）；
    任何失败回退 fallback_intent（有则保持正则原行为，None 则放行，不崩）。

    v1.12.9：意图枚举从 v1.12.0 的 8 个核心意图扩展到操作类全意图
    （create/stop/delete/resume/query/change_time/change_freq/change_recipients/
    set_recipient_self/set_per_source/change_sources/set_template/template），
    供「正则未命中 + 看板上下文」时的 LLM 兜底分类使用（_cached_llm_classify）。
    """
    try:
        if llm_fn is None:
            from skills.agent import call_deepseek as llm_fn
        prompt = (
            "你是恩特小助手『每日项目看板』订阅意图分类器。判断下面这条钉钉消息"
            "是否在管理看板订阅。管理操作包括：开通(create)/停用(stop)/恢复(resume)/"
            "删除(delete)/查配置(query)/改推送时间(change_time)/改频率(change_freq)/"
            "增删接收人(change_recipients)/接收人设为自己(set_recipient_self)/"
            "每源独立总结开关(set_per_source)/增减任务数据源(change_sources)/"
            "切换模板(set_template)/模板列表(template)。\n"
            "如果消息不是在看板订阅管理（普通聊天、问天气、讨论概念、查故障、"
            "删文件等），返回 is_subscription=false。\n"
            "只返回一行 JSON，不要任何其他文字：\n"
            '{"is_subscription": true或false, "intent": "create或stop或resume或delete或'
            'query或change_time或change_freq或change_recipients或set_recipient_self或'
            'set_per_source或change_sources或set_template或template或null"}\n'
            f"消息：{text[:200]}"
        )
        raw = llm_fn(prompt, max_tokens=120)
        data = json.loads(raw.strip())
        if not data.get("is_subscription"):
            return None
        intent = data.get("intent")
        return intent if intent in _ALL_INTENTS else fallback_intent
    except Exception as e:
        logger.warning(f"看板意图 LLM 复核失败，回退({fallback_intent}): {e}")
        return fallback_intent


def _maybe_llm_verify(text: str, intent: str) -> Optional[str]:
    """正则命中后统一入口：可疑才复核；返回 None=放行，str=最终意图"""
    if not _needs_llm_verify(text, intent):
        return intent
    return _llm_verify_subscription(text, fallback_intent=intent)


# ===== LLM 兜底分类（v1.12.9 看板操作意图治本） =====
# 正则未命中 + 在看板上下文 → LLM 分类兜底。只判意图，参数/名称缺失走技能层现有兜底。
# 短缓存防 match/handle 对同 query 双调 LLM（match 与 handle 都会 parse 同一条消息）。
_LLM_CLASSIFY_TTL = 60.0  # 秒
_classify_cache: dict[tuple, tuple[float, str]] = {}
_classify_cache_lock = threading.Lock()


def _cached_llm_classify(text: str, user_id: str = "", llm_fn=None) -> Optional[str]:
    """正则未命中时的 LLM 兜底分类。返回意图名或 None（非订阅/失败→放行 Agent）。

    llm_fn is not None 时绕过缓存（测试注入 mock，避免缓存污染断言）。
    白名单校验 _ALL_INTENTS，防止 LLM 编造未注册意图。
    """
    key = (user_id, text)
    if llm_fn is None:
        with _classify_cache_lock:
            hit = _classify_cache.get(key)
            if hit and (time.time() - hit[0]) <= _LLM_CLASSIFY_TTL:
                return hit[1]
    intent = _llm_verify_subscription(text, fallback_intent=None, llm_fn=llm_fn)
    if intent is not None:
        with _classify_cache_lock:
            _classify_cache[key] = (time.time(), intent)
    return intent


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


def format_spec_summary(section_spec: list) -> str:
    """section_spec → 给用户看的一句话结构描述（如：要点 / 进展 / 风险与待决策）"""
    if not isinstance(section_spec, list):
        return "（无章节）"
    titles = []
    for section in section_spec:
        if isinstance(section, dict) and section.get("title"):
            titles.append(str(section["title"]))
    return " / ".join(titles[:8]) if titles else "（无章节）"


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
        lines.append("📌 该任务固定跟踪以上文件源；之后新发布的文档需主动说「把这个文档加进看板」才会纳入。")
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
        lines.append("📌 该任务固定跟踪以上文件源；之后新发布的文档需主动说「把这个文档加进看板」才会纳入。")
        lines.append(f"⏰ 推送时间：{format_weekdays(pending.get('weekdays', ''))} "
                     f"{pending.get('push_hour', 9):02d}:{pending.get('push_minute', 0):02d}")
        lines.append(f"🔔 提醒模式：{'仅数据有变化时推送' if pending.get('alert_mode', 'always') == 'changes_only' else '每天固定推送（附今日变化）'}")
        lines.append(f"👤 接收人：您自己")
        if pending.get("output_summary"):
            lines.append(f"📑 输出结构：{pending['output_summary']}")
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
        count = len(pending.get("sub_ids") or [])
        target = f" {count} 个" if count else ""
        lines.append(f"好的，将删除您的{target}看板订阅（此操作不可恢复，历史推送配置一并清除）。")
        lines.append("回复「确认」删除；如果只是想暂停，说「停掉看板」即可。")

    elif intent == "set_per_source":
        # v1.12.5：每源独立总结（开启/关闭，确认后落地）
        per_source = bool(pending.get("per_source"))
        lines.append("好的，将调整订阅的输出方式：")
        lines.append("📦 " + ("每个数据源单独总结一条（各自聚焦、互不混合）"
                              if per_source else
                              "所有数据源合并成一份看板报告（总体结论先行）"))
        lines.append("")
        lines.append("回复「确认」生效；或告诉我其他调整（如「改到10点」「也推给张工」）。")

    elif intent == "change_sources":
        # v1.12.7：任务级源增删确认；v1.12.9：remove_all（清空全部源）单独文案
        action = pending.get("action")
        name = pending.get("doc_name") or ""
        if action == "remove_all":
            lines.append("好的，将清空看板任务的全部数据源（下次推送将无数据可出）：")
            lines.append("")
            lines.append("回复「确认」清空；回复「取消」则保留现状。")
        else:
            verb = "加入" if action == "add" else "移除"
            lines.append(f"好的，将把《{name}》{verb}看板任务的数据源：")
            lines.append("")
            lines.append("回复「确认」生效；回复「取消」则不改动。")

    elif intent == "stop":
        count = len(pending.get("sub_ids") or [])
        lines.append(f"好的，将停止您的 {count} 个每日看板订阅。")
        lines.append("回复「确认」停止；如果只是想调整，直接告诉我要改什么（如「改到10点」）。")

    elif intent == "resume":
        count = len(pending.get("sub_ids") or [])
        lines.append(f"好的，将恢复您的 {count} 个每日看板订阅。")
        lines.append("回复「确认」恢复推送；回复「取消」则保持暂停。")

    elif intent == "set_template":
        name = pending.get("template_name") or pending.get("template_key") or ""
        lines.append(f"好的，将把您的看板切换为「{name}」模板。")
        lines.append("回复「确认」生效；或说「看板模板」先看看有哪些可用模板。")

    elif intent in ("describe_template", "submit_template"):
        name = pending.get("template_name") or "自定义模板"
        if intent == "describe_template":
            lines.append(f"好的，将按您的描述创建看板模板「{name}」，结构：")
        else:
            lines.append(f"好的，将从文件《{pending.get('file_name') or '上传文件'}》"
                         f"识别看板模板「{name}」，结构：")
        lines.append(f"📐 {format_spec_summary(pending.get('section_spec'))}")
        lines.append("回复「确认」保存模板并应用到您的看板；回复「取消」则不保存。")

    elif intent == "edit_template":
        # v1.12.1：编辑已有用户模板内容（整体重述覆盖）
        name = pending.get("template_name") or pending.get("template_key") or ""
        lines.append(f"好的，将把「{name}」模板调整为以下结构：")
        lines.append(f"📐 {format_spec_summary(pending.get('section_spec'))}")
        lines.append("回复「确认」覆盖保存；回复「取消」则不改动。")

    elif intent == "edit_task_prompt":
        lines.append(f"好的，将覆盖任务编号 {pending.get('sub_id')} 的固定提示词。")
        lines.append("新的提示词如下：")
        lines.append("---")
        lines.append((pending.get("task_prompt") or "")[:1200])
        if len(pending.get("task_prompt") or "") > 1200:
            lines.append("…（确认后保存完整内容）")
        lines.append("---")
        lines.append("回复「确认」后生效；回复「取消」则保留旧提示词。证据引用、不得编造和来源校验仍是系统硬约束。")

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


def is_kanban_context(text: str, user_id: str = "") -> bool:
    """是否「正在看板语境」——LLM 兜底分类的承接依据（v1.12.9）。

    看板词面命中 → True（is_kanban_topic 原逻辑）；
    否则只要有 user_id 且在看板会话中（有 pending 或 600s 内刚聊过看板）→ True。
    这样「帮我停掉这个」「删除」「不是停掉，是删除」这类口语指代
    （无「看板」二字）在看板对话中也能被 parse 承接，而不是落 Agent 被 LLM 编造。
    从未聊过看板的用户随口说「删除」→ False，照旧放行不误接。
    """
    if is_kanban_topic(text):
        return True
    if not user_id:
        return False
    if get_pending(user_id):
        return True
    return has_recent_kanban_activity(user_id=user_id)


# ===== pending 与看板活动 =====
# v1.12.1（M3）：pending 统一收口到 pending_context（type=kanban），薄封装保留 API；
# 看板活动窗口（_activity_by_user）是「最近聊过看板」的承接安全判定，与 pending 独立保留。
_ACTIVITY_TIMEOUT = 600   # 最近看板活动（秒），用于确认词防误触
_activity_lock = threading.Lock()
_last_activity_ts: float = 0.0
_activity_by_user: dict[str, float] = {}


def set_pending(user_id: str, pending: dict):
    """登记看板 pending 并记录该用户刚进行过看板对话"""
    from pending_context import PT_KANBAN, set as pc_set
    pc_set(user_id, PT_KANBAN, dict(pending))
    touch_activity(user_id)


def touch_activity(user_id: str):
    """记录该用户刚进行过看板对话，供承接式命令安全判定。"""
    global _last_activity_ts
    with _activity_lock:
        _last_activity_ts = time.time()
        _activity_by_user[user_id] = _last_activity_ts


def get_pending(user_id: str) -> Optional[dict]:
    """取看板 pending（type=kanban 才返回 payload；被其他类型覆盖时视为无）"""
    from pending_context import PT_KANBAN, get as pc_get
    entry = pc_get(user_id)
    if entry and entry["type"] == PT_KANBAN:
        return dict(entry["payload"])
    return None


def clear_pending(user_id: str):
    """清看板 pending（仅当当前还是 kanban 类型才清，防误清 tool/learn）"""
    from pending_context import PT_KANBAN, clear_type
    clear_type(user_id, PT_KANBAN)


def has_recent_kanban_activity(timeout: float = _ACTIVITY_TIMEOUT,
                                user_id: str | None = None) -> bool:
    """最近 timeout 秒内是否有过看板对话（技能 match 用：简短「确认」只在
    刚聊过看板时拦截，避免抢普通对话）"""
    with _activity_lock:
        last = _activity_by_user.get(user_id, 0.0) if user_id is not None else _last_activity_ts
    return (time.time() - last) <= timeout

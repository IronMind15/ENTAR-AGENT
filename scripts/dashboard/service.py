"""
看板服务层（v1.11.0）— 采集→解析→组装→推送 的公共编排

技能（立即推送样例）、工具（dash_push）、定时调度（scheduler）复用同一套
编排，避免三处各写一遍「collect_all → parse_source_records → assemble → send」。
"""

import json
import logging
from datetime import datetime
from typing import Optional

logger = logging.getLogger("dashboard.service")


def load_enabled_sources() -> list:
    """全部启用且可用的数据源（配置层，v1.11.5 过滤空 base_id 静态残留）"""
    from .config_model import load_sources, source_usable
    return [s for s in load_sources()
            if s.enabled and source_usable(s)]


# ===== 动态数据源桥接（v1.11.0：发文档动态注册，不依赖配置文件） =====
def build_dynamic_source(cand) -> "SourceConfig":
    """文档候选 → SourceConfig（key=`doc_<id>`，消费侧与配置源同构）"""
    from .config_model import FieldSpec, SourceConfig
    field_map: dict[str, FieldSpec] = {}
    try:
        raw_map = json.loads(cand.field_map or "{}")
    except Exception:
        raw_map = {}
    for fid, spec in (raw_map or {}).items():
        if isinstance(spec, dict):
            field_map[str(fid)] = FieldSpec(
                label=str(spec.get("label") or fid),
                type=str(spec.get("type") or "string"),
                max_len=int(spec.get("max_len") or 200),
            )
    status_groups = {}
    try:
        raw_groups = json.loads(cand.status_groups or "{}")
        if isinstance(raw_groups, dict):
            status_groups = {
                str(g): [str(v) for v in vals] if isinstance(vals, list) else []
                for g, vals in raw_groups.items()
            }
    except Exception:
        pass
    return SourceConfig(
        key=f"doc_{cand.id}",
        name=cand.name or f"文档{cand.node_id[:8]}",
        source="dingtalk_doc",
        kind=cand.kind or "notable",
        base_id=cand.node_id,
        source_url=cand.url or "",
        table_mode=cand.table_mode or "fixed",
        table_id=cand.sheet_id or "",
        field_map=field_map,
        status_groups=status_groups,
        enabled=True,
        operator_id=cand.operator_union or "",
    )


def resolve_subscription_sources(sub) -> list:
    """订阅 → SourceConfig 列表（v1.12.7，任务级源选择）

    v1.12.7（D1）改版：每个任务绑定自己的数据源集合（sub.data_sources 是
    任务唯一事实源）——「实时算」只体现在每次推送时对任务绑定的源做实时
    解析与拉取（内容实时、边界固定），不再实时拉 owner 全部候选。用户新
    发布的文档默认不进已有任务，需要主动说「把这个文档加进看板」。

    doc_<id> 键按候选实时查最新信息（名称/权限/sheet_id）：candidate 被
    删除或停用 → 该源自动退出推送并计为不可用；非 doc_ 键走静态配置源。
    懒执行：本函数只在推送/预览/查询时被调用。
    """
    from .config_model import get_source, source_usable
    out = []
    seen = set()
    for key in sub.data_sources or []:
        if not isinstance(key, str) or not key or key in seen:
            continue
        seen.add(key)
        if key.startswith("doc_"):
            cand = None
            try:
                from .doc_candidates import get_candidate_store
                cand = get_candidate_store().get(int(key[len("doc_"):]))
            except Exception as e:
                logger.warning(f"解析订阅源 {key} 失败: {e}")
            if (cand and cand.enabled
                    and cand.kind in ("notable", "workbook", "doc", "folder")):
                out.append(build_dynamic_source(cand))
            continue
        src = get_source(key)
        if src and src.enabled and source_usable(src):
            out.append(src)
    return out


def effective_source_keys(sub) -> set:
    """订阅当前生效的数据源 key 集合（v1.12.7：任务绑定解析，去重/覆盖判断用）"""
    try:
        return {s.key for s in resolve_subscription_sources(sub)}
    except Exception:
        return set(getattr(sub, "data_sources", None) or [])


def source_resolution_warnings(sub, resolved_sources: list) -> list[str]:
    """v1.12.7：仅当订阅当前全无数据源、但曾绑定过时提示（迁移兜底）。

    任务绑定解析下，绑定键对应的源可能已被删除/停用/未配置——部分源缺失
    时任务照常推剩余源，不打断；仅当全部不可用而旧绑定非空时提醒，避免
    静默空推。
    """
    if resolved_sources:
        return []
    stored = [str(k) for k in (sub.data_sources or [])]
    if not stored:
        return []
    return [f"订阅原本绑定了 {len(stored)} 个数据源，现全部不可用"
            "（已删除、停用或未配置）：" + "、".join(stored[:5])]


def load_all_available_sources(user_id: str = "") -> list:
    """全部可用数据源：配置源 + enabled 动态源

    v1.11.10：query 传 user_id 只取本人动态源（防跨用户串看板）；push 空=全量。
    """
    sources = load_enabled_sources()
    try:
        from .doc_candidates import get_candidate_store
        for cand in get_candidate_store().list_all_enabled(user_id):
            if cand.kind in ("notable", "workbook", "doc", "folder"):
                sources.append(build_dynamic_source(cand))
    except Exception:
        pass
    return sources


def collect_and_parse(sources: list, operator_id: str = "", staff_id: str = ""):
    """采集 → 解析，返回 (parsed_results, errors)

    - 单源全失败（error 非空且无 records）跳过，进 errors（不中断整体）
    - v1.12.6（C8）：部分失败（error 与 records 并存，如文件夹个别子文档失败）
      保留已采记录并同时上报 error，不整源丢弃
    - parsed: parse_source_records 结果列表
    """
    from .collector import Collector
    from .parser import parse_source_records

    src_by_key = {s.key: s for s in sources}
    collected = Collector().collect_all(
        sources, operator_id=operator_id, staff_id=staff_id)
    parsed = []
    errors = []
    for c in collected:
        if c.get("error") and not c.get("records"):
            errors.append(f"{c.get('name') or c.get('source_key')}: {c['error']}")
            continue
        src = src_by_key.get(c["source_key"])
        if src and c.get("records"):
            # v1.12.6（C8）：文件夹部分子文档失败 → error 与 records 并存。
            # 保留已采到的记录（不整源丢弃），同时把失败项上报，让报告提示不完整。
            if c.get("error"):
                errors.append(f"{c.get('name') or c.get('source_key')}: {c['error']}")
            # 采集器可能取得了文件级真实标题；传给 parser，不能再退回候选旧名。
            if c.get("name") and c.get("name") != src.name:
                from dataclasses import replace
                src = replace(src, name=str(c["name"]))
            parsed.append(parse_source_records(
                src, c["records"], c.get("table_name", "")))
    return parsed, errors


def resolve_template(sub) -> Optional["DashboardTemplate"]:
    """订阅 → 任务提示词对应的模板快照；旧订阅才回落到实时模板。

    v1.13.1：有 task_prompt_spec 时只使用创建/明确修改任务时保存的结构，
    不因同名模板后来被编辑而悄悄改变历史任务行为。
    """
    if sub is None:
        return None
    try:
        from .template_store import get_template_store
        spec = getattr(sub, "task_prompt_spec", None) or {}
        if spec:
            from .template_store import DashboardTemplate
            return DashboardTemplate(
                key=spec.get("key", getattr(sub, "template_id", "daily")),
                name=spec.get("name", "每日简报"),
                title=spec.get("title", ""),
                map_instructions=spec.get("map_instructions", ""),
                reduce_instructions=spec.get("reduce_instructions", ""),
                section_spec=spec.get("section_spec", []) or [],
            )
        return get_template_store().get(sub.template_id, user_id=sub.owner_user_id)
    except Exception as e:
        logger.warning(f"取看板模板失败({getattr(sub, 'template_id', '?')}): {e}")
        return None


def ensure_task_prompt(sub, *, force: bool = False) -> str:
    """为订阅生成一次任务提示词快照；返回固定提示词文本。

    旧任务首次执行时懒迁移，之后也固定。``force=True`` 只用于用户明确
    切换或编辑模板，不能由每次定时运行触发。
    """
    if getattr(sub, "task_prompt", "") and getattr(sub, "task_prompt_spec", None) and not force:
        return sub.task_prompt
    from .template_store import get_template_store
    from .task_prompt import build_prompt
    template = get_template_store().get(
        getattr(sub, "template_id", "daily"), user_id=getattr(sub, "owner_user_id", ""))
    if template is None:
        template = get_template_store().get("daily")
    prompt, spec, digest, created_at = build_prompt(sub, template)
    sub.task_prompt = prompt
    sub.task_prompt_spec = spec
    sub.task_prompt_version = "task-prompt-v1"
    sub.task_prompt_hash = digest
    sub.task_prompt_created_at = created_at
    # 已有任务在生成/刷新快照时立即同步到该用户专属文件夹；新建任务 id 尚未
    # 分配，创建后会由调用方再同步一次。
    if getattr(sub, "id", 0):
        try:
            from .task_prompt import sync_prompt_file
            sync_prompt_file(sub)
        except Exception as exc:
            logger.warning("任务提示词文件同步失败(%s): %s", getattr(sub, "id", "?"), exc)
    return prompt


def sync_task_prompt_file(sub) -> str:
    """显式同步任务提示词文件（新建任务获得数据库 ID 后调用）。"""
    try:
        from .task_prompt import sync_prompt_file
        return sync_prompt_file(sub)
    except Exception as exc:
        logger.warning("任务提示词文件同步失败(%s): %s", getattr(sub, "id", "?"), exc)
        return ""


def render_task_prompt_message(sub) -> str:
    """看板主动消息的首条固定提示词。"""
    from .task_prompt import render_prompt_message
    return render_prompt_message(sub)


def render_source_links(sources: list) -> str:
    """全局最后一条来源链接，保证用户无需从报告正文里翻找。"""
    lines = ["🔗 本次看板数据来源"]
    for source in sources or []:
        name = getattr(source, "name", "数据源") or "数据源"
        url = getattr(source, "source_url", "") or ""
        if url:
            lines.append(f"- [{name}]({url})")
        else:
            lines.append(f"- {name}（未提供可访问链接）")
    return "\n".join(lines) if len(lines) > 1 else ""


def assemble(parsed: list, title: str = "恩特能源每日项目看板",
             date_str: str = "", llm_func=None, old_snapshot=None,
             evidence_pipeline: bool = False, errors: list[str] | None = None,
             template=None, task_prompt: str = "") -> str:
    """组装 Markdown。

    evidence_pipeline=True 使用完整字段、分批 LLM 和来源校验；默认保留旧接口，
    避免外部调用在升级时失效。template 只作用于证据流水线（旧规则模板不支持）。
    """
    if evidence_pipeline:
        from .llm_pipeline import build_dashboard_report
        return build_dashboard_report(
            parsed, old_snapshot, llm_func, title=title, date_str=date_str,
            collection_errors=errors, template=template,
            task_prompt=task_prompt).text
    from .assembler import assemble_markdown, llm_assemble
    if llm_func:
        return llm_assemble(parsed, title=title, date_str=date_str,
                            llm_func=llm_func)
    return assemble_markdown(parsed, title=title, date_str=date_str)


def assemble_report(parsed: list, title: str = "恩特能源每日项目看板",
                    date_str: str = "", llm_func=None, old_snapshot=None,
                    errors: list[str] | None = None, template=None,
                    task_prompt: str = ""):
    """返回含语义分页的证据报告；定时推送使用此接口。"""
    from .llm_pipeline import build_dashboard_report
    return build_dashboard_report(
        parsed, old_snapshot, llm_func, title=title, date_str=date_str,
        collection_errors=errors, template=template, task_prompt=task_prompt)


def assemble_per_source_messages(parsed: list, title: str = "恩特能源每日项目看板",
                                 date_str: str = "", llm_func=None,
                                 old_snapshot=None, errors: list[str] | None = None,
                                 template=None, task_prompt: str = "") -> list[str]:
    """每个数据源单独跑一轮组装 → 每个源总结一条（识别/预览逐源聚焦）。

    v1.12.5（消息流）：数据源多时一次性全塞 LLM reduce 复杂/超限。这里按源
    拆分——LLM 每次只总结一个源（更聚焦、不超上下文），每个源的结果作为一条
    消息返回，调用方顺序发送。

    - ``parsed`` 为 collect_and_parse 结果（每项 = parse_source_records 返回）。
    - 每个源单独走 build_dashboard_report（map/reduce 自然只含该源记录）。
    - ``old_snapshot`` 传全量即可：field_diff 内部按 source_key 索引，自动过滤
      出该源的旧快照做变化对比。
    - 单源若仍超长，report.messages 自带语义分页，全量追加（多条也算一条源）。
    """
    from .llm_pipeline import build_dashboard_report
    msgs: list[str] = []
    for item in parsed:
        report = build_dashboard_report(
            [item], old_snapshot, llm_func, title=title, date_str=date_str,
            collection_errors=errors, template=template, task_prompt=task_prompt)
        logger.info(
            "[看板逐源总结] source=%s accepted=%s fallback=%s rejected=%s "
            "evidence_repaired=%s llm_errors=%s pages=%s",
            item.get("name") or item.get("source_key"),
            report.verification.get("accepted_claims", 0),
            report.verification.get("fallback_claims", 0),
            report.verification.get("rejected_claims", 0),
            report.verification.get("repaired_evidence_claims", 0),
            report.verification.get("llm_errors", 0), len(report.messages))
        msgs.extend(report.messages)
    return msgs


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def push(recipients: list, title: str, text: str):
    """推送给 staff_id 列表（notifier 内部去重、≤20 人）

    Returns:
        (ok: bool, message: str)
    """
    if not recipients:
        return False, "无接收人"
    try:
        from scripts.dingtalk_notifier import DingTalkNotifier
        DingTalkNotifier().send_markdown_to_users(list(recipients), title, text)
        return True, ""
    except Exception as e:
        logger.warning(f"看板推送失败: {e}")
        return False, str(e)[:200]


def push_messages(recipients: list, title: str, messages: list[str]):
    """依次发送语义分页；任一页失败即返回失败和页码。"""
    for index, message in enumerate(messages, 1):
        ok, error = push(recipients, title, message)
        if not ok:
            return False, f"第 {index}/{len(messages)} 页失败：{error}"
    return True, ""

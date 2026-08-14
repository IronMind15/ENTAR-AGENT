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
    """订阅 → SourceConfig 列表（v1.12.6，C6：每次推送实时解析）

    v1.12.6（C6）改版：订阅取消持久绑定——每次推送实时拉取 owner 当前
    enabled 的文档候选（doc_candidates.list_dashboard_ready，按 user_id
    隔离）+ 静态配置源。用户新发布的文件自动进入下次推送（需求「发布的
    每个文件都能被解读到」）；不同 owner 的文件互不影响（隔离，C7 后不
    存在跨订阅改数据源）。懒执行：本函数只在推送/预览/查询时被调用。

    `sub.data_sources` 仅作迁移兜底：owner 无任何动态候选且静态源为空时，
    回退旧绑定解析（老订阅不丢源；doc_ 键不再参与，防跨用户引用）。
    """
    out = []
    try:
        from .doc_candidates import get_candidate_store
        owner = getattr(sub, "owner_user_id", "") or getattr(sub, "owner_union_id", "")
        for cand in get_candidate_store().list_dashboard_ready(owner):
            out.append(build_dynamic_source(cand))
    except Exception as e:
        logger.warning(f"拉取订阅 owner 动态候选失败: {e}")
    out.extend(load_enabled_sources())
    if out:
        return out
    return _resolve_stored_config_sources(sub)


def _resolve_stored_config_sources(sub) -> list:
    """迁移兜底：旧绑定里的配置源 key（doc_ 键由实时候选接管，不再走此路）"""
    from .config_model import get_source, source_usable
    out = []
    for key in sub.data_sources or []:
        if isinstance(key, str) and key.startswith("doc_"):
            continue
        src = get_source(key)
        if src and src.enabled and source_usable(src):
            out.append(src)
    return out


def effective_source_keys(sub) -> set:
    """订阅当前生效的数据源 key 集合（C6：实时解析，去重/覆盖判断用）"""
    try:
        return {s.key for s in resolve_subscription_sources(sub)}
    except Exception:
        return set(getattr(sub, "data_sources", None) or [])


def source_resolution_warnings(sub, resolved_sources: list) -> list[str]:
    """C6 后：仅当订阅当前全无数据源、但曾绑定过时提示（迁移兜底）。

    旧逻辑比对 sub.data_sources 与解析结果差集——C6 订阅实时拉取 owner
    enabled 候选，旧绑定不再参与解析，「部分源缺失」不再是常见态。仅当
    解析为空而旧绑定非空（老订阅源全部失效）时提醒，避免静默空推。
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
    """订阅 → 其输出格式模板；模板缺失/不可见 → None（回落 daily 旧行为）

    v1.12.0：用户私有模板被删时订阅仍引用它，这里优雅回落而不是崩。
    """
    if sub is None:
        return None
    try:
        from .template_store import get_template_store
        return get_template_store().get(sub.template_id, user_id=sub.owner_user_id)
    except Exception as e:
        logger.warning(f"取看板模板失败({getattr(sub, 'template_id', '?')}): {e}")
        return None


def assemble(parsed: list, title: str = "恩特能源每日项目看板",
             date_str: str = "", llm_func=None, old_snapshot=None,
             evidence_pipeline: bool = False, errors: list[str] | None = None,
             template=None) -> str:
    """组装 Markdown。

    evidence_pipeline=True 使用完整字段、分批 LLM 和来源校验；默认保留旧接口，
    避免外部调用在升级时失效。template 只作用于证据流水线（旧规则模板不支持）。
    """
    if evidence_pipeline:
        from .llm_pipeline import build_dashboard_report
        return build_dashboard_report(
            parsed, old_snapshot, llm_func, title=title, date_str=date_str,
            collection_errors=errors, template=template).text
    from .assembler import assemble_markdown, llm_assemble
    if llm_func:
        return llm_assemble(parsed, title=title, date_str=date_str,
                            llm_func=llm_func)
    return assemble_markdown(parsed, title=title, date_str=date_str)


def assemble_report(parsed: list, title: str = "恩特能源每日项目看板",
                    date_str: str = "", llm_func=None, old_snapshot=None,
                    errors: list[str] | None = None, template=None):
    """返回含语义分页的证据报告；定时推送使用此接口。"""
    from .llm_pipeline import build_dashboard_report
    return build_dashboard_report(
        parsed, old_snapshot, llm_func, title=title, date_str=date_str,
        collection_errors=errors, template=template)


def assemble_per_source_messages(parsed: list, title: str = "恩特能源每日项目看板",
                                 date_str: str = "", llm_func=None,
                                 old_snapshot=None, errors: list[str] | None = None,
                                 template=None) -> list[str]:
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
            collection_errors=errors, template=template)
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
        from dingtalk_notifier import DingTalkNotifier
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

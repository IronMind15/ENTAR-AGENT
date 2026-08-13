"""钉钉文档总结工具（v1.11.3，v1.12.0 改名 doc_summarize）— 总结用户最近发来的钉钉文档，可选主动推回本人

定位候选（doc_candidates）→ 全读（summary=False 走翻页/5000 块）→
转 Markdown → call_deepseek 总结 → （可选）主动推回用户本人。
工具只回摘要不塞原文，防止撑爆 Agent 上下文窗口。
"""

import json
import logging

from tools import register

logger = logging.getLogger("tools.doc_summarize")

DEFINITION = {
    "name": "doc_summarize",
    "description": (
        "总结用户最近发来的钉钉文档（AI表格/在线表格/普通文档）要点。"
        "用户要求『帮我总结刚发的文档』『总结成推送』时使用。"
        "doc_ref 填对话历史中用户发送过的文档 node_id 或完整链接；"
        "不填则默认总结用户最近一份文档。"
        "push_to_self=true 时总结后主动推送给用户本人钉钉单聊。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "doc_ref": {
                "type": "string",
                "description": "文档 node_id 或链接（从对话历史用户发送的文档取得）",
            },
            "push_to_self": {
                "type": "boolean",
                "description": "是否总结后主动推送用户本人（用户明确要『推送给我的/发给我』时置 true）",
            },
        },
        "required": [],
    },
}


@register(
    DEFINITION,
    policy={
        "confirm": lambda args: bool(args.get("push_to_self")),
        "risk": "external_send",
        "summary": "总结文档并主动推送到用户钉钉单聊",
    },
    sector="doc",
    display="📄 总结文档...",
    user_desc=(
        "总结钉钉文档内容；push_to_self=True 时主动推送到用户单聊"
        "（写操作，确认后执行）。"
    ),
)
def execute(args: dict) -> str:
    try:
        from tools import get_current_staff_id, get_current_user_id
        from dashboard import doc_learn
        from dashboard.doc_candidates import get_candidate_store
        from dingtalk_doc_client import get_doc_client

        user_id = get_current_user_id()
        if not user_id:
            return json.dumps({"error": "无法识别当前用户，请重试"}, ensure_ascii=False)
        staff_id = get_current_staff_id()

        # 1. 定位文档：doc_ref 指定 → 按 node_id/url 查；否则取最近一份候选
        doc_ref = (args.get("doc_ref") or "").strip()
        store = get_candidate_store()
        cand = None
        if doc_ref:
            cand = (store.find_by_node_id(user_id, doc_ref)
                    or store.find_by_url(user_id, doc_ref)
                    or store.find_by_name(user_id, doc_ref))
        if cand is None:
            cand = store.get_pending(user_id)
        if cand is None:
            return json.dumps(
                {"error": "没有找到要总结的钉钉文档，请先发送文档链接"},
                ensure_ascii=False)

        # 2. 全读（summary=False：workbook 翻页全量、doc 5000 块）
        client = get_doc_client()
        try:
            result = client.read_document(
                cand.url, operator_id=cand.operator_union or user_id,
                staff_id=staff_id, summary=False)
        except Exception as e:
            return json.dumps({"error": f"文档读取失败：{e}"}, ensure_ascii=False)
        if not result.get("ok"):
            return json.dumps({"error": result.get("message", "文档读取失败")},
                              ensure_ascii=False)

        records = result.get("records") or []
        if result.get("kind") == "doc" and result.get("markdown"):
            md = result["markdown"]
        else:
            md = doc_learn._records_to_markdown(records)
        md = (md or "").strip()
        if not md:
            return json.dumps({"error": "文档无内容，无法总结"}, ensure_ascii=False)

        doc_name = cand.name or result.get("name") or f"文档{cand.node_id[:8]}"

        # 3. 总结（截断 8000 防撑爆 LLM；失败兜底给前 500 字原文）
        from skills.agent import call_deepseek
        summary = call_deepseek(
            f"以下是钉钉文档《{doc_name}》的内容：\n\n{md[:8000]}\n\n"
            "请用中文总结这份文档的要点，150 字以内，分条列出。",
            max_tokens=2000).strip()
        if not summary:
            summary = md[:500]

        # 4. 可选：主动推回本人（避免 Agent 再回全文双发，推后只回简短确认）
        pushed = False
        if args.get("push_to_self"):
            if staff_id:
                try:
                    from dingtalk_notifier import DingTalkNotifier
                    DingTalkNotifier().send_markdown_to_users(
                        [staff_id], f"《{doc_name}》总结", summary)
                    pushed = True
                except Exception as e:
                    logger.warning(f"总结主动推送失败: {e}")
            if not pushed:
                return json.dumps(
                    {"error": "总结完成但主动推送失败（缺员工 ID 或接口异常），可直接向用户展示"},
                    ensure_ascii=False)

        if pushed:
            return json.dumps(
                {"ok": True, "doc_name": doc_name, "pushed": True,
                 "message": f"已主动推送《{doc_name}》总结给用户本人"},
                ensure_ascii=False)
        return json.dumps(
            {"ok": True, "summary": summary, "records": len(records),
             "doc_name": doc_name, "pushed": False},
            ensure_ascii=False)
    except Exception as e:
        logger.exception(f"doc_summarize 执行异常: {e}")
        return json.dumps({"error": f"总结失败：{e}"}, ensure_ascii=False)

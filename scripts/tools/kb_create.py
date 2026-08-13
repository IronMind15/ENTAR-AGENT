"""
工具：kb_create（原 create_knowledge_base，v1.12.0 改名）
创建新的知识库（v1.11.5 多知识库改造）。

以前知识库绑定具体内容（标准 PDF → standards、故障 Excel → error_codes），
只能通过改代码扩展。现在把「创建知识库」做成独立功能：用户在钉钉说
「创建知识库，名字叫产品手册，用来放产品说明书，研发部」→ Agent 调本工具
注册新库。之后：
  - 查询：通用 kb_search 工具可选择该库（或全库自动搜）
  - 学习：发文档后回复「把这个文档学到产品手册」入库到该库
  - 权限预留：创建时指定的 department 为该库归属部门（默认 public）

数据存 data/user_store.db 的 knowledge_bases 表（见 scripts/kb_registry.py）。
"""

import json
import logging

from tools import register

logger = logging.getLogger("tool.create_kb")


def _list_kb_summary() -> str:
    """动态列出现有知识库（供 LLM 感知当前有哪些库）"""
    try:
        from kb_registry import list_knowledge_bases
        kbs = list_knowledge_bases(enabled_only=True)
        if not kbs:
            return ""
        parts = []
        for kb in kbs:
            dept = kb.get("department") or "public"
            parts.append(f"{kb['name']}（部门:{dept}）")
        return "；".join(parts)
    except Exception:
        return ""


DEFINITION = {
    "name": "kb_create",
    "description": (
        "创建新的知识库。当用户说「创建知识库」「新建一个库」「建个XX库放XX」"
        "（如『创建知识库，名字叫产品手册，用来放产品说明书，研发部』）时调用。"
        "创建成功后该库可学习入库、可被通用搜索。"
        "现有知识库：" + (_list_kb_summary() or "（暂无）") + "。"
        "注意：这只是注册知识库元数据，不是把文档入库。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "知识库名称，简短易记，如『产品手册』『售后案例』",
            },
            "description": {
                "type": "string",
                "description": "用途说明：这个库放什么内容（产品说明书/规格书/案例等），"
                               "留空也可",
            },
            "department": {
                "type": "string",
                "enum": ["public", "pmo", "rd", "mfg", "bz", "ops"],
                "description": "归属部门（权限预留）：public=全公司 / pmo=项目管理 / "
                               "rd=研发 / mfg=制造 / bz=商业 / ops=运营，默认 public",
            },
        },
        "required": ["name"],
    },
}


@register(
    DEFINITION,
    policy={
        "confirm": True,
        "risk": "create",
        "summary": "创建新的企业知识库（会写入知识库配置）",
    },
    sector="kb",
    display="📚 创建知识库...",
    user_desc=(
        "用户说「创建知识库/新建一个库/建个XX库放XX」时使用。\n"
        '示例：创建知识库，名字叫产品手册，用来放产品说明书，研发部 '
        '→ kb_create(name="产品手册", description="产品说明书、规格书", department="rd")\n'
        "注意：这只是注册库配置，不是把文档入库；创建后文档学到该库可回复「把这个文档学到产品手册」。"
    ),
)
def execute(args: dict) -> str:
    """创建知识库"""
    name = (args.get("name") or "").strip()
    description = (args.get("description") or "").strip()
    department = (args.get("department") or "public").strip().lower()

    if not name:
        return json.dumps({"error": "知识库名称不能为空，请提供库名"},
                          ensure_ascii=False)

    logger.info(f"  工具调用: kb_create(name={name}, dept={department})")

    from kb_registry import create_knowledge_base as do_create

    result = do_create(name, description, department)
    if not result.get("ok"):
        return json.dumps({"error": result.get("message", "创建失败")},
                          ensure_ascii=False)

    kb = result["kb"]
    return json.dumps({
        "created": True,
        "kb": {
            "key": kb.get("key"),
            "name": kb.get("name"),
            "description": kb.get("description"),
            "department": kb.get("department"),
        },
        "message": (
            f"已创建知识库「{kb.get('name')}」（部门:{kb.get('department') or 'public'}）。"
            f"以后发文档后回复『把这个文档学到{kb.get('name')}』即可入库；"
            f"查询时告诉我知识库名即可定向搜索。"
        ),
    }, ensure_ascii=False)

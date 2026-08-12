"""对用户上传文件执行删除或重新学习；所有动作都需二次确认。"""

import json

from tools import get_current_user_id, register

DEFINITION = {
    "name": "manage_uploaded_file",
    "description": "删除本人上传文件及知识库内容，或强制重新学习文件。两类操作都会先要求用户二次确认。",
    "parameters": {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["delete", "relearn"]},
            "target": {"type": "string", "description": "文件名或文件编号"},
        },
        "required": ["action", "target"],
    },
}


@register("manage_uploaded_file", DEFINITION, policy={
    "confirm": True,
    "risk": "delete_or_modify",
    "summary": "删除上传文件/知识库内容，或强制重新学习并覆盖索引",
})
def execute(args: dict) -> str:
    from knowledge_review import delete_file_for_user, relearn_file_for_user

    user_id = get_current_user_id()
    target = str(args.get("target") or "").strip()
    action = str(args.get("action") or "").strip()
    if not user_id or not target:
        return json.dumps({"error": "无法识别用户或目标文件"}, ensure_ascii=False)
    try:
        from skills.dingtalk_bot import _is_admin
        is_admin = _is_admin(user_id)
    except Exception:
        is_admin = False
    if action == "delete":
        result = delete_file_for_user(user_id, target, is_admin=is_admin)
        if result.get("status") != "ok":
            return json.dumps({"error": result.get("message", "删除失败")}, ensure_ascii=False)
        source_note = "源文件已删除" if result.get("source_deleted") else "源文件删除失败并已保留"
        message = (f"已删除 {result.get('file_name', target)}；知识库内容 "
                   f"{result.get('deleted_chunks', 0)} 块已移除，{source_note}。")
        return json.dumps({"ok": True, "message": message}, ensure_ascii=False)
    if action == "relearn":
        result = relearn_file_for_user(user_id, target, is_admin=is_admin)
        if result.get("status") != "ok":
            return json.dumps({"error": result.get("message", "重新学习失败")}, ensure_ascii=False)
        return json.dumps({"ok": True, "message": result.get("message") or "文件已重新学习"},
                          ensure_ascii=False)
    return json.dumps({"error": "不支持的文件操作"}, ensure_ascii=False)

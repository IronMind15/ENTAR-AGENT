"""
find_employee — 钉钉通讯录员工查询工具

用户问「谁负责采购」「研发中心王工」「张三电话多少」等找人问题时，
由 DeepSeek function calling 调用本工具：实时拉取钉钉通讯录 → 匹配 → 返回。

权限约定：
  - 基础信息（姓名/部门/职位/工号）全员可查
  - 手机号/邮箱等敏感字段仅审核人（CONTACT_ADMIN_STAFF_IDS 白名单）可见，服务端剥离
  - 工具不返回的联系方式说明无权限，LLM 应如实说明，禁止编造
"""

import json
import logging

from tools import register

logger = logging.getLogger("tools")

DEFINITION = {
    "name": "find_employee",
    "description": (
        "查询公司同事信息（姓名/部门/职位/工号）。当用户想找/问某个同事、"
        "某部门有哪些人、谁负责某事项（采购/研发/测试等）时使用。"
        "keyword 填员工姓名（含称呼，如\"王工\"）或职位关键词（如\"采购\"）；"
        "dept_name 可选填部门名（如\"研发中心\"）缩小范围。"
        "注意：手机号/邮箱仅在你有权限（审核人）时出现在结果里，其余情况只返回基础信息，"
        "不要向用户编造联系方式。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "keyword": {
                "type": "string",
                "description": "员工姓名/称呼，或职位关键词。必填，除非提供了 userid。",
            },
            "dept_name": {
                "type": "string",
                "description": "部门名称，可选。如：研发中心、采购部、制造部。",
            },
            "userid": {
                "type": "string",
                "description": "钉钉员工ID，可选。用于精确查询某个人。",
            },
        },
        "required": ["keyword"],
    },
}


@register("find_employee", DEFINITION)
def execute(args: dict) -> str:
    """执行员工查询，返回 JSON 字符串结果。"""
    try:
        keyword = str(args.get("keyword") or "").strip()
        dept_name = str(args.get("dept_name") or "").strip()
        userid = str(args.get("userid") or "").strip()

        if not keyword and not userid:
            return json.dumps(
                {"error": "请提供要查询的姓名、职位关键词或钉钉ID"},
                ensure_ascii=False,
            )

        # 懒导入：避免 tools 包半初始化时循环依赖
        from tools import get_current_staff_id
        from contact_api import get_contact_client, is_contact_admin, ContactPermissionError

        staff_id = get_current_staff_id()
        include_sensitive = is_contact_admin(staff_id)
        logger.info(
            f"find_employee: keyword={keyword!r} dept={dept_name!r} "
            f"userid={userid!r} staff={staff_id or '空'} sensitive={include_sensitive}"
        )

        client = get_contact_client()
        result = client.search(
            keyword=keyword,
            dept_name=dept_name,
            userid=userid,
            include_sensitive=include_sensitive,
        )
        # 非审核人时再次兜底确认敏感键不在结果里（search 内已剥离）
        if not include_sensitive:
            for item in result.get("results", []):
                item.pop("mobile", None)
                item.pop("email", None)
        return json.dumps(result, ensure_ascii=False)
    except ContactPermissionError as e:
        logger.warning(f"find_employee 权限错误: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:
        logger.exception(f"find_employee 执行异常: {e}")
        return json.dumps(
            {"error": "查询员工信息失败，请稍后重试或联系管理员检查钉钉通讯录权限配置"},
            ensure_ascii=False,
        )

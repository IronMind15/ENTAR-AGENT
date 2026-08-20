"""
钉钉文件接收处理模块

功能：
1. 下载用户发送的文件（复用 dingtalk_stream SDK）
2. 保存到本地目录（按日期/用户分目录）
3. 判断文件类型，可选自动处理

依赖：
- dingtalk_stream SDK（已有，无需额外安装）
- requests（已有）
"""

import os
import logging
import re
import requests

# 全局直连 Session：钉钉文件下载走国内地址，强制直连，不跟随系统/环境代理（避免 Clash 劫持）
_NET_SESSION = requests.Session()
_NET_SESSION.trust_env = False

from datetime import datetime
from pathlib import Path

from paths import UPLOADS_DIR
from typing import Optional

from doc_mgr.identity import file_sha256

logger = logging.getLogger("file_handler")

# 文件保存根目录
_UPLOAD_ROOT = UPLOADS_DIR

# 支持自动处理的文件类型
_AUTO_PROCESS_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".md"}

# 可「帮我学习」直接入库的文件类型（v1.10.2，比自动处理集合宽，含 Word/PPT/CSV；v1.11.11 补 .txt 纯文本）
_LEARN_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".md", ".docx", ".pptx", ".csv", ".txt"}

# 图片扩展名：当作「文件消息」发来的图片也走识图（v1.11.11，复用 image_describe 能力）
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# 特殊不支持类型的引导文案（v1.11.11）：让用户知道该怎么做，而不是干等
_SPECIAL_HINTS = {
    ".doc": "老版 Word（.doc）暂不支持入库，请用 WPS/Office 另存为 .docx 后重发，即可「帮我学习」。",
    ".ppt": "老版 PPT（.ppt）暂不支持入库，请用 WPS/Office 另存为 .pptx 后重发，即可「帮我学习」。",
    ".zip": "压缩包暂不支持解包入库，请解压后逐个发送里面的文件。",
    ".rar": "压缩包暂不支持解包入库，请解压后逐个发送里面的文件。",
    ".7z": "压缩包暂不支持解包入库，请解压后逐个发送里面的文件。",
}


def sanitize_file_name(file_name: str) -> str:
    """移除路径片段和 Windows 非法字符，避免上传文件名逃逸目录。"""
    raw = str(file_name or "").replace("\\", "/")
    name = raw.rsplit("/", 1)[-1].strip().strip(".")
    name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", name)
    if not name:
        name = "uploaded_file"

    stem, ext = os.path.splitext(name)
    if stem.upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }:
        stem = f"_{stem}"
    # 给日期、用户目录和冲突序号预留路径长度空间
    if len(stem) > 160:
        stem = stem[:160]
    if len(ext) > 20:
        ext = ext[:20]
    return f"{stem}{ext}"


def _safe_path_component(value: str, fallback: str = "未分组") -> str:
    """净化路径目录组件：仅保留字母数字/中文/`-_.`，截断 60 字符，防空逃逸。

    末尾 strip(".") 防止纯点组件（"." / ".."）造成目录穿越。
    """
    value = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "", str(value or "")).strip()
    value = "".join(c for c in value if c.isalnum() or c in "-_.")
    value = value.strip(".")
    if not value:
        return fallback
    return value[:60]


def _get_user_main_department(user_id: str) -> str:
    """取用户主部门：department_names（JSON 数组）第一个元素；无则「未分组」。

    v1.10.2：上传目录按主部门分组。未来恢复部门划分时此处即部门归属来源。
    """
    try:
        from user_store import get_store
        user = get_store().get_user(user_id) or {}
        names_raw = user.get("department_names", "") or ""
        if isinstance(names_raw, str) and names_raw.strip():
            import json
            names = json.loads(names_raw)
            if names:
                return _safe_path_component(str(names[0]), "未分组")
    except Exception as e:
        logger.debug(f"查询用户主部门失败: {e}")
    return "未分组"


def get_upload_dir(user_id: str, user_name: str = "") -> Path:
    """获取用户上传目录：data/uploads/{主部门}/{员工名字}/{YYYY-MM-DD}/

    v1.10.2 改版：按「主部门 / 员工名字 / 日期」三级存储，
    替代旧的「用户名_ID / 日期」。
    """
    today = datetime.now().strftime("%Y-%m-%d")

    department = _get_user_main_department(user_id)
    employee = _safe_path_component(user_name or user_id, fallback="unknown")

    user_dir = _UPLOAD_ROOT / department / employee / today
    user_dir.mkdir(parents=True, exist_ok=True)
    return user_dir


def get_file_type(file_name: str) -> str:
    """获取文件扩展名（小写）"""
    return os.path.splitext(file_name)[1].lower()


def should_auto_process(file_name: str) -> bool:
    """判断是否应该自动处理该文件"""
    ext = get_file_type(file_name)
    return ext in _AUTO_PROCESS_EXTENSIONS


def download_and_save_file(
    download_code: str,
    file_name: str,
    user_id: str,
    chatbot_handler,
    user_name: str = "",
    target_collection: str = "",
) -> dict:
    """
    下载并保存文件（复用 dingtalk_stream SDK）

    Args:
        download_code: 钉钉下载码
        file_name: 文件名
        user_id: 用户标识
        chatbot_handler: ChatbotHandler 实例（用于获取 access_token 和下载链接）
        user_name: 用户昵称（可选，用于目录命名）
        target_collection: 目标知识库（空表示待管理员分配）

    Returns:
        {"success": bool, "file_path": str|None, "file_name": str, "file_size": int, "message": str}
    """
    file_name = sanitize_file_name(file_name)
    result = {
        "success": False,
        "file_path": None,
        "file_name": file_name,
        "file_size": 0,
        "message": "",
    }

    try:
        # 1. 获取下载链接（复用 SDK 方法）
        logger.info(f"获取文件下载链接: {file_name}")
        download_url = chatbot_handler.get_image_download_url(download_code)

        if not download_url:
            result["message"] = "获取下载链接失败"
            logger.error(f"获取下载链接失败: {download_code}")
            return result

        # 2. 下载文件
        logger.info(f"下载文件: {file_name}")
        response = _NET_SESSION.get(download_url, timeout=120, stream=True)
        response.raise_for_status()

        file_size = len(response.content)
        result["file_size"] = file_size

        # 3. 检查文件大小（限制 100MB）
        max_size = 100 * 1024 * 1024  # 100MB
        if file_size > max_size:
            result["message"] = f"文件过大（{file_size / 1024 / 1024:.1f}MB），最大支持 100MB"
            logger.warning(f"文件过大: {file_name} ({file_size} bytes)")
            return result

        # 4. 保存文件（目录名包含用户名和ID）
        user_dir = get_upload_dir(user_id, user_name)
        file_path = user_dir / file_name

        # 处理文件名冲突：如果文件已存在，添加序号
        counter = 1
        while file_path.exists():
            name_part = os.path.splitext(file_name)[0]
            ext_part = os.path.splitext(file_name)[1]
            file_path = user_dir / f"{name_part}_{counter}{ext_part}"
            counter += 1

        with open(file_path, "wb") as f:
            f.write(response.content)

        result["success"] = True
        result["file_path"] = str(file_path)
        result["file_name"] = file_path.name
        result["message"] = "文件保存成功"

        logger.info(f"文件已保存: {file_path} ({file_size} bytes)")

        # 记录到同步追踪器（标记为待处理，管理员可在后台分配库）
        try:
            from doc_mgr.sync_tracker import SyncTracker
            tracker = SyncTracker()
            tracker.upsert_file(
                file_path=str(file_path),
                file_name=file_path.name,
                file_size=file_size,
                file_hash=file_sha256(str(file_path)),
                target_collection=target_collection,
                upload_user_id=user_id,
                upload_user_name=user_name or "",
            )
            logger.info(f"  → 已记录上传者: {user_name or user_id}")
        except Exception as track_err:
            logger.warning(f"记录同步追踪失败（不影响主流程）: {track_err}")

        return result

    except requests.Timeout:
        result["message"] = "文件下载超时，请重试"
        logger.error(f"文件下载超时: {file_name}")
    except requests.RequestException as e:
        result["message"] = f"文件下载失败: {str(e)[:100]}"
        logger.error(f"文件下载失败: {e}")
    except Exception as e:
        result["message"] = f"文件处理失败: {str(e)[:100]}"
        logger.error(f"文件处理失败: {e}")

    return result


def format_file_received_message(result: dict, auto_process: bool = False) -> str:
    """
    格式化文件接收确认消息

    Args:
        result: download_and_save_file 的返回结果
        auto_process: 是否自动处理文件

    Returns:
        格式化的 Markdown 消息
    """
    if not result["success"]:
        return f"❌ 文件接收失败\n\n原因：{result['message']}"

    file_size = result["file_size"]
    if file_size > 1024 * 1024:
        size_str = f"{file_size / 1024 / 1024:.1f} MB"
    elif file_size > 1024:
        size_str = f"{file_size / 1024:.1f} KB"
    else:
        size_str = f"{file_size} B"

    msg = f"✅ 文件已收到并保存\n\n"
    msg += f"📄 文件名：{result['file_name']}\n"
    msg += f"📏 文件大小：{size_str}\n"

    if auto_process:
        ext = get_file_type(result["file_name"])
        if ext in _IMAGE_EXTENSIONS:
            # v1.11.11：图片当文件发也走识图，bot 侧会追加识图结果
            msg += "\n👀 图片已保存，正在识别内容……\n"
        elif ext in _LEARN_EXTENSIONS:
            # v1.11.0：上传后主动推荐入库，回复「入库」/「确认」即学习。
            # （旧审核文案见 git 历史；未来恢复审核流程时改回提示申请编号。）
            msg += (
                "\n👌 要不要我把这份文件入库知识库？回复「入库」/「确认」即可。\n"
                "📁 回复「我的文件」可查看已上传文件；「删除学习 序号」可删除。\n"
            )
        else:
            hint = _SPECIAL_HINTS.get(ext)
            if hint:
                msg += f"\n⚠️ {hint}\n"
            else:
                msg += "\n⚠️ 该文件类型暂不支持入库，已保存到待处理区。\n"

    return msg

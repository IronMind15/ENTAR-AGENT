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
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger("file_handler")

# 文件保存根目录
_UPLOAD_ROOT = Path(__file__).parent.parent / "data" / "uploads"

# 支持自动处理的文件类型
_AUTO_PROCESS_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".md"}


def get_upload_dir(user_id: str, user_name: str = "") -> Path:
    """获取用户上传目录（先按用户，再按日期）

    目录结构：data/uploads/用户名_ID/YYYY-MM-DD/
    """
    today = datetime.now().strftime("%Y-%m-%d")

    # 清理用户ID和用户名中的特殊字符，确保路径安全
    safe_user_id = "".join(c for c in user_id if c.isalnum() or c in "-_")
    if not safe_user_id:
        safe_user_id = "unknown"

    safe_user_name = "".join(c for c in user_name if c.isalnum() or c in "-_中文" or '一' <= c <= '鿿')

    # 构建目录名：用户名_ID（有昵称时）或 纯 ID
    if safe_user_name:
        dir_name = f"{safe_user_name}_{safe_user_id}"
    else:
        dir_name = safe_user_id

    # 先按用户分目录，再按日期分目录
    user_dir = _UPLOAD_ROOT / dir_name / today
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
        response = requests.get(download_url, timeout=120, stream=True)
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
                file_hash=str(int(os.path.getmtime(str(file_path)))),
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
        if ext in _AUTO_PROCESS_EXTENSIONS:
            msg += f"\n👤 文件已保存到待处理区，请联系管理员登录后台完成入库同步。\n"

    return msg

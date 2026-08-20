"""项目路径与运行数据边界。

生产代码、测试代码和部署脚本都通过本模块取得运行目录，避免每个模块
自行拼接 ``data/``、``logs/`` 和 ``knowledge_base/``，导致测试误写生产
数据或部署时漏排运行文件。

默认行为保持兼容：生产环境继续使用仓库根目录下的 data/、logs/ 和
knowledge_base/。测试进程若未显式指定 ``ENTAR_RUNTIME_DIR``，自动使用
data/_test_runtime/；生产部署可通过 ``ENTAR_RUNTIME_DIR`` 和
``ENTAR_KNOWLEDGE_BASE_DIR`` 将运行数据移出代码仓库。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
DATA_ROOT = PROJECT_ROOT / "data"


def _is_testing() -> bool:
    value = os.environ.get("ENTAR_TESTING", "").strip().lower()
    return value in {"1", "true", "yes", "on"} or "unittest" in sys.modules


TESTING = _is_testing()

# ENTAR_RUNTIME_DIR 是唯一推荐的运行数据覆盖入口。保留生产默认路径，
# 确保升级 v1.13.1 时不会把现有 user_store.db / uploads 自动换位置。
_runtime_override = os.environ.get("ENTAR_RUNTIME_DIR", "").strip()
RUNTIME_DIR = Path(_runtime_override).expanduser().resolve() if _runtime_override else (
    DATA_ROOT / "_test_runtime" if TESTING else DATA_ROOT
)

_kb_override = os.environ.get("ENTAR_KNOWLEDGE_BASE_DIR", "").strip()
KNOWLEDGE_BASE_DIR = Path(_kb_override).expanduser().resolve() if _kb_override else (
    RUNTIME_DIR / "knowledge_base" if TESTING or _runtime_override else PROJECT_ROOT / "knowledge_base"
)

_log_override = os.environ.get("ENTAR_LOG_DIR", "").strip()
LOG_DIR = Path(_log_override).expanduser().resolve() if _log_override else (
    RUNTIME_DIR / "logs" if TESTING or _runtime_override else PROJECT_ROOT / "logs"
)

DB_PATH = RUNTIME_DIR / "user_store.db"
UPLOADS_DIR = RUNTIME_DIR / "uploads"
DASHBOARD_TASKS_DIR = RUNTIME_DIR / "dashboard_tasks"
DASHBOARD_TEMPLATES_DIR = RUNTIME_DIR / "dashboard_templates"

# 以下是随仓库交付的静态/业务源文件目录，不因测试模式自动切换。
STANDARDS_DIR = DATA_ROOT / "standards"
FAULT_CODES_DIR = DATA_ROOT / "fault_codes"
EXPERIENCE_DIR = DATA_ROOT / "experience"
PCB_DIR = DATA_ROOT / "pcb"
EVAL_DIR = DATA_ROOT / "eval"


def ensure_runtime_dirs() -> None:
    """创建运行目录；仅在真正需要写运行数据时调用。"""
    for directory in (RUNTIME_DIR, LOG_DIR, UPLOADS_DIR,
                      DASHBOARD_TASKS_DIR, DASHBOARD_TEMPLATES_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def as_str(path: Path) -> str:
    """兼容旧模块使用 ``os.path`` 的字符串路径。"""
    return str(path)

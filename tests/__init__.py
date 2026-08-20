"""恩特小助手自动化回归测试。"""
"""测试包级安全护栏。

必须在任何业务模块导入前设置测试标记，阻止 config.py 回读开发机
local_config.py 中的真实服务凭据。所有外部调用均应由测试显式 mock。
"""

import os
from pathlib import Path

os.environ.setdefault("ENTAR_TESTING", "1")

# 测试默认使用独立运行目录，避免任何未显式传入 db_path 的模块误写生产
# data/user_store.db、uploads 或看板任务快照。显式设置时尊重调用方配置。
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault(
    "ENTAR_RUNTIME_DIR",
    str(_PROJECT_ROOT / "data" / "_test_runtime"),
)

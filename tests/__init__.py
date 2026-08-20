"""恩特小助手自动化回归测试。"""
"""测试包级安全护栏。

必须在任何业务模块导入前设置测试标记，阻止 config.py 回读开发机
local_config.py 中的真实服务凭据。所有外部调用均应由测试显式 mock。
"""

import os

os.environ.setdefault("ENTAR_TESTING", "1")

"""恩特小助手脚本包入口。

保留历史上 ``python scripts/main.py`` 的直接启动方式，同时提供稳定的
``python -m scripts.main`` 调用方式。项目内部模块仍使用既有的扁平导入名，
这里把 ``scripts/`` 放入模块搜索路径，避免不同启动目录造成导入错乱。
"""

from pathlib import Path
import sys


_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

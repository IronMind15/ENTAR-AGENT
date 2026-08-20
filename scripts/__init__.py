"""恩特小助手脚本包。

活动代码统一从 ``scripts`` 包加载，避免同一文件同时以 ``tools`` / ``skills``
和 ``scripts.tools`` / ``scripts.skills`` 两个模块名进入 ``sys.modules``。
直接执行 ``python scripts/main.py`` 的兼容处理位于入口脚本本身，不再由包
初始化阶段修改全局模块搜索路径。
"""

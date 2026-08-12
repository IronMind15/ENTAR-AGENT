"""
恩特小助手 — 技能注册中心

所有技能模块通过 @register 装饰器注册到 _skill_registry。
main.py 和 dingtalk_bot.py 通过 get_matched_skill() 统一路由。

添加新技能只需：
  1. 在 skills/ 下新建 .py 文件
  2. 定义继承 BaseSkill 的类，实现 match() 和 handle()
  3. 用 @register 装饰
  4. 在本文件底部加入 from . import 新模块
"""

from typing import Optional


class BaseSkill:
    """技能基类 — 所有技能模块继承此类

    子类需定义类属性:
        name: str        — 技能名称
        description: str — 一句话描述
        priority: int    — 优先级（数值越大越先匹配）

    子类需实现类方法:
        match(cls, query)  → bool   判断是否应处理该查询
        handle(cls, query) → dict   处理查询，返回 {"answer": str, "source": str}
    """
    name: str = ""
    description: str = ""
    priority: int = 0

    @classmethod
    def match(cls, query: str, user_id: str = "") -> bool:
        raise NotImplementedError

    @classmethod
    def handle(cls, query: str, **kwargs) -> dict:
        raise NotImplementedError


# ===== 注册中心 =====
_skill_registry: list[type[BaseSkill]] = []


def register(cls):
    """注册技能类（用作类装饰器）"""
    _skill_registry.append(cls)
    _skill_registry.sort(key=lambda s: s.priority, reverse=True)
    return cls


def get_matched_skill(query: str, user_id: str = "") -> Optional[type[BaseSkill]]:
    """遍历已注册技能，返回第一个 match() 返回 True 的

    技能按 priority 降序遍历（高优先级先匹配）。
    user_id 透传给 match（v1.11.9：确认词等承接式判定需要按用户隔离，
    避免 A 的看板活动时间戳误拦截 B 的「确认」，见审查 Critical 1）。
    RAG Agent 作为兜底（priority=50，match 始终返回 True），
    因此此函数始终有返回值，但在防御性编程中仍保留 None 分支。
    """
    for skill_cls in _skill_registry:
        if skill_cls.match(query, user_id):
            return skill_cls
    return None


def get_skill_list() -> list[type[BaseSkill]]:
    """获取所有注册的技能列表（调试/启动展示用）"""
    return list(_skill_registry)


# ===== 自动导入技能模块（确保 @register 装饰器执行） =====
from . import error_query      # noqa: E402, F811 — 优先级 100：仅精确故障代码快速通道
from . import pcb_calc         # noqa: E402, F811 — 优先级 90 ：PCB 设计计算（IPC-2221 秒回）
from . import dashboard        # noqa: E402, F811 — 优先级 80 ：每日项目看板（v1.11.0）
from . import standards_query  # noqa: E402, F811 — 优先级 70 ：标准编号快速通道 + 语义搜索（v1.11.6 统一）
from . import agent            # noqa: E402, F811 — 优先级 50 ：RAG Agent（LLM + 工具调用，统一处理所有问题）

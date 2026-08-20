"""看板任务提示词快照。

任务提示词不是对话上下文，也不是每次临时由主 Agent 生成的指令。
它在任务创建或用户明确修改输出模板时固化，执行模型每次只读取这份快照，
完成本次 map/reduce 后结束，不依赖上一次执行的隐藏对话状态。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from datetime import datetime

from scripts.paths import DASHBOARD_TASKS_DIR


PROMPT_VERSION = "task-prompt-v1"
_TASK_PROMPT_DIR = str(DASHBOARD_TASKS_DIR)
logger = logging.getLogger("dashboard.task_prompt")


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def template_spec(template) -> dict:
    """提取模板的不可变执行规格，避免只保存 template_id。"""
    return {
        "key": getattr(template, "key", "daily"),
        "name": getattr(template, "name", "每日简报"),
        "title": getattr(template, "title", ""),
        "map_instructions": getattr(template, "map_instructions", ""),
        "reduce_instructions": getattr(template, "reduce_instructions", ""),
        "section_spec": getattr(template, "section_spec", []) or [],
    }


def build_prompt(sub, template) -> tuple[str, dict, str, str]:
    """生成可展示、可审计的任务提示词和快照元数据。"""
    spec = template_spec(template)
    title = getattr(sub, "title", "恩特能源每日项目看板") or "恩特能源每日项目看板"
    source_names = ", ".join(str(x) for x in (getattr(sub, "data_sources", []) or []))
    prompt = f"""你正在执行一个一次性的文档监测任务，不是参与持续聊天。

任务名称：{title}
任务提示词版本：{PROMPT_VERSION}
监测数据源：{source_names or '由本次任务运行时提供'}
输出模板：{spec['name']}（{spec['key']}）

任务目标：
1. 只根据本次运行提供的文档数据和与上次快照的差异，提炼最新变化。
2. 优先报告新增、删除、字段更新、风险、阻塞、待决策和跨团队依赖。
3. 没有证据的内容不能补写、猜测、美化或声称正常。
4. 每条结论必须能够回溯到本次输入中的来源记录和字段；系统会再次校验引用。
5. 如果输入包含部分读取失败，必须在输出中保留数据完整性提醒，不能把部分成功说成全部成功。
6. 如果没有重要变化，明确说明没有发现重要变化，不要用旧内容伪装成新进展。

筛选阶段指令：
{spec['map_instructions'] or '优先筛选本次发生变化的记录，存量只保留持续风险、阻塞或待决策事项。'}

汇总阶段指令：
{spec['reduce_instructions'] or '依据有证据的记录生成精炼总结，结论必须可回溯原文。'}

输出结构快照：
{json.dumps(spec['section_spec'], ensure_ascii=False)}

本提示词在任务创建时固化。每次执行只把本次文档数据、差异和必要的系统输出约束附加进来；
本次执行完成后丢弃临时上下文，不继承模型上一次执行的对话记忆。""".strip()
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    created_at = _now()
    return prompt, spec, digest, created_at


def _safe_user_folder(user_id: str) -> str:
    """用户 ID 仅能作为目录名，不允许路径穿越或 Windows 非法字符。"""
    safe = "".join(c for c in (user_id or "") if c.isalnum() or c in ("_", "-"))
    return safe[:80] or "unknown_user"


def prompt_file_path(sub) -> str:
    """每个看板任务独占一份可审计 JSON：data/dashboard_tasks/{user}/task_{id}.json。"""
    sub_id = int(getattr(sub, "id", 0) or 0)
    if sub_id <= 0:
        return ""
    folder = _safe_user_folder(getattr(sub, "owner_user_id", ""))
    return os.path.join(_TASK_PROMPT_DIR, folder, f"task_{sub_id}.json")


def sync_prompt_file(sub) -> str:
    """将任务提示词快照同步到所属用户文件夹；失败不影响任务执行。"""
    path = prompt_file_path(sub)
    if not path:
        return ""
    payload = {
        "task_id": int(getattr(sub, "id", 0) or 0),
        "title": getattr(sub, "title", ""),
        "owner_user_id": getattr(sub, "owner_user_id", ""),
        "data_sources": list(getattr(sub, "data_sources", []) or []),
        "template_id": getattr(sub, "template_id", "daily"),
        "prompt_version": getattr(sub, "task_prompt_version", "") or PROMPT_VERSION,
        "prompt_hash": getattr(sub, "task_prompt_hash", ""),
        "prompt_updated_at": getattr(sub, "task_prompt_created_at", ""),
        "template_snapshot": getattr(sub, "task_prompt_spec", {}) or {},
        "task_prompt": getattr(sub, "task_prompt", ""),
        "saved_at": _now(),
    }
    temp_path = ""
    try:
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        # 固定的 .tmp 在两个调度线程或一次测试并发写同一任务时会互相覆盖；
        # Windows 还可能暂时锁住旧 JSON。每次使用唯一临时文件，并小范围重试替换。
        fd, temp_path = tempfile.mkstemp(prefix=f"task_{payload['task_id']}_",
                                         suffix=".tmp", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        for attempt in range(3):
            try:
                os.replace(temp_path, path)
                return path
            except PermissionError:
                if attempt == 2:
                    raise
                time.sleep(0.05 * (attempt + 1))
    except Exception as exc:
        logger.warning("任务提示词文件同步失败(%s): %s", getattr(sub, "id", "?"), exc)
        return ""
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def set_custom_prompt(sub, text: str) -> str:
    """用户明确确认后覆盖任务提示词，保留模板结构和硬证据约束。"""
    prompt = (text or "").strip()
    if len(prompt) < 20:
        raise ValueError("提示词至少需要 20 个字符，请明确写出希望如何总结。")
    if len(prompt) > 4000:
        raise ValueError("提示词超过 4000 个字符，请精简后再保存。")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
    sub.task_prompt = prompt
    sub.task_prompt_version = "task-prompt-user-v1"
    sub.task_prompt_hash = digest
    sub.task_prompt_created_at = _now()
    return prompt


def render_prompt_message(sub) -> str:
    """主动推送的第一条：让用户逐次核对实际输入给模型的固定提示词。"""
    prompt = (getattr(sub, "task_prompt", "") or "").strip()
    return "\n".join([
        "📜 本次看板任务固定提示词",
        f"任务：{getattr(sub, 'title', '每日项目看板')}（编号 {getattr(sub, 'id', 0)}）",
        f"版本：{getattr(sub, 'task_prompt_version', '') or PROMPT_VERSION}",
        f"哈希：{getattr(sub, 'task_prompt_hash', '') or '未生成'}",
        "说明：以下提示词会随本次文档数据一起发送给看板执行模型；本次完成后不保留临时上下文。",
        "",
        prompt or "（提示词尚未生成）",
        "",
        "如需修改，请回复「编辑看板任务提示词改成：……」，我会先请您确认。",
    ])

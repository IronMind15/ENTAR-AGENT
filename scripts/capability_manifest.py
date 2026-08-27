"""
恩特小助手 — 能力清单生成器（v1.12.0）

从三套入口的「登记表/注册中心」生成单一能力快照文档 docs/能力清单.md，
避免人工维护能力表滞后（此前 prompt 工具段、显示映射、能力清单各自手写）。

数据来源（全部是单一事实源，本脚本只做聚合展示）：
  - LLM 工具   → tools 注册中心（tools.get_tool_metadata）
  - 技能层     → skills 注册中心（skills.get_skill_list）
  - Bot 命令   → dingtalk_bot._BOT_COMMANDS 登记表（v1.12.0）
  - 看板意图   → subscription_commands._INTENT_DEFS 登记表（v1.12.0）
  - 已停用残留 → 停用工具文件（search_standards / search_experience_kb）+ removed 命令

惰性 import + try/except 降级：本脚本可独立运行（CLI），单点失败不影响整体输出。

用法：
  python scripts/capability_manifest.py --write   # 生成/覆盖 docs/能力清单.md
  python scripts/capability_manifest.py           # 只打印，不落盘
"""

import datetime
import os
import sys

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

_OUTPUT = os.path.join(_PROJECT_ROOT, "docs", "能力清单.md")


# ===== 各入口采集（惰性 import + 降级） =====

def collect_tools() -> list[dict]:
    """LLM 工具：tools 注册中心元数据"""
    try:
        from scripts.tools import get_tool_metadata
        return get_tool_metadata()
    except Exception as e:  # pragma: no cover — 降级路径
        return [{"error": f"tools 采集失败: {e}"}]


def collect_skills() -> list[dict]:
    """技能层：skills 注册中心（name/description/priority）"""
    try:
        from scripts.skills import get_skill_list
        return [{"name": s.name, "description": s.description,
                 "priority": s.priority} for s in get_skill_list()]
    except Exception as e:  # pragma: no cover
        return [{"error": f"skills 采集失败: {e}"}]


def collect_bot_commands() -> list[dict]:
    """Bot 命令：dingtalk_bot._BOT_COMMANDS 登记表"""
    try:
        from scripts.skills import dingtalk_bot as bot
        return list(bot._BOT_COMMANDS)
    except Exception as e:  # pragma: no cover
        return [{"error": f"bot 命令采集失败: {e}"}]


def collect_dashboard_intents() -> list[dict]:
    """看板意图：subscription_commands._INTENT_DEFS 登记表"""
    try:
        from scripts.dashboard.subscription_commands import _INTENT_DEFS
        return list(_INTENT_DEFS)
    except Exception as e:  # pragma: no cover
        return [{"error": f"看板意图采集失败: {e}"}]


# ===== 渲染 =====

def _render_tools(tools: list[dict]) -> str:
    lines = ["## 🤖 LLM 工具（function calling，由注册中心生成）", ""]
    if tools and "error" in tools[0]:
        return lines[0] + "\n\n" + "> " + tools[0]["error"] + "\n"
    # 按板块分组（SECTOR_ORDER 顺序）。
    # 审查修复（v1.13.4）：SECTOR_ORDER/SECTOR_LABELS 复用 tools 注册中心副本，
    # 此前手写同一份 dict/tuple——两处维护易漂移（新增板块只改注册中心即可）。
    try:  # 独立运行 + 单点降级
        from scripts.tools import SECTOR_ORDER, SECTOR_LABELS
    except Exception:  # pragma: no cover — 降级路径
        SECTOR_ORDER = ("kb", "calc", "dash", "contact", "image", "doc")
        SECTOR_LABELS = {
            "kb": "📚 知识库", "calc": "🧮 计算", "dash": "📊 项目看板",
            "contact": "👥 通讯录", "image": "🖼️ 图片", "doc": "📄 文档",
        }
    by_sector: dict[str, list[dict]] = {}
    for t in tools:
        by_sector.setdefault(t.get("sector") or "_", []).append(t)
    for sector in SECTOR_ORDER:
        metas = by_sector.get(sector)
        if not metas:
            continue
        metas.sort(key=lambda m: m["name"])
        lines.append(f"### {SECTOR_LABELS.get(sector, sector)}")
        lines.append("")
        for m in metas:
            confirm = "（写操作：执行前须用户确认）" if m.get("confirm") else ""
            display = f" · {m['display']}" if m.get("display") else ""
            lines.append(f"- **{m['name']}**{display}{confirm}")
            desc = (m.get("description") or "").strip()
            if desc:
                lines.append(f"  - {desc}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_skills(skills: list[dict]) -> str:
    lines = ["## 🧩 技能层（路由处理器，按 priority 匹配）", ""]
    if skills and "error" in skills[0]:
        return lines[0] + "\n\n> " + skills[0]["error"] + "\n"
    for s in sorted(skills, key=lambda x: x.get("priority", 0), reverse=True):
        lines.append(f"- **{s.get('name')}**（priority {s.get('priority')}）: "
                     f"{s.get('description') or ''}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_bot_commands(cmds: list[dict]) -> str:
    lines = ["## 📟 Bot 命令（钉钉秒回正则，由 _BOT_COMMANDS 登记表生成）", ""]
    if cmds and "error" in cmds[0]:
        return lines[0] + "\n\n> " + cmds[0]["error"] + "\n"
    for c in cmds:
        status = c.get("status")
        if status == "removed":
            marker = "⛔ 已移除"
        else:
            marker = "✅"
        lines.append(f"- {marker} **{c.get('name')}**（{c.get('trigger')}）: "
                     f"{c.get('desc') or ''}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_dashboard_intents(intents: list[dict]) -> str:
    lines = ["## 📊 看板订阅意图（由 _INTENT_DEFS 登记表生成）", ""]
    if intents and "error" in intents[0]:
        return lines[0] + "\n\n> " + intents[0]["error"] + "\n"
    for it in intents:
        lines.append(f"- **{it.get('name')}**（{it.get('id')}）: "
                     f"触发「{it.get('trigger')}」— {it.get('desc') or ''}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_retired() -> str:
    lines = ["## ⏸️ 已停用 / 残留（保留不注册，恢复时取消注释）", ""]
    lines.append("- **search_standards / search_experience_kb**：v1.11.5 起停用注册，"
                 "统一由 kb_search 通用查询替代（文件保留作参考）")
    lines.append("- **Bot 命令 sync_review（审核口令）**：v1.10.2 停用上传审核，"
                 "v1.12.0 移除拦截，代码保留注释可恢复")
    lines.append("- **上传审核流程**（v1.10.2 停用）：`queue_review_for_upload` 调用保留注释，"
                 "恢复部门划分与审核时取消注释即可")
    lines.append("")
    return "\n".join(lines)


def render_capability_snapshot_md() -> str:
    """渲染完整能力快照 Markdown"""
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    header = (
        "# 🗺️ 恩特小助手能力清单\n\n"
        f"> ⚙️ 由 `scripts/capability_manifest.py` 自动生成（{ts}），勿手动编辑。\n"
        "> 数据源：tools 注册中心 / skills 注册中心 / bot 命令登记表 / 看板意图登记表。\n"
    )
    return "\n\n".join([
        header, _render_tools(collect_tools()).strip(),
        _render_skills(collect_skills()).strip(),
        _render_bot_commands(collect_bot_commands()).strip(),
        _render_dashboard_intents(collect_dashboard_intents()).strip(),
        _render_retired().strip(),
    ]) + "\n"


def write_snapshot(path: str | None = None) -> str:
    """生成能力清单并写入文件，返回写入路径"""
    target = path or _OUTPUT
    content = render_capability_snapshot_md()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        f.write(content)
    return target


if __name__ == "__main__":
    if "--write" in sys.argv:
        out = write_snapshot()
        print(f"✅ 已生成能力清单: {out}")
    else:
        print(render_capability_snapshot_md())

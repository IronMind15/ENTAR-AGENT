#!/usr/bin/env python3
"""生成 Codex Desktop vs Claude Code 对比分析 PPT"""

from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
import os

# ── 主题色 ──────────────────────────────────────────────
CLAUDE_ORANGE = RGBColor(0xD4, 0x7B, 0x2A)   # Claude 橙
CODEX_GREEN   = RGBColor(0x10, 0xA3, 0x7C)   # Codex 绿
DARK_BG       = RGBColor(0x1E, 0x1E, 0x2E)   # 深色背景
WHITE         = RGBColor(0xFF, 0xFF, 0xFF)
LIGHT_GRAY    = RGBColor(0x6B, 0x72, 0x80)
ACCENT_BLUE   = RGBColor(0x3B, 0x82, 0xF6)
DARK_TEXT      = RGBColor(0x2D, 0x2D, 0x3F)
LIGHT_BG      = RGBColor(0xF5, 0xF5, 0xFA)
BORDER_GRAY   = RGBColor(0xE0, 0xE0, 0xE8)

prs = Presentation()
prs.slide_width  = Inches(13.333)
prs.slide_height = Inches(7.5)

W = prs.slide_width
H = prs.slide_height

# ── 工具函数 ────────────────────────────────────────────

def add_shape(slide, left, top, width, height, fill_color=None, line_color=None, shape_type=MSO_SHAPE.ROUNDED_RECTANGLE):
    shape = slide.shapes.add_shape(shape_type, left, top, width, height)
    shape.shadow.inherit = False
    if fill_color:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill_color
    else:
        shape.fill.background()
    if line_color:
        shape.line.color.rgb = line_color
        shape.line.width = Pt(1)
    else:
        shape.line.fill.background()
    return shape

def add_text_box(slide, left, top, width, height, text, font_size=18, bold=False, color=DARK_TEXT, alignment=PP_ALIGN.LEFT, font_name="Microsoft YaHei"):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.color.rgb = color
    p.font.name = font_name
    p.alignment = alignment
    return txBox

def add_bullet_list(slide, left, top, width, height, items, font_size=16, color=DARK_TEXT, bullet_color=None, spacing=Pt(8)):
    txBox = slide.shapes.add_textbox(left, top, width, height)
    tf = txBox.text_frame
    tf.word_wrap = True
    for i, item in enumerate(items):
        if i == 0:
            p = tf.paragraphs[0]
        else:
            p = tf.add_paragraph()
        p.text = item
        p.font.size = Pt(font_size)
        p.font.color.rgb = color
        p.font.name = "Microsoft YaHei"
        p.space_after = spacing
        p.level = 0
    return txBox

def add_two_column_table(slide, left, top, col_widths, rows, header_bg=None):
    """画一个两列表格（无边框表格样式）"""
    total_width = sum(col_widths)
    row_height = Inches(0.45)
    header_h = Inches(0.5)
    y = top
    # header
    if header_bg:
        for ci, (cw, txt) in enumerate(zip(col_widths, rows[0])):
            x = left + sum(col_widths[:ci])
            shape = add_shape(slide, x, y, cw, header_h, fill_color=header_bg)
            tf = shape.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = txt
            p.font.size = Pt(14)
            p.font.bold = True
            p.font.color.rgb = WHITE
            p.font.name = "Microsoft YaHei"
            p.alignment = PP_ALIGN.CENTER
            tf.paragraphs[0].space_before = Pt(0)
            tf.paragraphs[0].space_after = Pt(0)
        y += header_h
    # data rows
    for ri, row in enumerate(rows[1:] if header_bg else rows):
        row_bg = LIGHT_BG if ri % 2 == 0 else WHITE
        for ci, (cw, txt) in enumerate(zip(col_widths, row)):
            x = left + sum(col_widths[:ci])
            shape = add_shape(slide, x, y, cw, row_height, fill_color=row_bg, line_color=BORDER_GRAY)
            tf = shape.text_frame
            tf.word_wrap = True
            tf.margin_left = Inches(0.15)
            tf.margin_right = Inches(0.15)
            p = tf.paragraphs[0]
            p.text = txt
            p.font.size = Pt(13)
            p.font.color.rgb = DARK_TEXT
            p.font.name = "Microsoft YaHei"
            p.alignment = PP_ALIGN.LEFT
        y += row_height
    return y

def add_page_number(slide, num, total):
    add_text_box(slide, Inches(12.3), Inches(7.0), Inches(0.8), Inches(0.4),
                 f"{num}/{total}", font_size=10, color=LIGHT_GRAY, alignment=PP_ALIGN.RIGHT)

def make_section_header(slide, title, subtitle=""):
    """给每页加统一的顶部标题栏"""
    # 顶部色条
    add_shape(slide, 0, 0, W, Inches(0.08), fill_color=ACCENT_BLUE)
    if subtitle:
        add_text_box(slide, Inches(0.6), Inches(0.3), Inches(10), Inches(0.5),
                     title, font_size=28, bold=True, color=DARK_TEXT)
        add_text_box(slide, Inches(0.6), Inches(0.85), Inches(10), Inches(0.4),
                     subtitle, font_size=14, color=LIGHT_GRAY)
    else:
        add_text_box(slide, Inches(0.6), Inches(0.35), Inches(10), Inches(0.6),
                     title, font_size=28, bold=True, color=DARK_TEXT)
    # 分隔线
    add_shape(slide, Inches(0.6), Inches(1.1), Inches(12), Inches(0.02), fill_color=BORDER_GRAY)

TOTAL_SLIDES = 10

# ══════════════════════════════════════════════════════════
# SLIDE 1 — 封面
# ══════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
# 深色背景
add_shape(slide, 0, 0, W, H, fill_color=DARK_BG)
# 装饰元素
add_shape(slide, 0, 0, Inches(0.12), H, fill_color=CODEX_GREEN)
add_shape(slide, Inches(0.12), 0, Inches(0.12), H, fill_color=CLAUDE_ORANGE)

add_text_box(slide, Inches(1.5), Inches(1.5), Inches(10), Inches(1.2),
             "Codex Desktop vs Claude Code", font_size=44, bold=True, color=WHITE)
add_text_box(slide, Inches(1.5), Inches(3.0), Inches(10), Inches(0.8),
             "桌面端 AI 编程代理深度对比分析", font_size=28, color=RGBColor(0xBB, 0xBB, 0xCC))
add_text_box(slide, Inches(1.5), Inches(4.2), Inches(10), Inches(0.6),
             "产品架构 · 模型能力 · 工作流 · 适用场景", font_size=18, color=LIGHT_GRAY)

# 装饰标签
tag1 = add_shape(slide, Inches(1.5), Inches(5.2), Inches(2.5), Inches(0.5), fill_color=CODEX_GREEN)
tf = tag1.text_frame; tf.paragraphs[0].text = "OpenAI Codex"; tf.paragraphs[0].font.size = Pt(16); tf.paragraphs[0].font.color.rgb = WHITE; tf.paragraphs[0].font.bold = True; tf.paragraphs[0].alignment = PP_ALIGN.CENTER
tag2 = add_shape(slide, Inches(4.3), Inches(5.2), Inches(2.5), Inches(0.5), fill_color=CLAUDE_ORANGE)
tf = tag2.text_frame; tf.paragraphs[0].text = "Anthropic Claude"; tf.paragraphs[0].font.size = Pt(16); tf.paragraphs[0].font.color.rgb = WHITE; tf.paragraphs[0].font.bold = True; tf.paragraphs[0].alignment = PP_ALIGN.CENTER
tag3 = add_shape(slide, Inches(7.1), Inches(5.2), Inches(2.5), Inches(0.5), fill_color=ACCENT_BLUE)
tf = tag3.text_frame; tf.paragraphs[0].text = "2026年7月"; tf.paragraphs[0].font.size = Pt(16); tf.paragraphs[0].font.color.rgb = WHITE; tf.paragraphs[0].font.bold = True; tf.paragraphs[0].alignment = PP_ALIGN.CENTER

add_page_number(slide, 1, TOTAL_SLIDES)

# ══════════════════════════════════════════════════════════
# SLIDE 2 — 目录
# ══════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
make_section_header(slide, "📋 目录")

toc_items = [
    "01  产品概览 — 两大 AI 编程代理是什么",
    "02  Codex Desktop — OpenAI 的多智能体指挥中心",
    "03  Claude Code — Anthropic 的全能终端代理",
    "04  核心差异 — 架构、界面、工作流对比",
    "05  模型能力 — 模型阵容与基准测试",
    "06  生态与集成 — GitHub、MCP、插件",
    "07  各自优势总结",
    "08  选型建议 — 什么场景选哪一个？",
    "09  总结与展望",
]
add_bullet_list(slide, Inches(1.5), Inches(1.5), Inches(10), Inches(5.5),
                toc_items, font_size=20, color=DARK_TEXT, spacing=Pt(14))
add_page_number(slide, 2, TOTAL_SLIDES)

# ══════════════════════════════════════════════════════════
# SLIDE 3 — 产品概览
# ══════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
make_section_header(slide, "01  产品概览", "两大 AI 编程代理的定位与起源")

# Codex 卡
card_w = Inches(5.5)
card_h = Inches(4.8)
card_x = Inches(0.8)
card_y = Inches(1.5)
shape = add_shape(slide, card_x, card_y, card_w, card_h, fill_color=WHITE, line_color=CODEX_GREEN)
add_text_box(slide, card_x + Inches(0.3), card_y + Inches(0.2), card_w - Inches(0.6), Inches(0.5),
             "🤖 Codex Desktop", font_size=22, bold=True, color=CODEX_GREEN)
codex_items = [
    "开发商：OpenAI",
    "首次发布：2025年4月（CLI）→ 2026年2月（macOS桌面）→ 2026年3月（Windows）",
    "定位：多智能体并行编程指挥中心",
    "核心体验：异步委派任务 → 后台云端沙箱执行 → 审查差异",
    "月活：200万+ 周活用户（2026年3月）",
    "许可证：CLI 开源（Apache-2.0），桌面应用闭源",
    "订阅：捆绑 ChatGPT Plus $20/月起",
]
add_bullet_list(slide, card_x + Inches(0.3), card_y + Inches(0.8), card_w - Inches(0.6), card_h - Inches(1.0),
                codex_items, font_size=14, color=DARK_TEXT, spacing=Pt(6))

# Claude Code 卡
card_x2 = Inches(6.8)
shape = add_shape(slide, card_x2, card_y, card_w, card_h, fill_color=WHITE, line_color=CLAUDE_ORANGE)
add_text_box(slide, card_x2 + Inches(0.3), card_y + Inches(0.2), card_w - Inches(0.6), Inches(0.5),
             "🟠 Claude Code", font_size=22, bold=True, color=CLAUDE_ORANGE)
claude_items = [
    "开发商：Anthropic",
    "首次发布：2025年初（CLI）→ 持续迭代至桌面 + IDE 扩展",
    "定位：全场景 AI 编程代理工具链",
    "核心体验：终端交互式深度编码 → 多智能体协作 → 全流程 PR",
    "基准领先：SWE-bench Verified 88.6%（Opus 4.8）",
    "许可证：闭源",
    "订阅：Claude Pro $20/月起，Max $100-200/月",
]
add_bullet_list(slide, card_x2 + Inches(0.3), card_y + Inches(0.8), card_w - Inches(0.6), card_h - Inches(1.0),
                claude_items, font_size=14, color=DARK_TEXT, spacing=Pt(6))

# 标语
add_text_box(slide, Inches(1.5), Inches(6.5), Inches(10), Inches(0.5),
             '💡 两者都是当前 AI 编程代理的第一梯队，但「工程设计哲学」截然不同',
             font_size=13, color=LIGHT_GRAY, alignment=PP_ALIGN.CENTER)
add_page_number(slide, 3, TOTAL_SLIDES)

# ══════════════════════════════════════════════════════════
# SLIDE 4 — Codex Desktop 详解
# ══════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
make_section_header(slide, "02  Codex Desktop 详解", "OpenAI 的多智能体并行编程指挥中心")

features = [
    ("🧠 多智能体并行", "同时运行多个 AI 代理，每个代理在独立隔离线程中工作。\n内置 Git Worktree，多个代理可并行修改同一仓库。"),
    ("☁️ 云端沙箱执行", "任务在隔离云容器中运行，不接触本地环境。\n支持数小时乃至数天的长时间后台任务。"),
    ("🖥️ 计算机操作（Computer Use）", "2026年4月上线。Codex 可操控桌面应用——看见屏幕、\n点击、打字。同一 Mac 上运行多个代理互不干扰。"),
    ("🔌 90+ 插件生态", "Figma → UI 代码、Linear 项目管理、Vercel 部署、\nGPT Image 图像生成、文档/PDF/表格创建等。"),
    ("⚡ GPT-5.4-Codex 系列", "专门优化的编码模型。Spark 变体 15× 推理加速\n（Cerebras 硬件驱动）。"),
    ("⏰ 自动化任务调度", "安排定时后台任务（每日 Issue 分类、CI 摘要、\n版本发布简报）。电脑关闭也可运行（云端触发）。"),
]
y = Inches(1.5)
for title, desc in features:
    x = Inches(0.8) if features.index((title, desc)) % 2 == 0 else Inches(6.8)
    card = add_shape(slide, x, y, Inches(5.5), Inches(1.6), fill_color=WHITE, line_color=CODEX_GREEN)
    add_text_box(slide, x + Inches(0.25), y + Inches(0.1), Inches(5), Inches(0.4),
                 title, font_size=16, bold=True, color=CODEX_GREEN)
    add_text_box(slide, x + Inches(0.25), y + Inches(0.5), Inches(5), Inches(1.0),
                 desc, font_size=12, color=DARK_TEXT)
    if features.index((title, desc)) % 2 == 1:
        y += Inches(1.8)

add_page_number(slide, 4, TOTAL_SLIDES)

# ══════════════════════════════════════════════════════════
# SLIDE 5 — Claude Code 详解
# ══════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
make_section_header(slide, "03  Claude Code 详解", "Anthropic 的全场景 AI 编程代理工具链")

features = [
    ("🖥️ 终端原生体验", "最成熟的 CLI 编程代理。精细权限管理、渐进式上下文、\n多文件重构的深度交互体验。"),
    ("🧩 层级子代理（Sub-agents）", "Workflow 引擎支持层次化代理编排。\nPipeline / Parallel 模式，自动分解复杂任务。"),
    ("🔗 MCP 协议生态", "50+ 一键式 MCP 连接器（Slack、Notion、Google Drive、\n数据库等）。开放的 Model Context Protocol 标准。"),
    ("📜 深度配置系统", "CLAUDE.md 项目指南、自定义 Hooks、斜杠命令、\nSkills 技能系统、Keybindings 快捷键。"),
    ("🏆 基准测试领先", "SWE-bench Verified 88.6%（Claude Opus 4.8）。\nPR 接受率：文档任务 92.3%，新功能 72.6%。"),
    ("📱 多平台覆盖", "终端 · VS Code 扩展 · JetBrains 扩展 · 桌面应用 ·\nWeb 界面 · Slack 机器人 — 全场景覆盖。"),
]
y = Inches(1.5)
for title, desc in features:
    x = Inches(0.8) if features.index((title, desc)) % 2 == 0 else Inches(6.8)
    card = add_shape(slide, x, y, Inches(5.5), Inches(1.6), fill_color=WHITE, line_color=CLAUDE_ORANGE)
    add_text_box(slide, x + Inches(0.25), y + Inches(0.1), Inches(5), Inches(0.4),
                 title, font_size=16, bold=True, color=CLAUDE_ORANGE)
    add_text_box(slide, x + Inches(0.25), y + Inches(0.5), Inches(5), Inches(1.0),
                 desc, font_size=12, color=DARK_TEXT)
    if features.index((title, desc)) % 2 == 1:
        y += Inches(1.8)

add_page_number(slide, 5, TOTAL_SLIDES)

# ══════════════════════════════════════════════════════════
# SLIDE 6 — 核心差异对比
# ══════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
make_section_header(slide, "04  核心差异对比", "架构、界面、工作流、定价全面对比")

rows = [
    ["维度", "Codex Desktop", "Claude Code"],
    ["主要界面", "桌面应用 + ChatGPT 集成 + CLI", "终端（首选）+ IDE 扩展 + 桌面"],
    ["工作流风格", "异步委派：下达任务 → 后台执行 → 审查差异", "交互协作：实时对话 → 边看边改"],
    ["执行环境", "云端沙箱（本地不运行代码）", "本地机器 + GitHub Actions Runner"],
    ["多智能体", "✅ 原生并行，云端隔离", "✅ Workflow 引擎，层级编排"],
    ["Token 效率", "较高（同任务用 1/3-1/4 Cluade 的 Token）", "较低（上下文更深更广）"],
    ["开源", "CLI 开源（Apache-2.0）", "CLI 闭源"],
    ["Sandbox", "系统级开源沙箱", "权限分级管理 + Hooks"],
    ["GitHub 集成", "⭐ 强：自动 PR 审查、@Codex 提及", "✅ 完善：PR 工作流"],
    ["起步价", "ChatGPT Plus $20/月", "Claude Pro $20/月"],
    ["重度使用", "$200/月 Pro 含更多配额", "$100-200/月 Max 含更大上下文"],
]

add_two_column_table(slide, Inches(0.6), Inches(1.4),
                     [Inches(2.2), Inches(5.0), Inches(5.0)],
                     rows, header_bg=DARK_BG)

add_text_box(slide, Inches(0.8), Inches(6.5), Inches(11.5), Inches(0.5),
             "💡 核心差异不在模型强弱，而在工作流重心：Codex = 异步委派平台，Claude Code = 交互式编码工具链",
             font_size=13, color=LIGHT_GRAY, alignment=PP_ALIGN.CENTER)
add_page_number(slide, 6, TOTAL_SLIDES)

# ══════════════════════════════════════════════════════════
# SLIDE 7 — 模型能力对比
# ══════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
make_section_header(slide, "05  模型能力对比", "模型阵容与基准测试（2026年中）")

# 模型阵容
rows = [
    ["维度", "Codex Desktop", "Claude Code"],
    ["主力模型", "GPT-5.4-Codex", "Claude Opus 4.8 / Sonnet 5"],
    ["快速变体", "GPT-5.4-Codex-Spark（15×加速）", "Claude Haiku 4.5（快速/轻量）"],
    ["SWE-bench Verified", "~75%（GPT-5.5）", "88.6%（Opus 4.8）🥇"],
    ["Terminal-Bench 2.0", "77.3%（GPT-5.5）🥇", "~65%"],
    ["代码生成", "⭐ 高效、Token 经济", "⭐ 深度、上下文理解强"],
    ["多文件重构", "✅ 强，云端沙箱执行", "✅ 更强，终端深度交互"],
    ["调试能力", "✅ 异步审查式", "⭐ 交互式逐步深入"],
    ["推理控制粒度", "低/中/高/最低 四级", "取决于模型 + 配置"],
]

add_two_column_table(slide, Inches(0.6), Inches(1.4),
                     [Inches(2.2), Inches(5.0), Inches(5.0)],
                     rows, header_bg=DARK_BG)

# 底部说明
note_items = [
    "🏆 SWE-bench Verified 是业界公认的编码代理综合基准",
    "⚡ Codex-Spark 使用 Cerebras 专用硬件实现 15 倍推理加速，适合对延迟敏感的场景",
    "🔬 学术研究（arXiv 2026）：Claude Code 在文档任务（92.3%）和新功能（72.6%）PR 接受率领先",
]
add_bullet_list(slide, Inches(0.8), Inches(6.0), Inches(11), Inches(1.2),
                note_items, font_size=12, color=LIGHT_GRAY, spacing=Pt(4))
add_page_number(slide, 7, TOTAL_SLIDES)

# ══════════════════════════════════════════════════════════
# SLIDE 8 — 生态与集成
# ══════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
make_section_header(slide, "06  生态与集成对比", "GitHub 集成、插件系统、IDE 支持")

rows = [
    ["维度", "Codex Desktop", "Claude Code"],
    ["GitHub PR 审查", "⭐⭐⭐ 自动审查 + @Codex 评论修复", "⭐⭐ PR 工作流 + 手动触发"],
    ["GitHub Issues", "⭐⭐⭐ @Codex 提及即处理", "⭐⭐ 通过 Workflow 集成"],
    ["IDE 支持", "VS Code、JetBrains、Xcode 26.3", "VS Code、JetBrains、Neovim"],
    ["插件生态", "90+ 插件 + 企业插件系统", "50+ MCP 连接器 + Skills"],
    ["Figma 集成", "✅ 双向设计→代码（MCP）", "✅ 通过 MCP 连接"],
    ["项目管理", "Linear、Jira 插件", "通过 Skills/MCP 社区"],
    ["CI/CD", "Vercel、Netlify、Cloudflare", "自定义 Hooks + Actions"],
    ["远程开发", "SSH 远程（alpha）", "GitHub Codespaces"],
]

add_two_column_table(slide, Inches(0.6), Inches(1.4),
                     [Inches(2.2), Inches(5.0), Inches(5.0)],
                     rows, header_bg=DARK_BG)

# 两个生态对比框
# Codex 生态
bx = Inches(0.8); by = Inches(5.2); bw = Inches(5.5); bh = Inches(1.5)
card = add_shape(slide, bx, by, bw, bh, fill_color=WHITE, line_color=CODEX_GREEN)
add_text_box(slide, bx + Inches(0.3), by + Inches(0.1), bw - Inches(0.6), Inches(0.4),
             "🟢 Codex 生态特色", font_size=15, bold=True, color=CODEX_GREEN)
add_text_box(slide, bx + Inches(0.3), by + Inches(0.5), bw - Inches(0.6), Inches(0.8),
             "GitHub 深度集成 · 云端执行 · 企业插件市场 · 图像/文档生成\n90+ 即装即用插件 · 企业级安全管理插件",
             font_size=12, color=DARK_TEXT)

# Claude 生态
bx2 = Inches(6.8)
card = add_shape(slide, bx2, by, bw, bh, fill_color=WHITE, line_color=CLAUDE_ORANGE)
add_text_box(slide, bx2 + Inches(0.3), by + Inches(0.1), bw - Inches(0.6), Inches(0.4),
             "🟠 Claude Code 生态特色", font_size=15, bold=True, color=CLAUDE_ORANGE)
add_text_box(slide, bx2 + Inches(0.3), by + Inches(0.5), bw - Inches(0.6), Inches(0.8),
             "MCP 开放协议 · 深度可定制 Hooks/Harness\nCLAUDE.md 项目级配置 · Skills 技能系统 · 子代理编排",
             font_size=12, color=DARK_TEXT)

add_page_number(slide, 8, TOTAL_SLIDES)

# ══════════════════════════════════════════════════════════
# SLIDE 9 — 各自优势总结
# ══════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
make_section_header(slide, "07  各自优势总结")

# Codex 优势
bx = Inches(0.8); by = Inches(1.5); bw = Inches(5.5); bh = Inches(4.0)
card = add_shape(slide, bx, by, bw, bh, fill_color=WHITE, line_color=CODEX_GREEN)
add_text_box(slide, bx + Inches(0.3), by + Inches(0.2), bw - Inches(0.6), Inches(0.5),
             "✅ Codex Desktop 的强项", font_size=20, bold=True, color=CODEX_GREEN)
codex_pros = [
    "▶ 异步工作流 — 一次下达任务，后台执行，回头审查",
    "▶ 云端安全沙箱 — 不污染本地环境，适合隔离任务",
    "▶ GitHub 原生集成 — PR 自动审查 + @Codex Issue 处理",
    "▶ Token 效率高 — 同等产出消耗更少 Token",
    "▶ 多代理并行 — 云端同时运行多个独立代理",
    "▶ 计算机操作 — 可操控桌面应用进行端到端测试",
    "▶ 自动化调度 — 定时任务，电脑离线也可运行",
]
add_bullet_list(slide, bx + Inches(0.3), by + Inches(0.8), bw - Inches(0.6), bh - Inches(1.0),
                codex_pros, font_size=14, color=DARK_TEXT, spacing=Pt(7))

# Claude 优势
bx2 = Inches(6.8)
card = add_shape(slide, bx2, by, bw, bh, fill_color=WHITE, line_color=CLAUDE_ORANGE)
add_text_box(slide, bx2 + Inches(0.3), by + Inches(0.2), bw - Inches(0.6), Inches(0.5),
             "✅ Claude Code 的强项", font_size=20, bold=True, color=CLAUDE_ORANGE)
claude_pros = [
    "▶ 交互式深度编码 — 实时对话式多文件重构体验最佳",
    "▶ SWE-bench 领先 — 88.6% 综合编码能力最强",
    "▶ MCP 开放生态 — 50+ 一键连接器，标准开放协议",
    "▶ 深度定制能力 — Hooks / Skills / CLAUDE.md / Keybindings",
    "▶ 层级子代理 — Workflow 引擎支持复杂任务分解",
    "▶ 渐进式权限管理 — 精细控制安全与合规",
    "▶ PR 质量领先 — 文档 92.3%、新功能 72.6% 接受率",
]
add_bullet_list(slide, bx2 + Inches(0.3), by + Inches(0.8), bw - Inches(0.6), bh - Inches(1.0),
                claude_pros, font_size=14, color=DARK_TEXT, spacing=Pt(7))

add_page_number(slide, 9, TOTAL_SLIDES)

# ══════════════════════════════════════════════════════════
# SLIDE 10 — 选型建议 + 结束
# ══════════════════════════════════════════════════════════
slide = prs.slides.add_slide(prs.slide_layouts[6])
make_section_header(slide, "08  选型建议 & 总结", "什么场景选哪个？未来展望")

# 选型矩阵
rows = [
    ["场景 / 需求", "推荐", "理由"],
    ["团队工作流依赖 GitHub（PR/Issue）", "Codex Desktop", "原生 GitHub 集成，自动 PR 审查"],
    ["深度交互式编码 / 多文件重构", "Claude Code", "终端体验最优，SWE-bench 领先"],
    ["异步委派任务，不占本地资源", "Codex Desktop", "云端沙箱执行，离线也可运行"],
    ["需要深度定制工具链（Hooks/规则）", "Claude Code", "Hooks + Skills + CLAUDE.md 最强"],
    ["高 Token 效率 / 成本敏感", "Codex Desktop", "同任务 Token 消耗少 3-4×"],
    ["复杂层级任务编排", "Claude Code", "Workflow 引擎 + 子代理原生支持"],
    ["计算机操作 / 桌面端测试", "Codex Desktop", "Computer Use 能力独一无二"],
    ["混合生态集成（Slack/Notion/DB）", "Claude Code", "MCP 开放协议，连接器丰富"],
    ["综合编码能力优先", "Claude Code", "SWE-bench 88.6% 行业最高"],
]

add_two_column_table(slide, Inches(0.6), Inches(1.4),
                     [Inches(4.5), Inches(2.5), Inches(5.3)],
                     rows, header_bg=DARK_BG)

# 金句
add_text_box(slide, Inches(0.8), Inches(6.5), Inches(11.5), Inches(0.5),
             "💡 两者不是竞争关系，而是互补。最强大的配置 = 两个都要，各司其职",
             font_size=15, bold=True, color=ACCENT_BLUE, alignment=PP_ALIGN.CENTER)

add_page_number(slide, 10, TOTAL_SLIDES)

# ── 保存 ────────────────────────────────────────────────
output_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Codex_vs_Claude_对比分析.pptx")
prs.save(output_path)
result_msg = f"PPT generated: {output_path}"
print(result_msg)

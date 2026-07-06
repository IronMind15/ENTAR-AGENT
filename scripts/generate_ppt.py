"""
生成 PCS 故障查询系统架构 PPT
"""
from pptx import Presentation
from pptx.util import Inches, Pt, Emu
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_CONNECTOR_TYPE, MSO_SHAPE

# ===== 颜色方案 =====
C_PRIMARY = RGBColor(0x1A, 0x73, 0xE8)     # 主色蓝
C_SECONDARY = RGBColor(0x34, 0xA8, 0x53)    # 绿色
C_ACCENT = RGBColor(0xFF, 0xA0, 0x00)       # 橙色
C_DARK = RGBColor(0x20, 0x20, 0x20)         # 深色文字
C_GRAY = RGBColor(0x66, 0x66, 0x66)         # 灰色文字
C_LIGHT_BG = RGBColor(0xF5, 0xF5, 0xF5)     # 浅灰背景
C_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
C_RED = RGBColor(0xDC, 0x35, 0x45)
C_PURPLE = RGBColor(0x7B, 0x2D, 0xC0)
C_BG_DARK = RGBColor(0x1A, 0x1A, 0x2E)     # 深色背景

prs = Presentation()
prs.slide_width = Inches(13.33)
prs.slide_height = Inches(7.5)

W = prs.slide_width
H = prs.slide_height


def add_bg(slide, color=C_WHITE):
    """设置幻灯片背景色"""
    bg = slide.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = color


def add_shape(slide, left, top, width, height, fill_color=None, line_color=None, line_width=None, shape_type=MSO_SHAPE.ROUNDED_RECTANGLE):
    """添加形状"""
    shape = slide.shapes.add_shape(shape_type, left, top, width, height)
    if fill_color:
        shape.fill.solid()
        shape.fill.fore_color.rgb = fill_color
    else:
        shape.fill.background()
    if line_color:
        shape.line.color.rgb = line_color
        if line_width:
            shape.line.width = line_width
    else:
        shape.line.fill.background()
    return shape


def add_textbox(slide, left, top, width, height, text, font_size=12, bold=False, color=C_DARK, alignment=PP_ALIGN.LEFT, font_name="微软雅黑"):
    """添加文本框"""
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


def add_arrow_shape(slide, left, top, width, height, fill_color=C_PRIMARY):
    """添加下箭头"""
    shape = slide.shapes.add_shape(MSO_SHAPE.DOWN_ARROW, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill_color
    shape.line.fill.background()
    return shape


def add_right_arrow(slide, left, top, width, height, fill_color=C_PRIMARY):
    """添加右箭头"""
    shape = slide.shapes.add_shape(MSO_SHAPE.RIGHT_ARROW, left, top, width, height)
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill_color
    shape.line.fill.background()
    return shape


def add_rect_with_text(slide, left, top, width, height, text, fill_color=C_PRIMARY, text_color=C_WHITE, font_size=11, bold=False, shape_type=MSO_SHAPE.ROUNDED_RECTANGLE):
    """添加带文字的矩形"""
    shape = add_shape(slide, left, top, width, height, fill_color=fill_color, shape_type=shape_type)
    tf = shape.text_frame
    tf.word_wrap = True
    tf.paragraphs[0].alignment = PP_ALIGN.CENTER
    p = tf.paragraphs[0]
    p.text = text
    p.font.size = Pt(font_size)
    p.font.bold = bold
    p.font.color.rgb = text_color
    p.font.name = "微软雅黑"
    return shape


# ============================================================
# SLIDE 1: 封面
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])  # blank
add_bg(slide, C_BG_DARK)

# 标题
add_textbox(slide, Inches(1), Inches(1.5), Inches(11), Inches(1.2),
            "🔧 恩特小助手", font_size=44, bold=True, color=C_WHITE, alignment=PP_ALIGN.CENTER)

# 副标题
add_textbox(slide, Inches(1), Inches(2.8), Inches(11), Inches(0.8),
            "PCS 故障代码智能查询系统", font_size=28, color=RGBColor(0xAA, 0xCC, 0xFF), alignment=PP_ALIGN.CENTER)

# 分隔线
add_shape(slide, Inches(4.5), Inches(3.7), Inches(4), Inches(0.04), fill_color=C_ACCENT)

# 说明
add_textbox(slide, Inches(2), Inches(4.2), Inches(9), Inches(0.5),
            "基于 RAG 架构 | Chroma 向量检索 | DeepSeek 自然语言处理 | 钉钉 Stream 模式",
            font_size=16, color=RGBColor(0x88, 0x88, 0xAA), alignment=PP_ALIGN.CENTER)

add_textbox(slide, Inches(2), Inches(5.5), Inches(9), Inches(0.4),
            "恩特能源 · 内部工具   |   2026年7月",
            font_size=14, color=RGBColor(0x66, 0x66, 0x88), alignment=PP_ALIGN.CENTER)

# 底部装饰条
add_shape(slide, Inches(0), Inches(7.3), W, Inches(0.2), fill_color=C_PRIMARY)


# ============================================================
# SLIDE 2: 目录
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(slide, C_WHITE)

add_textbox(slide, Inches(0.8), Inches(0.5), Inches(5), Inches(0.6),
            "📑 内容目录", font_size=28, bold=True, color=C_DARK)
add_shape(slide, Inches(0.8), Inches(1.15), Inches(2.5), Inches(0.04), fill_color=C_PRIMARY)

items = [
    ("01", "系统整体架构", "从用户入口到数据存储的完整链路"),
    ("02", "模型搭建过程", "RAG 系统的构建步骤与技术选型"),
    ("03", "查询策略详解", "精确匹配 vs 语义搜索的双级机制"),
    ("04", "钉钉机器人工作流", "Stream 模式下的消息处理流程"),
    ("05", "背后信息传输", "数据在各组件间的流转时序"),
    ("06", "网络架构", "本地服务与钉钉云的连接方式"),
    ("07", "技术栈一览", "所有组件、工具与依赖"),
    ("08", "部署与使用", "快速上手指南"),
]

for i, (num, title, desc) in enumerate(items):
    y = Inches(1.6) + Inches(0.7) * i
    col = 0 if i < 4 else 1
    x = Inches(0.8) + Inches(6.2) * col
    yy = Inches(1.6) + Inches(0.7) * (i if i < 4 else i - 4)

    add_textbox(slide, x, yy, Inches(0.5), Inches(0.5), num,
                font_size=22, bold=True, color=C_PRIMARY)
    add_textbox(slide, x + Inches(0.6), yy, Inches(4), Inches(0.35), title,
                font_size=16, bold=True, color=C_DARK)
    add_textbox(slide, x + Inches(0.6), yy + Inches(0.32), Inches(4.5), Inches(0.3), desc,
                font_size=11, color=C_GRAY)


# ============================================================
# SLIDE 3: 系统整体架构（框架图）
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(slide, C_WHITE)

add_textbox(slide, Inches(0.8), Inches(0.3), Inches(6), Inches(0.5),
            "🏗️ 系统整体架构", font_size=26, bold=True, color=C_DARK)
add_shape(slide, Inches(0.8), Inches(0.85), Inches(2), Inches(0.03), fill_color=C_PRIMARY)

# ---- 顶层：用户入口 ----
# 用户图标
add_rect_with_text(slide, Inches(0.8), Inches(1.3), Inches(2.5), Inches(0.7),
                   "👤 用户", C_DARK, C_WHITE, 14, True)
add_rect_with_text(slide, Inches(4), Inches(1.3), Inches(2.5), Inches(0.7),
                   "🌐 Web 页面\nlocalhost:8000", C_PRIMARY, C_WHITE, 12, True)
add_rect_with_text(slide, Inches(7), Inches(1.3), Inches(2.5), Inches(0.7),
                   "💬 钉钉群聊\n@恩特小助手", C_SECONDARY, C_WHITE, 12, True)

# 箭头：用户→入口
add_right_arrow(slide, Inches(3.3), Inches(1.55), Inches(0.6), Inches(0.25), C_GRAY)
# 箭头：Web 和钉钉 → 服务
arr1 = add_arrow_shape(slide, Inches(5.25), Inches(2.05), Inches(0.3), Inches(0.35), C_PRIMARY)
add_arrow_shape(slide, Inches(8.25), Inches(2.05), Inches(0.3), Inches(0.35), C_SECONDARY)

# ---- 中层：FastAPI 服务 ----
add_rect_with_text(slide, Inches(3.5), Inches(2.6), Inches(6), Inches(1.1),
                   "🚀 FastAPI 服务 (main.py)\n\n"
                   "error_query.handle() — 两级查询引擎",
                   C_PRIMARY, C_WHITE, 14, True)

# 箭头：服务 → 下方组件
add_arrow_shape(slide, Inches(5.25), Inches(3.75), Inches(0.3), Inches(0.35), C_PRIMARY)
add_arrow_shape(slide, Inches(8), Inches(3.75), Inches(0.3), Inches(0.35), C_ACCENT)

# ---- 左下方：Chroma ----
add_rect_with_text(slide, Inches(3), Inches(4.3), Inches(3.5), Inches(1.0),
                   "💾 Chroma 向量数据库\n\n"
                   "BAAI/bge-small-zh-v1.5\n"
                   "133 条故障记录",
                   C_PURPLE, C_WHITE, 12, True)

# ---- 右下方：DeepSeek ----
add_rect_with_text(slide, Inches(7), Inches(4.3), Inches(3.5), Inches(1.0),
                   "🧠 DeepSeek API\n\n"
                   "仅用于：自然语言关键词提取\n"
                   "精确匹配不调用（秒回）",
                   C_ACCENT, C_WHITE, 12, True)

# 箭头：Chroma → Excel
add_arrow_shape(slide, Inches(3.25), Inches(5.35), Inches(0.25), Inches(0.3), C_PURPLE)
add_rect_with_text(slide, Inches(1.5), Inches(5.8), Inches(3.5), Inches(0.6),
                   "📊 Excel 数据源\nPCS参数表 V1.6.2.xlsx\n遥信（DI）sheet 第51~183行",
                   C_GRAY, C_WHITE, 11, True)

# 同步脚本标注
add_rect_with_text(slide, Inches(1.5), Inches(6.5), Inches(3.5), Inches(0.4),
                   "🔄 sync_kb.py 同步脚本",
                   RGBColor(0xDD, 0xDD, 0xDD), C_DARK, 10, False)

# 右侧：钉钉 Stream 连接
add_rect_with_text(slide, Inches(10), Inches(2.6), Inches(2.5), Inches(0.6),
                   "📡 WebSocket\n→ 钉钉服务器", RGBColor(0x00, 0x96, 0x88), C_WHITE, 11, True)

# 右侧连接标注
add_arrow_shape(slide, Inches(9.5), Inches(2.7), Inches(0.4), Inches(0.25), RGBColor(0x00, 0x96, 0x88))


# ============================================================
# SLIDE 4: 模型搭建过程
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(slide, C_WHITE)

add_textbox(slide, Inches(0.8), Inches(0.3), Inches(8), Inches(0.5),
            "🔨 模型搭建过程", font_size=26, bold=True, color=C_DARK)
add_shape(slide, Inches(0.8), Inches(0.85), Inches(2), Inches(0.03), fill_color=C_PRIMARY)

# 流程图：搭建步骤
steps = [
    ("1️⃣ 数据准备", "PCS参数表 V1.6.2.xlsx\n提取遥信（DI）sheet\n第51~183行数据", C_PRIMARY),
    ("2️⃣ 字段解析", "故障代码 / 名称 / 地址\n故障原因 / 说明 / 备注\n共 12 个字段", C_SECONDARY),
    ("3️⃣ Embedding", "BAAI/bge-small-zh-v1.5\n中文语句 → 向量\n768 维特征", C_ACCENT),
    ("4️⃣ 存入 Chroma", "逐行写入向量数据库\n支持 metadata 过滤\n支持语义查询", C_PURPLE),
    ("5️⃣ 查询引擎", "两级查询策略\n精确匹配 + 语义搜索\nDeepSeek 辅助提取", C_RED),
]

for i, (title, desc, color) in enumerate(steps):
    x = Inches(1) + Inches(2.4) * i
    # 卡片
    add_rect_with_text(slide, x, Inches(1.5), Inches(2.2), Inches(2.5),
                       f"{title}\n\n{desc}", color, C_WHITE, 11, False)
    # 箭头（除最后一个）
    if i < 4:
        add_right_arrow(slide, x + Inches(2.25), Inches(2.6), Inches(0.25), Inches(0.2), C_GRAY)

# 底部说明：同步脚本
add_rect_with_text(slide, Inches(1.5), Inches(4.5), Inches(10), Inches(1.2),
                   "🔄 一键同步命令：python scripts/sync_kb.py\n\n"
                   "流程：读取 Excel → 解析行数据 → 构建文档文本 → 计算向量嵌入 → 写入 Chroma\n"
                   "       删除旧集合 → 创建新集合（id: error_codes）→ 分批写入（每批50条）\n\n"
                   "📌 每次修改 Excel 后运行一次即可增量更新",
                   C_LIGHT_BG, C_DARK, 12, False)

# 右侧说明框
add_rect_with_text(slide, Inches(1.5), Inches(6.0), Inches(10), Inches(1.0),
                   "⚙️ 关键路径要点\n\n"
                   "• Embedding 模型首次加载自动从 HuggingFace 下载（约30MB）\n"
                   "• 国内网络需设置镜像：os.environ[\"HF_ENDPOINT\"] = \"https://hf-mirror.com\"\n"
                   "• Chroma 使用余弦距离（cosine）作为向量相似度度量",
                   RGBColor(0xE8, 0xF0, 0xFE), C_PRIMARY, 11, False)


# ============================================================
# SLIDE 5: 查询策略详解
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(slide, C_WHITE)

add_textbox(slide, Inches(0.8), Inches(0.3), Inches(8), Inches(0.5),
            "🎯 查询策略详解", font_size=26, bold=True, color=C_DARK)
add_shape(slide, Inches(0.8), Inches(0.85), Inches(2), Inches(0.03), fill_color=C_PRIMARY)

# 主流程图
add_rect_with_text(slide, Inches(0.8), Inches(1.3), Inches(11.5), Inches(0.6),
                   "📥 用户提问 → error_query.handle(q)",
                   C_DARK, C_WHITE, 14, True)

add_arrow_shape(slide, Inches(6.5), Inches(1.95), Inches(0.25), Inches(0.25), C_DARK)

# 分支判断
add_rect_with_text(slide, Inches(2), Inches(2.4), Inches(9), Inches(0.5),
                   "❓ 提取故障代码（正则匹配 d4-1、df-8、d10-1~d10-16 等）",
                   RGBColor(0xFF, 0xF3, 0xE0), C_ACCENT, 13, True)

# 两个分支
add_arrow_shape(slide, Inches(3.5), Inches(2.95), Inches(0.2), Inches(0.2), C_SECONDARY)
add_arrow_shape(slide, Inches(9.5), Inches(2.95), Inches(0.2), Inches(0.2), C_PRIMARY)

# 左分支：精确匹配
add_rect_with_text(slide, Inches(1), Inches(3.3), Inches(5), Inches(1.2),
                   "✅ 分支 A：精确匹配（秒回）\n\n"
                   "• collection.get(where={\"fault_code\": code})\n"
                   "• 元数据直接过滤，不调向量搜索\n"
                   "• 不调 DeepSeek API（零延迟）\n"
                   "• 返回该故障全部字段信息 + 来源行号",
                   C_SECONDARY, C_WHITE, 11, False)

# 右分支：语义搜索
add_rect_with_text(slide, Inches(6.8), Inches(3.3), Inches(5.5), Inches(1.2),
                   "🔍 分支 B：语义搜索\n\n"
                   "• 自然语言 → DeepSeek 提取关键词\n"
                   "• Chroma query(query_texts=keywords)\n"
                   "• 返回 top-5 相似结果 + 匹配度标记\n"
                   "• 来源标注：遥信（DI）表 第XX行",
                   C_PRIMARY, C_WHITE, 11, False)

# 结果合并
add_arrow_shape(slide, Inches(3.5), Inches(4.55), Inches(0.2), Inches(0.2), C_SECONDARY)
add_arrow_shape(slide, Inches(9.5), Inches(4.55), Inches(0.2), Inches(0.2), C_PRIMARY)

add_rect_with_text(slide, Inches(2), Inches(4.9), Inches(9), Inches(0.5),
                   "📤 返回格式化结果：{answer: \"...\", source: \"遥信（DI）表 第XX行\"}",
                   C_DARK, C_WHITE, 13, True)

# 底部表格：场景对照
add_textbox(slide, Inches(0.8), Inches(5.7), Inches(5), Inches(0.4),
            "查询场景对照表：", font_size=14, bold=True, color=C_DARK)

table_data = [
    ("输入示例", "匹配方式", "LLM?", "速度"),
    ("d4-1", "精确匹配（metadata）", "❌", "⚡毫秒"),
    ("急停告警", "语义搜索（名称）", "❌", "⚡毫秒"),
    ("外部急停信号闭合", "语义搜索（原因）", "❌", "⚡毫秒"),
    ("这个报错怎么回事", "LLM提取→语义搜索", "✅提取", "较快"),
    ("硬件", "语义搜索（关键词）", "❌", "⚡毫秒"),
]

for i, (a, b, c, d) in enumerate(table_data):
    y = Inches(6.1) + Inches(0.22) * i
    is_header = i == 0
    add_textbox(slide, Inches(1), y, Inches(2.5), Inches(0.25), a,
                font_size=10, bold=is_header, color=C_DARK if is_header else C_GRAY)
    add_textbox(slide, Inches(3.8), y, Inches(2.5), Inches(0.25), b,
                font_size=10, bold=is_header, color=C_DARK if is_header else C_GRAY)
    add_textbox(slide, Inches(6.8), y, Inches(1), Inches(0.25), c,
                font_size=10, bold=is_header, color=C_DARK if is_header else C_GRAY)
    add_textbox(slide, Inches(8.2), y, Inches(1), Inches(0.25), d,
                font_size=10, bold=is_header, color=C_DARK if is_header else C_GRAY)


# ============================================================
# SLIDE 6: 钉钉机器人工作流
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(slide, C_WHITE)

add_textbox(slide, Inches(0.8), Inches(0.3), Inches(8), Inches(0.5),
            "🤖 钉钉机器人工作流", font_size=26, bold=True, color=C_DARK)
add_shape(slide, Inches(0.8), Inches(0.85), Inches(2), Inches(0.03), fill_color=C_PRIMARY)

# Stream 模式对比
add_textbox(slide, Inches(0.8), Inches(1.2), Inches(5), Inches(0.4),
            "Stream 模式 vs 传统 Webhook 模式", font_size=14, bold=True, color=C_ACCENT)

# 左侧：传统模式
add_rect_with_text(slide, Inches(0.8), Inches(1.7), Inches(5), Inches(2.0),
                   "❌ 传统 Webhook 模式\n\n"
                   "• 需要公网 IP / 域名\n"
                   "• 需要防火墙白名单\n"
                   "• 需要 SSL 证书\n"
                   "• 钉钉 → 你的服务器（被动等待）",
                   C_LIGHT_BG, C_RED, 12, False)

# 右侧：Stream 模式
add_rect_with_text(slide, Inches(6.8), Inches(1.7), Inches(5.5), Inches(2.0),
                   "✅ Stream 模式（当前方案）\n\n"
                   "• 不需要公网 IP\n"
                   "• 不需要防火墙白名单\n"
                   "• 不需要 SSL 证书\n"
                   "• 我们 → 钉钉服务器（主动连接）",
                   C_LIGHT_BG, C_SECONDARY, 12, False)

# 消息处理流程
add_textbox(slide, Inches(0.8), Inches(4.0), Inches(5), Inches(0.4),
            "消息处理时序：", font_size=14, bold=True, color=C_DARK)

# 流程图
add_rect_with_text(slide, Inches(0.8), Inches(4.5), Inches(2.5), Inches(0.5),
                   "① 用户在钉钉群\n@恩特小助手", C_PRIMARY, C_WHITE, 11, True)
add_right_arrow(slide, Inches(3.4), Inches(4.65), Inches(0.3), Inches(0.2), C_GRAY)
add_rect_with_text(slide, Inches(3.8), Inches(4.5), Inches(2.5), Inches(0.5),
                   "② WebSocket\n推送到我们的服务", C_SECONDARY, C_WHITE, 11, True)
add_right_arrow(slide, Inches(6.4), Inches(4.65), Inches(0.3), Inches(0.2), C_GRAY)
add_rect_with_text(slide, Inches(6.8), Inches(4.5), Inches(2.5), Inches(0.5),
                   "③ process() 处理\n调用 error_query", C_ACCENT, C_WHITE, 11, True)

add_arrow_shape(slide, Inches(8), Inches(5.05), Inches(0.2), Inches(0.2), C_ACCENT)
add_rect_with_text(slide, Inches(6.8), Inches(5.4), Inches(2.5), Inches(0.5),
                   "④ reply_markdown()\n回复到群聊", C_PURPLE, C_WHITE, 11, True)

# 代码要点
add_rect_with_text(slide, Inches(0.8), Inches(6.2), Inches(11.5), Inches(0.9),
                   "📝 代码关键点\n\n"
                   "• ChatbotHandler.process() 是 async 方法 ← SDK 的 raw_process() 会 await 它\n"
                   "• reply_markdown() / reply_text() 是同步方法 ← 不要加 await！\n"
                   "• 处理结束后 return (AckMessage.STATUS_OK, \"ok\") ← 返回元组给 SDK\n"
                   "• session_webhook 临时 URL 用于回复当前消息（每个消息不同）",
                   RGBColor(0xE8, 0xF0, 0xFE), C_PRIMARY, 11, False)


# ============================================================
# SLIDE 7: 背后信息传输
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(slide, C_WHITE)

add_textbox(slide, Inches(0.8), Inches(0.3), Inches(8), Inches(0.5),
            "📡 信息传输流程", font_size=26, bold=True, color=C_DARK)
add_shape(slide, Inches(0.8), Inches(0.85), Inches(2), Inches(0.03), fill_color=C_PRIMARY)

# 时序图说明
add_textbox(slide, Inches(0.8), Inches(1.2), Inches(11), Inches(0.4),
            "从用户提问到获得回复的完整数据链路：", font_size=14, bold=True, color=C_DARK)

# 时序步骤
steps_data = [
    ("用户", "发送消息到群聊"),
    ("钉钉服务器", "通过 WebSocket 推送 CallbackMessage"),
    ("dingtalk_bot.py", "ChatbotHandler.process() 接收消息"),
    ("error_query.py", "handle() 执行查询"),
    ("", "├── 精确匹配 → Chroma metadata 过滤（本机内存）"),
    ("", "└── 语义搜索 → Chroma query（本机向量检索）"),
    ("", "    └── 自然语言 → DeepSeek API（仅提取关键词）"),
    ("error_query.py", "组装格式化结果 Markdown"),
    ("dingtalk_bot.py", "reply_markdown() 通过 session_webhook 回复"),
    ("钉钉服务器", "推送到群聊展示"),
    ("用户", "看到回复"),
]

for i, (actor, desc) in enumerate(steps_data):
    y = Inches(1.7) + Inches(0.46) * i
    # Actor 列
    if actor:
        color = C_PRIMARY if "用户" in actor else (C_SECONDARY if "钉钉" in actor else (C_ACCENT if "error" in actor else C_PURPLE))
        add_rect_with_text(slide, Inches(0.8), y, Inches(2), Inches(0.38),
                          actor, color, C_WHITE, 10, True)
    # 箭头列
    if i < len(steps_data) - 1 and steps_data[i][0] and steps_data[i+1][0]:
        pass  # skip decorative arrows for simplicity
    # 描述列
    desc_color = C_DARK if not desc.startswith(" ") else C_GRAY
    indent = 0 if not desc.startswith(" ") else 0.3
    add_textbox(slide, Inches(3.2) + Inches(indent), y, Inches(9), Inches(0.38),
               desc.strip(), font_size=11, color=desc_color)

# 底部标注
add_rect_with_text(slide, Inches(0.8), Inches(6.8), Inches(11.5), Inches(0.5),
                   "💡 关键优化：精确匹配完全不调用 DeepSeek API，既省钱又省时间。DeepSeek 仅在处理复杂自然语言时用于关键词提取（prompt 极小，消耗可忽略）。",
                   C_LIGHT_BG, C_DARK, 11, False)


# ============================================================
# SLIDE 8: 网络架构
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(slide, C_WHITE)

add_textbox(slide, Inches(0.8), Inches(0.3), Inches(8), Inches(0.5),
            "🌐 网络架构", font_size=26, bold=True, color=C_DARK)
add_shape(slide, Inches(0.8), Inches(0.85), Inches(2), Inches(0.03), fill_color=C_PRIMARY)

# 左侧：局域网
add_rect_with_text(slide, Inches(0.8), Inches(1.3), Inches(5.5), Inches(0.5),
                   "🏠 本地局域网", C_DARK, C_WHITE, 14, True)

add_rect_with_text(slide, Inches(1), Inches(2.1), Inches(2.5), Inches(0.6),
                   "💻 本机 (10.168.1.225)\nFastAPI 服务 :8000", C_PRIMARY, C_WHITE, 12, True)
add_textbox(slide, Inches(1.5), Inches(2.8), Inches(2), Inches(0.3),
            "localhost:8000", font_size=10, color=C_GRAY, alignment=PP_ALIGN.CENTER)

add_rect_with_text(slide, Inches(4.2), Inches(2.1), Inches(2), Inches(0.6),
                   "👥 同事电脑\n浏览器访问", C_SECONDARY, C_WHITE, 12, True)
add_textbox(slide, Inches(4.3), Inches(2.8), Inches(1.8), Inches(0.3),
            "http://10.168.1.225:8000", font_size=10, color=C_GRAY, alignment=PP_ALIGN.CENTER)

# 本机内部组件
add_rect_with_text(slide, Inches(1), Inches(3.4), Inches(2), Inches(0.5),
                   "📁 Chroma 向量库", C_PURPLE, C_WHITE, 11, True)
add_rect_with_text(slide, Inches(3.3), Inches(3.4), Inches(2), Inches(0.5),
                   "📊 Excel 数据源", C_GRAY, C_WHITE, 11, True)

# 右侧：外网
add_rect_with_text(slide, Inches(7.5), Inches(1.3), Inches(5), Inches(0.5),
                   "☁️ 外部服务", C_DARK, C_WHITE, 14, True)

add_rect_with_text(slide, Inches(7.7), Inches(2.1), Inches(2.2), Inches(0.6),
                   "🤖 钉钉服务器\nWebSocket 接入", RGBColor(0x00, 0x96, 0x88), C_WHITE, 12, True)
add_rect_with_text(slide, Inches(10.3), Inches(2.1), Inches(2), Inches(0.6),
                   "🧠 DeepSeek API\nHL 推理", C_ACCENT, C_WHITE, 12, True)
add_rect_with_text(slide, Inches(7.7), Inches(3.0), Inches(2.2), Inches(0.5),
                   "🤗 HuggingFace\n模型下载", C_PURPLE, C_WHITE, 11, True)

# 连接线说明
add_rect_with_text(slide, Inches(7.7), Inches(3.7), Inches(4.6), Inches(1.2),
                   "🔗 连接说明\n\n"
                   "• 钉钉：WebSocket 长连接（由我们发起）\n"
                   "• DeepSeek：HTTPS 请求（需 API Key）\n"
                   "• HuggingFace：首次下载模型（国内镜像 hf-mirror.com）\n"
                   "• 本机<->局域网：HTTP（需防火墙放行）",
                   C_LIGHT_BG, C_DARK, 11, False)

# 底部：防火墙
add_rect_with_text(slide, Inches(0.8), Inches(5.3), Inches(11.5), Inches(1.5),
                   "🛡️ 防火墙配置\n\n"
                   "• 入站规则：允许 TCP 端口 8000（用于局域网访问）\n"
                   "  命令：netsh advfirewall firewall add rule name=\"恩特小助手 8000\" dir=in action=allow protocol=TCP localport=8000\n\n"
                   "• 出站规则：允许 Python 进程访问 DeepSeek API（api.deepseek.com）和 HuggingFace 镜像\n\n"
                   "• 钉钉 Stream 模式：不需要配置任何防火墙规则（由我们主动发起 WebSocket 连接）",
                   C_LIGHT_BG, C_DARK, 11, False)


# ============================================================
# SLIDE 9: 技术栈一览
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(slide, C_WHITE)

add_textbox(slide, Inches(0.8), Inches(0.3), Inches(8), Inches(0.5),
            "📋 技术栈一览", font_size=26, bold=True, color=C_DARK)
add_shape(slide, Inches(0.8), Inches(0.85), Inches(2), Inches(0.03), fill_color=C_PRIMARY)

stacks = [
    ("🚀 Web 框架", "FastAPI", "Python 异步 Web 框架，提供 REST API + 静态页面"),
    ("💾 向量数据库", "Chroma", "本地持久化向量库，支持 metadata 过滤 + 语义搜索"),
    ("🧠 Embedding", "bge-small-zh-v1.5", "BAAI 出品，30MB，CPU 运行，中文语义理解"),
    ("🔮 LLM", "DeepSeek API", "deepseek-chat 模型，仅用于关键词提取"),
    ("🤖 钉钉 SDK", "dingtalk-stream", "Stream 模式，WebSocket 长连接，无需公网 IP"),
    ("📊 数据源", "OpenPyXL", "读取 .xlsx 文件，解析 PCS 参数表"),
    ("🌐 HTTP", "httpx", "调用 DeepSeek API 的 HTTP 客户端"),
    ("📄 文档处理", "python-pptx", "生成此 PPT 演示文稿"),
]

for i, (cat, name, desc) in enumerate(stacks):
    col = i // 4
    row = i % 4
    x = Inches(0.8) + Inches(6.2) * col
    y = Inches(1.3) + Inches(1.4) * row

    add_rect_with_text(slide, x, y, Inches(2.2), Inches(0.4),
                       cat, C_PRIMARY, C_WHITE, 11, True)
    add_textbox(slide, x + Inches(2.3), y, Inches(3.5), Inches(0.4),
                name, font_size=14, bold=True, color=C_DARK)
    add_textbox(slide, x + Inches(2.3), y + Inches(0.35), Inches(3.5), Inches(0.8),
                desc, font_size=10, color=C_GRAY)


# ============================================================
# SLIDE 10: 部署与使用
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(slide, C_WHITE)

add_textbox(slide, Inches(0.8), Inches(0.3), Inches(8), Inches(0.5),
            "🚀 部署与使用", font_size=26, bold=True, color=C_DARK)
add_shape(slide, Inches(0.8), Inches(0.85), Inches(2), Inches(0.03), fill_color=C_PRIMARY)

# 左侧：本地部署
add_textbox(slide, Inches(0.8), Inches(1.2), Inches(5), Inches(0.4),
            "📦 本地部署", font_size=16, bold=True, color=C_DARK)

steps = [
    ("1️⃣", "安装依赖", "pip install fastapi uvicorn chromadb\n  sentence-transformers openpyxl httpx dingtalk-stream"),
    ("2️⃣", "配置凭证", "编辑 local_config.py\n填 DeepSeek API Key + 钉钉 ClientID/Secret"),
    ("3️⃣", "同步数据", "python scripts/sync_kb.py\n（首次会自动下载 Embedding 模型）"),
    ("4️⃣", "启动服务", "python scripts/main.py\n→ http://localhost:8000"),
    ("5️⃣", "防火墙放行", "netsh advfirewall...\n（局域网其他设备访问需要）"),
]

for i, (num, title, desc) in enumerate(steps):
    y = Inches(1.7) + Inches(0.85) * i
    add_textbox(slide, Inches(0.8), y, Inches(0.4), Inches(0.3),
                num, font_size=18, bold=True, color=C_PRIMARY)
    add_textbox(slide, Inches(1.3), y, Inches(1.5), Inches(0.3),
                title, font_size=13, bold=True, color=C_DARK)
    add_textbox(slide, Inches(2.8), y, Inches(4), Inches(0.6),
                desc, font_size=10, color=C_GRAY)

# 右侧：云服务器
add_textbox(slide, Inches(7.5), Inches(1.2), Inches(5), Inches(0.4),
            "☁️ 云服务器部署（计划中）", font_size=16, bold=True, color=C_DARK)

add_rect_with_text(slide, Inches(7.5), Inches(1.8), Inches(5), Inches(4.5),
                   "待办步骤\n\n"
                   "1. 购买云服务器（2C2G 40GB 起步）\n"
                   "   阿里云/腾讯云/华为云 ≈ 30元/月\n\n"
                   "2. 安装 Python + Git\n\n"
                   "3. 上传代码 + knowledge_base/\n   + local_config.py\n\n"
                   "4. 安装依赖并启动\n\n"
                   "5. 配置 systemd/supervisor\n   开机自启 + 挂了自动重启\n\n"
                   "6. 7×24 小时在线\n   息屏/断网不影响",
                   C_LIGHT_BG, C_DARK, 12, False)


# ============================================================
# SLIDE 11: 结尾
# ============================================================
slide = prs.slides.add_slide(prs.slide_layouts[6])
add_bg(slide, C_BG_DARK)

add_textbox(slide, Inches(1), Inches(2), Inches(11), Inches(1),
            "🔧 恩特小助手", font_size=40, bold=True, color=C_WHITE, alignment=PP_ALIGN.CENTER)
add_textbox(slide, Inches(1), Inches(3.2), Inches(11), Inches(0.6),
            "PCS 故障代码智能查询系统", font_size=24, color=RGBColor(0xAA, 0xCC, 0xFF), alignment=PP_ALIGN.CENTER)

add_shape(slide, Inches(5), Inches(4.2), Inches(3), Inches(0.04), fill_color=C_ACCENT)

add_textbox(slide, Inches(2), Inches(4.7), Inches(9), Inches(0.5),
            "数据驱动 · 智能查询 · 即时响应",
            font_size=18, color=RGBColor(0x88, 0x88, 0xAA), alignment=PP_ALIGN.CENTER)

add_textbox(slide, Inches(2), Inches(5.5), Inches(9), Inches(0.4),
            "恩特能源 · 内部工具",
            font_size=14, color=RGBColor(0x66, 0x66, 0x88), alignment=PP_ALIGN.CENTER)

# 底部装饰条
add_shape(slide, Inches(0), Inches(7.3), W, Inches(0.2), fill_color=C_PRIMARY)


# ===== 保存 =====
output_path = "d:/ENTAR_AGENT/恩特小助手-PCS故障查询系统架构.pptx"
prs.save(output_path)
print(f"✅ PPT 已生成：{output_path}")

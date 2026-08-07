"""
Word 文档提取器

使用 python-docx 提取 .docx 文件的文本内容，
输出 Markdown 格式文本，以便复用 MarkdownChunker 进行切块。

转换规则：
  - Heading 1~6 → # ~ ######
  - 普通段落 → 原文
  - 表格 → Markdown 表格（| col1 | col2 |）
  - 列表 → - 条目（无序）/ 1. 条目（有序）
"""

import logging
from typing import Optional

logger = logging.getLogger("doc_mgr.extractors.word")


def extract_docx_text(filepath: str) -> str:
    """提取 Word 文档文本，输出 Markdown 格式

    Args:
        filepath: .docx 文件路径

    Returns:
        Markdown 格式文本；提取失败返回空字符串
    """
    try:
        from docx import Document
        from docx.oxml.ns import qn
    except ImportError:
        logger.error("python-docx 未安装，请运行 pip install python-docx")
        return ""

    try:
        doc = Document(filepath)
    except Exception as e:
        logger.error(f"无法打开 Word 文件 {filepath}: {e}")
        return ""

    parts: list[str] = []

    # 按文档顺序遍历 body 子元素（段落和表格交错出现）
    for element in doc.element.body:
        tag = element.tag

        # ---- 段落 ----
        if tag == qn("w:p"):
            para = _find_paragraph(doc, element)
            if para is None:
                continue
            text = para.text.strip()
            if not text:
                continue

            style_name = para.style.name if para.style else ""

            # 标题
            if style_name.startswith("Heading"):
                level = _parse_heading_level(style_name)
                if level:
                    parts.append(f"{'#' * level} {text}")
                    continue

            # 列表
            numPr = para._element.find(qn("w:pPr"))
            if numPr is not None:
                numPr = numPr.find(qn("w:numPr"))
            if numPr is not None:
                # 有编号属性 → 列表项
                parts.append(f"- {text}")
                continue

            # 普通段落
            parts.append(text)

        # ---- 表格 ----
        elif tag == qn("w:tbl"):
            table = _find_table(doc, element)
            if table is None:
                continue
            md_table = _table_to_markdown(table)
            if md_table:
                parts.append(md_table)

    result = "\n\n".join(parts)
    if not result.strip():
        logger.warning(f"Word 文件未提取到文本: {filepath}")
    return result


def _find_paragraph(doc, element):
    """从 Document 对象中查找与 XML element 对应的 Paragraph"""
    for para in doc.paragraphs:
        if para._element is element:
            return para
    return None


def _find_table(doc, element):
    """从 Document 对象中查找与 XML element 对应的 Table"""
    for table in doc.tables:
        if table._element is element:
            return table
    return None


def _parse_heading_level(style_name: str) -> Optional[int]:
    """从样式名解析标题层级

    python-docx 标题样式名格式：'Heading 1', 'Heading 2', ..., 'Heading 9'
    中文 Word 可能是 '标题 1', '标题 2', ...
    """
    for prefix in ("Heading ", "标题 "):
        if style_name.startswith(prefix):
            try:
                level = int(style_name[len(prefix):])
                return min(level, 6)  # Markdown 最多 6 级
            except ValueError:
                pass
    return None


def _table_to_markdown(table) -> str:
    """将 Word 表格转为 Markdown 表格格式

    输出示例：
    | 列1 | 列2 | 列3 |
    | --- | --- | --- |
    | 数据1 | 数据2 | 数据3 |
    """
    rows = table.rows
    if not rows:
        return ""

    # 提取所有单元格文本
    grid: list[list[str]] = []
    max_cols = 0
    for row in rows:
        cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
        max_cols = max(max_cols, len(cells))
        grid.append(cells)

    if max_cols == 0:
        return ""

    # 补齐列数
    for row_cells in grid:
        while len(row_cells) < max_cols:
            row_cells.append("")

    lines: list[str] = []

    # 表头（第一行）
    header = grid[0]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * max_cols) + " |")

    # 数据行
    for row_cells in grid[1:]:
        lines.append("| " + " | ".join(row_cells) + " |")

    return "\n".join(lines)

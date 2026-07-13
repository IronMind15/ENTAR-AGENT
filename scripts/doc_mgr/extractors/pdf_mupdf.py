"""
PDF 文本提取器

用 PyMuPDF 从 PDF 中提取文字，并识别标准编号和标准名称。
对于扫描型 PDF（文字提取不到）会返回空，留待后续 OCR 处理。

detect_standard_id / detect_standard_title 函数从 sync_standards.py 迁移。
"""

import os
import re
import logging
from typing import Optional

logger = logging.getLogger("doc_mgr.extractors.pdf")

# ===== 页眉页脚清理模式 =====
PAGE_NUM_PATTERN = re.compile(r'^\s*\d+\s*$')

FOOTER_PATTERNS = [
    re.compile(r'^\s*GB/T\s+\d+[—\-–][\dX]+\s*$'),
    re.compile(r'^\s*IEC\s+\d+[—\-–].*$'),
    re.compile(r'^\s*EN\s+\d+.*$'),
    re.compile(r'^\s*第\s*\d+\s*页[，,]\s*共\s*\d+\s*页\s*$'),
]

# ===== 标准编号检测模式 =====
STD_ID_PATTERNS = [
    r'(?:^|[^代替\d])CNCA/CTS\s*\d+\s*[—\-–:]\s*\d{4}',
    r'(?:^|[^代替\d])CQC\s+\d{3,}\s*[—\-–:]\s*\d{4}',
    r'(?:^|[^代替\d])GB/T\s+\d{3,}(?:\.\d+)?\s*[—\-–]\s*\d{4}\s*[Xx*]?',
    r'(?:^|[^代替\d])GB\s+\d{3,}(?:\.\d+)?\s*[—\-–]\s*\d{4}\s*[Xx*]?',
    r'(?:^|[^代替\d])IEC\s+\d{4,}(?:-\d+)?\s*[—\-–:]\s*\d{4}',
    r'(?:^|[^代替\d])IEC(?:/CEI)?\s+\d{4,}(?:-\d+)?',
    r'(?:^|[^代替\d])BS\s+EN\s+\d{4,}\s*[—\-–:]\s*\d{4}',
    r'(?:^|[^代替\d])EN\s+\d{4,}\s*[—\-–:]\s*\d{4}',
    r'(?:^|[^代替\d])EN\s+\d{4,}',
]

# 文件名 → 标准编号映射
NAME_TO_STD_ID = {
    'I60664-1E2': 'IEC 60664-1',
    'I60664': 'IEC 60664-1',
    'EN 62109-1': 'EN 62109-1',
    'EN50178': 'EN 50178',
    'EN50438': 'EN 50438',
    'GBT16935': 'GB/T 16935.1-2008',
    'GB_T_34133': 'GB/T 34133-2023',
    'GB_T 34120': 'GB/T 34120-2023',
    '62109': 'CNCA/CTS 0022-2013',
}

# 文件名 → 标准名称映射
NAME_TO_TITLE = {
    '62109': '光伏发电系统用储能变流器 技术规范',
    'CQC': '光伏发电系统用储能变流器 技术规范',
    'EN 62109-1': 'Safety of power converters for use in photovoltaic power systems',
    'EN50178': 'Electronic equipment for use in power installations',
    'EN50438': 'Requirements for the connection of micro-generators',
    'GBT16935': '绝缘配合 第1部分：低压系统内设备的绝缘配合',
    'GB_T_34133': '储能变流器检测技术规程',
    'I60664': 'Insulation coordination for equipment within low-voltage systems',
    'GB_T 34120': '电化学储能系统储能变流器技术要求',
}


def clean_page_text(text: str) -> str:
    """清理单页文本（去掉页码、标准号页脚等）"""
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if PAGE_NUM_PATTERN.match(stripped):
            continue
        if any(p.match(stripped) for p in FOOTER_PATTERNS):
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)


def extract_pdf_text(filepath: str) -> tuple[str, str, str]:
    """从 PDF 提取文字，识别标准编号和名称

    Args:
        filepath: PDF 文件路径

    Returns:
        (full_text, std_id, std_title)
        文字提取失败时返回 ("", "", "")
    """
    import fitz  # PyMuPDF

    try:
        doc = fitz.open(filepath)
    except Exception as e:
        logger.error(f"无法打开 PDF: {e}")
        return "", "", ""

    pages = []
    total_chars = 0
    for i in range(doc.page_count):
        try:
            page_text = doc[i].get_text()
        except Exception:
            page_text = ""
        cleaned = clean_page_text(page_text)
        pages.append(cleaned)
        total_chars += len(cleaned)

    doc.close()

    if total_chars < 100:
        logger.warning(f"文字不足 ({total_chars}字符)，判定为扫描型 PDF")
        return "", "", ""

    full_text = '\n'.join(pages)
    file_name = os.path.basename(filepath)
    std_id = detect_standard_id(full_text, file_name)
    std_title = detect_standard_title(full_text, file_name)

    logger.info(f"PDF 提取完成: {total_chars} 字符 — {std_id} {std_title}")
    return full_text, std_id, std_title


def detect_standard_id(text: str, file_name: str) -> str:
    """从 PDF 内容中识别标准编号

    优先级：文件名推断 > 封面区域模式匹配 > 全文搜索
    """
    # 第1优先：文件名推断
    for key, std_id in NAME_TO_STD_ID.items():
        if key in file_name:
            return std_id

    # 第2优先：封面区域（前 1500 字符）
    cover_text = text[:1500]
    cover_flat = re.sub(r'\n+', ' ', cover_text)

    for p in STD_ID_PATTERNS:
        m = re.search(p, cover_flat)
        if m:
            raw = m.group(0)
            raw = re.sub(r'[—–]', '-', raw)
            raw = re.sub(r'\s+', ' ', raw).strip()
            raw = re.sub(r'^[^a-zA-Z\d]', '', raw)
            return raw

    # 第3优先：全文搜索
    full_flat = re.sub(r'\n+', ' ', text[:8000])
    for p in STD_ID_PATTERNS:
        m = re.search(p, full_flat)
        if m:
            raw = m.group(0)
            raw = re.sub(r'[—–]', '-', raw)
            raw = re.sub(r'\s+', ' ', raw).strip()
            raw = re.sub(r'^[^a-zA-Z\d]', '', raw)
            return raw

    return file_name.replace('.pdf', '').replace('_', ' ')


def detect_standard_title(text: str, file_name: str) -> str:
    """从 PDF 内容中识别标准名称"""
    # 文件名映射优先
    for key, title in NAME_TO_TITLE.items():
        if key in file_name:
            return title

    # 从封面页中找标题行
    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if line and 6 < len(line) < 70:
            if any(kw in line for kw in ['标准', '规范', '规程', '技术', '要求',
                                          'Testing', 'Specification', 'Standard',
                                          'Insulation', 'Safety', 'Requirements',
                                          'Electronic equipment']):
                if not re.match(r'^[\dIVXLCDM\s./\-—–]+$', line):
                    return line

    # 后备
    for line in lines:
        line = line.strip()
        if len(line) > 10 and not re.match(r'^[\d\s./\-—–]+$', line):
            return line[:100]

    return file_name.replace('.pdf', '').replace('_', ' ')

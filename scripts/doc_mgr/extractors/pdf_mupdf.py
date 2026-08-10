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


# ==================== PDF 类型检测路由（v1.10.3）====================
# 单页文字 ≥ 该字符数才判定为「文字页」（过滤页码/页脚/水印等零散文字）。
# 取 50：真实文字页动辄数千字符，封面/图表页也有 50+ 字符；水印级零散文字（页码/机密戳）
# 通常 < 50，且 validate_local_text 兜底会拦截「水印凑字」的伪文字版。
PDF_TEXT_PAGE_MIN_CHARS = 50
# 文字页覆盖率 ≥ 95% → 文字版（本地提取免费高保真，省 MinerU 额度）
PDF_TEXT_COVERAGE = 0.95
# 文字页覆盖率 ≤ 5% → 扫描版（无文字层，必须 MinerU VLM 识别）
PDF_SCAN_COVERAGE = 0.05


def classify_pdf_type(filepath: str) -> tuple[str, dict]:
    """检测 PDF 类型：text（文字版）/ scanned（扫描版）/ mixed（混合版）

    逐页 get_text() → clean_page_text → 统计每页字符数，
    每页字符 ≥ PDF_TEXT_PAGE_MIN_CHARS 判定为「文字页」，
    coverage = 文字页数 / 总页数：
      - coverage ≥ 0.95 → "text"    （纯文字版 → 本地 PyMuPDF 免费高保真提取）
      - coverage ≤ 0.05 → "scanned" （纯扫描版 → MinerU VLM 识别）
      - 其他            → "mixed"   （混合 → 保守走 MinerU，避免局部质量下降）

    Args:
        filepath: PDF 文件路径

    Returns:
        (pdf_type, 摘要 dict：pages/text_pages/total_chars/coverage)
    """
    import fitz

    try:
        doc = fitz.open(filepath)
    except Exception as e:
        logger.error(f"无法打开 PDF 检测类型: {e}")
        return "scanned", {"pages": 0, "text_pages": 0, "total_chars": 0, "coverage": 0.0}

    total_pages = doc.page_count
    text_pages = 0
    total_chars = 0
    for i in range(total_pages):
        try:
            page_text = doc[i].get_text()
        except Exception:
            page_text = ""
        cleaned = clean_page_text(page_text)
        chars = len(''.join(cleaned.split()))
        total_chars += chars
        if chars >= PDF_TEXT_PAGE_MIN_CHARS:
            text_pages += 1

    doc.close()

    coverage = text_pages / total_pages if total_pages else 0.0
    if coverage >= PDF_TEXT_COVERAGE:
        pdf_type = "text"
    elif coverage <= PDF_SCAN_COVERAGE:
        pdf_type = "scanned"
    else:
        pdf_type = "mixed"

    summary = {
        "pages": total_pages,
        "text_pages": text_pages,
        "total_chars": total_chars,
        "coverage": round(coverage, 4),
    }
    logger.info(
        f"PDF 类型检测: {pdf_type} "
        f"({text_pages}/{total_pages} 页有文字, 覆盖率 {coverage:.1%}, {total_chars} 字符)"
    )
    return pdf_type, summary


def validate_local_text(full_text: str, expected_chars: int) -> bool:
    """校验本地 PyMuPDF 提取质量，防止「伪文字层」导致的质量下降

    两种异常判定为失败（引擎回退 MinerU）：
      1. 字符量骤减：提取字符 < expected_chars * 0.5
         （两者都基于 get_text()，正常应接近；骤减说明文字层损坏/水印凑字）
      2. 乱码率过高：可读字符（CJK/字母数字/常见标点）占比 < 60%
         （替换符 U+FFFD / 控制字符 / 编码损坏的典型特征）

    Args:
        full_text: 本地提取的全文
        expected_chars: classify_pdf_type 返回的 total_chars

    Returns:
        True=质量合格；False=质量可疑应回退 MinerU
    """
    text = full_text or ""
    actual = len(''.join(text.split()))

    # 1. 字符量骤减检查
    if expected_chars > 0 and actual < expected_chars * 0.5:
        logger.warning(
            f"本地提取字符量骤减（期望 ~{expected_chars}，实际 {actual}），疑似伪文字层"
        )
        return False

    # 2. 乱码率检查（文本非空时才有意义）
    if actual > 0:
        readable = 0
        for ch in text:
            if ch.isspace():
                continue
            if ('一' <= ch <= '鿿'          # CJK 汉字
                    or 'a' <= ch.lower() <= 'z'      # ASCII 字母
                    or '0' <= ch <= '9'              # 数字
                    or ch in '.,;:!?()[]{}<>"\'/\\|_-+=*&%$#@^~`、。，；：？！（）《》【】'):
                readable += 1
        ratio = readable / max(actual, 1)
        if ratio < 0.6:
            logger.warning(f"本地提取乱码率过高（可读字符占比 {ratio:.1%}），疑似编码损坏")
            return False

    return True

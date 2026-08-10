"""
CSV 文件提取器

使用 stdlib csv 模块提取 .csv 文件的结构化数据，
输出格式与 Excel 提取器对齐：list[dict] + format 函数。
每行数据直接作为一个 Chroma 文档入库，不经过切块。

编码策略：utf-8-sig → gbk → latin-1 回退（兼容中文 Windows 导出）。
"""

import csv
import logging
from typing import Optional

logger = logging.getLogger("doc_mgr.extractors.csv")

# 编码尝试顺序
_ENCODINGS = ("utf-8-sig", "gbk", "gb18030", "latin-1")


def extract_csv_rows(
    filepath: str,
    delimiter: Optional[str] = None,
) -> list[dict]:
    """提取 CSV 文件行数据

    Args:
        filepath: CSV 文件路径
        delimiter: 分隔符，None 则自动检测（逗号/制表符/分号）

    Returns:
        list[dict]，每条含 _row_num（行号）+ 列名作为 key。
        提取失败返回空列表。
    """
    # 尝试不同编码
    raw_text = None
    used_encoding = None
    for enc in _ENCODINGS:
        try:
            with open(filepath, "r", encoding=enc) as f:
                raw_text = f.read()
            used_encoding = enc
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
        except OSError as e:
            logger.error(f"无法打开 CSV 文件 {filepath}: {e}")
            return []

    if raw_text is None:
        logger.error(f"CSV 文件编码无法识别: {filepath}")
        return []

    # 自动检测分隔符
    if delimiter is None:
        delimiter = _detect_delimiter(raw_text)

    # 解析 CSV
    reader = csv.DictReader(
        raw_text.splitlines(),
        delimiter=delimiter,
    )

    if not reader.fieldnames:
        logger.warning(f"CSV 文件无表头: {filepath}")
        return []

    records: list[dict] = []
    for row_num, row in enumerate(reader, start=2):  # 第 1 行是表头，数据从第 2 行开始
        try:
            # 跳过全空行（DictReader 对缺失字段补 None，需容错）
            if all(not (v or "").strip() for v in row.values()):
                continue
            row["_row_num"] = row_num
            row["_encoding"] = used_encoding
            records.append(row)
        except (AttributeError, ValueError, TypeError) as e:
            logger.warning(f"CSV 第 {row_num} 行解析跳过（不规则行）: {e}")
            continue

    if not records:
        logger.warning(f"CSV 文件未提取到数据行: {filepath}")

    return records


def format_csv_row(row: dict, headers: Optional[list[str]] = None) -> str:
    """将一行 CSV 数据格式化为可搜索文本

    输出格式：列名：值 | 列名：值 | ...
    与 Excel 的 format_excel_row 风格保持一致。

    Args:
        row: 一行数据 dict
        headers: 指定输出列顺序；None 则使用 row 中所有非内部字段

    Returns:
        格式化文本
    """
    parts: list[str] = []

    if headers is None:
        headers = [k for k in row.keys() if not k.startswith("_")]

    for key in headers:
        value = row.get(key, "")
        if value is None:
            value = ""
        value = str(value).strip()
        if value:
            parts.append(f"{key}：{value}")

    return " | ".join(parts)


def _detect_delimiter(text: str) -> str:
    """通过第一行自动检测分隔符"""
    first_line = text.split("\n")[0] if text else ""
    candidates = [",", "\t", ";", "|"]
    best = ","
    best_count = 0
    for d in candidates:
        count = first_line.count(d)
        if count > best_count:
            best_count = count
            best = d
    return best

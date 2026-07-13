"""
PyMuPDF 结构分析切块器

核心思路：利用 PyMuPDF 提取的文字位置/字体信息，
结合中文标准的章节号模式，替代 Unstructured 做 heading 检测。

对比 Unstructured：
  - 零额外依赖（PyMuPDF 已安装）
  - 对中文标准更可靠（Unstructured 的英文训练模型对中文标准识别差）
  - 轻量级，无 OCR 全家桶

Heading 检测策略（多信号融合）：
  1. 章节号模式 — 行首匹配 "X" / "X.X" / "第X章" / "附录A" 等
  2. 字体信号 — SimHei（黑体）/ TimesNewRomanBold 等粗体/标题字体
  3. 位置信号 — 左边界对齐（非缩进段落）
  4. 短文本信号 — 标题一般不长于 80 字符
"""

import logging
import re
from typing import Optional

from ..models import Chunk
from .base import Chunker

logger = logging.getLogger("doc_mgr.chunkers.pymupdf")

# 中文标准标题字体列表（SimHei 黑体、Microsoft YaHei 雅黑、FangSong 仿宋等）
HEADING_FONTS = {"simhei", "simsun", "microsoftyahei", "fangsong",
                 "timesnewromanps-boldmt", "timesnewromanbold",
                 "helvetica-bold", "arial-bold"}
# 注: SimSun 既有正文也有标题，不能用字体名单独判定
# 这里不用于判断，仅用于参考

# 正文常用字体（纯正文时使用）
BODY_FONTS = {"simsun", "timesnewromanpsmt", "timesnewroman",
              "helvetica", "arial", "songti"}

# 非正文类的跳过字体
SKIP_FONTS = {}


class PdfChunker(Chunker):
    """基于 PyMuPDF 的 PDF 文档智能切块器"""

    def __init__(self, min_chunk_chars: int = 50, max_chunk_chars: int = 3000):
        self.min_chunk_chars = min_chunk_chars
        self.max_chunk_chars = max_chunk_chars

    def chunk(self, text: str, metadata: Optional[dict] = None,
              filepath: str = "") -> list[Chunk]:
        """解析 PDF 并按章节切块

        Args:
            text: PyMuPDF 提取的全文（备用）
            metadata: 基础 metadata
            filepath: PDF 文件路径（用于结构分析）

        Returns:
            Chunk 列表
        """
        meta = metadata or {}
        lines = self._extract_lines(filepath)

        if not lines:
            logger.warning("PyMuPDF 结构分析无结果，回退全文单块")
            return [Chunk(text=text[:3000], metadata=meta, chapter="全文")]

        # 清理：移除页眉页脚页码
        cleaned = self._clean_lines(lines)
        if not cleaned:
            return [Chunk(text=text[:3000], metadata=meta, chapter="全文")]

        # 检测标题行
        sections = self._detect_sections(cleaned)
        logger.info(f"检测到 {len(sections)} 个章节/区域")

        # 构建 Chunk
        chunks = self._build_chunks(sections, meta)

        # 后处理
        chunks = self.merge_small_chunks(chunks, min_chars=self.min_chunk_chars)
        chunks = self.split_oversized_chunks(chunks, max_chars=self.max_chunk_chars)

        logger.info(f"PyMuPDF 切块完成: {len(chunks)} 块")
        return chunks

    # ==================== 行提取 ====================

    def _extract_lines(self, filepath: str) -> list[dict]:
        """用 PyMuPDF 按行提取文本、字体、位置信息"""
        try:
            import fitz
        except ImportError:
            logger.error("PyMuPDF (fitz) 未安装")
            return []

        try:
            doc = fitz.open(filepath)
        except Exception as e:
            logger.error(f"PyMuPDF 打开失败: {e}")
            return []

        lines = []
        for page_num in range(len(doc)):
            page = doc[page_num]
            try:
                blocks = page.get_text("dict")["blocks"]
            except Exception:
                continue

            for block in blocks:
                if block["type"] != 0:  # 不是文本块
                    continue

                for line in block["lines"]:
                    spans = line["spans"]
                    if not spans:
                        continue

                    # 合并 span 文本（一行可能有多个 span 分段）
                    full_text = "".join(s["text"] for s in spans)
                    full_text = full_text.strip()
                    if not full_text:
                        continue

                    # 用第一个 span 的字体信息代表整行
                    first = spans[0]
                    lines.append({
                        "text": full_text,
                        "font": first["font"],
                        "size": round(first["size"], 1),
                        "bold": bool(first["flags"] & 2),
                        "page": page_num + 1,
                        "bbox": line["bbox"],
                        "x": line["bbox"][0],  # X 坐标（左边界）
                        "y": line["bbox"][1],  # Y 坐标（上边界）
                    })

        doc.close()
        return lines

    # ==================== 清理 ====================

    @staticmethod
    def _clean_lines(lines: list[dict]) -> list[dict]:
        """移除页眉/页脚/页码/目录等干扰行

        策略：
          1. 标准 ID 短行（< 40 字符）→ 无条件清除（页眉页脚标识）
          2. y < 80 区域 → 页面顶部导航区，清除短行
          3. 页码行 → 清除
          4. 目录占位符 → 清除
        """
        cleaned = []
        # 标准编号模式（用于识别页眉页脚）
        std_pattern = re.compile(
            r'^(CNCA|GB/T|GB |IEC|EN|ISO|CQC|NB/T|DL/T|JB/T|YD/T|SJ/T|QB/T)',
        )

        for line in lines:
            text = line["text"].strip()
            y = line["y"]
            length = len(text)

            # 1. 标准 ID 短行 → 页眉页脚（如 "CNCA/CTS0022-2013", "CQC3310—2014"）
            if std_pattern.match(text) and length < 40:
                continue

            # 2. 页眉区域（y < 80）→ 清除短行（< 60 字符）
            if y < 80 and length < 60:
                continue

            # 3. 纯数字页码
            if re.match(r'^\d{1,3}$', text):
                continue

            # 4. 目录占位行（"......"）
            if text.startswith(".") and len(text) > 10:
                continue

            cleaned.append(line)

        return cleaned

    # ==================== 标题检测 ====================

    # 章节号模式：数字段落起始
    CHAPTER_NUM = re.compile(r'^(\d{1,2}(?:\.\d+)*)\s')
    # 中文 "第X章"
    ZH_CHAPTER = re.compile(r'^第\s*([一二三四五六七八九十百零\d]+)\s*章')
    # 附录
    APPENDIX_CN = re.compile(r'^附录\s*([A-Z一二三四五])')
    APPENDIX_EN = re.compile(r'^(Annex|Appendix)\s+([A-Z])', re.IGNORECASE)

    # 标题文字应包含中文或英文单词（不是纯数值/单位）
    HAS_TITLE_TEXT = re.compile(r'[一-鿿]|[A-Z][a-z]{2,}')

    def _is_heading(self, line: dict) -> bool:
        """判断一行是否可能是章节标题（多信号融合）

        信号优先级：章节号模式 > 标题字体 > 左边界
        """
        text = line["text"].strip()
        font = line["font"].lower()
        length = len(text)

        # 太长的行不可能是标题
        if length > 80:
            return False
        if length < 2:
            return False

        # === 信号1：章节号模式（强信号）===
        #   "7.12 切换时间" ✓  |  "1 500"（纯数值）✗
        m = self.CHAPTER_NUM.match(text)
        if m:
            after = text[m.end():].strip()
            # 章节号后必须有描述性文字（中文或英文单词），不是纯数值
            if self.HAS_TITLE_TEXT.search(after):
                return True
            # 纯数值/单位行 → 不是标题
            return False

        if self.ZH_CHAPTER.match(text):
            return True
        if self.APPENDIX_CN.match(text) or self.APPENDIX_EN.match(text):
            return True

        # === 信号2：标题字体 ===
        # SimHei（黑体）、粗体、微软雅黑 → 通常是章节标题
        is_heading_font = (
            "simhei" in font or
            "bold" in font or
            "yahei" in font
        )

        if not is_heading_font:
            return False

        # 以下仅对 SimHei/粗体 文本有效

        # 排除注/说明/例等非标题段落
        if text.startswith(("注：", "注意：", "说明：", "例")):
            return False

        # 排除以标点起始的句段碎片
        if text[0] in "，。、；：）】」？！)]":
            return False

        # 极短行（< 5 字）：只有纯中文且不以标点结尾才算标题
        if length < 5:
            if re.search(r'[，。、；：？！\.,;:!?]$', text):
                return False
            if not re.search(r'[一-鿿]', text):
                return False

        # 以句号/感叹号/问号结尾 → 完整句子，不是标题
        if re.search(r'[。！？]$', text):
            return False

        # 包含句中句号 → 多句拼接的正文碎片，不是标题
        if re.search(r'。', text):
            return False

        # 全英文行（无中文）且长度 > 20 → 术语翻译，不是章节标题
        if not re.search(r'[一-鿿]', text) and length > 20:
            return False

        # SimHei + 短文本 → 很可能是标题（页眉已在 _clean_lines 中移除）
        return True

        return False

    # ==================== 章节检测 ====================

    def _detect_sections(self, lines: list[dict]) -> list[dict]:
        """检测章节边界，返回章节列表"""
        sections = []
        current = {"heading": "", "content": [], "heading_font": "",
                    "heading_size": 0, "heading_page": 0}

        for line in lines:
            text = line["text"]
            if self._is_heading(line):
                # 保存上一节
                if current["content"] and current["heading"]:
                    sections.append(current)

                # 新一节
                current = {
                    "heading": text,
                    "content": [],
                    "heading_font": line["font"],
                    "heading_size": line["size"],
                    "heading_page": line["page"],
                }
            else:
                current["content"].append(text)

        # 最后一节
        if current["content"] and current["heading"]:
            sections.append(current)

        # 如果整个文档没有检测到任何标题（_is_heading 全部返回 False）
        if not sections:
            # 尝试每行第一个句号前的词作为伪标题
            lines_text = [l["text"] for l in lines]
            logger.warning("未检测到章节标题，使用全文单块")
            sections.append({
                "heading": "全文",
                "content": lines_text,
                "heading_font": "",
                "heading_size": 0,
                "heading_page": 0,
            })

        return sections

    # ==================== Chunk 构建 ====================

    @staticmethod
    def _extract_chapter_number(text: str) -> str:
        """从标题文本中提取章节号（同原 UnstructuredChunker 的逻辑）"""
        m = re.match(r'第\s*([一二三四五六七八九十百零\d]+)\s*章', text)
        if m:
            cn = m.group(1)
            cn_map = {"一": "1", "二": "2", "三": "3", "四": "4", "五": "5",
                      "六": "6", "七": "7", "八": "8", "九": "9", "十": "10",
                      "零": "0"}
            if cn.isdigit():
                return cn
            return "".join(cn_map.get(c, c) for c in cn)

        m = re.match(r'(\d+(?:\.\d+)*)\s', text)
        if m:
            return m.group(1)

        m = re.match(r'附录\s*([A-Z])', text)
        if m:
            return f"附录{m.group(1)}"

        m = re.match(r'(Annex|Appendix)\s+([A-Z])', text, re.IGNORECASE)
        if m:
            return f"{m.group(1)} {m.group(2)}"

        return text[:20]

    def _build_chunks(self, sections: list[dict], base_meta: dict) -> list[Chunk]:
        """从章节列表构建 Chunk 对象"""
        chunks = []
        for idx, sec in enumerate(sections):
            text = "\n\n".join(sec["content"])
            heading = sec["heading"]

            meta = dict(base_meta)
            meta["chapter_title"] = heading
            meta["heading_font"] = sec["heading_font"]
            meta["heading_size"] = sec["heading_size"]
            meta["heading_page"] = sec["heading_page"]

            chapter = self._extract_chapter_number(heading)
            if chapter:
                meta["chapter"] = chapter

            chunks.append(Chunk(
                text=text,
                metadata=meta,
                chunk_index=idx,
                chapter=meta.get("chapter", ""),
                chapter_title=heading,
            ))

        return chunks

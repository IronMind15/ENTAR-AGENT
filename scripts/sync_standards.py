"""
同步脚本：标准 PDF → Chroma 向量库（standards collection）

处理策略：
  1. 对 data/standards/ 下每份 PDF 尝试 PyMuPDF 提取文字
  2. 提取到文字（>100字符）→ 按章节切块 → 写入 Chroma
  3. 提取不到（纯扫描 PDF）→ 跳过，日志记录，留待后续 OCR 处理

回退机制：
  - 扫描型 PDF 跳过不影响已入库的文本 PDF
  - 可随时重新运行：已入库的不重复添加（增量模式）
  - 用户可用外部工具 OCR 扫描 PDF → 保存为文本 → 运行补充脚本
"""

import logging
import os
import re
import sys

# 统一日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sync_standards")

if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

import fitz  # PyMuPDF
import chromadb
from chromadb import PersistentClient
from chromadb.utils import embedding_functions

os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# ===== 路径 =====
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
STD_DIR = os.path.join(DATA_DIR, "standards")
CHROMA_DIR = os.path.join(_PROJECT_ROOT, "knowledge_base")
COLLECTION_NAME = "standards"

# ===== 切块常量 =====
# 中文章节标题模式（支持多级章节）
CHAPTER_PATTERN_CN = re.compile(
    r'^(\d+)\s+(范围|规范性引用文件|术语和定义|基本规定|'
    r'检测条件|检测装置|外观检查|通信功能检查|保护功能检测|'
    r'电气性能检测|安全性能检测|环境适应性检测|电磁兼容检测|'
    r'电磁兼容性检测|标识[\s、]?包装检测|'
    r'标志[\s、]?包装[\s、]?[一-鿿]*|'
    r'产品类型|使用[\s、]、?安装及运输条件|'
    r'产品标识和资料|结构和性能要求'
    r')',
    re.MULTILINE,
)

# 通用章节标题模式——只匹配一级章节（纯数字，最多两位，不含点号）
CHAPTER_PATTERN_GENERIC = re.compile(
    r'^(\d{1,2})\s+(\S[^\n]{2,80})$',
    re.MULTILINE,
)

# 附录模式
APPENDIX_PATTERN_CN = re.compile(r'^附录\s+([A-Z])\s', re.MULTILINE)
APPENDIX_PATTERN_EN = re.compile(r'^(Annex|Appendix)\s+([A-Z])\s', re.MULTILINE)

# 纯数字页码行
PAGE_NUM_PATTERN = re.compile(r'^\s*\d+\s*$')

# 页眉页脚清理（常见的）
FOOTER_PATTERNS = [
    re.compile(r'^\s*GB/T\s+\d+[—\-]\d+X*\s*$'),   # GB/T 34133-XXXX
    re.compile(r'^\s*IEC\s+\d+[—\-].*$'),           # IEC 60664-1 ...
    re.compile(r'^\s*EN\s+\d+.*$'),                  # EN 50178 ...
    re.compile(r'^\s*第\s*\d+\s*页[，,]\s*共\s*\d+\s*页\s*$'),  # 第 X 页，共 X 页
]

# 需要扫描处理的 PDF 文件列表（回退登记）
SCANNED_PDFS = []  # 同步过程中填充


def detect_standard_id(text: str, file_name: str) -> str:
    """从 PDF 内容中识别标准编号

    优先级：文件名推断（最可靠） > 封面区域模式匹配 > 全文中搜索
    只在前 1500 字符中查找封面区域，避免命中正文中的引用文献。

    注意：部分 PDF 封面页标准号含换行（如 IEC/EN 编号跨行），
    需要先用简单替换将换行转空格，再做模式匹配。
    """
    # ===== 第1优先：文件名推断（最可靠） =====
    name_map = {
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
    for key, std_id in name_map.items():
        if key in file_name:
            return std_id

    # ===== 第2优先：封面区域模式匹配 =====
    cover_text = text[:1500]
    cover_flat = re.sub(r'\n+', ' ', cover_text)

    # 常见标准编号模式
    patterns = [
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

    for p in patterns:
        m = re.search(p, cover_flat)
        if m:
            raw = m.group(0)
            raw = raw.replace('—', '-').replace('–', '-').replace('\n', ' ')
            raw = re.sub(r'\s+', ' ', raw).strip()
            # 去掉开头可能的干扰字符
            raw = re.sub(r'^[^a-zA-Z\d]', '', raw)
            return raw

    # ===== 第3优先：全文搜索 =====
    full_flat = re.sub(r'\n+', ' ', text[:8000])
    for p in patterns:
        m = re.search(p, full_flat)
        if m:
            raw = m.group(0)
            raw = raw.replace('—', '-').replace('–', '-').replace('\n', ' ')
            raw = re.sub(r'\s+', ' ', raw).strip()
            raw = re.sub(r'^[^a-zA-Z\d]', '', raw)
            return raw

    return file_name.replace('.pdf', '').replace('_', ' ')


def detect_standard_title(text: str, file_name: str) -> str:
    """从 PDF 内容中识别标准名称

    优先从封面页中间区域提取标题行，避免匹配到引言/前言/页眉等。
    """
    # 文件名→标题的硬编码映射（最可靠）
    title_map = {
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
    for key, title in title_map.items():
        if key in file_name:
            return title

    # 自动检测——从封面页中找标题行
    lines = text.split('\n')
    # 中文标准：找长度适中、含"标准/规范/规程/技术"等关键词的行
    for i, line in enumerate(lines):
        line = line.strip()
        if line and 6 < len(line) < 70:
            if any(kw in line for kw in ['标准', '规范', '规程', '技术', '要求',
                                          'Testing', 'Specification', 'Standard',
                                          'Insulation', 'Safety', 'Requirements',
                                          'Electronic equipment']):
                # 排除页码行、编号行、引用行
                if not re.match(r'^[\dIVXLCDM\s./\-—–]+$', line):
                    return line

    # 后备：用第一段有内容的行（超过 10 个字符）
    for line in lines:
        line = line.strip()
        if len(line) > 10 and not re.match(r'^[\d\s./\-—–]+$', line):
            return line[:100]

    return file_name.replace('.pdf', '').replace('_', ' ')


def clean_page_text(text: str) -> str:
    """清理单页文本（去页眉页码）"""
    lines = text.split('\n')
    cleaned = []
    for line in lines:
        stripped = line.strip()
        # 跳过纯数字页码行
        if PAGE_NUM_PATTERN.match(stripped):
            continue
        # 跳过常见页脚
        is_footer = any(p.match(stripped) for p in FOOTER_PATTERNS)
        if is_footer:
            continue
        cleaned.append(line)
    return '\n'.join(cleaned)


def extract_text_from_pdf(filepath: str) -> tuple[str, str]:
    """从 PDF 提取文字

    Returns:
        (full_text, confidence)
        confidence: "text" 表示文字提取成功, "ocr_needed" 表示需 OCR, "failed" 表示失败
    """
    try:
        doc = fitz.open(filepath)
    except Exception as e:
        logger.error(f"  无法打开 PDF: {e}")
        return "", "failed"

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

    file_name = os.path.basename(filepath)
    if total_chars < 100:
        logger.warning(f"  ↪ 文字不足 ({total_chars}字符)，判定为扫描型 PDF，跳过（后续可用 OCR 处理）")
        SCANNED_PDFS.append(file_name)
        return "", "ocr_needed"

    full_text = '\n'.join(pages)
    return full_text, "text"


def _is_valid_chapter_title(num: str, title: str) -> bool:
    """判断是否是有效的章节标题（过滤表格内容、页码等假阳性）"""
    if len(title) < 3:
        return False
    # 纯数字/符号 → 不是标题
    if re.match(r'^[\d\s./\-—–()（）、,，;；:：]+$', title):
        return False
    # 含技术单位/符号 → 大概率是表格内容
    if re.search(r'[μµk]V|mA|kHz|MHz|dB|W/m', title):
        return False
    # 英文表格描述开头
    if re.match(r'^[12]\s+(For example|The terms|Electronic equipment|This includes)', title):
        return False
    if title.startswith(('<', '>', 'V', 'm', 'k', 'd', 'h', 'H')):
        return False
    # 含 "/" 的短文本 → 表格单元格
    if '/' in title and len(title) < 15:
        return False
    # 以 "类" "型" "组" 开头的分类标记
    if re.match(r'^[一两三四五六七八九十组ABCD\d]', title):
        # 但章节标题本身可能是中文开头，只排除短文本
        if len(title) < 8:
            return False
    # 首字为括号 → 非标题
    if title.startswith(('（', '(', '[')):
        return False
    # 含"分钟后"、"组A"、"限值/" → 表格内容
    if re.search(r'(分钟后|组[A-D]类|限值[/（]|测量距离)', title):
        return False
    # 中文标准章节标题常见特征：含2个以上中文字符
    cn_chars = len(re.findall(r'[一-鿿]', title))
    if cn_chars >= 2:
        return True
    # 英文标题至少5个字符长度，首字母大写
    if re.match(r'^[A-Z][a-zA-Z\s,]{4,}', title):
        # 排除法语/技术描述行
        if re.search(r'(Limites|inférieures|crête|isolation|exigence|satisfaire|une deuxième)', title, re.IGNORECASE):
            return False
        # 排除纯技术参数描述
        if re.match(r'^[12]\s+[LU]', title):
            return False
        return True
    return False


def chunk_by_chapters(full_text: str, std_id: str, std_title: str,
                      file_name: str, confidence: str) -> list[dict]:
    """按章节切块

    Returns:
        list of dict: {text, metadata}
    """
    chunks = []

    # 查找章节标题位置：同时尝试多种模式，合并去重后按位置排序
    seen_positions: set[int] = set()
    chapter_matches: list[dict] = []

    def _add_match(start: int, end: int, chapter: str, title: str, level: str):
        if start in seen_positions:
            return
        seen_positions.add(start)
        chapter_matches.append({
            'start': start, 'end': end,
            'chapter': chapter, 'title': title, 'level': level,
        })

    # 方法1：中文章节标题（特定模式，针对 GB/T 格式）
    for m in CHAPTER_PATTERN_CN.finditer(full_text):
        _add_match(m.start(), m.end(), m.group(1), m.group(2).strip(), 'chapter')

    # 方法2：通用数字章节
    for m in CHAPTER_PATTERN_GENERIC.finditer(full_text):
        num = m.group(1)
        title_text = m.group(2).strip()
        if not _is_valid_chapter_title(num, title_text):
            continue
        # 跳过目录行（含连续点号 "......." 的）
        # TOC 的章节条目后接 "..." 或 "......"
        line_start = m.start()
        line_end = m.end()
        line_context = full_text[line_start:line_start + 80]
        if '......' in line_context or '……' in line_context:
            continue
        _add_match(m.start(), m.end(), num, title_text,
                   'section' if '.' in num else 'chapter')

    # 方法3：附录
    for m in APPENDIX_PATTERN_CN.finditer(full_text):
        _add_match(m.start(), m.end(), f'附录 {m.group(1)}', f'附录 {m.group(1)}', 'appendix')
    for m in APPENDIX_PATTERN_EN.finditer(full_text):
        _add_match(m.start(), m.end(), f'{m.group(1)} {m.group(2)}', f'{m.group(1)} {m.group(2)}', 'appendix')

    # 按位置排序
    chapter_matches.sort(key=lambda x: x['start'])

    # 去重：如果同一章节号出现多次（目录+正文），保留正文位置的条目
    # 策略：同章节号的两个匹配，保留靠后的（跳过目录页的）
    seen_chapters: dict[str, list[int]] = {}
    for i, m in enumerate(chapter_matches):
        key = f"{m['chapter']}|{m['title']}"
        if key not in seen_chapters:
            seen_chapters[key] = []
        seen_chapters[key].append(i)

    to_remove: set[int] = set()
    for key, indices in seen_chapters.items():
        if len(indices) > 1:
            # 多个相同章节 → 取最后一个（正文），前面的删掉
            for idx in indices[:-1]:
                # 但只删距离 > 500 字符的（太近说明不是目录vs正文）
                cur_pos = chapter_matches[idx]['start']
                next_pos = chapter_matches[indices[-1]]['start']
                if next_pos - cur_pos > 500:
                    to_remove.add(idx)

    # 按索引降序删除（避免移位问题）
    for idx in sorted(to_remove, reverse=True):
        chapter_matches.pop(idx)

    if not chapter_matches:
        # 没找到章节标记 → 整份作为一块
        page_hint = guess_page_range(full_text, file_name)
        chunks.append({
            'text': full_text[:5000],
            'metadata': {
                'std_id': std_id,
                'std_title': std_title,
                'chapter': '全文',
                'chapter_title': '全文',
                'page': page_hint[0],
                'file_name': file_name,
                'confidence': confidence,
            },
        })
        return chunks

    # 按章节切分
    for i, match in enumerate(chapter_matches):
        next_start = chapter_matches[i + 1]['start'] if i + 1 < len(chapter_matches) else len(full_text)
        chunk_text = full_text[match['end']:next_start].strip()

        if len(chunk_text) < 20:
            continue

        page_num = estimate_page(full_text, match['start'], file_name)

        chunks.append({
            'text': chunk_text,
            'metadata': {
                'std_id': std_id,
                'std_title': std_title,
                'chapter': match['chapter'],
                'chapter_title': match['title'],
                'page': page_num,
                'file_name': file_name,
                'confidence': confidence,
            },
        })

    return chunks


def estimate_page(full_text: str, char_pos: int, file_name: str) -> int:
    """根据字符位置估算页码

    简单策略：按换行符分割，统计文档中的页分隔痕迹
    由于我们按页提取时加了换行，可以大致估算
    """
    # 粗略估算：每页约 1000-2000 字符
    char_per_page = 1500
    return max(1, char_pos // char_per_page + 1)


def guess_page_range(full_text: str, file_name: str) -> tuple[int, int]:
    """估算文档总页数范围"""
    char_per_page = 1500
    total_pages = max(1, len(full_text) // char_per_page + 1)
    return (1, total_pages)


def get_existing_ids(collection) -> set[str]:
    """获取已有标准文档 ID（用于增量模式）"""
    existing: set[str] = set()
    try:
        data = collection.get()
        for meta in data.get('metadatas', []):
            if meta and meta.get('std_id') and meta.get('chapter'):
                existing.add(f"{meta['std_id']}|{meta['chapter']}")
    except Exception:
        pass
    return existing


def sync():
    logger.info("=" * 60)
    logger.info("  恩特能源 - 标准知识库同步")
    logger.info("=" * 60)

    # [1/4] 扫描 PDF 文件
    logger.info("[1/4] 扫描标准 PDF 文件...")
    if not os.path.isdir(STD_DIR):
        logger.error(f"  目录不存在: {STD_DIR}")
        return

    pdf_files = sorted([
        f for f in os.listdir(STD_DIR)
        if f.lower().endswith('.pdf')
    ])

    if not pdf_files:
        logger.warning("  未找到 PDF 文件")
        return

    logger.info(f"  找到 {len(pdf_files)} 份 PDF: {', '.join(pdf_files)}")

    # [2/4] 连接 Chroma
    logger.info("[2/4] 连接 Chroma 向量库...")
    os.makedirs(CHROMA_DIR, exist_ok=True)
    client = PersistentClient(path=CHROMA_DIR)

    logger.info("  加载 embedding 模型（首次约 30MB）...")
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="BAAI/bge-small-zh-v1.5"
    )

    try:
        collection = client.get_collection(COLLECTION_NAME, embedding_function=ef)
        old_count = collection.count()
        existing_ids = get_existing_ids(collection)
        logger.info(f"  Chroma 已有 {old_count} 条标准记录, {len(existing_ids)} 个唯一章节")
    except (ValueError, chromadb.errors.NotFoundError):
        collection = client.create_collection(
            name=COLLECTION_NAME,
            embedding_function=ef,
            metadata={"hnsw:space": "cosine"},
        )
        existing_ids = set()
        logger.info("  Chroma 集合不存在，已新建")

    # [3/4] 处理每份 PDF
    logger.info("[3/4] 提取文字并按章节切块...")
    total_new = 0
    total_skipped_text = 0
    stats: list[dict] = []

    global SCANNED_PDFS
    SCANNED_PDFS = []

    for pdf_file in pdf_files:
        filepath = os.path.join(STD_DIR, pdf_file)
        file_size = os.path.getsize(filepath)
        file_size_mb = file_size / 1024 / 1024

        logger.info(f"\n  📄 {pdf_file} ({file_size_mb:.1f}MB)")

        # 提取文字
        full_text, confidence = extract_text_from_pdf(filepath)

        if not full_text:
            if confidence == "ocr_needed":
                stats.append({"file": pdf_file, "status": "跳过(需OCR)", "chunks": 0})
            else:
                stats.append({"file": pdf_file, "status": "提取失败", "chunks": 0})
            continue

        # 识别标准信息
        std_id = detect_standard_id(full_text, pdf_file)
        std_title = detect_standard_title(full_text, pdf_file)
        logger.info(f"  识别: {std_id} — {std_title}")

        # 按章节切块
        chunks = chunk_by_chapters(full_text, std_id, std_title, pdf_file, confidence)
        logger.info(f"  切块: {len(chunks)} 个章节")

        # 增量写入
        new_chunks = [
            ch for ch in chunks
            if f"{ch['metadata']['std_id']}|{ch['metadata']['chapter']}" not in existing_ids
        ]

        if not new_chunks:
            logger.info(f"  增量模式：无新增章节")
            stats.append({"file": pdf_file, "status": "已存在(增量跳过)", "chunks": len(chunks)})
            total_skipped_text += 1
            continue

        # 写入 Chroma
        ids = []
        documents = []
        metadatas = []
        id_counter: dict[str, int] = {}  # 去重计数器

        for ch in new_chunks:
            # 生成唯一 ID（带序号防重复）
            safe_chapter = re.sub(r'[^a-zA-Z0-9一-鿿\-]', '_', ch['metadata']['chapter'])
            base_uid = f"{ch['metadata']['std_id']}_{safe_chapter}".replace(' ', '_').replace('/', '_')
            count = id_counter.get(base_uid, 0)
            id_counter[base_uid] = count + 1
            uid = f"{base_uid}_{count}" if count > 0 else base_uid
            ids.append(uid)
            documents.append(ch['text'])
            metadatas.append(ch['metadata'])

        # 分批写入
        BATCH_SIZE = 50
        for i in range(0, len(ids), BATCH_SIZE):
            end = min(i + BATCH_SIZE, len(ids))
            collection.add(
                ids=ids[i:end],
                documents=documents[i:end],
                metadatas=metadatas[i:end],
            )

        total_new += len(new_chunks)
        logger.info(f"  写入 {len(new_chunks)} 个新章节")
        stats.append({
            "file": pdf_file,
            "status": "✅ 成功",
            "chunks": len(new_chunks),
            "total_chunks": len(chunks),
            "std_id": std_id,
        })

    # [4/4] 汇总报告
    logger.info("\n" + "=" * 60)
    logger.info("  同步完成!")
    logger.info("=" * 60)
    logger.info(f"  知识库总计: {collection.count()} 条标准记录")
    logger.info(f"  本次新增: {total_new} 个章节")
    logger.info(f"  处理文件: {len(pdf_files)}")

    logger.info("\n  处理明细:")
    for s in stats:
        logger.info(f"    {s['status']}: {s['file']} ({s['chunks']} 章节)")

    if SCANNED_PDFS:
        logger.warning(f"\n  跳过的扫描型 PDF（共 {len(SCANNED_PDFS)} 份，需 OCR）：")
        for f in SCANNED_PDFS:
            logger.warning(f"    ⚠ {f}")

    logger.info("\n  💡 提示:")
    logger.info("  - 扫描型 PDF 可后续用外部 OCR 工具处理")
    logger.info("  - 重新运行本脚本不会重复添加已有章节（增量模式）")
    logger.info("  - 如需重建库，请从 Chroma 中删除 standards collection 后重试")
    logger.info("=" * 60)


if __name__ == "__main__":
    sync()

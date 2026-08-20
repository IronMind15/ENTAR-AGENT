"""
PDF 解析双路径对比脚本（v1.10.3）

对同一份 PDF 分别跑「本地 PyMuPDF」和「MinerU VLM」两条解析路径，输出对比报告，
用于验证文件检测路由（文字版走本地 / 扫描版走 MinerU）不会造成文档质量下降。

用法：
    python scripts/compare_pdf_parsers.py                     # 默认对比 4 份精选已入库标准
    python scripts/compare_pdf_parsers.py --files a.pdf b.pdf # 自定义文件
    python scripts/compare_pdf_parsers.py --no-mineru         # 只跑检测+本地，不烧 MinerU 额度

注意：
  - 默认会真实调用 MinerU（消耗当天 1000 页额度），扫描版每页计 1 页。
  - 只做提取+切块计数，**不写 Chroma**，不污染真实知识库。
"""
import argparse
import io
import logging
import os
import re
import sys
import time
from pathlib import Path

# 修复 Windows 控制台编码
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.paths import STANDARDS_DIR

logging.basicConfig(level=logging.WARNING, format="%(message)s")

# 默认对比集（用户确认的 4 份精选：2 扫描 + 2 文字）
DEFAULT_FILES = [
    STANDARDS_DIR / "GBT16935.1-2008.pdf",   # 扫描版 58p
    STANDARDS_DIR / "EN50438_2008.pdf",       # 扫描版 54p
    STANDARDS_DIR / "EN50178.pdf",            # 文字版 102p
    STANDARDS_DIR / "GB_T_34133-2023.pdf",    # 文字版 49p
]

# 中文句子切分（句号/分号/问号/感叹号/换行）
_SENTENCE_SPLIT = re.compile(r'[。；;！？\n]+')
# 英文句号切分：句号后跟空白+大写字母 或 结尾（避免拆开缩写/小数/编号）
_EN_SENTENCE_SPLIT = re.compile(r'(?<=[a-zA-Z0-9)])\.(?=\s+[A-Z]|\s*$)')


def _is_sentence(s: str) -> bool:
    """句子判定：中文句子 20~80 字符；英文句子 ≥5 个单词且 ≤120 字符"""
    if re.search(r'[一-鿿]', s):
        return 20 <= len(s) <= 80
    return len(re.findall(r'[A-Za-z]+', s)) >= 5 and len(s) <= 120


def _normalize(text: str) -> str:
    """归一化：去空白/换行/全角空格 + 去 Markdown 痕迹（# * | [ ] ( ) _ -）"""
    return re.sub(r'[\s　#*|\\\[\]()_\-]+', '', text or '')


def _sample_sentences(full_text: str, max_n: int = 10) -> list[str]:
    """从正文区域抽样句子（跳过封面/目录/尾页），避免抽到目录行导致误判

    取文本行 20%~85% 区段，按中文句号/分号/换行 + 英文句号切句，
    句子判定见 _is_sentence（中英文均支持）。
    首次实现直接从开头抽，抽到的全是目录行（"1 范围"、"2 规范性引用文件"），
    与 MinerU 的目录排版不同导致重合率虚低为 0%。
    """
    lines = (full_text or "").split('\n')
    body = lines[len(lines) // 5: int(len(lines) * 0.85)]
    sentences = []
    for line in body:
        for chunk in _SENTENCE_SPLIT.split(line):
            for raw in _EN_SENTENCE_SPLIT.split(chunk):
                s = raw.strip()
                if _is_sentence(s):
                    sentences.append(s)
                if len(sentences) >= max_n:
                    return sentences
    return sentences


def _overlap_rate(local_text: str, mineru_text: str) -> tuple[float, int, int]:
    """文字版内容重合度（关键方向）：MinerU 正文抽样句子在本地提取中的命中率

    本地文字版提取应包含 MinerU 提取到的所有正文内容；
    命中率低说明本地路径漏内容（此时路由会回退 MinerU 保质量）。
    反向命中高意义有限（本地漏字时反向照样命中），故只报这一个方向。
    英文句子匹配大小写不敏感（_normalize 后再 lower）。
    """
    sentences = _sample_sentences(mineru_text)
    if not sentences:
        return 0.0, 0, 0
    local_norm = _normalize(local_text).lower()
    hit = sum(1 for s in sentences if _normalize(s).lower() in local_norm)
    return hit / len(sentences), hit, len(sentences)


def run_local(file_path: Path, expected_chars: int) -> dict:
    """本地 PyMuPDF 路径：提取 + 质量校验 + 切块计数"""
    from scripts.doc_mgr.extractors.pdf_mupdf import extract_pdf_text, validate_local_text
    from scripts.doc_mgr.chunkers import PdfChunker

    t0 = time.time()
    full_text, std_id, std_title = extract_pdf_text(str(file_path))
    elapsed = time.time() - t0

    has_text = bool(full_text)
    result = {
        "has_text": has_text,
        "chars": len(''.join((full_text or "").split())),
        "std_id": std_id,
        "std_title": std_title,
        "chunks": 0,
        "quality_ok": has_text and validate_local_text(full_text, expected_chars),
        "elapsed": elapsed,
    }
    if full_text:
        chunker = PdfChunker()
        chunks = chunker.chunk(full_text, {
            "std_id": std_id, "std_title": std_title,
            "file_name": file_path.name, "confidence": "text",
        }, filepath=str(file_path))
        result["chunks"] = len(chunks)
    return result


def run_mineru(file_path: Path, force: bool = False) -> dict:
    """MinerU 路径：远程转换 → Markdown → 切块计数"""
    from scripts.doc_mgr import engine
    from scripts.doc_mgr.chunkers import MarkdownChunker

    t0 = time.time()
    try:
        md_path = engine._try_mineru(
            str(file_path), file_path.name,
            content_hash="", force=force,
        )
    except Exception as e:
        return {"chars": 0, "std_id": "", "std_title": "",
                "chunks": 0, "error": str(e), "elapsed": time.time() - t0}
    elapsed = time.time() - t0

    if not (md_path and os.path.isfile(md_path)):
        return {"chars": 0, "std_id": "", "std_title": "",
                "chunks": 0, "error": "MinerU 未产出结果", "elapsed": elapsed}

    with open(md_path, "r", encoding="utf-8") as f:
        md_text = f.read()

    from scripts.doc_mgr.extractors.pdf_mupdf import detect_standard_id, detect_standard_title
    std_id = detect_standard_id(md_text, file_path.name)
    std_title = detect_standard_title(md_text, file_path.name)

    chunker = MarkdownChunker()
    chunks = chunker.chunk(md_text, {
        "std_id": std_id, "std_title": std_title,
        "file_name": file_path.name, "source": "mineru",
    }, filepath=str(file_path))
    return {
        "chars": len(''.join(md_text.split())),
        "std_id": std_id,
        "std_title": std_title,
        "chunks": len(chunks),
        "elapsed": elapsed,
    }


def compare_one(file_path: Path, no_mineru: bool, force_mineru: bool = False) -> dict:
    from scripts.doc_mgr.extractors.pdf_mupdf import classify_pdf_type

    print(f"\n{'='*70}")
    print(f"[文件] {file_path.name}")

    # 1. 类型检测
    pdf_type, det = classify_pdf_type(str(file_path))
    print(f"  类型检测: {pdf_type}（覆盖率 {det['coverage']*100:.1f}%，"
          f"{det['text_pages']}/{det['pages']} 页有文字）")

    # 2. 本地路径
    local = run_local(file_path, det["total_chars"])
    if local["has_text"]:
        qm = "✅通过" if local["quality_ok"] else "❌未通过"
    else:
        qm = "（无文字层）"
    print(f"  本地 PyMuPDF: 字符 {local['chars']:,} | 切块 {local['chunks']} | "
          f"{local['elapsed']:.1f}s | 质量校验 {qm}")
    if local["std_id"]:
        print(f"    std_id={local['std_id']}  std_title={local['std_title'][:40]}")

    # 3. MinerU 路径
    mineru = {"error": "已跳过（--no-mineru）", "skipped": True}
    if not no_mineru:
        mineru = run_mineru(file_path, force=force_mineru)
        if "error" in mineru:
            print(f"  MinerU: ❌ {mineru['error']}")
        else:
            print(f"  MinerU: 字符 {mineru['chars']:,} | 切块 {mineru['chunks']} | "
                  f"{mineru['elapsed']:.1f}s")
            if mineru["std_id"]:
                print(f"    std_id={mineru['std_id']}  std_title={mineru['std_title'][:40]}")

    # 4. 重合度 + 判定（关键方向：MinerU 正文句子在本地提取中的命中率）
    if (not mineru.get("skipped") and "error" not in mineru
            and mineru["chars"] > 0 and local["chars"] > 0):
        overlap, hit, total = _overlap_rate(
            extract_local_text(file_path), read_mineru_text(file_path, mineru)
        )
        if total == 0:
            print("  内容重合度: 正文无可抽样句子（跳过）")
        else:
            print(f"  内容重合度: MinerU 正文句子在本地提取中命中 {hit}/{total}（{overlap:.0%}）")
    else:
        overlap, hit, total = 0.0, 0, 0

    verdict = _verdict(pdf_type, local, mineru)
    print(f"  ▶ 判定: {verdict}")

    return {
        "file": file_path.name, "type": pdf_type, "coverage": det["coverage"],
        "pages": det["pages"], "local": local, "mineru": mineru, "overlap": overlap,
        "verdict": verdict,
    }


def extract_local_text(file_path: Path) -> str:
    from scripts.doc_mgr.extractors.pdf_mupdf import extract_pdf_text
    full_text, _, _ = extract_pdf_text(str(file_path))
    return full_text or ""


def read_mineru_text(file_path: Path, mineru: dict) -> str:
    """读回 MinerU 的 Markdown 用于重合度对比（避免二次调用）"""
    from scripts.doc_mgr import engine
    md_path = engine._try_mineru(str(file_path), file_path.name, content_hash="")
    if md_path and os.path.isfile(md_path):
        with open(md_path, "r", encoding="utf-8") as f:
            return f.read()
    return ""


def _verdict(pdf_type: str, local: dict, mineru: dict) -> str:
    if mineru.get("skipped"):
        base = "文字版" if pdf_type == "text" else f"{pdf_type}"
        if local["has_text"] and local["quality_ok"]:
            return f"{base}：本地提取正常（MinerU 未跑，路由走本地省额度）"
        if pdf_type == "text":
            return "文字版但本地质量校验未通过 → 路由会正确回退 MinerU"
        return f"{base}：本地无法提取（正确），路由送 MinerU 保质量"
    if "error" in mineru:
        return "MinerU 失败（路由将回退本地；若本地也空则标记 ocr_needed）"
    if pdf_type == "text":
        if local["quality_ok"] and mineru["chars"] > 0:
            return "文字版：本地保真 OK（路由走本地，省 MinerU 额度，质量不降）"
        if local["quality_ok"]:
            return "文字版：本地提取正常（MinerU 未产出，路由仍走本地）"
        return "文字版但本地质量校验未通过 → 路由会正确回退 MinerU"
    # scanned / mixed
    if local["chars"] == 0:
        return "扫描版：本地无法提取（正确），路由送 MinerU 保质量"
    if local["quality_ok"]:
        return f"类型={pdf_type} 但本地可提取 → 路由会走 MinerU（保守保质量）"
    return f"类型={pdf_type}，本地质量校验未通过 → 路由送 MinerU（正确）"


def main() -> None:
    parser = argparse.ArgumentParser(description="PDF 本地/MinerU 双路径对比")
    parser.add_argument("--files", nargs="*", help="自定义 PDF 路径（默认 4 份精选）")
    parser.add_argument("--no-mineru", action="store_true", help="不调 MinerU（省额度）")
    parser.add_argument("--force-mineru", action="store_true", help="强制 MinerU 重新处理（不走缓存）")
    args = parser.parse_args()

    files = [Path(p) for p in args.files] if args.files else DEFAULT_FILES
    existing = [p for p in files if p.is_file()]
    if len(existing) != len(files):
        print("❌ 部分文件不存在，跳过：",
              ", ".join(str(p.name) for p in files if not p.is_file()))

    print(f"📊 PDF 双路径对比 — {'含 MinerU 真实调用（烧额度）' if not args.no_mineru else '仅本地（不烧额度）'}")
    print(f"   共 {len(existing)} 份文档，预期消耗 MinerU 额度："
          f"{sum(_page_count(p) for p in existing if not args.no_mineru)} 页")

    results = []
    for i, p in enumerate(existing, 1):
        print(f"\n[进度] {i}/{len(existing)}")
        results.append(compare_one(p, args.no_mineru, args.force_mineru))

    # 汇总
    print(f"\n{'='*70}\n汇总\n{'='*70}")
    for r in results:
        mc = r["mineru"]["chars"] if "error" not in r["mineru"] else 0
        print(f"  {r['type']:<8} {r['file'][:38]:<40} "
              f"本地{r['local']['chars']:>7,}字/{r['local']['chunks']:>3}块 | "
              f"MinerU{mc:>7,}字/{r['mineru'].get('chunks', 0):>3}块 | 重合{r['overlap']:.0%}")


def _page_count(p: Path) -> int:
    import fitz
    try:
        return fitz.open(str(p)).page_count
    except Exception:
        return 0


if __name__ == "__main__":
    main()

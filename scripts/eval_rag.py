"""
RAG 检索质量基线评估脚本 — 评估「裸检索」能否把正确答案送进 Top-N

背景：
  恩特 Agent 循环里 LLM 会兜底（无关上下文会被忽略），所以检索质量问题
  容易被 LLM 掩盖。本脚本直接评估检索层（enhanced_query），把那条被掩盖
  的质量线亮出来。

用法：
  python scripts/eval_rag.py                    # 三种模式全跑（纯向量/混合/完整）
  python scripts/eval_rag.py --mode full        # 只跑完整模式（混合+重排）
  python scripts/eval_rag.py --topk 5           # 控制 Top-N（默认 5）

评估集：data/eval/eval_set.json（每条 = 真实问法 + 期望命中的故障代码/标准编号）
指标：
  Recall@1/@3/@5  期望命中是否出现在 Top-N
  MRR             期望命中的平均倒数排名（排名越靠前越高）
"""

import argparse
import json
import os
import re
import sys

# Windows UTF-8 控制台
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

_PARENT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_PARENT_DIR)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 项目根目录（用于定位 data/eval）
_ROOT = os.path.dirname(_PARENT_DIR)
EVAL_SET_PATH = os.path.join(_ROOT, "data", "eval", "eval_set.json")

_MODES = {
    "vector": {"use_hybrid": False, "use_rerank": False},
    "hybrid": {"use_hybrid": True, "use_rerank": False},
    "full": {"use_hybrid": True, "use_rerank": True},
}
_MODE_LABEL = {
    "vector": "纯向量检索（无混合/无重排）",
    "hybrid": "混合检索（BM25+向量 RRF）",
    "full": "完整（混合 + bge-reranker 重排）",
}


def _norm(s: str) -> str:
    """归一化匹配键：小写 + 去分隔符 + 去空白"""
    s = (s or "").lower().strip()
    s = re.sub(r"[\s\-_/.,:：，。·—]+", "", s)
    return s


def _hit(retrieved: list, expected: list) -> bool:
    """判定检索结果中是否命中任一期望值（相等 或 长>=4 的包含关系）"""
    exp = [_norm(e) for e in expected]
    for r in retrieved:
        rn = _norm(r)
        if not rn:
            continue
        for e in exp:
            if rn == e or (len(e) >= 4 and e in rn) or (len(rn) >= 4 and rn in e):
                return True
    return False


def _first_rank(retrieved: list, expected: list) -> int:
    """返回期望命中的最小排名（1 起），未命中返回 None"""
    exp = [_norm(e) for e in expected]
    for idx, r in enumerate(retrieved):
        rn = _norm(r)
        if not rn:
            continue
        for e in exp:
            if rn == e or (len(e) >= 4 and e in rn) or (len(rn) >= 4 and rn in e):
                return idx + 1
    return None


def load_cases() -> list:
    with open(EVAL_SET_PATH, encoding="utf-8") as f:
        data = json.load(f)
    return data["meta"], data["cases"]


def run_mode(mode: str, cases: list, topk: int) -> dict:
    """跑单个模式，返回统计结果"""
    from scripts.skills.enhanced_search import enhanced_query

    opts = _MODES[mode]
    meta_info, _ = load_cases()  # 取 match_field
    match_field = meta_info["match_field"]

    rows = []
    for case in cases:
        collection = case["collection"]
        q = case["query"]
        expected = case["expected"]
        key = match_field.get(collection, "")

        results = enhanced_query(
            collection, query_text=q, n_results=topk,
            use_hybrid=opts["use_hybrid"], use_rerank=opts["use_rerank"],
        )
        docs = results.get("documents", [[]])[0] or []
        metas = results.get("metadatas", [[]])[0] or []
        retrieved = [str(m.get(key, "")) for m in metas] if metas else []

        rank = _first_rank(retrieved, expected)
        hit = rank is not None
        rank_label = f"rank={rank}" if rank else "MISS"
        # 提取命中的实际值，便于诊断
        matched_val = ""
        if rank:
            matched_val = retrieved[rank - 1]

        rows.append({
            "id": case["id"], "type": case.get("type", ""),
            "query": q, "expected": expected,
            "hit": hit, "rank": rank,
            "matched": matched_val, "n_docs": len(docs),
        })
    return rows


def summarize(rows: list, topk: int) -> dict:
    total = len(rows)
    hits = [r for r in rows if r["hit"]]
    recall1 = sum(1 for r in hits if r["rank"] == 1) / total
    recall3 = sum(1 for r in hits if r["rank"] <= 3) / total
    recall5 = sum(1 for r in hits if r["rank"] <= 5) / total
    mrr = sum(1.0 / r["rank"] for r in hits) / total if total else 0.0
    return {
        "total": total, "hits": len(hits),
        "recall1": recall1, "recall3": recall3, "recall5": recall5, "mrr": mrr,
    }


def print_summary(title: str, stats: dict) -> None:
    print(f"  {title}")
    print(
        f"    Recall@1={stats['recall1']:.2f}  "
        f"Recall@3={stats['recall3']:.2f}  "
        f"Recall@{topk}={stats['recall5']:.2f}  "
        f"MRR={stats['mrr']:.3f}  "
        f"({stats['hits']}/{stats['total']} 命中)"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG 检索质量基线评估")
    parser.add_argument("--mode", choices=["all", *list(_MODES.keys())],
                        default="all", help="评估模式")
    parser.add_argument("--topk", type=int, default=5, help="Top-N（默认5）")
    args = parser.parse_args()

    topk = args.topk
    meta, cases = load_cases()
    print(f"评估集: {len(cases)} 条  |  Top-{topk}  |  匹配字段: {meta['match_field']}")
    print()

    modes = list(_MODES.keys()) if args.mode == "all" else [args.mode]

    for mode in modes:
        print(f"==== 模式「{mode}」— {_MODE_LABEL[mode]} ====")
        rows = run_mode(mode, cases, topk)

        # 逐条明细（仅显示 MISS 或 rank>1，减少噪音）
        misses = [r for r in rows if not r["hit"]]
        slow = [r for r in rows if r["hit"] and r["rank"] > 1]
        for r in misses:
            print(f"  ✗ {r['id']} [{r['type']}] 「{r['query']}」 期望={r['expected']} → 未命中")
        for r in slow:
            print(f"  ~ {r['id']} [{r['type']}] 「{r['query']}」 期望={r['expected']} → 命中 rank={r['rank']} ({r['matched']})")

        # 汇总
        print()
        print_summary("整体", summarize(rows, topk))
        for t in ["fault_code", "fault_symptom", "std_id", "std_clause"]:
            sub = [r for r in rows if r["type"] == t]
            if sub:
                print_summary(f"  [{t}] ({len(sub)}条)", summarize(sub, topk))
        print()

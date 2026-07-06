"""
同步脚本：PCS参数表 V1.6.2.xlsx → Chroma 向量库
读取「遥信（DI）」sheet，从第 51 行开始解析故障代码数据
"""

import os
import sys

# Windows UTF-8
if sys.platform == "win32":
    sys.stdin.reconfigure(encoding="utf-8")
    sys.stdout.reconfigure(encoding="utf-8")

import openpyxl
from chromadb import PersistentClient
from chromadb.utils import embedding_functions

# HuggingFace 国内镜像（避免下载失败）
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

# ===== 路径 =====
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_SCRIPT_DIR)
DATA_DIR = os.path.join(_PROJECT_ROOT, "data")
CHROMA_DIR = os.path.join(_PROJECT_ROOT, "knowledge_base")

# ===== 常量 =====
EXCEL_FILE = "PCS参数表 V1.6.2.xlsx"
SHEET_NAME = "遥信（DI）"
START_ROW = 51  # 数据从第 51 行开始
COLLECTION_NAME = "error_codes"

# Excel 列号 → 字段名（第 1 行表头）
COLUMNS = {
    1: "seq",
    2: "name",
    3: "address",
    4: "bit_address",
    5: "attribute",
    6: "data_type",
    7: "default_value",
    8: "notes",
    9: "description",
    10: "notes2",
    11: "fault_code",
    12: "cause",
}


def safe_str(v):
    """安全转字符串，None → '' """
    if v is None:
        return ""
    if isinstance(v, (int, float)):
        return str(int(v)) if v == int(v) else str(v)
    return str(v).strip()


def read_excel_data():
    """读取 Excel，返回结构化记录列表"""
    filepath = os.path.join(DATA_DIR, EXCEL_FILE)
    if not os.path.exists(filepath):
        print(f"  [FAIL] 未找到文件: {filepath}")
        return []

    print(f"  Excel 文件: {EXCEL_FILE}")
    print(f"  Sheet:      {SHEET_NAME}")
    print(f"  起始行:     {START_ROW}")

    wb = openpyxl.load_workbook(filepath, data_only=True)
    if SHEET_NAME not in wb.sheetnames:
        print(f"  [FAIL] 未找到 sheet: {SHEET_NAME}（已有: {wb.sheetnames}）")
        return []

    ws = wb[SHEET_NAME]
    records = []

    for row_idx in range(START_ROW, ws.max_row + 1):
        # 读取各列
        raw = {}
        has_data = False
        for col, field in COLUMNS.items():
            v = ws.cell(row=row_idx, column=col).value
            raw[field] = safe_str(v)
            if raw[field]:
                has_data = True

        if not has_data:
            continue  # 跳过空行

        # 构建文档文本（供语义搜索用）
        doc_parts = []
        for field in ["name", "description", "cause", "notes", "fault_code"]:
            if raw.get(field):
                label = {"fault_code": "故障代码"}.get(field, field)
                doc_parts.append(f"{label}：{raw[field]}")
        doc_text = " | ".join(doc_parts)

        records.append({
            "doc_text": doc_text,
            "metadata": {
                "sheet_name": SHEET_NAME,
                "row_num": row_idx,
                "seq": raw.get("seq", ""),
                "fault_code": raw.get("fault_code", ""),
                "name": raw.get("name", ""),
                "address": raw.get("address", ""),
                "bit_address": raw.get("bit_address", ""),
                "attribute": raw.get("attribute", ""),
                "data_type": raw.get("data_type", ""),
                "default_value": raw.get("default_value", ""),
                "notes": raw.get("notes", ""),
                "notes2": raw.get("notes2", ""),
                "description": raw.get("description", ""),
                "cause": raw.get("cause", ""),
            },
        })

    wb.close()
    return records


def sync():
    print("=" * 50)
    print("  恩特能源 - 知识库同步")
    print("=" * 50)
    print()

    # [1/3] 读取 Excel
    print("[1/3] 读取 Excel 数据...")
    records = read_excel_data()
    if not records:
        print("  [FAIL] 未读取到任何数据，终止同步")
        return
    print(f"  [OK] 读取到 {len(records)} 条故障记录")
    print()

    # [2/3] 初始化 Chroma
    print("[2/3] 初始化 Chroma 向量库...")
    os.makedirs(CHROMA_DIR, exist_ok=True)
    client = PersistentClient(path=CHROMA_DIR)

    # 删除旧集合，重建
    try:
        client.delete_collection(COLLECTION_NAME)
        print("  [OK] 已清除旧知识库集合")
    except Exception:
        print("  [OK] 无旧集合，直接创建")

    print("  加载 embedding 模型（首次下载约 30MB）...")
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="BAAI/bge-small-zh-v1.5"
    )
    collection = client.create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )
    print("  [OK] Chroma 初始化完成")
    print()

    # [3/3] 写入数据
    print("[3/3] 写入 Chroma（分批写入）...")
    BATCH_SIZE = 50
    total = len(records)
    for i in range(0, total, BATCH_SIZE):
        batch = records[i : i + BATCH_SIZE]
        ids = [f"row_{r['metadata']['row_num']}" for r in batch]
        documents = [r["doc_text"] for r in batch]
        metadatas = [r["metadata"] for r in batch]

        collection.add(ids=ids, documents=documents, metadatas=metadatas)
        print(f"  写入 {min(i + BATCH_SIZE, total)}/{total} 条...")

    print()
    print("=" * 50)
    print("  同步完成!")
    print("=" * 50)
    print(f"  [OK] 知识库总计:  {collection.count()} 条")
    print(f"  [OK] 数据来源:    {SHEET_NAME}")
    print(f"  [OK] 数据行范围:  第 {START_ROW} ~ {records[-1]['metadata']['row_num']} 行")
    print(f"  [DIR] Excel 位置: {DATA_DIR}")
    print(f"  [DIR] 知识库位置: {CHROMA_DIR}")
    print()


if __name__ == "__main__":
    sync()

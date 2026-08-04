"""经验知识库 Markdown 入库链路测试（第二步 experience_kb）

验证：
1. 五段式模板被 MarkdownChunker 正确切块（# 标题无正文不产生块）
2. process_file 对 .md 显式 target_collection="experience_kb" 透传
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

TEMPLATE = """# 经验条目：测试设备故障排查

## 故障现象
现场设备运行约半年后，报"IGBT 过温告警"，PCS 功率自动降额至 50%，复位后数小时复现，环境温度约 35℃，用户多次复位仍复现，售后上门确认非误报。

## 排查步骤
1. 检查柜内进风滤网，发现积灰严重，先清理或更换。
2. 用红外热像仪测量 IGBT 模块与散热器温度，确认是真实过温还是采样异常。
3. 检查三只散热风扇运转与转向，用转速表对比各相转速差异，发现一只风扇转速明显偏低。

## 根因
散热风道滤网积灰加上一只风扇转速异常，造成散热器局部热点，触发 IGBT 过温降额；旧固件阈值换算偏差进一步放大告警灵敏度，导致误报频发。

## 解决方案
1. 清理风道并更换异常风扇（备件：FAN-12038-DC24V）。
2. NTC 采样端子重新插拔并涂导电膏，确认阻值归位。
3. 升级控制板固件至 v1.4.2，修正过温阈值换算偏差，恢复正常告警灵敏度。

## 验证结果
满载 100% 连续运行 8 小时，IGBT 壳温稳定在 78℃，告警未再出现、无功率降额；3 个月回访未复发。
"""


class MarkdownChunkerTests(unittest.TestCase):
    """五段式模板切块"""

    def test_five_stage_template_chunks(self):
        from doc_mgr.chunkers import MarkdownChunker
        chunker = MarkdownChunker()
        chunks = chunker.chunk(TEMPLATE, {"file_name": "test.md"}, filepath="test.md")
        titles = [c.metadata.get("chapter_title", "") for c in chunks]
        self.assertEqual(titles, ["故障现象", "排查步骤", "根因", "解决方案", "验证结果"])
        self.assertEqual(len(chunks), 5)


class ProcessFileCollectionTests(unittest.TestCase):
    """process_file 对 .md 显式指定 experience_kb collection"""

    def test_markdown_to_experience_kb(self):
        from doc_mgr import engine

        tmp = tempfile.mkdtemp()
        md_path = os.path.join(tmp, "经验测试.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(TEMPLATE)

        collections = []

        class FakeStore:
            def replace_document(self, collection, *args, **kwargs):
                collections.append(collection)
                return len(collections)

            def get(self, collection, **kwargs):
                return {"ids": [], "metadatas": []}

        # 避免写真实 data/user_store.db
        with mock.patch.object(engine, "get_store", return_value=FakeStore()), \
             mock.patch.object(engine, "SyncTracker"):
            doc = engine.process_file(md_path, target_collection="experience_kb")

        self.assertIn("experience_kb", collections)
        self.assertEqual(doc.status, "done")

    def test_reedit_experience_replaces_version(self):
        """纠错链路：编辑经验 .md 重新同步 → 同一 doc_id 触发版本替换（旧版本下线）"""
        from doc_mgr import engine

        tmp = tempfile.mkdtemp()
        md_path = os.path.join(tmp, "经验.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(TEMPLATE)

        calls = []

        class FakeStore:
            def replace_document(self, collection, doc_id, file_name,
                                 ids, documents, metadatas, legacy_ids=None):
                version_id = metadatas[0].get("version_id", "") if metadatas else ""
                calls.append({
                    "collection": collection,
                    "doc_id": doc_id,
                    "version": version_id,
                })
                return len(calls)

            def get(self, collection, **kwargs):
                return {"ids": [], "metadatas": []}

        with mock.patch.object(engine, "get_store", return_value=FakeStore()), \
             mock.patch.object(engine, "SyncTracker"):
            engine.process_file(md_path, target_collection="experience_kb")
            # 模拟纠错：修改内容后重新同步
            with open(md_path, "w", encoding="utf-8") as f:
                f.write(TEMPLATE + "\n\n## 验证结果补充\n纠错后重新验证通过，未复发。")
            engine.process_file(md_path, target_collection="experience_kb")

        self.assertEqual(len(calls), 2)
        # doc_id 基于路径稳定 → 修改后同一 doc_id → replace_document 走版本替换
        self.assertEqual(calls[0]["doc_id"], calls[1]["doc_id"])
        # 内容变化 → content_hash 变化 → 新版本，触发换版
        self.assertNotEqual(calls[0]["version"], calls[1]["version"])
        self.assertTrue(all(c["collection"] == "experience_kb" for c in calls))

    def test_default_collection_for_md_is_standards(self):
        """无显式 target_collection 时 .md 默认落 standards（保持原行为）"""
        from doc_mgr import engine

        tmp = tempfile.mkdtemp()
        md_path = os.path.join(tmp, "普通.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write("# 标题\n\n## 章节\n内容")

        collections = []

        class FakeStore:
            def replace_document(self, collection, *args, **kwargs):
                collections.append(collection)
                return len(collections)

            def get(self, collection, **kwargs):
                return {"ids": [], "metadatas": []}

        with mock.patch.object(engine, "get_store", return_value=FakeStore()), \
             mock.patch.object(engine, "SyncTracker"):
            engine.process_file(md_path)

        self.assertIn("standards", collections)


if __name__ == "__main__":
    unittest.main()

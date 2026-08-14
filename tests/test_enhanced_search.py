"""enhanced_search BM25 缓存失效测试（v1.12.x 审查 Critical 3）

BM25 索引是进程内缓存，与向量库（Chroma）是两份数据——文档变更后
缓存不失效会让混合检索永远用旧索引，新文档向量能查到但 BM25 侧查不到。
invalidate_bm25_cache 在学习/删除/重新学习后调用，测试其按 collection 清理的语义。
"""

import os
import sys
import unittest


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from skills import enhanced_search as es  # noqa: E402


class Bm25CacheInvalidationTests(unittest.TestCase):
    def setUp(self):
        self._saved = es._bm25_cache
        es._bm25_cache = {}

    def tearDown(self):
        es._bm25_cache = self._saved

    def _fill(self):
        es._bm25_cache = {
            "standards|": {"bm25": "s1"},
            "standards|{'dept': 'public'}": {"bm25": "s2"},
            "error_codes|": {"bm25": "e1"},
        }

    def test_invalidate_all_clears_everything(self):
        self._fill()
        es.invalidate_bm25_cache()
        self.assertEqual(es._bm25_cache, {})

    def test_invalidate_by_collection_only_clears_that_collection(self):
        self._fill()
        es.invalidate_bm25_cache("standards")
        self.assertNotIn("standards|", es._bm25_cache)
        self.assertNotIn("standards|{'dept': 'public'}", es._bm25_cache)
        self.assertIn("error_codes|", es._bm25_cache)

    def test_invalidate_unknown_collection_is_noop(self):
        self._fill()
        es.invalidate_bm25_cache("experience_kb")
        self.assertEqual(
            set(es._bm25_cache),
            {"standards|", "standards|{'dept': 'public'}", "error_codes|"})


if __name__ == "__main__":
    unittest.main()

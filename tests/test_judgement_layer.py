"""判定层治本框架测试（v1.12.0 M1/M2/M3）

覆盖：
- M1 领域互斥让位：detect_domains 判定 + bot 删除/重学分支让位看板技能。
- M2 操作歧义澄清：多领域句反问用户 + 按编号/标签/取消解析。
- M3 澄清确认路由：澄清 pending 的确认/取消统一路由，与现有确认词互不干扰。
- 查询链路：非操作类盲区走统一查知识库（不补枚举）。
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from routing import (ask_clarification, clear_clarification, detect_domains,
                     get_clarification, resolve_clarification)

_USER_IDS = ("u1", "u2", "u3")


class DetectDomainsTests(unittest.TestCase):
    """M1：声明式领域检测"""

    def test_kanban_delete_identified(self):
        """「删除看板订阅/删掉我的看板」→ 命中 kanban（M1 让位依据）"""
        self.assertEqual(detect_domains("删除看板订阅"), ["kanban"])
        self.assertEqual(detect_domains("删掉我的看板"), ["kanban"])

    def test_delete_learn_no_domain(self):
        """「删除学习 1/重新学习 1」→ 无业务领域，照旧文件命令"""
        self.assertEqual(detect_domains("删除学习 1"), [])
        self.assertEqual(detect_domains("重新学习 1"), [])

    def test_fault_domain_in_delete_sentence(self):
        """含故障码的删除句命中 fault（M2 澄清依据）"""
        self.assertIn("fault", detect_domains("删除 d4-1 的内容"))

    def test_standard_domain_in_relearn_sentence(self):
        """含标准号的句命中 standard"""
        self.assertIn("standard", detect_domains("重新学习 GB/T 34133-2023"))

    def test_empty_or_plain_chat(self):
        self.assertEqual(detect_domains(""), [])
        self.assertEqual(detect_domains("你好"), [])


class ClarificationTests(unittest.TestCase):
    """M2/M3：澄清上下文（按 user_id 隔离、超时、解析路由）"""

    def setUp(self):
        for uid in _USER_IDS:
            clear_clarification(uid)

    def _ask(self, uid="u1"):
        ask_clarification(uid, [
            {"key": "delete", "label": "删除学习内容「1」"},
            {"key": "kanban", "label": "看板订阅相关操作"},
        ], ctx={"action": "delete", "target": "1", "raw": "删除 d4-1 的内容"})

    def test_resolve_by_number(self):
        self._ask()
        r = resolve_clarification("u1", "1")
        self.assertEqual(r["key"], "delete")
        self.assertEqual(r["_ctx"]["action"], "delete")
        self.assertIsNone(get_clarification("u1"))  # 解析后清除

    def test_resolve_by_label_text(self):
        self._ask()
        r = resolve_clarification("u1", "删除学习内容")
        self.assertEqual(r["key"], "delete")
        self.assertIsNone(get_clarification("u1"))

    def test_cancel_clears(self):
        self._ask()
        r = resolve_clarification("u1", "取消")
        self.assertEqual(r["key"], "cancel")
        self.assertIsNone(get_clarification("u1"))

    def test_user_isolation(self):
        self._ask("u1")
        self.assertIsNone(get_clarification("u2"))
        self.assertIsNone(resolve_clarification("u2", "1"))
        # u1 的澄清不受 u2 探测影响
        self.assertIsNotNone(get_clarification("u1"))

    def test_unresolved_keeps_pending(self):
        self._ask()
        self.assertIsNone(resolve_clarification("u1", "这是一条普通消息"))
        self.assertIsNotNone(get_clarification("u1"))  # 保留，可继续澄清

    def test_render_clarification_numbered(self):
        self._ask()
        from routing import render_clarification
        rendered = render_clarification("u1")
        self.assertIn("1.", rendered)
        self.assertIn("2.", rendered)
        self.assertIn("取消", rendered)


class BotFileCommandGateTests(unittest.TestCase):
    """M1 让位 + M2 澄清 + M3 澄清路由（bot._process_text 集成）"""

    def setUp(self):
        from skills.dingtalk_bot import ErrorQueryHandler
        self.handler = ErrorQueryHandler()
        for uid in _USER_IDS:
            clear_clarification(uid)

    @mock.patch("skills.get_matched_skill")
    def test_delete_kanban_defers_to_skill(self, mock_get_skill):
        """「删除看板订阅」→ bot 删除正则让位 → 落到技能层（dashboard）"""
        fake = mock.MagicMock()
        fake.name = "dashboard"
        fake.handle.return_value = {"answer": "看板删除确认", "source": "dashboard"}
        mock_get_skill.return_value = fake
        result = self.handler._process_text("删除看板订阅", "u1", "s1")
        self.assertEqual(result["source"], "dashboard")
        fake.handle.assert_called_once()
        mock_get_skill.assert_called_once_with("删除看板订阅", "u1")

    @mock.patch("knowledge_review.delete_file_for_user")
    def test_delete_learn_unchanged(self, mock_delete):
        """「删除学习 1」→ 无业务领域 → 照旧 knowledge_delete（回归）"""
        mock_delete.return_value = {"status": "ok", "file_name": "a.pdf",
                                    "deleted_chunks": 2, "source_deleted": True}
        result = self.handler._process_text("删除学习 1", "u1", "s1")
        self.assertEqual(result["source"], "knowledge_delete")
        self.assertIn("回复「确认」", result["answer"])

    @mock.patch("knowledge_review.relearn_file_for_user")
    def test_relearn_unchanged(self, mock_relearn):
        """「重新学习 1」→ 照旧 knowledge_relearn（回归）"""
        mock_relearn.return_value = {"status": "ok", "file_name": "a.pdf",
                                     "collection": "standards", "chunk_count": 3}
        result = self.handler._process_text("重新学习 1", "u1", "s1")
        self.assertEqual(result["source"], "knowledge_relearn")
        self.assertIn("回复「确认」", result["answer"])

    def test_multi_domain_clarifies_then_routes(self):
        """跨域歧义句（含故障码 + 看板词，无「学习」动词）→ 反问澄清，不直接删；回复编号路由"""
        result = self.handler._process_text("删除 d4-1 的看板", "u1", "s1")
        self.assertEqual(result["source"], "clarification")
        self.assertIn("1.", result["answer"])
        self.assertIn("d4-1 的看板", result["answer"])
        # 用户回复「1」→ 路由回删除学习确认流程（pending 待确认，未真删）
        result2 = self.handler._process_text("1", "u1", "s1")
        self.assertEqual(result2["source"], "knowledge_delete")
        self.assertIn("回复「确认」", result2["answer"])
        self.assertIsNone(get_clarification("u1"))
        from tools import cancel_pending_operation
        cancel_pending_operation("u1")

    @mock.patch("knowledge_review.delete_file_for_user")
    def test_delete_learn_with_standard_id_proceeds(self, mock_delete):
        """「删除学习 GB/T 34133-2023」→ 单标准领域只是文件名，带「学习」动词 → 照旧删除（v1.12.2 修复）"""
        mock_delete.return_value = {"status": "ok", "file_name": "GB_T_34133-2023.pdf",
                                    "deleted_chunks": 2, "source_deleted": True}
        result = self.handler._process_text("删除学习 GB/T 34133-2023", "u1", "s1")
        self.assertEqual(result["source"], "knowledge_delete")
        self.assertIn("回复「确认」", result["answer"])

    @mock.patch("knowledge_review.relearn_file_for_user")
    def test_relearn_with_pcb_term_proceeds(self, mock_relearn):
        """「重新学习 母线载流计算」→ 单 PCB 领域只是文件名 → 照旧重学（v1.12.2 修复）"""
        mock_relearn.return_value = {"status": "ok", "file_name": "母线载流计算.md",
                                     "collection": "experience_kb", "chunk_count": 3}
        result = self.handler._process_text("重新学习 母线载流计算", "u1", "s1")
        self.assertEqual(result["source"], "knowledge_relearn")
        self.assertIn("回复「确认」", result["answer"])

    def test_clarify_cancel_via_bot(self):
        """澄清反问中用户回「取消」→ 已取消且不执行任何操作

        v1.12.1（M3）：取消走统一 pending 路由（先于 clarify 分派），
        source 为 pending_cancel，澄清被清空。
        """
        result = self.handler._process_text("删除 d4-1 的看板", "u2", "s2")
        self.assertEqual(result["source"], "clarification")
        result2 = self.handler._process_text("取消", "u2", "s2")
        self.assertEqual(result2["source"], "pending_cancel")
        self.assertIn("已取消", result2["answer"])
        self.assertIsNone(get_clarification("u2"))

    @mock.patch("skills.get_matched_skill", return_value=None)
    def test_clarify_word_does_not_block_normal_flow(self, mock_get_skill):
        """澄清 pending 存在但回复无法解析 → 不阻塞、不误路由，pending 保留"""
        ask_clarification("u3", [{"key": "a", "label": "操作A"}])
        result = self.handler._process_text("确认", "u3", "s3")
        # 无看板/工具 pending，「确认」落到兜底，不触碰澄清
        self.assertEqual(result["source"], "fallback")
        self.assertIsNotNone(get_clarification("u3"))
        clear_clarification("u3")


class QueryChainTests(unittest.TestCase):
    """查询盲区（非操作类）：统一查知识库链路，不补前缀枚举"""

    def setUp(self):
        from skills.dingtalk_bot import ErrorQueryHandler
        self.handler = ErrorQueryHandler()
        for uid in _USER_IDS:
            clear_clarification(uid)

    @mock.patch("kb_registry.get_visible_knowledge_bases")
    @mock.patch("tools.kb_search._search_one")
    def test_cqc_routes_to_standards_semantic(self, mock_search, mock_visible):
        """CQC 3310 无前缀枚举 → kb_search 留空库全库搜索仍命中"""
        mock_visible.return_value = [{
            "key": "std", "name": "标准知识库",
            "collection": "standards", "department": "public", "enabled": 1,
        }]
        mock_search.return_value = [{
            "std_id": "CQC 3310-2017", "std_title": "光伏并网逆变器",
            "_match_type": "semantic", "_score": 0.42,
        }]
        from tools.kb_search import execute
        out = json.loads(execute({"query": "CQC 3310 标准是什么"}))
        self.assertTrue(out["found"])
        # 原样 query 进入语义搜索（不依赖前缀枚举即命中库中 CQC 数据）
        self.assertEqual(mock_search.call_args[0][0], "CQC 3310 标准是什么")
        self.assertEqual(out["results"][0]["std_id"], "CQC 3310-2017")
        self.assertEqual(out["results"][0]["kb_name"], "标准知识库")

    @mock.patch("skills.get_matched_skill")
    def test_kanban_settings_query_falls_to_agent(self, mock_get_skill):
        """「看板设置是什么」→ 非操作类 → 让位 Agent（mock 验证不误接订阅管理）"""
        fake = mock.MagicMock()
        fake.name = "agent"
        fake.handle.return_value = {"answer": "由 agent 调 dash_query", "source": "agent"}
        mock_get_skill.return_value = fake
        result = self.handler._process_text("看板设置是什么", "u1", "s1")
        self.assertEqual(result["source"], "agent")
        fake.handle.assert_called_once()


if __name__ == "__main__":
    unittest.main()

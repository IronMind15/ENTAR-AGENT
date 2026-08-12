"""v1.10.2 文件接收处理测试：目录结构（主部门/员工/日期）+ 回执文案"""

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from file_handler import (
    _safe_path_component, _get_user_main_department,
    format_file_received_message, get_upload_dir,
)


class UploadDirTests(unittest.TestCase):
    """目录结构：data/uploads/{主部门}/{员工名字}/{YYYY-MM-DD}/"""

    def test_dir_structure_department_employee_date(self):
        with patch("file_handler._get_user_main_department",
                   return_value="研发中心"):
            user_dir = get_upload_dir("union-1", "张三")
        self.assertTrue(user_dir.name.startswith("2026-"), user_dir.name)
        self.assertEqual("张三", user_dir.parts[-2])
        self.assertEqual("研发中心", user_dir.parts[-3])
        self.assertTrue(user_dir.is_dir())

    def test_no_department_defaults_ungrouped(self):
        with patch("file_handler._get_user_main_department",
                   return_value="未分组"):
            user_dir = get_upload_dir("union-1", "张三")
        self.assertEqual("未分组", user_dir.parts[-3])

    def test_get_upload_dir_creates_today_folder(self):
        with patch("file_handler._get_user_main_department",
                   return_value="研发中心"):
            user_dir = get_upload_dir("union-1", "张三")
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(today, user_dir.name)

    def test_employee_name_sanitized(self):
        """员工名含路径片段/非法字符时被净化，不逃逸目录"""
        with patch("file_handler._get_user_main_department",
                   return_value="研发中心"):
            user_dir = get_upload_dir("union-1", "../../admin")
        self.assertNotIn("..", user_dir.parts)


class SafePathComponentTests(unittest.TestCase):
    def test_blocks_path_escape(self):
        self.assertNotIn("/", _safe_path_component("../../隐藏"))
        self.assertNotIn("\\", _safe_path_component("..\\..\\a"))

    def test_empty_uses_fallback(self):
        self.assertEqual("未分组", _safe_path_component(""))
        self.assertEqual("未分组", _safe_path_component("../"))

    def test_keeps_chinese_and_alnum(self):
        self.assertEqual("研发中心A1", _safe_path_component("研发中心 A1"))

    def test_long_component_truncated(self):
        self.assertEqual(60, len(_safe_path_component("长" * 100)))


class FormatMessageTests(unittest.TestCase):
    def _ok(self, file_name="测试.pdf", size=1024):
        return {"success": True, "file_name": file_name, "file_size": size,
                "file_path": "/x"}

    def test_received_message_suggests_learn(self):
        """v1.11.0 回执主动推荐入库，回复「入库/确认」即可"""
        msg = format_file_received_message(self._ok(), auto_process=True)
        self.assertIn("要不要我把这份文件入库知识库", msg)
        self.assertIn("入库", msg)
        self.assertIn("我的文件", msg)
        # 不含旧审核流程关键字（申请编号 / 审核人同意 / 已提交）
        self.assertNotIn("申请编号", msg)
        self.assertNotIn("审核人", msg)
        self.assertNotIn("已提交", msg)

    def test_unsupported_type_message(self):
        msg = format_file_received_message(self._ok("音频.mp3"), auto_process=True)
        self.assertIn("不支持", msg)

    def test_txt_is_learnable(self):
        """v1.11.11 .txt 纯文本也能「帮我学习」入库"""
        msg = format_file_received_message(self._ok("说明.txt"), auto_process=True)
        self.assertIn("要不要我把这份文件入库知识库", msg)

    def test_legacy_doc_hint(self):
        """v1.11.11 老版 .doc 引导另存为 .docx"""
        msg = format_file_received_message(self._ok("报告.doc"), auto_process=True)
        self.assertIn("另存为 .docx", msg)

    def test_legacy_ppt_hint(self):
        """v1.11.11 老版 .ppt 引导另存为 .pptx"""
        msg = format_file_received_message(self._ok("汇报.ppt"), auto_process=True)
        self.assertIn("另存为 .pptx", msg)

    def test_archive_hint(self):
        """v1.11.11 压缩包引导解压后再发"""
        msg = format_file_received_message(self._ok("资料.zip"), auto_process=True)
        self.assertIn("解压后", msg)

    def test_failure_message(self):
        msg = format_file_received_message(
            {"success": False, "file_name": "a.pdf", "file_size": 0,
             "message": "文件下载超时，请重试"})
        self.assertIn("下载超时", msg)


class ReceivedMessageTests(unittest.TestCase):
    """v1.11.0 回执含「推荐入库」问句"""

    def test_recommend_learn_question_for_learnable_file(self):
        msg = format_file_received_message(
            {"success": True, "file_name": "报告.pdf", "file_size": 2048,
             "file_path": "/x/报告.pdf", "message": "保存成功"},
            auto_process=True)
        self.assertIn("要不要我把这份文件入库知识库", msg)
        self.assertIn("入库", msg)

    def test_image_file_hint(self):
        """v1.11.11 图片当文件发 → 提示正在识别内容（不再笼统说不支持）"""
        msg = format_file_received_message(
            {"success": True, "file_name": "图片.png", "file_size": 1024,
             "file_path": "/x/图片.png", "message": "保存成功"},
            auto_process=True)
        self.assertIn("正在识别内容", msg)

    def test_failure_message(self):
        msg = format_file_received_message(
            {"success": False, "file_name": "", "file_size": 0,
             "file_path": None, "message": "下载超时"})
        self.assertIn("下载超时", msg)


if __name__ == "__main__":
    unittest.main()

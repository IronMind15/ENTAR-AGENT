"""
image_describe 工具测试（千问视觉识图）

覆盖：工具注册、缺参数、文件不存在、路径白名单、magic bytes 格式识别、
不支持格式、缺 key、请求体组装（base64 data URI / question 覆盖）、
5xx 重试、超时降级、空 choices 降级。
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_ROOT / "scripts"

from scripts.tools import image_describe, get_tool_names, get_tool_definitions  # noqa: E402


class _FakeResp:
    """模拟 httpx.Response"""

    def __init__(self, status_code, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text or json.dumps(json_data, ensure_ascii=False) if json_data else text

    def json(self):
        return self._json


# 各种格式的 magic bytes 头（+ 尾部填充，模拟真实图片字节）
MAGIC = {
    "png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 20,
    "jpeg": b"\xff\xd8\xff\xe0" + b"\x00" * 20,
    "gif": b"GIF89a" + b"\x00" * 20,
    "webp": b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 20,
    "bmp": b"BM" + b"\x00" * 20,
}


class DescribeImageToolTests(unittest.TestCase):
    """image_describe 工具测试"""

    def setUp(self):
        # 在真实上传目录下建临时子目录，保证路径白名单校验通过
        upload_root = image_describe._UPLOAD_ROOT
        upload_root.mkdir(parents=True, exist_ok=True)
        import tempfile
        self.tmp = tempfile.TemporaryDirectory(dir=str(upload_root))
        self.tmp_path = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _write_image(self, magic_bytes, name="img.png"):
        p = self.tmp_path / name
        p.write_bytes(magic_bytes)
        return str(p)

    # ── 注册 ──
    def test_tool_registered(self):
        self.assertIn("image_describe", get_tool_names())
        defs = get_tool_definitions()
        item = next(d for d in defs if d["function"]["name"] == "image_describe")
        self.assertEqual(item["function"]["parameters"]["required"], ["image_path"])

    # ── 参数校验 ──
    def test_missing_image_path(self):
        out = json.loads(image_describe.execute({}))
        self.assertIn("image_path", out.get("error", ""))

    def test_file_not_found(self):
        out = json.loads(image_describe.execute({"image_path": "/no/such/foo.png"}))
        self.assertIn("不存在", out.get("error", ""))

    def test_path_outside_upload_root(self):
        # 传 scripts/config.py（不在上传目录内）应被白名单拦截
        out = json.loads(image_describe.execute({"image_path": str(SCRIPTS_DIR / "config.py")}))
        self.assertIn("上传目录", out.get("error", ""))

    def test_filename_lookup_in_uploads(self):
        """v1.11.5：LLM 上下文只有文件名（image_1.png，三段式路径不全）时，
        按文件名在上传目录递归查找兜底"""
        nested = self.tmp_path / "研发中心" / "张三" / "2026-08-11"
        nested.mkdir(parents=True, exist_ok=True)
        img = nested / "image_1.png"
        img.write_bytes(MAGIC["png"])
        with mock.patch.object(image_describe, "_UPLOAD_ROOT", self.tmp_path), \
             mock.patch.object(image_describe._HTTP_CLIENT, "post") as mpost:
            mpost.return_value = _FakeResp(200, {"choices": [{"message": {"content": "电路板特写"}}]})
            out = json.loads(image_describe.execute({"image_path": "image_1.png"}))
        self.assertEqual(out["description"], "电路板特写")

    # ── 格式识别 ──
    def test_mime_detection(self):
        for mime, magic in MAGIC.items():
            self.assertEqual(image_describe.detect_image_mime(magic), mime, mime)
        self.assertIsNone(image_describe.detect_image_mime(b"notanimage"))
        self.assertIsNone(image_describe.detect_image_mime(b""))

    def test_unsupported_format(self):
        p = self._write_image(b"plain text content, not an image")
        out = json.loads(image_describe.execute({"image_path": p}))
        self.assertIn("无法识别", out.get("error", ""))

    # ── 缺 key ──
    def test_missing_api_key(self):
        p = self._write_image(MAGIC["png"])
        with mock.patch.object(image_describe._HTTP_CLIENT, "post") as mpost, \
             mock.patch.object(image_describe, "DASHSCOPE_API_KEY", ""):
            out = json.loads(image_describe.execute({"image_path": p}))
            self.assertIn("未配置", out.get("error", ""))
            mpost.assert_not_called()

    # ── 请求体组装 ──
    def test_request_body_assembled(self):
        p = self._write_image(MAGIC["png"])
        with mock.patch.object(image_describe._HTTP_CLIENT, "post") as mpost:
            mpost.return_value = _FakeResp(200, {"choices": [{"message": {"content": "一张电路板照片"}}]})
            out = json.loads(image_describe.execute({"image_path": p}))
            self.assertEqual(out["description"], "一张电路板照片")
            # 验证请求体
            req_body = mpost.call_args[1]["json"]
            self.assertEqual(req_body["model"], image_describe.VISION_MODEL)
            content = req_body["messages"][0]["content"]
            self.assertEqual(content[0]["type"], "image_url")
            self.assertTrue(content[0]["image_url"]["url"].startswith("data:image/png;base64,"))
            self.assertEqual(content[1]["text"], "请描述这张图片的内容")

    def test_question_override(self):
        p = self._write_image(MAGIC["jpeg"], "photo.jpg")
        with mock.patch.object(image_describe._HTTP_CLIENT, "post") as mpost:
            mpost.return_value = _FakeResp(200, {"choices": [{"message": {"content": "参数表"}}]})
            image_describe.execute({"image_path": p, "question": "图里有什么参数"})
            req_body = mpost.call_args[1]["json"]
            self.assertEqual(req_body["messages"][0]["content"][1]["text"], "图里有什么参数")

    # ── 重试与降级 ──
    def test_retry_on_5xx(self):
        p = self._write_image(MAGIC["png"])
        with mock.patch.object(image_describe._HTTP_CLIENT, "post") as mpost, \
             mock.patch.object(image_describe.time, "sleep"):
            mpost.side_effect = [_FakeResp(500, {}), _FakeResp(500, {}), _FakeResp(500, {})]
            out = json.loads(image_describe.execute({"image_path": p}))
            self.assertEqual(mpost.call_count, 3)
            self.assertIn("识别失败", out.get("error", ""))

    def test_success_after_retry(self):
        p = self._write_image(MAGIC["png"])
        with mock.patch.object(image_describe._HTTP_CLIENT, "post") as mpost, \
             mock.patch.object(image_describe.time, "sleep"):
            mpost.side_effect = [_FakeResp(503, {}), _FakeResp(200, {"choices": [{"message": {"content": "成功"}}]})]
            out = json.loads(image_describe.execute({"image_path": p}))
            self.assertEqual(mpost.call_count, 2)
            self.assertEqual(out["description"], "成功")

    def test_empty_choices(self):
        p = self._write_image(MAGIC["png"])
        with mock.patch.object(image_describe._HTTP_CLIENT, "post") as mpost:
            mpost.return_value = _FakeResp(200, {"choices": []})
            out = json.loads(image_describe.execute({"image_path": p}))
            self.assertIn("识别失败", out.get("error", ""))

    def test_request_timeout(self):
        p = self._write_image(MAGIC["png"])
        with mock.patch.object(image_describe._HTTP_CLIENT, "post") as mpost, \
             mock.patch.object(image_describe.time, "sleep"):
            mpost.side_effect = httpx.TimeoutException("timeout")
            out = json.loads(image_describe.execute({"image_path": p}))
            self.assertEqual(mpost.call_count, 3)
            self.assertIn("识别失败", out.get("error", ""))


if __name__ == "__main__":
    unittest.main()

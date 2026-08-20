"""dingtalk_notifier 推送重试测试（v1.12.x 审查 High 7）

_post_with_retry：网络瞬断（ConnectionError/Timeout）与 5xx 自动重试，
4xx 不重试。钉钉 API 偶发断连会让主动推送整条丢失（失败告警只能事后发现），
失败成本远高于重试成本——与 DeepSeek 调用同款重试纪律（v1.2.9）。
"""

import os
import sys
import unittest
from unittest import mock

import requests


PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SCRIPTS_DIR = os.path.join(PROJECT_ROOT, "scripts")

from scripts.dingtalk_notifier import DingTalkNotifier, _RETRY_ATTEMPTS  # noqa: E402


def _resp(status=200, payload=None):
    r = mock.MagicMock(status_code=status)
    if payload is not None:
        r.json.return_value = payload
    if status >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(str(status))
    return r


class RetryTests(unittest.TestCase):
    def _notifier(self):
        return DingTalkNotifier(
            client_id="cid", client_secret="sec",
            api_base="http://api.test", session=mock.MagicMock())

    def test_retries_on_connection_error_then_succeeds(self):
        n = self._notifier()
        n._session.post.side_effect = [requests.ConnectionError("boom"), _resp()]
        with mock.patch("scripts.dingtalk_notifier.time.sleep"):
            resp = n._post_with_retry("http://api.test/x")
        self.assertEqual(n._session.post.call_count, 2)
        self.assertEqual(resp.status_code, 200)

    def test_retries_on_5xx_then_succeeds(self):
        n = self._notifier()
        n._session.post.side_effect = [_resp(500), _resp()]
        with mock.patch("scripts.dingtalk_notifier.time.sleep"):
            resp = n._post_with_retry("http://api.test/x")
        self.assertEqual(n._session.post.call_count, 2)

    def test_no_retry_on_4xx(self):
        n = self._notifier()
        n._session.post.return_value = _resp(400)
        with mock.patch("scripts.dingtalk_notifier.time.sleep"):
            with self.assertRaises(requests.HTTPError):
                n._post_with_retry("http://api.test/x")
        self.assertEqual(n._session.post.call_count, 1)

    def test_gives_up_after_all_attempts(self):
        n = self._notifier()
        n._session.post.side_effect = requests.ConnectionError("boom")
        with mock.patch("scripts.dingtalk_notifier.time.sleep"):
            with self.assertRaises(requests.ConnectionError):
                n._post_with_retry("http://api.test/x")
        self.assertEqual(n._session.post.call_count, _RETRY_ATTEMPTS)

    def test_send_markdown_uses_retry_path(self):
        """端到端：token 获取 + 主动发送都走 _post_with_retry"""
        n = self._notifier()
        n._session.post.side_effect = [
            _resp(200, {"accessToken": "tok", "expireIn": 7200}),
            _resp(200, {}),
        ]
        with mock.patch("scripts.dingtalk_notifier.time.sleep"):
            n.send_markdown_to_users(["u1"], "标题", "正文")
        self.assertEqual(n._session.post.call_count, 2)


if __name__ == "__main__":
    unittest.main()

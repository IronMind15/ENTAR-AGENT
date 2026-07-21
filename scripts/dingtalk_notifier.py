"""钉钉机器人主动单聊发送。

使用企业内部应用的 ClientID/ClientSecret 获取新版 API access token，
再通过机器人批量单聊接口向指定员工 userId（即 staff_id）发送 Markdown。
"""

import json
import logging
import threading
import time
from typing import Iterable

import requests

from config import (
    DINGTALK_API_BASE,
    DINGTALK_CLIENT_ID,
    DINGTALK_CLIENT_SECRET,
)

logger = logging.getLogger("dingtalk_notifier")


class DingTalkNotifier:
    """线程安全的钉钉主动消息客户端。"""

    def __init__(self, client_id: str = DINGTALK_CLIENT_ID,
                 client_secret: str = DINGTALK_CLIENT_SECRET,
                 api_base: str = DINGTALK_API_BASE,
                 session=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_base = api_base.rstrip("/")
        self._session = session or requests
        self._token = ""
        self._token_expires_at = 0.0
        self._lock = threading.Lock()

    def _get_access_token(self) -> str:
        """获取并缓存 access token，提前 60 秒刷新。"""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token

        if not self.client_id or not self.client_secret:
            raise RuntimeError("钉钉 ClientID/ClientSecret 未配置")

        with self._lock:
            if self._token and time.time() < self._token_expires_at - 60:
                return self._token

            response = self._session.post(
                f"{self.api_base}/v1.0/oauth2/accessToken",
                json={
                    "appKey": self.client_id,
                    "appSecret": self.client_secret,
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
            token = data.get("accessToken", "")
            if not token:
                raise RuntimeError(
                    f"获取钉钉 access token 失败: {data.get('message', '返回为空')}"
                )

            expires_in = int(data.get("expireIn", 7200) or 7200)
            self._token = token
            self._token_expires_at = time.time() + expires_in
            return token

    def send_markdown_to_users(self, user_ids: Iterable[str],
                               title: str, text: str) -> dict:
        """以机器人身份主动向 1～20 名员工发送单聊 Markdown。"""
        recipients = list(dict.fromkeys(
            user_id.strip() for user_id in user_ids if user_id.strip()
        ))
        if not recipients:
            raise ValueError("主动消息接收人不能为空")
        if len(recipients) > 20:
            raise ValueError("钉钉机器人单次最多主动发送给 20 人")

        token = self._get_access_token()
        response = self._session.post(
            f"{self.api_base}/v1.0/robot/oToMessages/batchSend",
            headers={
                "x-acs-dingtalk-access-token": token,
                "Content-Type": "application/json",
            },
            json={
                "robotCode": self.client_id,
                "userIds": recipients,
                "msgKey": "sampleMarkdown",
                "msgParam": json.dumps(
                    {"title": title, "text": text}, ensure_ascii=False
                ),
            },
            timeout=10,
        )
        response.raise_for_status()
        data = response.json() if response.content else {}
        if data.get("code") not in (None, "", 0, "0"):
            raise RuntimeError(
                f"钉钉主动消息失败: {data.get('code')} {data.get('message', '')}"
            )
        return data


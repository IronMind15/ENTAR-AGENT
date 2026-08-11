"""
钉钉文档/表格读取客户端（v1.11.0）

能力：
  1. 解析钉钉文档链接（alidocs.dingtalk.com）提取 node_id / sheet_id
  2. 类型探测：AI表格（notable）/ 在线表格（workbook）/ 普通文档（doc）
  3. 读取 AI表格（notable）记录：分页拉取（maxRecords + nextToken）
  4. operatorId = 操作人 unionId：显式传入优先；否则由 staff_id → unionid 兜底

认证：新版 API token（POST /v1.0/oauth2/accessToken），与 dingtalk_notifier 相同逻辑。
  - 默认直连 Session（trust_env=False），避免 Clash 劫持国内 API。

接口版本说明：
  - 方法论文档写的是旧版 /v1.0/aitable/...（GET）；钉钉现行推荐 /v1.0/notable/...
    实际走哪版以实测为准，本文件先按 notable 实现。
  - operatorId 参数位置（query/body）以实测为准，客户端已做两处兼容。

依赖：
  - requests（已有）
  - contact_api（unionId 兜底，仅按需 import）
"""

import json
import logging
import re
import threading
import time
from typing import Optional

import requests

from config import (
    DINGTALK_API_BASE,
    DINGTALK_CLIENT_ID,
    DINGTALK_CLIENT_SECRET,
)

logger = logging.getLogger("dingtalk_doc")


def _looks_like_union_id(op: str) -> bool:
    """合法钉钉 unionId 是纯字母数字（可能含 -_）；SDK 的 sender_id 可能是
    `$:LWCP_v1:$...` 这类会话包装 id，不是 unionId，须排除。"""
    return bool(op) and all(c.isalnum() or c in "-_" for c in op)

# 钉钉在线文档链接：https://alidocs.dingtalk.com/i/nodes/{node_id}[?sheet=...]
_ALIDOCS_NODE_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)?alidocs\.dingtalk\.com/[^?\s]*/nodes/([A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
# sheet/view 参数：?sheet=xxx 或 &sheetId=xxx（AI表格内工作表标识）
_SHEET_PARAM_RE = re.compile(r"[?&](?:sheet|sheetId|viewId)=([A-Za-z0-9_-]+)")

# ── 字段元数据接口候选路径（钉钉接口版本以实测为准，逐个尝试直到成功）──
_FIELD_CANDIDATE_PATHS = [
    "/v1.0/notable/bases/{base}/sheets/{sheet}/fields",
    "/v1.0/notable/bases/{base}/sheets/{sheet}/fields/list",
    "/v1.0/notable/bases/{base}/tables/{sheet}/fields",
]

# ── 在线表格（workbook/.axls）读取候选路径 ──
_WORKBOOK_SHEET_PATHS = [
    "/v1.0/notable/bases/{base}/sheets",            # 同体系（部分 .axls 走 notable）
    "/v1.0/workbook/bases/{base}/sheets",            # 候选
]
_WORKBOOK_RECORD_PATHS = [
    "/v1.0/notable/bases/{base}/sheets/{sheet}/records/list",
    "/v1.0/workbook/bases/{base}/sheets/{sheet}/records/list",
]

# ── 普通在线文档（doc）读取（v1.11.1，需 Storage.File.Read 权限）──
_DOC_BLOCKS_PATH = "/v1.0/doc/suites/documents/{node}/blocks"


class DingTalkDocPermissionError(RuntimeError):
    """应用未开通钉钉文档/表格读取权限，或操作人无文档访问权时抛出。"""


class DingTalkDocClient:
    """线程安全的钉钉文档/表格读取客户端（只读，不写任何数据）"""

    def __init__(self, client_id: str = DINGTALK_CLIENT_ID,
                 client_secret: str = DINGTALK_CLIENT_SECRET,
                 api_base: str = DINGTALK_API_BASE,
                 session=None):
        self.client_id = client_id
        self.client_secret = client_secret
        self.api_base = api_base.rstrip("/")
        if session is not None:
            self._session = session
        else:
            # 默认直连 Session：钉钉是国内服务，强制直连，不跟随系统/环境代理（避免 Clash 劫持）
            self._session = requests.Session()
            self._session.trust_env = False

        # 新版 API token 缓存
        self._token = ""
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()
        # staff_id → unionid 兜底缓存
        self._union_cache: dict[str, str] = {}

    # ── 认证（新版 API token，提前 60s 刷新）──────────────────
    def _get_access_token(self) -> str:
        """获取并缓存新版 API access token，提前 60 秒刷新。"""
        if self._token and time.time() < self._token_expires_at - 60:
            return self._token
        if not self.client_id or not self.client_secret:
            raise RuntimeError("钉钉 ClientID/ClientSecret 未配置")

        with self._token_lock:
            if self._token and time.time() < self._token_expires_at - 60:
                return self._token
            response = self._session.post(
                f"{self.api_base}/v1.0/oauth2/accessToken",
                json={"appKey": self.client_id, "appSecret": self.client_secret},
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

    # ── 链接解析 ────────────────────────────────────────────
    @staticmethod
    def parse_doc_url(url: str) -> Optional[dict]:
        """解析钉钉文档链接，返回 {node_id, sheet_id}；无法识别返回 None。

        支持：
          https://alidocs.dingtalk.com/i/nodes/{node_id}[?sheet={sheet_id}]
          AI表格分享链接的 sheetId 藏在 iframeQuery（URL 编码）里，如
          ?iframeQuery=viewId%3Dxx%26sheetId%3DhERWDMS → 解码后提取
        示例：https://alidocs.dingtalk.com/i/nodes/np9zOoBVBYQe06entLExnXArW1DK0g6l
        """
        if not url:
            return None
        m = _ALIDOCS_NODE_RE.search(url)
        if not m:
            return None
        node_id = m.group(1)
        sheet_id = ""
        sm = _SHEET_PARAM_RE.search(url)
        if sm:
            sheet_id = sm.group(1)
        # iframeQuery 参数里 URL 编码的 sheetId（AI表格分享链接常见）
        if not sheet_id:
            fm = re.search(r"[?&]iframeQuery=([^&\s]+)", url)
            if fm:
                try:
                    import urllib.parse
                    decoded = urllib.parse.unquote(fm.group(1))
                    sm2 = re.search(
                        r"(?:^|[?&])sheetId=([A-Za-z0-9_-]+)", decoded)
                    if sm2:
                        sheet_id = sm2.group(1)
                except Exception:
                    pass
        return {"node_id": node_id, "sheet_id": sheet_id}

    # ── operatorId（unionId）解析 ───────────────────────────
    def _unionid_for(self, staff_id: str) -> str:
        """staff_id(userid) → unionid 兜底查询（旧版 topapi/v2/user/get，缓存）。"""
        if staff_id in self._union_cache:
            return self._union_cache[staff_id]
        try:
            from contact_api import get_contact_client
            detail = get_contact_client().get_user_detail(staff_id)
            unionid = str(detail.get("unionid") or "")
        except Exception as e:
            logger.warning(f"获取用户 unionid 失败(staff={staff_id}): {e}")
            unionid = ""
        if unionid:
            self._union_cache[staff_id] = unionid
        return unionid

    def resolve_operator_id(self, operator_id: str = "", staff_id: str = "") -> str:
        """解析操作人 unionId（operatorId）：
        显式 operator_id 优先（须是合法 unionId——sender_id 可能带 `$:LWCP_v1:$` 包装，
        不是真 unionId，会触发钉钉 paramError-operatorId）；
        否则尝试 staff_id → unionid 兜底（contact_api）。
        """
        if _looks_like_union_id(operator_id):
            return operator_id
        if staff_id:
            unionid = self._unionid_for(staff_id)
            if unionid:
                return unionid
        raise RuntimeError(
            "缺少操作人 unionId（operatorId）：钉钉文档/表格读取需要操作人身份，"
            "请提供用户 unionId 或 staff_id。"
        )

    # ── 基础请求（新版 API，3 次指数退避）────────────────────
    def _request(self, method: str, path: str, body: dict | None = None,
                 operator_id: str = "") -> dict:
        """请求新版 API：operator_id 同时放 query 与 body（两处兼容，实测确定）。
        429/5xx 退避重试；403/权限错误转 DingTalkDocPermissionError。
        """
        last_err: Exception | None = None
        for attempt in range(1, 4):
            token = self._get_access_token()
            params = {}
            if operator_id:
                params["operatorId"] = operator_id
            send_body = dict(body or {})
            if operator_id:
                # 部分接口要求 operatorId 在 body；两处都带以便实测收敛
                send_body.setdefault("operatorId", operator_id)
            try:
                response = self._session.request(
                    method,
                    f"{self.api_base}{path}",
                    headers={
                        "x-acs-dingtalk-access-token": token,
                        "Content-Type": "application/json",
                    },
                    params=params,
                    json=send_body,
                    timeout=15,
                )
            except requests.RequestException as e:
                last_err = e
                time.sleep(0.5 * attempt)
                continue

            if response.status_code == 403:
                raise DingTalkDocPermissionError(
                    "应用未开通钉钉文档/AI表格读取权限（如 Document.Notable.Read），"
                    "请在钉钉开放平台为应用授权并发布版本；同时确认操作人对目标文档有访问权限。"
                )
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(0.5 * attempt)  # 0.5s / 1s 退避
                continue
            if response.status_code >= 400:
                # 其他 4xx 不重试，直接报错，避免接口不存在被静默当成"无数据"
                raise RuntimeError(
                    f"钉钉文档接口 HTTP {response.status_code}: {path} {response.text[:200]}"
                )
            try:
                data = response.json()
            except ValueError as e:
                raise RuntimeError(
                    f"钉钉文档接口返回非 JSON（HTTP {response.status_code}）"
                ) from e
            return data
        raise RuntimeError(f"钉钉文档请求多次失败（{path}）：{last_err}")

    # ── 读取 AI表格（notable）───────────────────────────────
    def list_sheets(self, base_id: str, operator_id: str = "") -> list[dict]:
        """列出 AI表格（Base）下全部工作表 → [{"sheetId","name"}]"""
        data = self._request(
            "GET",
            f"/v1.0/notable/bases/{base_id}/sheets",
            operator_id=operator_id,
        )
        # 实测：钉钉 notable 返回 {"value":[{name,id}]}，不是 {"sheets":[...]}
        sheets = (data.get("sheets") or data.get("result", {}).get("sheets")
                  or data.get("value") or data.get("items") or [])
        out = []
        for s in sheets:
            sid = str(s.get("sheetId") or s.get("sheet_id") or s.get("id") or "")
            if sid:
                out.append({"sheetId": sid, "name": str(s.get("name") or "")})
        return out

    def read_notable_records(self, base_id: str, sheet_id: str,
                             operator_id: str = "",
                             max_records: int = 100) -> list[dict]:
        """读取 AI表格工作表全部记录（分页 maxRecords + nextToken 续取）。

        Returns:
            记录列表，每条含 fields/cells 等原始字段
        """
        records: list[dict] = []
        next_token = ""
        while True:
            body = {"maxRecords": max_records}
            if next_token:
                body["nextToken"] = next_token
            data = self._request(
                "POST",
                f"/v1.0/notable/bases/{base_id}/sheets/{sheet_id}/records/list",
                body=body,
                operator_id=operator_id,
            )
            batch = (data.get("records") or data.get("result", {}).get("records")
                     or data.get("items") or data.get("value") or [])
            records.extend(batch)
            has_more = bool(data.get("hasMore")
                            or data.get("result", {}).get("hasMore"))
            next_token = (data.get("nextToken") or data.get("result", {}).get("nextToken")
                          or data.get("nextPageToken") or "")
            if not has_more or not next_token:
                break
            if len(records) > 10000:  # 安全上限，防异常游标死循环
                logger.warning("AI表格记录数超 10000，已截断")
                break
        return records

    def list_fields(self, base_id: str, sheet_id: str,
                    operator_id: str = "") -> list[dict]:
        """读取 AI表格字段元数据 → [{"fieldId","name","type"}]

        多候选路径逐个尝试（接口版本以实测为准）；除 403 权限错外任何失败
        返回 []，由调用方降级为 field_id 展示（不阻塞链路）。
        """
        for path_tpl in _FIELD_CANDIDATE_PATHS:
            path = path_tpl.format(base=base_id, sheet=sheet_id)
            try:
                data = self._request("GET", path, operator_id=operator_id)
            except DingTalkDocPermissionError:
                raise  # 权限错不降级，直接提示
            except Exception as e:
                logger.debug(f"字段接口探测失败({path}): {e}")
                continue
            fields = (data.get("fields") or data.get("result", {}).get("fields")
                      or data.get("items") or data.get("records")
                      or data.get("value") or [])
            if not fields:
                continue
            out = []
            for f in fields:
                fid = str(f.get("fieldId") or f.get("field_id") or f.get("id") or "")
                if fid:
                    out.append({
                        "fieldId": fid,
                        "name": str(f.get("name") or fid),
                        "type": str(f.get("type") or ""),
                    })
            return out
        return []

    def read_notable_field_names(self, base_id: str, sheet_id: str,
                                 operator_id: str = "") -> dict:
        """字段元数据 → {field_id: 中文名}；失败返回 {}（不抛异常）"""
        out: dict[str, str] = {}
        try:
            for f in self.list_fields(base_id, sheet_id, operator_id):
                out[f["fieldId"]] = f["name"]
        except DingTalkDocPermissionError:
            raise
        except Exception:
            pass
        return out

    def _notable_sheet_name(self, base_id: str, sheet_id: str,
                            operator_id: str = "") -> str:
        """取 AI表格工作表名（如「数据表」）；失败返回 ""（不抛异常）"""
        try:
            data = self._request(
                "GET",
                f"/v1.0/notable/bases/{base_id}/sheets/{sheet_id}",
                operator_id=operator_id)
            return str(data.get("name") or "")
        except Exception:
            return ""

    # ── 读取在线表格（workbook/.axls）────────────────────────
    def list_workbook_sheets(self, base_id: str, operator_id: str = "") -> list[dict]:
        """列在线表格工作表 → [{"sheetId","name"}]；全部候选失败抛异常（调用方降级）"""
        last_err: Exception | None = None
        for path_tpl in _WORKBOOK_SHEET_PATHS:
            path = path_tpl.format(base=base_id)
            try:
                data = self._request("GET", path, operator_id=operator_id)
            except DingTalkDocPermissionError:
                raise
            except Exception as e:
                last_err = e
                continue
            sheets = (data.get("sheets") or data.get("result", {}).get("sheets")
                      or data.get("items") or data.get("value") or [])
            if sheets:
                out = []
                for s in sheets:
                    sid = str(s.get("sheetId") or s.get("sheet_id") or s.get("id") or "")
                    if sid:
                        out.append({"sheetId": sid, "name": str(s.get("name") or "")})
                return out
        raise RuntimeError(
            f"在线表格读取暂不可用"
            + (f"（{last_err}）" if last_err else "，请先『帮我学习』入库"))

    def read_workbook_records(self, base_id: str, sheet_id: str,
                              operator_id: str = "",
                              max_records: int = 100) -> list[dict]:
        """读取在线表格工作表记录（分页，同 notable 语义）"""
        last_err: Exception | None = None
        for path_tpl in _WORKBOOK_RECORD_PATHS:
            path = path_tpl.format(base=base_id, sheet=sheet_id)
            try:
                data = self._request("POST", path, operator_id=operator_id,
                                     body={"maxRecords": max_records})
            except Exception as e:
                last_err = e
                continue
            records = (data.get("records") or data.get("result", {}).get("records")
                       or data.get("items") or data.get("value") or [])
            if records or not last_err:
                return records
        raise RuntimeError(
            f"在线表格读取暂不可用"
            + (f"（{last_err}）" if last_err else "，请先『帮我学习』入库"))

    # ── 读取普通在线文档（doc）────────────────────────────────
    def read_doc_content(self, node_id: str, operator_id: str = "",
                         max_blocks: int = 500) -> dict:
        """读取普通在线文档（doc）正文 → blocks 逐块转 Markdown。

        Args:
            node_id: 文档节点 ID
            operator_id: 操作人 unionId
            max_blocks: 内容块安全上限（钉钉一次性返回，无分页字段）

        Returns:
            {"ok": bool, "markdown": str, "blocks": list, "message": str}
        """
        try:
            data = self._request(
                "GET",
                _DOC_BLOCKS_PATH.format(node=node_id),
                operator_id=operator_id,
            )
        except DingTalkDocPermissionError:
            raise
        except Exception as e:
            return {"ok": False, "markdown": "", "blocks": [],
                    "message": str(e)[:200]}

        blocks = (data.get("result") or {}).get("data") or []
        if not blocks:
            return {"ok": False, "markdown": "", "blocks": [],
                    "message": "文档无内容（0 个内容块）"}
        blocks = blocks[:max_blocks]
        md = self._blocks_to_markdown(blocks)
        return {
            "ok": True, "markdown": md, "blocks": blocks,
            "message": f"读取普通文档成功，{len(blocks)} 个内容块",
        }

    @staticmethod
    def _blocks_to_markdown(blocks: list[dict]) -> str:
        """blocks → Markdown 全文：段落原文，表格转 Markdown 表格。

        实测结构：
          {"blockType": "paragraph", "paragraph": {"text": "..."}}
          {"blockType": "table", "table": {"cells": [[行], [行], ...]}}
        """
        parts: list[str] = []
        for b in blocks:
            btype = b.get("blockType")
            if btype == "paragraph":
                text = (b.get("paragraph") or {}).get("text", "")
                if text and text.strip():
                    parts.append(text.strip())
                    parts.append("")
            elif btype == "table":
                cells = (b.get("table") or {}).get("cells") or []
                if cells:
                    parts.append(DingTalkDocClient._table_to_markdown(cells))
                    parts.append("")
        return "\n".join(parts).strip()

    @staticmethod
    def _table_to_markdown(cells: list[list]) -> str:
        """二维 cells → Markdown 表格（单元格换行转 <br>，保持单行）。"""
        if not cells:
            return ""
        max_cols = max((len(r) for r in cells), default=0)
        if max_cols == 0:
            return ""

        def _row(r: list) -> str:
            padded = list(r) + [""] * (max_cols - len(r))
            cleaned = [
                str(v or "").replace("\n", "<br>").replace("|", "\\|").strip()
                for v in padded
            ]
            return "| " + " | ".join(cleaned) + " |"

        lines = [_row(cells[0])]
        lines.append("| " + " | ".join(["---"] * max_cols) + " |")
        lines.extend(_row(r) for r in cells[1:])
        return "\n".join(lines)

    def _doc_blocks_to_records(self, blocks: list[dict]) -> list[dict]:
        """blocks → 逐块 dict 列表（供 bot 预览 / records_count / 看板采集）。

        每个 block 一条：段落 → {"类型": "段落", "内容": text}；
        表格 → {"类型": "表格N", "内容": markdown 表格}。
        """
        records: list[dict] = []
        table_idx = 0
        for b in blocks:
            btype = b.get("blockType")
            if btype == "paragraph":
                text = (b.get("paragraph") or {}).get("text", "")
                if text and text.strip():
                    records.append({"类型": "段落", "内容": text.strip()})
            elif btype == "table":
                cells = (b.get("table") or {}).get("cells") or []
                if cells:
                    table_idx += 1
                    records.append({
                        "类型": f"表格{table_idx}",
                        "内容": self._table_to_markdown(cells),
                    })
        return records

    # ── 类型探测与统一入口 ───────────────────────────────────
    def detect_kind(self, node_id: str, operator_id: str = "") -> str:
        """探测文档类型：先按 notable（AI表格）试探，报类型不符再回退。

        返回：'notable' | 'workbook' | 'doc' | 'unknown'
        """
        # 1. 有 sheet 参数 → 大概率 AI表格
        # 2. 先尝试列 sheets（notable 特有接口）
        try:
            sheets = self.list_sheets(node_id, operator_id)
            if sheets:
                return "notable"
        except DingTalkDocPermissionError:
            raise  # 权限错误不降级，直接提示
        except Exception as e:
            msg = str(e)
            if "notable" in msg or "Target document" in msg or "base" in msg.lower():
                logger.info(f"notable 探测不符（{msg[:100]}），回退判断类型")
            else:
                logger.debug(f"notable 探测异常（继续判断）: {msg}")
        # 2. 回退试探 workbook（在线表格）
        try:
            sheets = self.list_workbook_sheets(node_id, operator_id)
            if sheets:
                logger.info(f"workbook 探测命中：{len(sheets)} 张工作表")
                return "workbook"
        except DingTalkDocPermissionError:
            raise
        except Exception as e:
            logger.info(f"workbook 探测不符: {str(e)[:120]}")
        # 3. 回退试探 doc（普通在线文档，需 Storage.File.Read 权限）
        try:
            doc = self.read_doc_content(node_id, operator_id)
            if doc.get("ok"):
                logger.info(f"doc 探测命中：{len(doc.get('blocks') or [])} 个内容块")
                return "doc"
        except DingTalkDocPermissionError:
            raise
        except Exception as e:
            logger.info(f"doc 探测不符: {str(e)[:120]}")
        # 4. 兜底：无强证据时返回 unknown，由调用方决定
        logger.info(f"文档类型探测结果: unknown（node={node_id[:12]}）")
        return "unknown"

    def read_document(self, url: str, operator_id: str = "",
                      staff_id: str = "", max_records: int = 100) -> dict:
        """统一入口：解析链接 → 解析操作人 → 探测类型 → 读取。

        Returns:
            {"ok": bool, "kind": str, "node_id": str, "sheet_id": str,
             "records": [...], "message": str}
        """
        parsed = self.parse_doc_url(url)
        if not parsed:
            return {"ok": False, "message": "无法识别的钉钉文档链接，请确认格式"}
        node_id = parsed["node_id"]
        sheet_id = parsed["sheet_id"]
        operator = self.resolve_operator_id(operator_id, staff_id)

        # 优先：URL 带 sheetId（AI表格分享特征）→ 直接按 notable 读取，
        # 绕开 list_sheets（该接口对部分 AI表格返回 400）
        if sheet_id:
            try:
                records = self.read_notable_records(
                    node_id, sheet_id, operator, max_records=max_records)
                field_names = self.read_notable_field_names(
                    node_id, sheet_id, operator)
                sheet_name = self._notable_sheet_name(
                    node_id, sheet_id, operator)
                return {
                    "ok": True, "kind": "notable", "node_id": node_id,
                    "sheet_id": sheet_id, "records": records,
                    "field_names": field_names, "sheet_name": sheet_name,
                    "message": f"读取 AI表格成功，共 {len(records)} 条记录",
                }
            except DingTalkDocPermissionError:
                raise
            except Exception as e:
                logger.info(f"带 sheetId 的 notable 读取失败（{str(e)[:100]}），回退类型探测")

        kind = self.detect_kind(node_id, operator)
        if kind == "notable":
            # 有 sheet 直接用；否则取第一张表
            target_sheet = sheet_id
            if not target_sheet:
                sheets = self.list_sheets(node_id, operator)
                if not sheets:
                    return {"ok": False, "kind": "notable", "node_id": node_id,
                            "message": "AI表格无可用工作表"}
                target_sheet = sheets[0]["sheetId"]
            records = self.read_notable_records(
                node_id, target_sheet, operator, max_records=max_records)
            field_names = self.read_notable_field_names(
                node_id, target_sheet, operator)
            return {
                "ok": True,
                "kind": "notable",
                "node_id": node_id,
                "sheet_id": target_sheet,
                "records": records,
                "field_names": field_names,  # {field_id: 中文名}，失败为 {}
                "message": f"读取 AI表格成功，共 {len(records)} 条记录",
            }
        if kind == "workbook":
            try:
                target_sheet = sheet_id
                if not target_sheet:
                    sheets = self.list_workbook_sheets(node_id, operator)
                    if not sheets:
                        return {"ok": False, "kind": "workbook", "node_id": node_id,
                                "message": "在线表格无可用工作表"}
                    target_sheet = sheets[0]["sheetId"]
                records = self.read_workbook_records(
                    node_id, target_sheet, operator, max_records=max_records)
                return {
                    "ok": True,
                    "kind": "workbook",
                    "node_id": node_id,
                    "sheet_id": target_sheet,
                    "records": records,
                    "field_names": {},
                    "message": f"读取在线表格成功，共 {len(records)} 条记录",
                }
            except DingTalkDocPermissionError:
                raise
            except Exception as e:
                return {
                    "ok": False, "kind": "workbook", "node_id": node_id,
                    "message": f"在线表格读取暂不可用，可先『帮我学习』入库（{str(e)[:100]}）",
                }
        if kind == "doc":
            try:
                doc = self.read_doc_content(node_id, operator)
                if not doc.get("ok"):
                    return {
                        "ok": False, "kind": "doc", "node_id": node_id,
                        "message": f"普通文档读取失败（{doc.get('message', '')}），可先『帮我学习』入库",
                    }
                records = self._doc_blocks_to_records(doc.get("blocks") or [])
                return {
                    "ok": True, "kind": "doc", "node_id": node_id, "sheet_id": "",
                    "records": records,
                    "field_names": {},
                    "markdown": doc.get("markdown", ""),
                    "sheet_name": "",
                    "message": f"读取普通文档成功，共 {len(records)} 个内容块",
                }
            except DingTalkDocPermissionError:
                raise
            except Exception as e:
                return {
                    "ok": False, "kind": "doc", "node_id": node_id,
                    "message": f"普通文档读取暂不可用，可先『帮我学习』入库（{str(e)[:100]}）",
                }
        return {
            "ok": False,
            "kind": kind,
            "node_id": node_id,
            "message": f"暂不支持该文档类型（{kind}）做看板数据源，可先『帮我学习』入库",
        }


# ── 模块级单例（双检锁，多线程安全）────────────────────────
_client: DingTalkDocClient | None = None
_client_lock = threading.Lock()


def get_doc_client() -> DingTalkDocClient:
    """获取钉钉文档客户端单例（懒加载 + 双检锁）。"""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = DingTalkDocClient()
    return _client

"""
钉钉文档读取探测脚本（v1.11.0 实施验证用）

独立运行，验证恩特小助手能否读取指定的钉钉文档/AI表格，并探测真实接口路径。

用法：
  python scripts/probe_dingtalk_doc.py <文档URL> [<URL2>...] [--operator <unionId>] [--raw]

  - <URL>       钉钉在线文档/表格分享链接（alidocs.dingtalk.com），可多个
  - --operator  操作人 unionId（钉钉 Stream 的 sender_id 即 unionId；真 unionId
                 是纯字母数字）。不传则尝试由配置的 staff_id 兜底查询；
                 都拿不到时读取会失败（钉钉要求 operatorId）
  - --raw       逐接口路径探测模式：对 notable/workbook/fields 各候选路径
                逐个发请求，输出每个路径的 HTTP 状态码 + 响应前 200 字，
                用于确定钉钉真实接口（--raw 下只读，安全）

默认模式（无 --raw）：解析 → 类型探测 → 读取 → 记录摘要（前 5 条 + 总数）。

需要 local_config.py 已配置 DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET。
"""

import argparse
import sys
from pathlib import Path

# Windows 控制台 GBK 打不出 emoji/部分 Unicode，强制 UTF-8 输出（替换不报错）
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.dingtalk_doc_client import (  # noqa: E402
    get_doc_client, DingTalkDocClient,
    _WORKBOOK_SHEET_PATHS, _WORKBOOK_RECORD_PATHS, _FIELD_CANDIDATE_PATHS,
)


def _summarize(records: list[dict], limit: int = 5) -> str:
    """把记录转成可读摘要（前 limit 条 + 字段名 + 总数）。"""
    if not records:
        return "（无记录）"
    lines = []
    for i, rec in enumerate(records[:limit], 1):
        cells = rec.get("cells") or rec.get("fields") or rec.get("record") or rec
        if isinstance(cells, dict):
            parts = [f"{k}={v}" for k, v in list(cells.items())[:6]]
        else:
            parts = [str(cells)[:120]]
        lines.append(f"  {i}. {', '.join(parts)}")
    if len(records) > limit:
        lines.append(f"  … 共 {len(records)} 条，仅显示前 {limit} 条")
    fields_hint = ""
    if records:
        first = records[0].get("cells") or records[0].get("fields") or {}
        if isinstance(first, dict) and first:
            fields_hint = f"字段: {list(first.keys())}"
    return "\n".join(lines) + ("\n" + fields_hint if fields_hint else "")


def _probe_path(client, method: str, path: str, operator_id: str = "",
                body: dict | None = None) -> tuple[int, str]:
    """直接发请求探测单个接口路径（只读）。返回 (HTTP 状态码, 响应前 200 字)。

    用 client 的 token + session 发请求，避免 _request 的异常包装吞掉状态码。
    """
    try:
        token = client._get_access_token()
        params = {"operatorId": operator_id} if operator_id else None
        resp = client._session.request(
            method,
            f"{client.api_base}{path}",
            headers={
                "x-acs-dingtalk-access-token": token,
                "Content-Type": "application/json",
            },
            params=params,
            json=dict(body or {}),
            timeout=15,
        )
        return resp.status_code, resp.text[:200].replace("\n", " ")
    except Exception as e:
        return 0, f"{type(e).__name__}: {str(e)[:150]}"


def _resolve_operator(client, operator_id: str = "") -> str:
    """解析操作人 unionId；拿不到返回空串（部分接口可能不需要）。"""
    if operator_id:
        return operator_id
    try:
        return client.resolve_operator_id(operator_id, "")
    except Exception as e:
        print(f"  [!] 无操作人 unionId: {e}")
        return ""


def probe_raw(url: str, operator_id: str = "") -> int:
    """逐接口路径探测：notable / workbook / fields 候选路径逐个发请求。"""
    client = get_doc_client()
    parsed = DingTalkDocClient.parse_doc_url(url)
    print("=" * 66)
    if not parsed:
        print("  [FAIL] 无法识别的钉钉文档链接")
        return 1
    node = parsed["node_id"]
    sheet = parsed["sheet_id"]
    print(f"① 链接解析: node_id={node}  sheet_id={sheet or '（未指定）'}")

    op = _resolve_operator(client, operator_id)
    print(f"② 操作人: {'unionId=' + op[:16] + '…' if op else '（未解析到，接口可能报缺 operatorId）'}")

    # 待探测路径列表：(method, path, body?)
    paths: list[tuple[str, str, dict | None]] = []
    # notable 基础
    paths.append(("GET", f"/v1.0/notable/bases/{node}", None))
    paths.append(("GET", f"/v1.0/notable/bases/{node}/sheets", None))
    if sheet:
        paths.append(("GET", f"/v1.0/notable/bases/{node}/sheets/{sheet}", None))
        paths.append(("POST", f"/v1.0/notable/bases/{node}/sheets/{sheet}/records/list",
                      {"maxRecords": 5}))
        for tpl in _FIELD_CANDIDATE_PATHS:
            paths.append(("GET", tpl.format(base=node, sheet=sheet), None))
    # workbook 候选
    for tpl in _WORKBOOK_SHEET_PATHS:
        paths.append(("GET", tpl.format(base=node), None))
    if sheet:
        for tpl in _WORKBOOK_RECORD_PATHS:
            paths.append(("POST", tpl.format(base=node, sheet=sheet), {"maxRecords": 5}))

    print("-" * 66)
    print(f"③ 逐接口探测（共 {len(paths)} 个路径）")
    for method, path, body in paths:
        status, text = _probe_path(client, method, path, op, body)
        if 200 <= status < 300:
            mark = "[OK]"
        elif status in (400, 404, 405):
            mark = "[!]"  # 接口路径不对 / 类型不符（预期中的候选失败）
        elif status == 403:
            mark = "[PERM]"  # 权限问题，需在开放平台授权
        else:
            mark = "[FAIL]"
        print(f"  {mark} {method:4} {path}")
        print(f"       -> HTTP {status}  {text}")
    print("=" * 66)
    print("解读：[OK]=接口可用；[!]=路径不对或文档类型不符（候选失败，预期）；"
          "[PERM]=权限未开；[FAIL]=其他错误")
    print("把 [OK] 的路径记下来，对照 dingtalk_doc_client.py 里的候选路径回填。")
    return 0


def probe_read(url: str, operator_id: str = "") -> int:
    """全链路读取：解析 → 类型探测 → 读取 → 摘要。"""
    client = get_doc_client()
    parsed = DingTalkDocClient.parse_doc_url(url)
    print("=" * 66)
    print("① 链接解析")
    if not parsed:
        print("  [FAIL] 无法识别的钉钉文档链接")
        return 1
    print(f"  node_id  = {parsed['node_id']}")
    print(f"  sheet_id = {parsed['sheet_id'] or '（未指定，取第一张表）'}")

    print("-" * 66)
    print("② 读取文档")
    op = _resolve_operator(client, operator_id)
    if not op:
        print("  [FAIL] 缺少操作人 unionId，读取失败（请用 --operator 传真 unionId）")
        return 1
    try:
        result = client.read_document(url, operator_id=op)
    except Exception as e:
        print(f"  [FAIL] 读取失败: {e}")
        return 1

    print(f"  {result.get('message')}")
    if not result.get("ok"):
        print(f"  kind={result.get('kind')}")
        return 1

    print("-" * 66)
    print(f"③ 数据摘要（kind={result.get('kind')}）")
    print(_summarize(result.get("records") or []))
    if result.get("sheet_name"):
        print(f"  表名: {result['sheet_name']}")
    print("=" * 66)
    print("[OK] 读取成功")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="钉钉文档/AI表格读取探测：默认全链路读取，--raw 逐接口探测",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n\n", 1)[1] if "\n\n" in __doc__ else "",
    )
    ap.add_argument("urls", nargs="+", help="钉钉文档分享链接（可多个）")
    ap.add_argument("--operator", default="", help="操作人 unionId（真 unionId，纯字母数字）")
    ap.add_argument("--raw", action="store_true",
                    help="逐接口路径探测模式（找真实接口路径）")
    args = ap.parse_args()

    code = 0
    for url in args.urls:
        code |= probe_raw(url, args.operator) if args.raw else probe_read(url, args.operator)
    return code


if __name__ == "__main__":
    sys.exit(main())

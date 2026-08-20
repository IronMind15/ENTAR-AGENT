"""钉钉文档接口路径探测（v1.11.0 实测用）

对指定 node_id 尝试多个候选接口路径，打印状态码/响应，收敛真实接口。

用法：
  python scripts/probe_paths.py <node_id> [operator_id]
"""

import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from scripts.dingtalk_doc_client import DingTalkDocClient  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print("用法: python scripts/probe_paths.py <node_id> [operator_id]")
        return 2
    node = sys.argv[1]
    operator = sys.argv[2] if len(sys.argv) > 2 else ""
    client = DingTalkDocClient()

    candidates = [
        ("GET", f"/v1.0/notable/bases/{node}/sheets"),
        ("GET", f"/v1.0/aitable/bases/{node}/sheets"),
        ("GET", f"/v1.0/notable/bases/{node}/tables"),
        ("GET", f"/v1.0/notable/bases/{node}/views"),
        ("GET", f"/v1.0/doc/nodes/{node}"),
        ("GET", f"/v1.0/doc/nodes/{node}/info"),
        ("GET", f"/v1.0/workbook/bases/{node}/sheets"),
        ("GET", f"/v1.0/table/bases/{node}/sheets"),
        ("POST", f"/v1.0/notable/bases/{node}/sheets/list"),
        ("POST", f"/v1.0/doc/nodes/{node}/content"),
    ]
    print(f"目标 node: {node}")
    print(f"operator: {operator[:20]}...")
    print("=" * 60)
    for method, path in candidates:
        try:
            data = client._request(method, path, operator_id=operator)
            snippet = json.dumps(data, ensure_ascii=False)[:250]
            print(f"✅ {method} {path}\n   → {snippet}\n")
        except Exception as e:
            msg = str(e)[:150]
            print(f"❌ {method} {path}\n   → {type(e).__name__}: {msg}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
看板数据采集层（v1.11.0）

复用 dingtalk_doc_client 单例，按数据源配置把原始记录从钉钉 AI表格拉下来。
看板数据每次实时拉取、不入 Chroma（与知识库物理隔离）。

- latest_week 模式：list_sheets → find_latest_week_table（数字最大周次）→ 读该表
- fixed 模式：直接读 table_id
- 单源失败记 error 不拖累整体（collect_all 并发采集，确定性返回）
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from .config_model import SourceConfig
from .parser import find_latest_week_table

logger = logging.getLogger("dashboard.collector")


class Collector:
    """多源采集器（依赖注入 client 便于单测）"""

    def __init__(self, client=None):
        self._client = client

    @property
    def client(self):
        if self._client is None:
            from dingtalk_doc_client import get_doc_client
            self._client = get_doc_client()
        return self._client

    def _resolve_operator(self, operator_id: str, staff_id: str) -> str:
        """operator_id 显式优先；否则 staff_id → unionId 兜底（读文档必须 unionId）"""
        if operator_id:
            return operator_id
        if staff_id:
            try:
                return self.client.resolve_operator_id(staff_id=staff_id)
            except Exception:
                return ""
        return ""

    def collect(self, source: SourceConfig, operator_id: str = "",
                staff_id: str = "") -> dict:
        """采集单个数据源

        Args:
            source: 数据源配置
            operator_id: 操作人 unionId（读文档身份；显式优先）
            staff_id: 操作人 staff_id（无 operator_id 时经 contact_api 兜底转 unionId）

        Returns:
            {"source_key", "name", "table_name", "records", "error"}
        """
        try:
            # v1.11.5：空 base_id 静态残留直接记 error，避免发 /bases//sheets 404
            from .config_model import source_usable
            if not source_usable(source):
                return {
                    "source_key": source.key, "name": source.name,
                    "table_name": "", "records": [],
                    "error": f"数据源 {source.key} 未配置 base_id，跳过（请用钉钉文档登记数据源）",
                }
            # 身份：动态源自带登记人 unionId（跨用户订阅优先用文档归属人身份读）
            op = source.operator_id or self._resolve_operator(operator_id, staff_id)
            kind = source.kind or "notable"
            if kind not in ("notable", "workbook", "doc"):
                return {
                    "source_key": source.key, "name": source.name,
                    "table_name": "", "records": [],
                    "error": f"暂不支持的文档类型: {kind}",
                }
            # v1.11.1：doc 无分表概念，直接读全文 → blocks 转 records
            if kind == "doc":
                doc = self.client.read_doc_content(source.base_id, op)
                if not doc.get("ok"):
                    return {
                        "source_key": source.key, "name": source.name,
                        "table_name": "", "records": [],
                        "error": doc.get("message", "普通文档读取失败"),
                    }
                blocks = doc.get("blocks") or []
                return {
                    "source_key": source.key, "name": source.name,
                    "table_name": "",
                    "records": self.client._doc_blocks_to_records(blocks) or [],
                    "error": "",
                }
            if source.table_mode == "latest_week" and kind == "notable":
                sheets = self.client.list_sheets(source.base_id, op)
                latest = find_latest_week_table(sheets)
                if not latest:
                    return {
                        "source_key": source.key, "name": source.name,
                        "table_name": "", "records": [],
                        "error": "未找到带周次编号的分表",
                    }
                sheet_id = str(latest.get("sheetId") or latest.get("tableId") or "")
                table_name = str(latest.get("name") or latest.get("tableName") or "")
            else:
                sheet_id = source.table_id
                table_name = ""
                if not sheet_id:
                    return {
                        "source_key": source.key, "name": source.name,
                        "table_name": "", "records": [],
                        "error": f"{kind} 数据源缺少 table_id",
                    }
            if kind == "workbook":
                records = self.client.read_workbook_records(
                    source.base_id, sheet_id, op)
            else:
                records = self.client.read_notable_records(
                    source.base_id, sheet_id, op)
            return {
                "source_key": source.key, "name": source.name,
                "table_name": table_name, "records": records or [], "error": "",
            }
        except Exception as e:
            logger.warning(f"看板采集失败 {source.key}: {e}")
            return {
                "source_key": source.key, "name": source.name,
                "table_name": "", "records": [], "error": str(e)[:200],
            }

    def collect_all(self, sources: list[SourceConfig], operator_id: str = "",
                    staff_id: str = "", max_workers: int = 3) -> list[dict]:
        """并发采集全部启用数据源；单源失败不影响其他

        Returns:
            [collect() 结果 dict, ...]，按配置顺序排序（确定性）
        """
        sources = [s for s in sources if getattr(s, "enabled", True)]
        if not sources:
            return []
        results: list[dict] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self.collect, s, operator_id, staff_id): s
                       for s in sources}
            for fut in as_completed(futures):
                s = futures[fut]
                try:
                    results.append(fut.result())
                except Exception as e:
                    results.append({
                        "source_key": s.key, "name": s.name,
                        "table_name": "", "records": [], "error": str(e)[:200],
                    })
        order = {s.key: i for i, s in enumerate(sources)}
        results.sort(key=lambda r: order.get(r["source_key"], 999))
        return results

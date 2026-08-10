"""
看板核心子系统（v1.11.0）

每日项目看板：从钉钉文档/AI表格数据源实时采集 → 解析统计 → Markdown 组装 → 推送。
数据不入 Chroma（每次实时拉取），与知识库物理隔离。

模块：
  config_model         数据源配置 dataclass + 加载
  parser               单元格解析 / 周次检测 / 统计（纯函数）
  collector            多源采集（复用 dingtalk_doc_client）
  assembler            Markdown 组装（规则模板 + LLM 兜底校验）
  alerts               变化检测（changes_only 主动提醒）
  subscription_store   订阅持久化（SQLite）
  subscription_commands 订阅管理指令解析 + 反问确认
  doc_candidates       钉钉文档「帮我学习」候选登记
  doc_learn            钉钉文档内容入库
"""

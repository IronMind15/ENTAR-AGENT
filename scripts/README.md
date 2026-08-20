# scripts 目录边界

`scripts/` 是当前兼容入口目录。生产服务从 `main.py` 启动，模块按下面的职责划分：

- 生产入口与领域模块：`main.py`、`config.py`、`paths.py`、`doc_mgr/`、`dashboard/`、`skills/`、`tools/`。
- 运维/同步脚本：`sync_*.py`、`mineru_extract.py`、`compare_pdf_parsers.py`、`probe_*.py`、`dup_cleanup.py`。
- 评估脚本：`eval_rag.py`、`validate_intent_llm.py`。
- 兼容归档：`web_page.py`、`tools/search_standards.py`、`tools/search_experience_kb.py`，当前不在生产注册或入口链路中。

新工具只需在 `tools/` 新建模块并使用 `@register`；新技能只需在 `skills/` 新建模块、定义 `BaseSkill` 子类并使用 `@register`。两个注册中心会自动发现活动模块，不能把旧归档模块重新加入注册中心。

## 运行数据

所有数据库、上传文件、看板快照、模板、日志和向量库路径由 `paths.py` 统一管理：

- 生产默认保持兼容，继续使用仓库下现有目录；部署环境建议设置 `ENTAR_RUNTIME_DIR`、`ENTAR_KNOWLEDGE_BASE_DIR` 和 `ENTAR_LOG_DIR` 移到仓库之外。
- 测试进程由 `tests/__init__.py` 自动设置 `data/_test_runtime/`，不会默认写入生产数据库或上传目录。
- 静态输入文件（标准 PDF、故障 Excel、经验 Markdown、PCB 查表和评估集）仍位于 `data/`，不应把它们与运行数据库、日志、上传文件混合打包。

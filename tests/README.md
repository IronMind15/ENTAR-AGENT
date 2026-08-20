# 测试目录边界

测试启动时会设置 `ENTAR_TESTING=1` 和 `ENTAR_RUNTIME_DIR=data/_test_runtime`。
未显式传入临时数据库的模块默认也会使用测试运行目录，不应触碰生产的
`data/user_store.db`、`data/uploads/`、`data/dashboard_tasks/` 或向量库。

真实 DeepSeek、钉钉、MinerU 凭据仍由 `config.py` 阻断读取；外部调用必须显式 mock。
新增测试优先使用 `tempfile` 或测试运行目录，禁止写入仓库生产数据目录。

# 恩特小助手 更新日志

> 📌 **本文档是项目唯一的版本记录（单一事实源）**——README.md、PROGRESS.md 的版本历史均指向本文件，发版时只在这里追加记录。
> 相关：待办清单见 [TODO.md](TODO.md)，进度看板见 [PROGRESS.md](PROGRESS.md)。

## v1.11.5（2026-08-11）

**🗂️ 多知识库架构改造 + 实测日志 9 问题修复**——用户实测日志排查 9 个问题 + 提出知识库架构改造，一次落地：①「创建知识库」做成独立通用功能（此前 KB 绑定具体内容，集合名散落硬编码），钉钉自然语言创建；②查询工具 3 个独立（search_knowledge_base / search_standards / search_experience_kb）合并成 1 个通用工具，可在不同知识库选择查询或全库合并搜索，LLM 不再选错工具（修「已学习文档搜不到」根因）；③知识库 schema 加 `department` 字段，分部门开权限逻辑预留（`get_visible_knowledge_bases` 按 centers 过滤，现全 public 不拦截）；④静态看板源空 base_id 运行时过滤 + 停用陈旧订阅（修 404 循环）；⑤无可用源返回清晰 JSON 杜绝 Agent 幻觉「推送成功」；⑥订阅指令误判修复（「只推给我自己」/「我已经粘贴过了重新发给了你」/「这跟看板功能有什么关系」）；⑦识图路径修复（写 memory + 文件名递归查找兜底）；⑧文档类型检测 unknown 原因聚合日志；⑨看板 LLM 组装 25s 超时规则兜底（实测曾 37.8s）。全量回归 **642 项全绿**（此前 604 项）。

### 功能

- **知识库注册表**（`scripts/kb_registry.py`）：SQLite 表 `knowledge_bases`（`key` PK / `name` / `description` / `collection` 默认=key / `department` 默认 public / `enabled` / `created_at`），`init_schema` + `seed_default_kbs` 种子三库（故障知识库 error_codes / 标准知识库 standards / 经验知识库 experience_kb，INSERT OR IGNORE）；`list/get/resolve_kb/create/update/delete` + `get_visible_knowledge_bases(centers)`（department ∈ centers∪public 预留过滤，centers 为 None → 全部）
- **钉钉自然语言创建知识库**（`scripts/tools/create_knowledge_base.py`）：参数 `{name, description="", department="public"}`，department 校验 `center_config.is_valid_center`（非法即拒），key 用中文名去空格生成（Chroma 支持 Unicode collection 名），重名报错；回执提示后续用法「发文档后回复『把这个文档学到XX』即可入库」；description 动态列出现有知识库，避免重名/选错
- **学习流程支持指定库**（`knowledge_review.py` + `dashboard/doc_learn.py`）：解析「把这个文档学到XX」→ `resolve_kb` → `target_collection = kb.collection`、`department = kb.department`；`learn_dingtalk_doc` 默认 standards 可被 bot 透传指定库名覆盖；未指定维持现状
- **通用知识库查询工具**（重写 `scripts/tools/search_knowledge_base.py`）：参数 `{query, knowledge_base=""}`——指定库按 collection 分发（`error_codes`→error_query.search_kb 故障码精确通道、`standards`→standards_query.search_kb 带 std_id 精确 + centers 过滤、`experience_kb`→experience_query.search_kb、自定义→enhanced_query + department where 过滤）；**留空 → 全部可见知识库搜索合并去重按距离排序**，每条带 `kb_name` + `source_label`；描述注明「知识库动态管理，可用库清单执行时获取」
- **停用旧工具**（`scripts/tools/__init__.py`）：移除 `search_standards` / `search_experience_kb` 两行注册（文件保留，文件头注释「已由通用 search_knowledge_base 替代 v1.11.5」）；工具注册 10→9，LLM 只见 1 个查询工具，选型错误根除
- **看板静态源过滤**（`dashboard/config_model.py` + `dashboard/service.py` + `dashboard/collector.py`）：`source_usable()` 判定 dingtalk_doc 源 base_id 空 → 不可用；`load_all_available_sources` 过滤不可用源并 `logger.warning`「静态数据源 {key} 未配置 base_id，跳过」；`collect()` 开头校验 base_id 非空，空则直接记 error 不发 HTTP 404（修 404 循环）
- **停用陈旧订阅**：一次性 SQL `UPDATE dashboard_subscriptions SET enabled=0 WHERE data_sources LIKE '%project_status%' OR data_sources LIKE '%test_issues%'`（引用静态源的订阅禁用，doc_ 动态源订阅保留）
- **杜绝 Agent 幻觉**（`tools/query_dashboard.py` + `tools/push_dashboard.py`）：无可用源/全部失败时返回清晰 JSON `{"error": "未配置可用的看板数据源（钉钉文档 base_id 未配置）。请先发钉钉文档，再回复「按这几个文档做每日看板」登记数据源。"}`，不再给 LLM 编造空间
- **订阅指令误判修复**（`dashboard/subscription_commands.py`）：
  - 「只推送给我自己」→ 新增 `_SET_SELF_RE` 命中 `set_recipient_self`，回复「看板默认就是只推送给您自己，无需调整」（不再误判加人）
  - 「我已经粘贴过了重新发给了你」→ `_extract_names` 新增 `_INVALID_NAME_RE`（含 我/你/他/她/它/自己/大家/们/的/地/得 等 → 丢弃）+ `_TRAILING_PARTICLE_RE` 去尾部语气词（了啊吧呀呢嘛啦哦噢么呗嘞），全丢返回 `[]`；`change_recipients` 分支改 `if _RECIPIENT_RE.search(text) and names:`，names 空继续下一意图
  - 「这跟看板功能有什么关系」→ `_NEGATIVE_RE` 扩展（看看/想看看/看一下/功能/关系/干嘛/什么关系/有什么用…）+ 兜底 create 收紧 `_CREATE_RE`（前缀 帮我/给我/替我 + 推/做/建/开通/开/设置/生成/要/需要 + 看板；每日/每天 + 看板；看板 + 推送/提醒/订阅），双保险不再误判开通
- **识图路径修复**（`skills/dingtalk_bot.py` + `tools/describe_image.py` + `prompts/system_prompt.txt`）：图片保存后写 memory `[发送图片] {绝对路径}`（LLM 上下文有真实路径）；`describe_image.execute` 路径不存在/不在白名单时按文件名在 `_UPLOAD_ROOT` 下 `rglob` 递归查找同名文件兜底，找不到才报错；system prompt 补真实目录结构示例（`data/uploads/研发中心/张三/2026-08-11/image_1.png` + 「识别图片时可用图片文件名，工具会自动查找」）
- **文档类型检测 unknown 原因聚合**（`dingtalk_doc_client.py`）：`_detect_kind_uncached` 聚合三类探测失败原因（接口/错误码）存 `reasons`，unknown 时 `logger.warning`「三类接口均未命中，可能是视图/子表或非三类文档」；bot 对 unknown 返回友好提示
- **看板 LLM 组装超时**（`dashboard/assembler.py`）：`llm_assemble` 经 `ThreadPoolExecutor(max_workers=1)` + `future.result(timeout=LLM_ASSEMBLE_TIMEOUT=25)`，超时/异常走规则兜底（实测曾 37.8s 拖慢推送）

### 测试

- 新增 38 项：新建 `test_kb_registry`（临时 DB 注册表 CRUD/种子三库/可见性/create 工具合法非法 department/重名）；新建 `test_search_knowledge_base`（通用工具指定库分发到经验/故障/标准、unknown 库提示、部门不可见/可见、留空全库合并按距离排序、无结果、停用库）；`test_experience_query` 迁移通用工具断言（search_experience_kb 不再注册）；`test_subscription_commands` 追加 ④⑤⑥ 用例（只推给我自己→set_self、我已经粘贴过了重新发给了你→None、这跟看板功能有什么关系→None）+ 去除尾部语气词人名断言；`test_describe_image` 文件名查找兜底；`test_dashboard_config` `SourceUsableTests`（base_id 空不可用）；`test_dashboard_llm` LLM 超时兜底；`test_doc_learn` 指定库入库（collection/department 继承）；`test_knowledge_review` 断言 kb 参数透传；`test_dashboard_scheduler`/`test_dashboard_service` 补 base_id mock
- 全量回归 **642 项通过**（此前 604 项）

### 已知限制

- 自定义知识库检索走 `enhanced_query`（无故障码精确匹配 / std_id 快速通道），需要精确通道的库仍用内置三库分发
- 新创建知识库 `department` 默认 public 全可见；分部门开权限逻辑（`get_visible_knowledge_bases`）已预留但现不拦截，待 v2.0 多中心权限一起启用
- 旧 `search_standards` / `search_experience_kb` 文件保留不再注册；测试若直接 import 模块仍可用（不经工具注册中心）
- 停用的陈旧订阅是测试期静态源订阅，用户真实 doc_ 动态源订阅不受影响；如需重新启用可再建

### 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/kb_registry.py` | 🆕 知识库注册表（SQLite 表 + CRUD + 种子三库 + 部门可见性） |
| `scripts/tools/create_knowledge_base.py` | 🆕 钉钉自然语言创建知识库工具 |
| `scripts/tools/search_knowledge_base.py` | 重写为通用查询工具（指定库分发 + 留空全库合并） |
| `scripts/tools/__init__.py` | 注册 create_knowledge_base；移除 search_standards/search_experience_kb 注册（10→9） |
| `scripts/tools/search_standards.py` | 文件头注释替代说明，不再注册 |
| `scripts/tools/search_experience_kb.py` | 文件头注释替代说明，不再注册 |
| `scripts/knowledge_review.py` | 学习指令解析指定知识库（resolve_kb → collection/department） |
| `scripts/dashboard/doc_learn.py` | `learn_dingtalk_doc` 支持指定库（默认 standards） |
| `scripts/dashboard/config_model.py` | `source_usable()` 静态源 base_id 过滤 |
| `scripts/dashboard/service.py` | `load_all_available_sources` 过滤不可用源 + warning |
| `scripts/dashboard/collector.py` | collect 前置校验 base_id 空直接记 error |
| `scripts/tools/query_dashboard.py` | 无可用源清晰错误 JSON（杜绝幻觉） |
| `scripts/tools/push_dashboard.py` | 同上 |
| `scripts/dashboard/subscription_commands.py` | set_recipient_self + 人名过滤 + _NEGATIVE/_CREATE 收紧 |
| `scripts/skills/dingtalk_bot.py` | 图片写 memory（[发送图片] 路径）+ unknown 文档友好提示 |
| `scripts/tools/describe_image.py` | 文件名 rglob 递归查找兜底 |
| `scripts/prompts/system_prompt.txt` | 补真实上传目录结构示例 |
| `scripts/dashboard/assembler.py` | llm_assemble 25s 超时 + 规则兜底 |
| `scripts/dingtalk_doc_client.py` | detect_kind unknown 原因聚合日志 |
| `data/user_store.db` | 一次性 SQL 停用陈旧静态源订阅 |

## v1.11.4（2026-08-11）

**📊 看板实测修复：每日必推 + 本次文档做数据源 + 创建时完整看板 + 输出格式增强**——用户实测 v1.11.3 看板后 4 个问题一次修复：①「每日有更新才推送」非每日必推 → `alert_mode` 默认改 `always`（每天必推），顶部注入变化标注（新增 `change_banner`：有变化 `📌 今日变化：板块：总数 2→3；新增关注1`、无变化 `📌 今日无变化`），`changes_only`/`off` 保留为选项；②发 4 个文件出现 6 条大标题 → 根因是 `_handle_doc_create` 取「全部历史 enabled 候选」（本次 4 个 + 昨天登记同文档 2 条），改为 bot 收集**本次**识别候选 id 透传，历史候选不再混入；③创建时示例看板简略（LLM 组装失败退回简略规则兜底）→ 规则兜底新增「📌 今日要点」段（关注项摘要 / 整体正常兜底）+ LLM prompt 强化严禁表格语法；④确认文案同步「每天固定推送（附今日变化）」。现有 5 个订阅 SQL 迁移为 `always`。全量回归 **604 项全绿**（此前 590 项）。

### 功能

- **每日必推 + 变化标注**（`dashboard_scheduler.py` `_execute_subscription`）：`alert_mode` 默认 `always`，无变化也必推；顶部注入 `change_banner` 变化标注（有变化列变更详情、无变化显式说明），`changes_only` 无变化静默 / `off` 跳过保留
- **新增 `change_banner`**（`dashboard/alerts.py`）：复用 `diff_summary`（去噪快照对比），`source_key` 经 `name_by_key` 映射成板块名，缺省回退 key
- **默认值改 `always`**（`config.py` `DASHBOARD_ALERT_MODE`、`config_model.py`、`subscription_store.py` DDL、`skills/dashboard.py` pending 共 4 处同步）
- **本次文档做数据源**（`dingtalk_bot.py` + `skills/dashboard.py`）：`_register_doc_candidate` 返回候选 id；`_handle_dingtalk_doc_link` 返回 `(answer, cand_ids)` 元组；`_handle_doc_link_with_kanban` 拆包后调 `DashboardSkill._handle_doc_create(user_id, source_candidate_ids=cand_ids)` 只取本次候选（过滤 enabled + notable/workbook/doc），无本次候选时兜底全部可看板候选
- **规则兜底「📌 今日要点」**（`dashboard/assembler.py`）：`assemble_markdown` 在数据日期后、首个 `##` 前插入——有关注项 `今日要点：{板块}关注{M}项（{前3标题}）`、无关注项 `今日要点：整体正常，共 N 条记录`
- **LLM prompt 强化**（`assembler.py` `_build_llm_prompt`）：明确规则 5「严禁 Markdown 表格语法（钉钉不支持会整条失败）+ 总字符 ≤5000 + 直接输出正文」，降低「LLM 看板未过校验→规则兜底」概率
- **确认文案同步**（`subscription_commands.py` `render_confirmation`）：`🔔 提醒模式：每天固定推送（附今日变化）`（默认 always）/ `仅数据有变化时推送`（changes_only）

### 测试

- 新增 52 项：`test_dashboard_alerts` 追加 `ChangeBannerTests`（无变化/变化详情/source_key 回退/name 映射/新增板块/首次快照）；`test_dashboard_scheduler` 追加 always 无变化必推含「今日无变化」banner、always 有变化注入「今日变化」+ 板块名、off 模式跳过；`test_dashboard_assembler` 追加今日要点存在（关注项摘要 + 无关注整体正常兜底）；`test_dashboard_skill` 追加 `_handle_doc_create(source_candidate_ids=...)` 只取本次候选（历史候选不混入）+ 缺省兜底全部；`test_subscription_commands` 追加默认/change_only 确认文案；`test_dingtalk_doc` 全量改元组拆包
- 全量回归 **604 项通过**（此前 590 项）

### 已知限制

- 修复前已创建的订阅（如测试期 id=5，混入 6 个数据源）保持原样，需重说「按这几个文档做每日看板」重建
- `change_banner` 变化标注与 LLM 输出的「📌 今日要点」并存——banner 在最顶、要点在其下，语义不冲突

### 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/dashboard/alerts.py` | 🆕 `change_banner`（每日必推顶部变化标注） |
| `scripts/dashboard_scheduler.py` | always 每日必推 + banner 注入；changes_only/off 保留 |
| `scripts/config.py` | `DASHBOARD_ALERT_MODE` 默认 `always` |
| `scripts/dashboard/config_model.py` | `alert_mode` 默认/校验 `always` |
| `scripts/dashboard/subscription_store.py` | DDL 默认 `'always'` |
| `scripts/skills/dashboard.py` | `_handle_doc_create(source_candidate_ids)` 只取本次候选；pending `alert_mode="always"` |
| `scripts/skills/dingtalk_bot.py` | `_register_doc_candidate` 返回 id；`_handle_dingtalk_doc_link` 返回 `(answer, cand_ids)` |
| `scripts/dashboard/assembler.py` | 规则兜底「今日要点」+ LLM prompt 强化严禁表格 |
| `scripts/dashboard/subscription_commands.py` | 确认文案「每天固定推送（附今日变化）」 |

## v1.11.3（2026-08-11）

**📑 文档概要回复（去上限）+ LLM 指令联动（总结成推送）+ 需求探索清单**——用户 3 个需求一次落地：①发文档链接不再有 5 个上限，回复改为「文档名（类型）· 概要 · 共 N 条记录」——识别时只读概要秒回（notable/workbook 首屏 10 条、doc 20 块），需要时（总结/学习/看板）全读（workbook 补 nextToken 翻页、doc 上限放宽到 5000 块）；②发文档后接着说「帮我总结成推送」→ LLM 识别指令调新增 `summarize_doc` 工具，总结后推回用户本人——卡片消息（richText/interactiveCard）识别后写入会话记忆是打通前提（此前 LLM 看不到刚发的文档）；③`detect_kind` 类型探测加实例级 TTL 缓存（30 分钟），N 文档识别每链接 3-4 次探测 API 的重复调用显著减少。另产出公司内部需求探索清单（9 组，docs/20260811-需求探索清单.md）。全量回归 **590 项全绿**（此前 564 项）。

### 功能

- **去链接上限 + 概要回复**（`dingtalk_bot.py` `_handle_dingtalk_doc_link`）：`for url in urls[:5]` → `urls[:_MAX_DOC_URLS]`（50 仅防异常超长输入）；识别改传 `summary=True`；回复格式 `📑 {名字}（{类型}）· 概要 · 共 N 条记录` + `_doc_summary`（notable/workbook 前 3 条预览、doc 取 markdown 前 150 字）
- **概要 vs 全读双模式**（`dingtalk_doc_client.py`）：`read_document(summary=False)` 默认全读保持现行为；`summary=True` 时 notable/workbook 传 `SUMMARY_RECORD_LIMIT=10`（首屏不翻页）、doc 传 `_DOC_SUMMARY_BLOCKS=20`；全读时 workbook 补 nextToken 翻页（镜像 notable 循环，多路径兼容 hasMore/nextToken/nextPageToken，安全上限 10000）、doc 上限 `_DOC_FULL_BLOCKS=5000`
- **补文档名**（`dingtalk_doc_client.py`）：notable 分支补 `sheet_name`（`_notable_sheet_name`）、workbook 分支取首表 name、doc 用 `_doc_title` 首个非空段落截断 20 字兜底「钉钉文档」；返回统一带 `name` 字段，bot 回复与候选登记均填真名
- **detect_kind TTL 缓存**（`dingtalk_doc_client.py`）：实例级 `_kind_cache`（get_doc_client 单例共享，30 分钟过期），缓解 N 文档识别时每链接 3-4 次探测 API；实例级保证测试隔离
- **卡片消息写 memory**（`dingtalk_bot.py`）：`_handle_rich_text_message` / `_handle_interactive_card_message` 识别文档后写 `memory.add(user_id, user/assistant, ...)`——LLM 才能看到刚发的文档（此前卡片路径直接 return 不写记忆，是「总结成推送」识别不出的根因）
- **新增 `summarize_doc` 工具**（`scripts/tools/summarize_doc.py`，第 10 个工具）：参数 `{doc_ref, push_to_self}`；定位候选（`find_by_node_id`/`find_by_url`/`find_by_name`，新增于 `doc_candidates.py`）→ 全读 → 转 Markdown（doc 走保真 markdown，其余 `_records_to_markdown`）→ `call_deepseek` 总结（截 8000 字，失败兜底原文前 500 字）→ `push_to_self=true` 时 `DingTalkNotifier` 主动推回本人（推后工具只回简短确认防双发）
- **用户上下文注入**：`tools/__init__.py` 加 `_current_user_id` contextvar + `set/get_current_user_id`；`dingtalk_bot._process_text` 顶部在 `set_current_staff_id` 旁加 `set_current_user_id(user_id)`
- **需求探索清单**：新增 `docs/20260811-需求探索清单.md`——9 组（会议工作流/经验库/识图/看板/权限/检索/钉钉生态/运维/第三步）+ 汇总表（复用资产/优先级/工作量/依赖）+ 建议实施顺序；TODO.md 加索引行

### 测试

- 新增 26 项：`test_dingtalk_doc` 追加 `SummaryModeTests`（summary=True 传 limit、全读不传、doc 概要 20 块、doc 全读 600 块不截断、detect_kind 缓存命中不重调 API）、`WorkbookPaginationTests`（两页翻页合并、limit 提前返回、安全上限截断死循环）、`BotSummaryModeTests`（bot 传 summary=True、回复含名字、候选带真名、富文本/交互卡片写 memory）；多链接聚合测试改为 6 份断言不再截断；`test_doc_candidates` 追加 `FindByTests`（find_by_node_id/url/name，含跨用户隔离与 URL 变体兜底）；新建 `test_summarize_doc_tool`（10 项：定位/兜底/无候选/无用户/读失败/LLM 失败兜底/doc markdown/推送/推送失败）
- 全量回归 **590 项通过**（此前 564 项）

### 已知限制

- 同一消息「文档链接 + 帮我总结成推送」会被文档识别拦截（`_handle_doc_link_with_kanban` 只合并看板意图），需「先发文档、再发指令」两步；同消息合并留待后续
- doc 全读放宽到 5000 块后单次响应变大，真实长文档耗时需实测观察
- workbook 翻页字段名按 notable 模板 + 多路径兼容，若接口字段不符会退化为首屏不崩

### 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/dingtalk_doc_client.py` | 🆕 概要/全读双模式 + workbook 翻页 + `_doc_title` + detect_kind 缓存 + 三分支补文档名 |
| `scripts/skills/dingtalk_bot.py` | 去 `urls[:5]` 上限 + 回复名字概要（`_doc_summary`）+ 卡片写 memory + 注入 `set_current_user_id` |
| `scripts/tools/summarize_doc.py` | 🆕 文档总结工具（定位→全读→总结→可选推送本人） |
| `scripts/tools/__init__.py` | 🆕 `_current_user_id` contextvar + 注册 summarize_doc |
| `scripts/dashboard/doc_candidates.py` | 🆕 `find_by_node_id`/`find_by_url`/`find_by_name` 查询 |
| `docs/20260811-需求探索清单.md` | 🆕 公司内部需求探索清单（9 组） |
| `tests/`（3 个文件） | 新增 26 项（概要模式/翻页/缓存/卡片记忆/summarize_doc 工具） |
| `TODO.md` | +需求探索清单索引行 |

## v1.11.2（2026-08-11）

**🔧 实测修复包：发多个文档不再丢 + 卡片消息也能做看板 + 「帮我学习」说明加强**——用户实测反馈 3 个问题一次修齐：①一次发 4 个文档链接只识别 3 个（`urls[:3]` 硬截断，第 4 个静默丢失 → 放宽到 `urls[:5]`）；②interactiveCard 卡片消息走文档识别后直接 return、跳过技能路由，「做每日看板」意图被吞 → 抽 `_handle_doc_link_with_kanban` 合并文档摘要 + 看板创建确认，接入文本/富文本/卡片三处路由；③「帮我学习」提示太简单 → 详细说明入库是什么、进哪个库（企业知识库·标准文档库）、能干什么（自然语言检索示例），并主动区分「看板实时拉取、不进知识库」；入库成功提示也补充块数/记录数/查看入口。全量回归 **564 项全绿**（此前 561 项）。

### 功能

- **修复发 4 丢 1**（`dingtalk_bot.py` `_handle_dingtalk_doc_link`）：`for url in urls[:3]` → `urls[:5]`（够用且防消息过长）
- **卡片/富文本做看板意图**（`dingtalk_bot.py`）：新增 `_handle_doc_link_with_kanban`（文档识别摘要非 None 且 `parse_doc_dashboard_intent` 命中 → 合并看板创建确认；异常降级只回文档摘要）；`_process_text` / `_handle_rich_text_message` / `_handle_interactive_card_message` 三处改走统一方法
- **「帮我学习」说明加强**（`dingtalk_bot.py`）：
  - 文档识别后提示改为三段式：入库进哪个库（企业知识库·标准文档库）→ 入库后效果（自然语言检索，如问「{首条关键词}」即可命中）→ 看板不受影响（实时拉取、不进知识库）
  - 入库成功提示改为「✅ 已存入企业知识库（标准文档库）· 内容块 N 块 · 记录 M 条 · 回复「我的文件」查看管理」
- **关键词示例**（`dingtalk_bot.py`）：新增 `_first_record_keyword` 从首条记录取非空字段值截断 8 字作检索示例，兜底「这份文档里的内容」

### 测试

- 新增 3 项：多链接聚合测试改为 4 条链接断言全部处理（`call_count=4`，验证 `urls[:5]`）；`_handle_doc_link_with_kanban` 含「做每日看板」→ 回复含文档摘要 + 看板确认、不含 → 不触发看板技能；interactiveCard 消息含「做每日看板」→ 回复合并摘要
- 既有「不推销推送」断言适配新文案：`assertNotIn("看板")` 改为 `assertIn("看板")`（新文案主动说明看板区别，非推销）+ `assertNotIn("订阅")` 保留推销约束
- 全量回归 **564 项通过**（此前 561 项）

### 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/skills/dingtalk_bot.py` | `urls[:3]→[:5]` + 🆕 `_handle_doc_link_with_kanban`/`_first_record_keyword` + 三处路由改走统一方法 + 学习提示/入库成功文案加强 |
| `tests/test_dingtalk_doc.py` | 多链接改 4 条断言 + 新增 3 项合并测试 + 1 处断言适配 |

## v1.11.1（2026-08-11）

**📄 钉钉文档类型自动识别（notable / workbook / doc 统一入口）+ doc 做看板数据源**——普通在线文档（doc）打通：入口不再区分类型，发任意钉钉文档链接（AI表格/在线表格/普通文档）都能自动识别读取。doc 走 `GET /v1.0/doc/suites/documents/{node}/blocks`（需 **Storage.File.Read** 权限），blocks 逐块转保真 Markdown（段落→正文、表格→Markdown 表格、单元格 `\n`→`<br>`）；「帮我学习」doc 直接按全文 Markdown 入库（跳过逐条拼装）；doc 同样可做每日看板数据源（文档表格格式不统一，采集全文由 LLM 提炼，不做字段解析）。全量回归 **561 项全绿**（此前 552 项）。

### 功能

- **doc 读取**（`dingtalk_doc_client.py`）：新增 `read_doc_content`（blocks 接口 + 500 块安全上限 + 403 权限错传播）、`_blocks_to_markdown`/`_table_to_markdown`（保真转换）、`_doc_blocks_to_records`（逐块 records 供预览/看板）
- **三类型自动识别**（`dingtalk_doc_client.detect_kind`）：顺序探测 notable → workbook → doc，命中哪个用哪个；403 权限错一律传播不降级；doc 失败才落 unknown
- **统一入口放行 doc**（`read_document` doc 分支）：返回 `{ok, kind:doc, records, markdown, field_names:{}}`，doc/unknown 不再 fall-through 到「暂不支持」
- **doc 保真入库**（`dashboard/doc_learn.py`）：`kind=doc` 且有 markdown 时用 `engine.process_text(markdown)` 全文直入
- **doc 做看板数据源**：bot `_register_doc_candidate` 放行（`enabled=kind in (notable,workbook,doc)`）；collector 加 doc 采集分支（无 table_id 概念，直接读全文 → 逐块 records）；`doc_candidates.list_dashboard_ready` + `service.load_all_available_sources` SQL 放行 doc
- bot 回复 kind 映射已含 `doc→"文档"`（v1.11.0 已就绪），doc 链接回复「📑 已识别钉钉文档（文档）· …」+「帮我学习」提示

### 测试

- 新增 9 项：`test_dingtalk_doc` 追加 `BlocksToMarkdownTests`（段落/表格/`\n` 单元格/缺行补齐）、`ReadDocContentTests`（blocks 接口 + 空内容 + 权限传播 + 500 上限）、`DetectKindDocTests`（notable/workbook 失败回退 doc + 权限传播）、`ReadDocumentDocTests`（doc 分支 markdown + records + 降级）、`BotDocLinkKindDocTests`（doc 链接回复 + 登记看板源 enabled）；`test_doc_learn` 追加 doc markdown 全文入库断言；`test_dashboard_collector` 追加 doc 采集分支测试；`test_dashboard_service`/`test_doc_candidates` 原「doc 排除」断言改为「doc 纳入」
- 全量回归 **561 项通过**（此前 552 项）

### 已知限制

- doc 读取需应用开通 **Storage.File.Read** 权限（未开通返回 403 并提示申请）
- doc 看板数据源不做表格字段解析（格式不统一），采集全文由 LLM 提炼
- doc blocks 接口无分页字段，一次性全返回，客户端按 500 块上限截断

### 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/dingtalk_doc_client.py` | 🆕 `read_doc_content` + blocks→Markdown 转换 + `detect_kind` 三类型探测 + `read_document` doc 分支 |
| `scripts/dashboard/doc_learn.py` | doc 走保真 Markdown 入库 |
| `scripts/skills/dingtalk_bot.py` | `_register_doc_candidate` 放行 doc 做看板源 |
| `scripts/dashboard/collector.py` | 🆕 doc 采集分支（读全文 → 逐块 records） |
| `scripts/dashboard/doc_candidates.py` `service.py` | `list_dashboard_ready` / `load_all_available_sources` 放行 doc |
| `tests/test_dingtalk_doc.py` `test_doc_learn.py` `test_dashboard_collector.py` `test_dashboard_service.py` `test_doc_candidates.py` | 新增 9 项 + 3 处「doc 排除」断言改为纳入 |

---

## v1.11.0（2026-08-10）

**📊 每日项目看板 — 发钉钉文档动态做看板（全动态数据源）+ 定时主动推送 + 上传文件自动推荐入库**——用户发钉钉文档（AI表格/在线表格）给小助手 → 识别登记 → 说「按这几个文档做每日看板」→ 反问确认（时间/频率/接收人）→ 每天定时拉这几个文档**最新数据**组装推送（`changes_only` 无变化静默）；数据源**全动态**不再手配 field_map（新增 `list_fields` 自动取字段中文名，接口不可用降级 field_id）；上传文件后小助手**主动推荐入库**、回复「入库/确认」即学。看板数据**不入 Chroma、每次实时拉取**（与知识库物理隔离）。全量回归 **552 项全绿**（此前 496 项）。

### 功能

- **数据源配置**（`dashboard/dashboard_sources.json`）：config.py 无法承载嵌套 field_map → 独立 JSON 定义数据源（kind/base_id/table_mode/field_map/status_groups）；`config_model.py` 加载（mtime 缓存 + 坏 JSON 容错）；`DASHBOARD_PUSH_HOUR` 等推送标量进 config.py 可覆盖
- **解析层**（`dashboard/parser.py`）：`extract_cell_value` 统一解析 AI表格单元格（dict/list/None）、`find_latest_week_table` 按数字取最新周次分表、`parse_source_records` 按 status_groups 分组统计（attention/normal/other）、`format_number` 小数→百分比、`#DIV/0!`→无数据
- **采集层**（`dashboard/collector.py`）：latest_week 模式列分表自动选最新周次、fixed 模式直读 table_id；staff_id→unionId 兜底（定时任务无对话上下文也能读文档）；单源失败记 error 不影响其他，并发采集
- **组装 + 变化检测**（`dashboard/assembler.py` + `alerts.py`）：规则模板（关注置顶/状态统计/无表格语法/≤5000 字符）+ `validate_markdown` 拦截表格语法 + `llm_assemble`（LLM 组装失败/超长/非法一律规则兜底）；`make_snapshot`/`has_changes`/`diff_summary` 去噪快照（只比对状态+名称，改一句进展不触发推送）
- **订阅持久化**（`dashboard/subscription_store.py`）：复用 user_store.db 新表 `dashboard_subscriptions`（同时存 owner_staff_id 推送 userId 与 owner_union_id 读文档 operatorId）；`subscription_commands.py` 保守指令解析（创建/改时间/改频率/改接收人/停止/查询）+ 反问确认 + 内存 pending
- **看板技能**（`skills/dashboard.py`，priority=80）：触发词防误触（文档/方案/方法论/怎么用/怎么样等放行给 Agent 调 `query_dashboard` 工具）；「帮我推个看板」→ 反问 → 「确认」→ 注册订阅 + 立即推样例；「改看板时间到10点」「每周一和周五」「也推给张工」「停掉看板」「我的看板几点推送」全支持
- **工具**（`tools/query_dashboard.py` + `push_dashboard.py`）：实时查询看板 / 立即推送订阅者；`agent.py` 新增 `call_deepseek` 公开包装（看板 LLM 组装用）；system_prompt 追加看板工具说明
- **定时调度**（`dashboard_scheduler.py` + main.py 挂载）：独立 BackgroundScheduler **1 分钟 interval tick + 到点扫描**（增删改订阅无需重注册）；`_is_due` 时间/星期/当天不重复三重判定；changes_only 无变化静默；推送后更新快照 + last_pushed_at
- **钉钉文档多链接 + 学习入库**（`dingtalk_bot.py` + `doc_candidates.py` + `doc_learn.py`）：消息中多条 alidocs 链接逐个读取聚合摘要；登记候选（user+url 唯一）；「帮我学习」先查文档候选（有则入库，无则走文件路径）；`engine.py` 新增 `process_text` 文本直入（从 `_process_markdown` 抽 `_index_markdown_text` 共用切块，**不改变 process_file 行为**，不写临时文件不进 data/uploads）
- **动态看板数据源（发文档驱动，全动态）**（`doc_candidates.py` 升级 + `service.py` 桥接）：用户发的文档登记为候选即成为看板源（合成 key `doc_<id>` 存订阅 data_sources，subscription_store 零改动）；`build_dynamic_source`/`resolve_subscription_sources` 统一桥接调度/技能/工具；collector 支持 **per-source 身份**（跨用户订阅用文档登记人 unionId 读）；parser 空 field_map 兜底（field_id 作列名不崩）；「按这几个文档做每日看板」意图解析 + 反问确认
- **字段元数据接口**（`dingtalk_doc_client.py`）：新增 `list_fields`/`read_notable_field_names`（多候选路径取 field_id→中文名，**接口不可用返回空不阻塞**）；`read_document` notable 分支携带 `field_names`；新增 workbook（在线表格）读取（候选路径 + 失败降级「暂不可用」提示）；`detect_kind` 回退探测 workbook
- **上传文件自动推荐入库**（`file_handler.py` + `knowledge_review.py` + `dingtalk_bot.py`）：保存后回执改为推荐问句「要不要入库？回复『入库/确认』即可」；新增 `learn_file_path`（按指定路径入库，规避多文件取最新歧义）+ 模块级内存 pending；「入库/确认」确认分支（有 pending 才拦截，无 pending 不抢普通对话）；图片不支持入库不推荐
- 周次编号统一 **1-7 制（周一=1）**，与 `datetime.isoweekday()` 对齐，`format_weekdays` 中文展示

### 测试

- 新增 221 项：`test_dashboard_config/parser/collector/assembler/alerts`（配置加载/解析/采集/组装/变化检测，用千问实测基准断言：33 周 7 条=4 滞后+3 正常；142 台=故障22/待验证3/已打包50/其他67）、`test_subscription_store/commands`（持久化 + 指令解析）、`test_dashboard_skill/llm`（订阅管理对话全流程 + LLM 组装兜底 + 文档看板创建）、`test_dashboard_scheduler`（fake clock 测到点判定 + 执行链 + doc_* 订阅）、`test_doc_learn` + `test_doc_mgr_engine` 追加（process_text 文本直入）+ `test_dingtalk_doc` 追加（多链接聚合/候选登记 + list_fields/workbook/field_names）、`test_doc_candidates` + `test_dashboard_service`（🆕 动态源注册表 + service 桥接）、`test_knowledge_review` + `test_file_handler` + `test_dingtalk_bot` 追加（上传推荐入库确认）
- 全量回归 **552 项通过**（此前 496 项）

### 已知限制

- 钉钉字段元数据接口（`list_fields`）与在线表格（workbook）读取路径以实测为准——接口不可用自动降级（字段名显示 field_id / 提示暂不可用），不阻塞链路
- 普通文档（doc）不做看板数据源，识别后引导「帮我学习」入库
- 钉钉文档卡片消息（非 text 链接）暂不识别；多链接最多处理 3 条
- pending 为内存态（看板订阅确认 + 上传推荐入库），重启失效（重说一句即可）

### 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/dashboard/` | 🆕 看板核心子系统（config_model/parser/collector/assembler/alerts/service/subscription_store/subscription_commands/doc_candidates/doc_learn/dashboard_sources.json/__init__.py）；doc_candidates 升级为动态源注册表 + service 桥接 |
| `scripts/dashboard_scheduler.py` | 🆕 定时推送调度器（1 分钟 tick）+ 动态源订阅解析 |
| `scripts/skills/dashboard.py` | 🆕 看板技能（priority=80）+「按文档做看板」动态源创建 |
| `scripts/tools/query_dashboard.py` `push_dashboard.py` | 🆕 看板查询/推送工具（含动态源） |
| `scripts/dingtalk_doc_client.py` | 🆕 `list_fields`/`read_notable_field_names`/workbook 读取/`field_names`/`detect_kind` 回退 |
| `scripts/skills/dingtalk_bot.py` | 多链接识别 + 候选登记（field_map）+「帮我学习」文档分支 + 上传推荐入库确认分支 |
| `scripts/knowledge_review.py` | 🆕 `learn_file_path`（按路径入库）+ 推荐入库内存 pending |
| `scripts/file_handler.py` | 回执文案改「要不要入库？」推荐问句 |
| `scripts/doc_mgr/engine.py` | 🆕 `process_text` + 抽 `_index_markdown_text`（process_file 行为不变） |
| `scripts/skills/agent.py` | 🆕 `call_deepseek` 公开包装 + `_TOOL_DISPLAY` 两条工具卡片 |
| `scripts/config.py` | 🆕 `DASHBOARD_*` 推送标量 |
| `scripts/main.py` | 挂载看板定时调度器 |
| `scripts/prompts/system_prompt.txt` | 追加看板工具说明 |
| `scripts/skills/__init__.py` `tools/__init__.py` | 注册看板技能与工具 |
| `tests/` | 🆕 13 个看板/上传测试文件 + 6 个扩展（221 项新增） |

---

## v1.10.3（2026-08-10）

**🔀 PDF 文件检测路由 — 文字版走本地 PyMuPDF（免费高保真），省 MinerU 每日 1000 页额度**——新增 `classify_pdf_type` 检测 PDF 文字层覆盖率（纯文字版/扫描版/混合版），纯文字版改走本地提取（免费 + 规避 MinerU VLM 对电子版的二次 OCR 误差），扫描/混合版仍走 MinerU 保质量；`PDF_ROUTING` 配置开关可一键切回旧行为。4 份精选已入库标准真实对比验证（烧 263 页额度）文字版句子重合 90%。全量回归 **331 项全绿**。

### 功能

- **PDF 类型检测**（`pdf_mupdf.py`）：`classify_pdf_type` 逐页提取→清理→统计字符，单页 ≥50 字符为「文字页」，覆盖率 ≥95%→`text`、≤5%→`scanned`、其余→`mixed`；毫秒级零依赖
- **本地质量校验**（`pdf_mupdf.py`）：`validate_local_text` 防「伪文字层」/乱码——提取字符量骤减（< 检测摘要 50%）或可读字符占比 <60% 判失败，回退 MinerU
- **引擎路由**（`engine.py`）：`_process_pdf` 按 `PDF_ROUTING` 路由——`auto`（默认）：纯文字版本地优先（成功省额度不烧 MinerU）、失败/质量差回退 MinerU；扫描/混合版 MinerU 优先（旧行为不变）；`mineru`：全部走 MinerU（一键切回）
- **对比脚本**（`compare_pdf_parsers.py` 🆕）：双路径对比报告（类型检测/字符量/切块/正文句子重合），`--no-mineru` 干跑省额度，**不写 Chroma 不污染知识库**
- 顺带修复：`_process_pdf` 嵌套闭包与模块级 `_try_mineru` 同名，闭包体内调用会解析到自身导致 `TypeError`（嵌套闭包改名 `_run_mineru`）

### 测试

- 新增 16 项：`test_pdf_routing.py`（分类 4 项 + 质量校验 4 项 + 引擎路由 8 项，含「auto+文字版 → MinerU 不被调用」关键断言），全部离线（PyMuPDF 合成 PDF，不联网不烧额度）
- 全量回归 **331 项通过**（此前 315 项）
- 真实 MinerU 对比 4 份精选已入库标准（烧 263 页额度）：类型检测 2 text / 2 scanned 全对；扫描版本地为空正确送 MinerU（GBT16935.1 66,959字/182块、EN50438 73,939字/110块）；文字版本地提取高保真（EN50178 270,196字/336块、GB_T_34133 34,877字/80块），MinerU→本地正文句子重合 **英文 EN50178 100%**（10/10）、**中文 GB_T_34133 90%**（9/10，唯一 MISS 是 MinerU 自己丢字）

### 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/doc_mgr/extractors/pdf_mupdf.py` | 🆕 `classify_pdf_type` + `validate_local_text` + 检测常量 |
| `scripts/config.py` | 🆕 `PDF_ROUTING`（auto 默认 / mineru 旧行为） |
| `scripts/doc_mgr/engine.py` | `_process_pdf` 路由改造 + 嵌套闭包改名 `_run_mineru` |
| `scripts/compare_pdf_parsers.py` | 🆕 双路径对比脚本（正文句子重合度） |
| `tests/test_pdf_routing.py` | 🆕 16 项 |

---

## v1.10.2（2026-08-10）

**📚 上传文件直接学习 + 我的文件/删除/管理员模式**——上传入库从「主管审核」简化为「回复『帮我学习』直接入库」；存储目录改为主部门/员工名字/日期；新增「我的文件」查看自己上传文件、「删除学习/重新学习」管理、`ENTARBOSS` 管理员模式（可任意删改）。**审核流程代码保留注释，未来可恢复**。全量回归 **315 项全绿**。

### 功能

- **上传直接学习**（`file_handler.py` + `knowledge_review.py` + `dingtalk_bot.py`）：上传后回执改为「回复『帮我学习』即可直接入库，无需审核」；用户回「帮我学习」→ 取该用户最新待学习文件 → 同步 `process_file` 入库（department=`public`，暂不划分部门）
- **存储目录改版**：`data/uploads/{主部门}/{员工名字}/{YYYY-MM-DD}/`（主部门取自 user_store `department_names` 首元素，无则「未分组」）；`_safe_path_component` 净化目录组件并挡住纯点组件（防目录穿越）
- **我的文件**：回复「我的文件」列出自己上传的文件（序号 + 文件名 + ✅已学习/⏳待学习/❌失败）+ 下一步建议
- **删除 / 重新学习**：「删除学习 序号」删知识库内容（按 doc_id 精确删，不误伤同名文件）+ 源文件 + tracker 记录；「重新学习 序号」force 重学。**权限硬约束：普通用户只能删/改自己上传的文件，越权一律拒绝**
- **管理员模式**：口令 `ENTARBOSS`（`config.ADMIN_MASTER_CODE` 可覆盖）→ 进入管理员模式，可「查看全部文件」并删改任意用户文件；「退出管理员」退出（会话内存态，重启失效）

### 停用说明

- 主管审核流程（`knowledge_review.create_request/handle_command/notify_*`）**代码保留未删除**，仅 `dingtalk_bot._handle_file_message` 中 `queue_review_for_upload` 调用改为注释。未来恢复部门划分与审核流程时取消注释，并把 `process_file` 的 `department` 改为用户主部门即可

### 测试

- 新增 32 项：`test_file_handler.py`（11 项：目录结构/净化/回执）+ `test_knowledge_review.py` 追加 10 项（学习/collection 显式传/删除/越权/管理员/重学）+ `test_dingtalk_bot.py` 追加 9 项（正则/路由/管理员切换/秒回）+ `test_sync_tracking.py` 追加 2 项（按上传者查询/删除记录）
- 全量回归 **315 项通过**（此前 283 项）

### 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/config.py` | 新增 `ADMIN_MASTER_CODE`（管理员口令，默认 ENTERBOSS） |
| `scripts/doc_mgr/sync_tracker.py` | 新增 `get_files_by_user` / `delete_file` |
| `scripts/file_handler.py` | `get_upload_dir` 改主部门/员工/日期；`_safe_path_component` + `_get_user_main_department`；回执改「帮我学习」 |
| `scripts/knowledge_review.py` | 新增 `learn_for_user` / `list_files_for_user` / `delete_for_user` / `relearn_for_user` + 模块级入口（审核代码保留） |
| `scripts/skills/dingtalk_bot.py` | 命令路由（我的文件/删除/重学/学习/管理员 ENTERBOSS）；注释 `queue_review_for_upload` |
| `tests/test_file_handler.py` | 🆕 11 项 |
| `tests/test_knowledge_review.py` | 追加 10 项 |
| `tests/test_dingtalk_bot.py` | 追加 9 项 |
| `tests/test_sync_tracking.py` | 追加 2 项 |

---

## v1.10.1（2026-08-10）

**🛡️ 上线前修复包**——上服务器部署前代码审查（4 路并行）发现并修复 4 个必修问题：长对话回放无预算可撑爆上下文窗口、MinerU 拆分子 PDF 在持久缓存目录永久累积、MinerU API 请求无超时可无限挂死、CSV 不规则行崩溃致整文件入库失败。全量回归 **283 项全绿**。

### 修复

- **窗口回放加字符预算**（`user_store.py` + `agent.py`）：`get_window_context` 新增 `max_content_chars` 参数（从旧到新累计，超预算保留最近的），agent 回放传入 `MEMORY_BUDGET_TOKENS`（20000）——长对话不再撑爆 DeepSeek 上下文窗口被 400 拒绝成「大模型暂时无响应」
- **拆分子 PDF 写入临时目录**（`engine.py`）：`_split_pdf` 输出目录参数 `cache_dir`→`output_dir`，调用处改传临时 `run_dir`，子 PDF 随 `run_context.cleanup()` 自动删除——不再在持久缓存目录累积孤儿 chunk 无限占磁盘
- **MinerU API 全部补超时**（`mineru_extract.py`）：申请上传 URL 30s、任务状态轮询 30s、结果下载 (30,120)s——连接半开不再无限挂死占死 task_manager worker
- **CSV 不规则行容错**（`csv_ext.py`）：全空行判断对 `csv.DictReader` 补的 `None` 值容错 + 逐行 try 跳过坏行——不规则 CSV 不再整文件入库失败

### 测试

- 全量回归 **283 项通过**（4 项修复零回归）

### 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/skills/agent.py` | 窗口回放传 `MEMORY_BUDGET_TOKENS` 预算截断 |
| `scripts/user_store.py` | `get_window_context` 新增 `max_content_chars` 参数 |
| `scripts/doc_mgr/engine.py` | `_split_pdf` 子 PDF 改写临时 `run_dir`（参数名 cache_dir→output_dir） |
| `scripts/mineru_extract.py` | get_upload_urls / get_task_status / download_result 补 timeout |
| `scripts/doc_mgr/extractors/csv_ext.py` | 全空行 `(v or "")` 容错 + 逐行 try |

---

## v1.10.0（2026-08-07）

**🖼️ 识图功能**——小助手从此「看得见」。钉钉发图自动识别并描述内容（qwen3.7-flash 视觉外挂），识别结果直接回流回复；同时新增 `tools/describe_image` 工具注册，DeepSeek Agent 将来可主动调图；Claude Code 侧同步落地 vision skill。顺带修复 MinerU OSS 上传被 Clash 劫持的隐患，并用真实 254 页扫描件完成超 200 页拆分入库全链路验证。回归 **283 项全绿**。

### 功能 A：钉钉识图（重点）

- **发图即识别**（`dingtalk_bot.py` `_handle_image_message`）：图片保存后逐张调视觉模型，成功张回 `📝 描述`、失败张 `⚠️ 已保存但识别失败`、未配 key 回退旧「图片已保存」文案——单张失败只降级该张，不拖垮整条
- **识别结果直接回复**：不走 `_process_text` 回流 DeepSeek，省一次往返
- 提示语 `🖼️ 收到图片，正在识别内容，请稍候...`

### 功能 B：describe_image 工具 + 视觉外挂

- **新工具**（`tools/describe_image.py`，`@register` 注册）：Agent 将来可主动调用 `{"image_path": ...}`
- **供应商无关设计**：httpx POST 阿里云百炼 OpenAI 兼容协议 `/chat/completions`，模型 `qwen3.7-flash`（可升 plus）
- **安全加固**：magic bytes 检测真实图片格式（PNG/JPEG/GIF/WebP/BMP，不信任扩展名）；8MB 大小守卫；路径白名单（必须位于 `data/uploads/`，防提示注入诱导读任意文件 base64 外传）；`_RETRYABLE_STATUS=(429,500,502,503,504)` 指数退避重试
- **配置**：`config.py` 导出 `DASHSCOPE_API_KEY` / `VISION_MODEL`；key 存 `local_config.py`（gitignore 不入库）

### 功能 C：Claude Code vision skill

- 用户级 skill `~/.claude/skills/vision/`：底层模型无原生视觉，收到图片时运行 `D:\claude-vision-skill\vision.js` 借助千问视觉模型识图

### 修复与验证

- **MinerU OSS 上传绕 Clash**（`mineru_extract.py`）：裸 `requests.put` 改 `_NET_SESSION.put`（已 `trust_env=False`），消除系统代理劫持导致的 `connect timeout`（实测从 180s 超时重试全败 → 5s 秒传）
- **真实 254 页扫描件端到端入库**：《PCB和电磁兼容设计》无文字层 PDF，拆分 200+54 两段 → MinerU VLM 识别 → 合并 → 226 块入库，识别质量（封面/ISBN/出版社）正确，总耗时约 3.5 分钟

### 测试

- 新增 17 项：`test_describe_image.py`（注册/参数/路径白名单/格式识别/请求体组装/5xx 重试/超时降级 13 项）+ `test_dingtalk_bot.py` 图片识图 4 项
- 全量回归 **283 项通过**（此前 266 项）

### 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/tools/describe_image.py` | 🆕 识图工具（qwen3.7-flash 视觉外挂，magic bytes + 白名单 + 重试） |
| `scripts/tools/__init__.py` | 注册 describe_image |
| `scripts/skills/dingtalk_bot.py` | 图片消息识图：保存后逐张识别，📝/⚠️/无 key 三级降级 |
| `scripts/skills/agent.py` | `_TOOL_DISPLAY` 加识图卡片 |
| `scripts/config.py` | 导出 DASHSCOPE_API_KEY / VISION_MODEL |
| `scripts/local_config.py` | 🆕 DASHSCOPE_API_KEY（gitignore 不入库） |
| `scripts/mineru_extract.py` | OSS 上传绕 Clash：`requests.put` → `_NET_SESSION.put` |
| `tests/test_describe_image.py` | 🆕 13 项识图工具测试 |
| `tests/test_dingtalk_bot.py` | 追加 4 项图片识图测试 |

---

## v1.9.1（2026-08-07）

**Agent 体验优化**——工具调用早期通知 + 系统提示词改进。Agent 在 tool_call 首次出现时立即通知（不等参数累积完），减少用户感知空白期；系统提示词新增"不主动汇报用户信息"规则，避免 bot 看到钉钉用户档案后主动说出来。回归 **266 项全绿**。

### 改进

- **工具调用早期通知**（`agent.py`）：`_call_deepseek_stream()` 中，tool_call delta 首次出现时立即 `on_chunk("🔍 搜索故障知识库...", "tool_call")`，不等参数累积完再通知。Web 端同样受益
- **系统提示词优化**（`system_prompt.txt`）：新增规则"你能看到用户的姓名、部门等信息，但不要主动说出来，除非用户明确问"；删除"回答最后可以问一句是否还需要进一步帮助"

### 测试

- 移除 14 项 AI 卡片测试（功能已回退）
- 全量回归 **266 项通过**

### 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/skills/agent.py` | 工具调用早期通知：tool_call 首次出现立即 on_chunk |
| `scripts/skills/dingtalk_bot.py` | 移除 AI 卡片代码（_StreamingCard 类 + httpx import）；process() 改回普通 markdown 回复 |
| `scripts/prompts/system_prompt.txt` | 新增"不主动汇报用户信息"规则；删除"回答最后问是否还需要帮助" |
| `tests/test_ai_card_streaming.py` | 删除（AI 卡片功能已回退） |
| `tests/test_dingtalk_bot.py` | 修复 test_text_reply_and_memory_also_in_to_thread 适配 markdown 回复 |

---

## v1.9.0（2026-08-07）

**引用溯源 + 反馈机制 + Prompt 后台配置**——三项 RAG 可观测性与可运维能力补齐。搜索结果统一生成 `source_label` 引用标签（LLM 直接复制即可）；Web/钉钉双通道 👍/👎 反馈收集；系统提示词可通过管理后台在线编辑、即时生效。回归 **266 项全绿**。

### 功能 A：引用溯源标准化

- **source_label 统一生成**：3 个搜索工具（`search_knowledge_base.py` / `search_standards.py` / `search_experience_kb.py`）返回结果自动附加 `source_label` 字段（如 `[GB/T 34133 第6章 第12页]`），LLM 直接引用无需自拼格式
- **system_prompt.txt 引用规则**：新增「来源标注规则」章节，要求 LLM 直接复制 source_label、末尾用「参考来源：」汇总

### 功能 B：👍/👎 反馈机制

- **数据库层**（`user_store.py`）：新增 `feedback` 表 + `add_feedback` / `get_feedback_stats` / `get_last_conversation` 方法；rating 校验 + 内容截断（500 字）
- **Web 端**（`web_page.py`）：每条 bot 消息后追加 👍/👎 按钮；点击后 POST `/feedback`、toast 提示、按钮锁定（CSS 变色 + pointer-events: none）
- **钉钉端**（`dingtalk_bot.py`）：每条回复末尾追加「回复 1 = 满意 👍 | 回复 2 = 不满意 👎」；用户回复 1/2 自动识别为反馈指令，从最近对话反查 query/answer 写入 feedback 表
- **API**（`main.py`）：新增 `POST /feedback` 端点（q/answer/source/rating/user）

### 功能 C：Prompt 后台配置

- **数据库层**（`user_store.py`）：新增 `prompts` 表 + `get_prompt` / `set_prompt` / `list_prompts` 方法；upsert 覆盖写入
- **加载改造**（`agent.py`）：`_load_system_prompt()` 优先从 DB 读取 → 回退文件 → 内存缓存（`_prompt_cache`）；新增 `reload_system_prompt()` 供后台清除缓存
- **管理页面**（`router.py`）：`GET /admin/prompts`（textarea 编辑页）、`POST /admin/prompts/system`（保存生效）、`POST /admin/prompts/reset`（重置为文件默认）；`GET /admin/feedback-stats`（反馈统计 JSON）

### 测试

- 新增 25 项（反馈 CRUD 6 + 最近对话 3 + Prompt 管理 5 + 便捷函数 4 + source_label 4 + Agent 加载 3）
- 修复 1 项：dingtalk_bot 旧测试 `test_text_reply_and_memory_also_in_to_thread` 适配反馈后缀
- 全量回归 **266 项通过**（此前 241 项）

### 修改文件

| 文件 | 改动 |
|------|------|
| `scripts/user_store.py` | +feedback 表 +prompts 表 +6 个新方法 +4 个便捷函数 |
| `scripts/main.py` | +POST /feedback 端点 |
| `scripts/web_page.py` | +反馈按钮 CSS/JS +toast |
| `scripts/tools/search_knowledge_base.py` | +source_label 字段 |
| `scripts/tools/search_standards.py` | +source_label 字段 |
| `scripts/tools/search_experience_kb.py` | +source_label 字段 |
| `scripts/prompts/system_prompt.txt` | +来源标注规则 |
| `scripts/skills/agent.py` | _load_system_prompt 改从 DB 读取 +缓存 +reload |
| `scripts/doc_mgr/router.py` | +feedback-stats +prompts 页面/端点 |
| `scripts/skills/dingtalk_bot.py` | +反馈指令识别 +回复追加反馈提示 |
| `tests/test_feedback_and_prompts.py` | 新建（25 项） |
| `tests/test_dingtalk_bot.py` | 修复 1 项适配反馈后缀 |

---

## v1.8.0（2026-08-07）

**文档格式补齐**——新增 Word (.docx)、PPT (.pptx)、CSV (.csv) 三种格式支持，支持格式从 3 种扩展到 6 种。Word/PPT 转为 Markdown 后复用 MarkdownChunker 切块；CSV 逐行格式化直接入库（同 Excel 模式）。回归 **241 项全绿**。

### 新功能

- **Word 提取器**（`doc_mgr/extractors/word.py`）：python-docx 按文档顺序遍历段落+表格，Heading 1~6 → # ~ ######，表格 → Markdown 表格（| col |），列表 → - 条目
- **PPT 提取器**（`doc_mgr/extractors/pptx_ext.py`）：python-pptx 按 Slide 提取，标题 → ## Slide N: 标题，备注 → > blockquote 格式
- **CSV 提取器**（`doc_mgr/extractors/csv_ext.py`）：stdlib csv 模块，自动编码检测（utf-8-sig → gbk → gb18030 → latin-1），自动分隔符检测（逗号/制表符/分号/竖线）
- **引擎路由扩展**（`engine.py`）：process_file 新增 3 个 elif 分支 + 3 个处理函数（_process_word / _process_pptx / _process_csv），默认 collection：docx/pptx → standards，csv → error_codes

### 格式支持总览

| 格式 | 处理方式 | 默认 Collection |
|------|---------|----------------|
| .pdf | MinerU / PyMuPDF | standards |
| .xlsx / .xls | openpyxl 逐行 | error_codes |
| .md | MarkdownChunker | standards |
| .docx ✨ | python-docx → Markdown → MarkdownChunker | standards |
| .pptx ✨ | python-pptx → Markdown → MarkdownChunker | standards |
| .csv ✨ | stdlib csv 逐行 | error_codes |

### 依赖

- 新增 `python-docx>=1.1.0`、`python-pptx>=0.6.23`
- CSV 使用 stdlib，无新依赖

### 测试

- 新增 14 项（Word 提取器 4 + PPT 提取器 2 + CSV 提取器 4 + 引擎路由 4）
- 全量回归 **241 项通过**（此前 227 项）

## v1.7.0（2026-08-06）

**钉钉通讯录员工查询**——新增 `find_employee` 工具，恩特小助手可查公司员工（姓名/部门/职位/工号）；手机号/邮箱等敏感字段仅审核人可见（服务端剥离）。回归 **227 项全绿**。

### 新功能

- **find_employee 工具**（`tools/find_employee.py`）：DeepSeek function calling 自动识别「谁负责采购」「研发中心王工」「张三电话多少」等找人问题并调用；走 tools 注册中心，agent.py 零改动自动生效
- **通讯录客户端**（`contact_api.py`）：部门树 BFS 遍历 + 逐部门 cursor 分页拉取 + 多部门去重合并 + 部分失败降级（单个部门拉取失败不整体报错，返回 warning）
- **实时数据**：每次查询实时拉钉钉通讯录；60 秒进程内短 TTL 缓存规避钉钉 QPS 限流（不做磁盘持久化）
- **匹配**：姓名/职位/工号/部门名任一子串命中；支持部门过滤与 userid 精确查询；结果按【】编号格式输出（钉钉友好）

### 权限与安全

- **基础信息全员可查**：姓名/部门/职位/工号
- **敏感字段仅审核人**：mobile/email 在服务端直接剥离（键不进返回 JSON，不依赖 LLM 自觉）；审核人白名单 `CONTACT_ADMIN_STAFF_IDS`（local_config 配置，留空 = 无人可查联系方式）
- **staff_id 上下文注入**：钉钉消息发起者 staff_id 经 contextvar 透传给工具（`set_current_staff_id`/`get_current_staff_id`）；Web 端无身份 → 天然只能查基础信息

### 接口切换（排障记录）

- 初版用新版 `api.dingtalk.com/v1.0/contact/` 接口，实测该应用全部 404（`InvalidAction.NotFound`，疑似网关/权限范围差异）
- → 改用旧版 `oapi.dingtalk.com` topapi（`gettoken` + `department/listsub` + `user/list` + `department/get` + `user/get`），与 user_store 同步用户信息同族接口，实测可用

### 提示词与文档

- system_prompt 能力清单加第 6 项 find_employee（含触发示例）
- 改写「绝对不能编造」段：员工信息必须经 find_employee 工具查询后回答；工具未返回的联系方式 = 无权限，直接说明，禁止编造

### 测试

- 新增 19 项（`contact_api` + `find_employee`）：token 缓存、部门树 BFS + 部门名映射、多部门去重、分页与死循环防护、姓名/职位/部门匹配、敏感字段脱敏、contextvar 身份传递、部分失败降级、权限错误友好化、缓存 TTL/force、工具注册
- 全量回归 **227 项通过**（此前 208 项）

### 真实环境验证（发版后补记）

- 90 名员工全量拉到，无部分失败；真实部门名/职位/工号正确

## v1.6.2（2026-08-06）

**上下文工程 + 长期记忆修复**——系统性修复长期记忆压缩失效（V4 思考挤空 content）、压缩误伤短期窗口的 bug，并把上下文组装重构为 KV Cache 友好结构（静态 system + 历史独立轮次 + 窗口批量滚动），加缓存命中率观测。回归 **208 项全绿**。

### 长期记忆修复（根本原因）

- **压缩一直「无有效输出」的根因**：DeepSeek V4 是思考型模型，压缩长对话时 reasoning token 占满 max_tokens(600) 把 content 挤空 → `memory_compress` 关闭 thinking（`thinking: disabled`）+ max_tokens 600→1000，压缩稳定返回 facts/summary（此前长期记忆表一直为空）
- **压缩失败不再标记 compressed**：失败批次保留待下次重试，避免「压缩失败却标记完成」导致旧对话被静默丢弃
- **压缩误伤短期窗口 bug**：`get_compress_batch` 原取「最旧 N 条未压缩」含窗口内对话，压缩后短期记忆被清空 → 改为按滚动点只取「窗口外」对话；并恢复 26 条被误标记的窗口内对话

### 上下文工程改造（KV Cache 友好）

- **静态 system**：用户档案/短记忆/长期记忆全部移出 system prompt，system 只留纯静态提示词（前缀缓存稳定）
- **历史独立轮次**：短期对话作为独立 user/assistant 消息回放（不再拼进 system 字符串）
- **窗口 20 轮 + 释放 1/2 批量滚动**：新增滚动点（`user_window` 表），窗口满 20 轮一次滚掉前 10 轮、保留最近 10 轮——两次滚动之间前缀稳定（替代原「每轮滑动」前缀每轮断裂）
- **动态内容放末尾**：用户档案 + 长期记忆拼在当前问题的 user 消息（消息末尾），更新只影响末段
- **缓存命中率日志**：`_call_deepseek` 记录 `prompt_cache_hit_tokens / miss` + 命中率，可直接观测改造收益

### 配置与完善

- 记忆预算 12000 硬编码 → `MEMORY_BUDGET_TOKENS=20000`（config 可配置）
- 长记忆参数扩大：注入条数 8→10、每用户上限 50→200、单条截断 150→200
- system_prompt 补齐 `calc_copper_busbar`（铜排载流，此前漏写第 5 个工具）
- 压缩调度触发改为「滚动点前有未压缩对话」（对应释放后后台压缩）

### 测试

- 更新 8 项记忆测试对齐新窗口机制（滚动点 / 窗口外压缩 / 失败保留）
- 全量回归 **208 项通过**

### 真实环境验证（发版后补记）

- 重启服务后 34 次真实请求：缓存命中率 **≈0% → 78%**（总），74% 的请求 ≥80%，多数稳定 **85-98%**（改造前动态 system 前缀每轮断裂，命中率 ≈0）
- 低命中的 4 次均为窗口滚动 / 新会话首轮（设计预期：滚动时才断一次，平时稳定命中）
- 长期记忆压缩真实入库验证：16 条对话 → 4 facts + 1 summary

## v1.6.1（2026-08-06）

**多人并发 v1.6.0 代码审查修复**——系统性检查后修复并发隐藏 bug：`asyncio.Lock` 跨事件循环绑定（钉钉断线重连后同用户并发消息抛 RuntimeError）、回复/记忆写入仍在事件循环阻塞（多人并发收益被回复环节抵消）、多处懒加载单例非线程安全。全部走线程池放行 + 双检锁，回归 **208 项全绿**。

### 并发 bug 修复

- **P1 锁跨事件循环绑定**：`_user_locks` 从裸 `asyncio.Lock` 改为 `(loop, lock)` 结构，`_get_user_lock` 用 `asyncio.get_running_loop()` 检测事件循环重建（钉钉重连时 SDK 每次 `asyncio.run` 都是新循环）自动重建锁。修复前旧循环中发生竞争的锁在新循环复用会抛 `RuntimeError: bound to a different event loop`（被外层 except 吞掉返回错误码 1000）
- **P2 回复阻塞事件循环**：文本消息路径的 `reply_markdown`/`reply_text`（同步 `requests.post`，无 timeout）与记忆写入（`memory.add` SQLite）原在事件循环直接执行，一人回复慢会卡住所有用户。现全部 `await asyncio.to_thread(...)`，与文件/图片路径行为对齐
- **P3 命名修正**：`_sync_user_info_async` 实为同步方法（内部自开线程），改名 `_sync_user_info`，调用处整体放线程池，不再阻塞事件循环

### 懒加载单例线程安全（并发放行后暴露）

v1.6.0 把处理放线程池后，首次调用可能在多线程下并发触发，以下单例统一加 `threading.Lock` 双检锁：

- `user_store.get_store()`：避免并发重复建表
- `doc_mgr.storage.get_store()`：避免并发重复加载 Chroma + embedding 模型（初始化较重）
- `doc_mgr.task_manager.get_manager()`：避免并发重复创建 ThreadPoolExecutor（泄漏线程）
- `knowledge_review.get_review_service()`：避免并发重复实例化 SyncTracker/DingTalkNotifier

### 测试

- 新增跨循环锁重建 2 项测试：同一循环复用同一锁、不同事件循环自动重建（防回归）
- 新增回复/记忆放行 to_thread 测试：`reply_markdown` 作为可调用对象传入 to_thread、`memory.add` 放行 2 次
- 更新既有 to_thread 测试适配新实现（to_thread 现被多次调用，按可调用对象筛选）
- 全量回归 **208 项通过**（此前 205 项 + 新增）

## v1.6.0（2026-08-05）

**多人并发消息处理**——钉钉机器人此前单线程事件循环被慢请求（DeepSeek/检索/文件下载）占死，一人问 LLM 问题，其他人全部排队。现在慢操作放行到线程池（`asyncio.to_thread`），多人同时交流互不阻塞；同一用户消息串行、不同用户并行（回复不乱序）；DeepSeek 请求加并发限流（默认 20），防费用失控 / API 429。

### 并发放行（核心）

- **事件循环不再被阻塞**：`process()` 的文本/文件/图片处理全部 `await asyncio.to_thread(...)` 放行，等待期间继续处理其他用户消息（秒回技能同样放行，毫秒级感知不到线程切换开销）
- **文本路由抽成 `_process_text`**：审核口令 → 审核消息 → 技能匹配 → 兜底，同步方法在线程池执行，不污染事件循环
- **contextvars 自动透传**：`asyncio.to_thread` 拷贝当前上下文，中心权限隔离（`set_user_centers`）不受影响
- **Web 端无需改动**：`POST /ask` 是同步路由，FastAPI 本身在线程池执行，天然并发安全
- **同一用户串行、不同用户并行**：每用户一把 `asyncio.Lock`（懒创建），同人连发消息严格按序处理（回复不乱序），不同用户各自独立并发

### DeepSeek 并发限流

- 新增配置 `MAX_CONCURRENT_LLM=20`（config.py），`threading.BoundedSemaphore` 限制同时进行的 DeepSeek 请求数
- 防多人同时问导致费用失控 / API 429 限流；重试退避不占并发槽（信号量仅覆盖网络等待）

### 测试与修复

- 新增 9 项测试：`_process_text` 路由（审核口令/审核消息/技能/兜底）、to_thread 包装、并发限流（6 路并发峰值验证）、同人串行/异人并行行为验证
- 顺手修复 test_memory 时间依赖 bug：mock 时间改为「当前时间 + 5s」，此前硬编码 10:00 导致每天 12:00 后该测试必挂
- 修复并发测试泄漏 mock 的坑：`asyncio.gather` 内并发协程各自 `mock.patch` 同一属性会因恢复顺序竞态泄漏 mock，patch 必须提到 gather 外层统一做一次
- 全量回归 **205 项通过**

## v1.5.6（2026-08-05）

**PCB 计算体验升级 + 记忆/性能优化**——王哥（PCB 部门主管）实测反馈驱动：新增通用参数补足层（54 类计算器声明参数表，缺失参数自动补足 + 多组合表格）、安规 AC/DC 与海拔识别、紫铜/铜排路由与专用工具、铜排温度语义，同时完成国内 API 全直连、记忆存储/注入优化和性能预热。

### 国内 API 全直连（代理劫持修复）

- **根因**：服务在 Clash 开着时启动，httpx/requests 默认跟随系统代理（127.0.0.1:7890），Clash 崩溃后出现 10061/10054/SSL EOF 混合故障
- httpx 强制直连：agent / error_query / memory_compress（`trust_env=False`）
- requests 强制直连：user_store（钉钉同步）/ dingtalk_notifier / mineru_extract / file_handler
- DeepSeek、钉钉、MinerU 均为国内服务，从此无视系统代理（10061 等不再出现）

### 通用参数补足层（架构级新能力）

- **声明式参数表**：`Param` dataclass + `CALC_DEFS`，54 类计算器声明可补足参数（名称/单位/默认值/常用组合/提取器）
- **补足引擎**：识别缺失参数 → 注入 2~3 个常用备选值 → 复用现有公式多组计算 → **markdown 表格输出**
- **反算支持**：求解目标参数不算缺失（如"求线宽"时只补温升/铜厚/层）
- **多组输入**：「3A 和 5A 各要多宽」同时输出两组结果
- 参数全给时不出现表格；模糊参数给歧义提示

### PCB 参数识别增强

- 温升支持「30度/30℃/30°C」（修复王哥反馈的"写了 30 度还是用 10 度"）
- 内外层词表扩充：内电层/中间层/电源层/表层/顶层/底层
- 铜厚支持「1盎司」
- 阻抗介电常数/差分间距/过孔壁厚/安规 PD/材料组/铜排温升密度等默认值输出标注

### 王哥实测问题修复

- **安规 AC/DC**：识别交流/直流，直流峰值 = 电压本身（不再误乘 √2，1000V 直流电气间隙从 0.748mm 修正为 0.57mm）
- **海拔识别**：支持「3000M海拔/海拔3000m/3000米海拔/大写M」，海拔 >2000m 修正系数生效
- **紫铜路由**：「紫铜 2x4」不再落 LLM 瞎算，路由到铜排计算器；新增 `calc_copper_busbar` 工具（牛顿散热 + ASTM B187）
- **铜排温度语义**：区分「环境温度 vs 温升」，模糊"温度"提示歧义

### 记忆系统优化

- **存储完整**：去掉入库前 500 字截断，长回答（如 PCB 多组合表格）尾部不再丢失
- **注入总预算**：单条不再截断，改总预算 12000 字保护（超出从最早丢弃）；窗口保持 8 轮
- 长期记忆压缩机制不变（facts + 摘要，只留关键）

### 性能与稳定性

- **bge-reranker 启动预热**：后台线程预加载，首次 RAG 查询不再冷启动约 3 秒
- **DeepSeek 耗时日志**：每次响应打印 `DeepSeek 响应 X.Xs`，便于定位瓶颈
- **修复 numpy 并发导入**：预热线程与崩溃恢复并发加载 numpy 导致 circular import（崩溃恢复先同步完成，预热错后执行）

## v1.5.5（2026-08-05）

**PCB 计算自然语言健壮性升级**——用 40 道社区真实问题（CSDN/电子星球/TI E2E/捷配/21ic）回归，修复「检测路由误判 + 参数提取缺陷 + 2 处公式/编码 bug」。**40 题从「7 对 26 败」提升到「36 对 0 败」**（4 题因 `match()` 判定落入 LLM 聊天兜底，属合理路由）。回归脚本 `data/pcb/pcb_forum_regression.py`，可随时复跑。

### 逻辑修复（检测路由误判，8 处）

- **具体词优先于泛化词**：晶振/负载电容 > "寄生电容"（晶振题不再误判 via_parasitic）；Snubber/振铃 > "寄生电容/参数"（Q26/Q28 不再误判）；Buck/降压/输出电容 > "纹波"（Q13 不再误判 pdn）
- **过孔寄生需过孔语境**（"过孔/板厚/焊盘/孔径"），避免劫持晶振、Snubber 的"寄生电容"
- **开关电源需损耗语境**："开关电源中走线承载多大电流"不再误判 smps（Q3 走线载流回归正常）
- **opamp 触发词补"放大器"**（"同相放大器"Q33 之前落到帮助菜单）
- **ADC 上下文守卫 + 分压检测补"从X得到Y"**：分压题不再因"ADC参考"误判 adc（Q34）

### 正则修复

- **`\b` 汉字边界 bug（系统性）**：Python Unicode 模式下汉字也是 word 字符，`5V供电` 的 V 与"供"之间无边界导致提取跳过正确值。修复 `_RE_VOLT` / LED / 稳压管 / 电功率 / 热设计的 `V\b`/`W\b`（Q30/Q32 串联 LED 之前全错）
- **`_num(v, 0)` 尾零 bug**：`rstrip("0")` 把 `_num(10,0)` 削成 "1"、`_num(50,0)` 削成 "5"（IoT 电池时长、crystal E24 推荐值显示全错）
- **LC 滤波器 `q` 变量覆盖入参 bug**：`q = z0/zl if zl else None` 把参数 q 覆盖为品质因数，导致后续用原始入参的行崩溃（Q36 新增第二组时暴露）
- **晶振匹配公式** `2(CL−Cstray)−Cin` → `2(CL−Cstray−Cin)`（Q16 偏差 2pF）

### 参数提取增强

- **过孔寄生**：板厚/孔径支持"1.6mm板厚"顺序；算 Lvia 后含"感抗"自动算 2πfL（Q8/Q9）
- **过孔载流**：孔壁铜厚 μm 优先于 oz（"25μm（1oz）"按 25μm 算，Q10）
- **LDO 热设计**：支持从 Vin/Vout/Iout 自动算功耗；`环境温度TA=45` 提取；铜箔面积 ∝1/√A 缩放 RθJA（Q17/Q18/Q19）
- **走线压降**：mil/oz/内层语境走 trace（不再误入 wire_drop）；`50mm长、0.5mm宽` 标签区分宽度与长度；温度对压降影响场景（Q20/Q21/Q22）
- **I2C 上拉**：多器件电容聚合（N×Ci + 走线 pF/cm）+ 给定上拉反推上升时间是否满足（Q23/Q25）
- **差分**：支持"线宽从 A 改为 B"变宽对比（Q6）
- **阻抗**：介质高度支持"1.6mm板厚"；带状线支持"总厚度1.2mm"；走线宽标签优先；**去掉静默默认 1.0mm**（提取失败改提示，避免错误答案；Q7/Q40）
- **分压**：支持"从5V得到3.3V R2取10kΩ 求R1"反推（Q34）
- **电池**：周期工作负载（每 Xs 采样 Yms + 休眠电流）路由 iot_battery 计算平均电流（Q38，之前误按恒流差 200 倍）
- **Snubber**：双频法正则放宽（"振铃频率38.5MHz"）+ Snubber 电容支持 3×Cp（Q28）
- **运放**：支持 Rg（同相增益）+ 输入信号输出幅度（Q33）
- **LC 滤波**：支持"若改用 XμH 和 YnF"第二组对比（Q36）
- **趋肤深度**：增加铜厚利用判断（h 与 2δ 对比，Q37）
- **走线裕量**：支持"安全裕量30%"解析（Q2）

### 代码审查修正（自审回归验证后追加）

对本次全部改动做专项审查（构造反例实测），又修复 6 处：

- **`采样` 劫持 iot_battery**：「12位ADC 采样率1MHz」等含"采样"的非电池题会被路由到 IoT 电池——改为要求"采样"同时出现电池/休眠/续航/mA 语境才命中
- **divider 反推 R1 除零**：「从5V得到0V R2取10kΩ」→ `vout_target=0` 除零崩溃，加 `vout_target > 0` 守卫
- **via 寄生感抗单位**：`100MHz` 场景误显示 `@100GHz`（频率值直接当 GHz 显示）——按输入单位跟随 GHz/MHz/kHz
- **thermal 功耗电流泛匹配**：「输入电流100mA、负载电流60mA」会取到输入电流——改为"负载电流/输出电流"优先
- **I2C 聚合 elif 互斥**：同时有 pF/cm 和走线电容 XpF 时漏算后者（95pF 算成 90pF）——改为独立累加
- **buck 检测词误伤 pdn**：「Vdd1.2V 允许纹波电流5A 目标阻抗」被"纹波电流"劫持到 buck——移除该泛化触发词（Buck 题仍有"输出电容/输入电容/占空比+输入"兜底）

### 全量验收补充（54 类计算器冒烟 + JSON 演化审计后追加）

对全部 54 类计算器逐一构造典型输入冒烟测试（`data/pcb/pcb_all_smoke.py`，53 类全部跑通、0 崩溃），又修复 3 处预存检测词问题：

- **Buck 题含"效率/损耗"误走 buck**：「Buck 24V转12V 5A Rds10mΩ 效率多少」应算开关电源损耗（smps），被"Buck"拓扑名抢到选型——buck 检测加效率/损耗/Rds/Qg 语境排除
- **zener 触发词缺"稳压值/稳压二极管"**：「稳压值5.1V」走错到 LED 限流——补触发词
- **RS-485 被 impedance 拦截**：RS-485 题含"特征阻抗"，在 impedance 检测时被算成微带阻抗——impedance 检测排除 rs485 语境

### 验证

- 40 题回归：本地算对 36 / 落 LLM 4 / 失败 0（修复前 7/4/26）。其中 4 题（Q11/Q12 Buck、Q24 I2C、Q27 Snubber 功耗）因 `match()` 判定非 PCB 计算落入 LLM 聊天兜底——属**合理路由**：Q12 本地参数提取会把纹波电流误当负载电流（LLM 理解更准），Q24 本地可算但 match 词表边界，Q11/Q27 参数歧义/计算器不覆盖。DeepSeek 计算这些公式稳定可靠
- **全量 53 类计算器冒烟**：50 通过 / 0 崩溃 / 3 提示（参数表达问题，已用正确参数验证非 bug）
- **JSON→代码演化审计**：`pcb_calculation_formulas.json` 51 分类全部有对应计算器（另 3 类 creepage/layout/trace2152 为保留独有能力）；抽查 16 个核心分类的公式/系数与代码**逐条一致**（IPC-2221 k 值、微带 87/√(εr+1.41)、带状 0.67π(0.8W+T)、差分 0.48/0.347、snubber 双频、晶振 2(CL−Cs−Cin)、via 5.08h、LDO 功耗等）
- 65 项 PCB 单元测试全绿；全量 166 项测试仅剩 1 项预存失败（`test_session_timeout_generates_new`，mock datetime 与 `_resolve_session_id` 实现不一致的时间敏感测试，与本次改动无关，已用 git stash 验证）

## v1.5.4（2026-08-05）

**PCB 计算技能全套升级**——以主管提供的权威公式源 pcb-tools.cn 为基准（data/pcb/），计算器从 13 类扩到 **54 类**（计算技能升级，非全新功能）：

### 新增

- **公式权威基准落地**：主管给的 pcb-tools.cn 公式全集（51 分类）存入 `data/pcb/pcb_calculation_formulas.json`，提取 3 张查表（ASTM B187 铜排载流 / 电阻色码 / 走线速查表）为独立数据文件，随 git 版本管理
- **新增 41 类计算器**（13→54）：
  - 高价值类：铜排载流（牛顿散热 + ASTM B187 实测表）、三相电功率、Snubber RC 吸收（双频法）、PDN 目标阻抗、开关电源效率与损耗、Buck 降压选型、额定电流速算、过孔寄生参数、热过孔热阻
  - 常用电路类：LC 谐振/滤波、感抗容抗、超级电容、电池续航/充电、IoT 电池寿命、导线压降、感应加热、运放/有源滤波/差动放大/惠斯通桥、三极管偏置、稳压管、LM317/MC34063、I2C 上拉、电阻并联/电容串联、电功率、ADC、RS-485、晶振匹配、SMD 焊盘、VSWR、频率波长、PWM、金属重量、EMC 单位、3W 串扰、555 定时器

### 修正（以 pcb-tools.cn 为准）

- 差分带状线系数 0.37 → 0.347（文件口径）
- LED 限流电阻支持串联 N 颗（R=(V−N·Vf)/I），Vf 参考表完整化
- 电阻分压升级为双源通用式（VR=(V1−V2)·R2/(R1+R2)+V2）
- 热设计升级为热阻链模型（TJ=TA+PD×(Rjc+Rcs+Rsa)），支持反推散热片 Rsa
- RC 增加瞬态 V(t)=Vi+(V0−Vi)e^(−t/τ)
- 走线载流增加官网速查表对照输出（公式 vs 官方曲线差异透明化）

### 保留（网站没有的独有能力）

- 安规 creepage（IEC-60664-1 查表 + 海拔修正）
- layout 布局综合校验（载流 + 安规 + 空间冲突判定 + 设计建议）

### 验证

- 新增 15 项 PCB 测试（以 pcb-tools.cn 数值为基准断言），全套 **166 项测试全绿**
- 全量冒烟覆盖 54 类端到端；修复 30+ 处自然语言参数提取健壮性问题

## v1.5.3（2026-08-05）

**双层会话记忆升级**——记忆从「最近 5 轮流水账」升级为「短期窗口 + 长期记忆」双层结构（记忆功能升级，非全新功能）：

### 新增

- **长期记忆表**（`long_term_memories`）：滚出短期窗口的旧对话由 LLM 异步压缩成「事实（fact）+ 摘要（summary）」入库，agent 常驻注入 system prompt（【长期记忆 - 用户历史背景】段），跨会话记得用户重要信息（项目 / 型号 / 偏好 / 未决事项）
- **异步压缩管线**（`skills/memory_compress.py` + `prompts/memory_compress.txt`）：`memory.add` 写后自动检测未压缩消息数，超阈值（默认 12 轮）在 task_manager 后台线程压缩窗口外最旧 8 轮——非阻塞、防抖、LLM 失败不卡流程、按 user_id 隔离不串用户
- **`task_manager.run_async`**：通用后台任务方法（复用线程池 + 任务列表可观测性，不动文档管线）
- **session_id 打标**：`conversations.session_id`（原预留字段）按时间间隔写入，供长期记忆来源追溯，为将来 Web「新对话按钮」留挂载点
- **短期窗口 5→8 轮**（`MAX_CONTEXT_ROUNDS`）；**配置开关**：`LONG_TERM_MEMORY_ENABLED` 等 8 项（默认开启，`False` 时行为与升级前一致）

### 数据迁移

- 首次启动幂等 backfill：旧消息除每用户最近一个窗口外标记 `compressed=1`（保留当前上下文，避免存量海量历史触发一次性批量 LLM 压缩）；长期记忆自本版上线日生效，不迁移历史内容

### 验证

- 新增 23 项记忆测试（短期窗口 / 去重 / session_id / 长期记忆 / 压缩 / JSON 解析 / 调度防抖 / backfill），全套 **151 项测试全绿**
- 按 user_id 隔离验证不串用户

## v1.5.2（2026-08-04）

**修改文件归属 + 修复 bge-reranker 重排静默失效**：

### 新增

- **管理后台支持修改文件归属**（上传后 / 入库后）：
  - 同步管理页每个文件行新增「归属中心」下拉（🌐公开 / 📋PMO / 🔬研发 / 🏭制造 / 💼商业 / ⚙️运营），默认显示当前归属
  - `sync-trigger` 新增 `department` 参数：已入库文件改归属后点「强制重学」即用新归属重写 Chroma metadata（版本替换）；未入库文件点「同步」用新归属入库
  - `sync-files` 返回 `suggested_department`；`update_department` 支持显式写回 `public`（改归属为全公司公开时需要）

### 修复

- **bge-reranker 重排静默失效**（`skills/enhanced_search.py`）：
  - 根因：BM25 混合检索对全库打分，RRF 融合后的候选 id 混入不在向量候选池（candidates）里的块，重排段访问 `candidates[cid]` 抛 `KeyError`，被降级保护吞掉——**重排模型自 v1.3.0 起一直未真正生效**
  - 修复：重排前过滤掉不在 `candidates` 里的 id，重排正常执行；实测开关重排，Top-5 排序结果明显变化

### 验证

- 128 项自动化测试全绿
- 隔离测试验证「改归属 → 强制重学 → Chroma metadata 更新 → 旧归属不再命中」链路
- 真实检索复现并确认 KeyError 消失、重排生效（1.1GB 模型正常参与打分）

## v1.5.1（2026-08-04）

**Web 端 UI 改版**——三块页面视觉统一 + 补上 RAG 助手关键体验（Markdown 渲染 + 来源展示）。纯前端改动，后端逻辑零变更：

### 新增

- **聊天页 `/`**（`scripts/web_page.py` 全量重写，后端 `/ask` 零改动）：
  - **手写 Markdown 渲染器**：表格 / 列表 / 代码块 / 粗体 / 行内代码 / 链接 / 引用，标准查询表格可正常展示
  - **来源 chip**：回答下方标注数据来源（📊遥信表 / 🔍语义搜索 / 📄标准编号 / 🧮PCB计算 / 💬agent 等），读取 `/ask` 已返回的 `source` 字段
  - **品牌视觉**：主蓝 `#4361ee` + 点缀色（参考 `docs/恩特小助手介绍页.html`）、渐变 logo、毛玻璃顶栏、hero 欢迎区、多色快捷提问 chips
  - 历史存储 key 升级 `entar_messages_v2`（存原始文本，渲染时统一转义 + Markdown），旧 `entar_messages` 数据自动迁移
- **管理后台 `/admin`**（`scripts/doc_mgr/views.py`）：顶部 Tab 条 → **左侧可折叠侧边栏 + 内容区**（移动端抽屉式开合），品牌令牌统一；5 个功能页交互逻辑原样保留
- **统计页 `/admin/stats`**（`scripts/doc_mgr/router.py`）：第三套旧风格 → 品牌令牌 + 深色模式切换，消除三块风格脱节

### 安全

- Markdown 渲染**先 `escapeHtml` 再结构化**，链接仅放行 `http/https`，防止存储型 XSS
- XSS 回归测试适配（统计页含合法 JS 块，改为验证「未转义的注入标签 ≤ 1 个」）

### 验证

- 后端 122 项测试全绿（除缺 `dingtalk_stream` 依赖的 1 项环境限制，属既有问题）
- Markdown 渲染器 15 项 node 用例全过（表格/列表/代码块/粗体/链接/XSS 转义/来源 chip 映射）
- 三个前端文件 Python + JS 语法校验通过；main.py 完整 import + 15 个 `/admin` 路由挂载正常

## v1.5.0（2026-08-04）

**第二步「经验知识库」启动**——从「故障 + 标准」扩展到部门工程经验沉淀：

### 新增

- **`experience_kb` collection + 五段式经验模板**（`data/experience/`）：
  - 模板「故障现象 → 排查步骤 → 根因 → 解决方案 → 验证结果」，一个条目一个 `.md`，MarkdownChunker 按标题切块入库
  - 复用 `process_file` 的 `.md` 链路 + `target_collection="experience_kb"`，存储层自动建库零改动
- **查询链路**：
  - `skills/experience_query.py`：`search_kb()` 混合检索 + 格式化（【标题】【阶段】【内容】【来源】）
  - `tools/search_experience_kb.py`：Agent 工具，LLM 判断「经验类」问题时自动调用；无独立技能避免与 RAGAgentSkill 双路由
  - `system_prompt.txt` 增加经验库工具使用说明
- **入库/审核链路**：
  - `router.py`：`FILE_DIRS`/`_ALLOWED_EXT`(.md)/`_ALLOWED_COLLECTIONS` 加 `experience_kb`，`.md` MIME 签名校验，`list_sync_files`/`sync_stats` 纳入新目录
  - `views.py`：上传/同步管理下拉框支持经验库
  - `knowledge_review.py`：`.md` 默认入经验库，审批口令支持「经验库」别名，通知文案含「经验知识库」
  - `sync_experiences.py`：CLI 批量灌库入口

### 验证

- 新增 18 项测试（查询 8 + 切块/入库 3 + 审核 4 + 路由 3），全套件 121 项通过
- 实测：示例经验入库 5 块、`search_experience_kb` 命中返回五段内容

## v1.4.2（2026-08-04）

PCB 计算技能新增布局综合校验模式（12 类 → 13 类）+ P1 安全收尾：

### 新增

- **布局综合校验模式**（`skills/pcb_calc.py`，`layout`）：
  - 一次输入「电流 + 铜厚 + 电压 + 材料组 + 可用宽度（沟道）」，同时输出**走线约束 + 安规间距 + 合计占用**并判定是否冲突
  - 走线约束按 IPC-2221（保守）判定并附 IPC-2152 参考；安规间距取电气间隙 / 爬电距离较大值（IEC-60664-1）
  - 合计占用采用保守假设：线宽 + 两侧各留安规间距（w + 2×spacing）
  - **冲突时按优先级给设计建议**：① 开槽 → ② 高 CTI 板材（III→II 爬电定量对比）→ ③ 三防漆（按污染等级降一档定量评估）→ ④ 加厚铜（线宽定量对比）→ ⑤ 调整布局
  - 支持内层 / 外层、污染等级 PD1~3、CTI 推断材料组、mil / mm 单位

### 安全收尾（P1）

- **上传校验增强**（`doc_mgr/router.py`）：文件名净化（去路径 / 危险字符 / 限长 120）+ 50MB 大小限制（超限 413）+ MIME 内容签名双重校验（PDF `%PDF` / xlsx ZIP / 旧版 xls OLE，防伪造扩展名）+ collection 白名单（防任意字符串注入路径）
- **Web 问答 GET→POST**（`main.py` + `web_page.py`）：问题内容不再进入 URL / 浏览器历史 / 代理日志；Web 的 `user` 参数明确为「仅记忆归属，不作为权限依据」，敏感操作一律走 `/admin` 密码或 user_store 鉴权
- **Docker 非 root**（`Dockerfile`）：新增 appuser（UID 1000）非 root 运行，挂载目录需在宿主侧 chown 为 1000:1000
- **XSS 回归测试**：`stats` 看板对用户可控字段（昵称/部门/用户ID）转义验证

### 验证

- PCB 计算测试 44 → 50 项；新增管理端安全测试 9 项；全套件 103 项（除缺 dingtalk_stream 依赖的 1 项外全部通过）
- 实测：3A/1oz/380V/材料组II/沟道15mm → ✅ 满足；10A/1oz/2.5kV/材料组III/沟道3mm → ❌ 冲突并给出分级建议
- 上传实测：伪造 `evil.pdf`（内容非 PDF）→ 400 拒绝；`../../evil.pdf` → 保存文件名净化；未知 collection → 400

## v1.4.1（2026-08-04）

PCB 计算技能扩展与修复（新增 3 类计算器 + 修复 2 个崩溃 bug）：

### 新增

- **PCB 计算技能 9 类 → 12 类**（`skills/pcb_calc.py`）：
  - **IPC-2152 精确载流**：ΔT^0.5·A^0.65 工程近似，与 IPC-2221 对照输出（已知电流反推线宽 / 已知线宽算载流 / 校验三模式）
  - **差分对阻抗**：Zdiff = 2×Z0×(1−0.48e^(−0.96s/h))，支持 USB 90Ω / 以太网 100Ω 目标**反推线宽**
  - **安规距离**：IEC-60664-1 电气间隙 / 爬电距离，查表 + 线性插值，支持污染等级 / 材料组 / CTI 推断 / 海拔修正 / 加强绝缘

### 修复

- **差分对计算器整体崩溃**：`re.search(已编译正则, str, flags)` 在 Python 3.7+ 抛 `ValueError`，正推/反推全挂 → 按 pattern 类型分支调用
- **信号波长计算崩溃**：`_extract_length` 无匹配返回 `(None, "m")`，误判导致 `1000 × None` TypeError，「100MHz 波长」直接报错
- **「0.2152 毫米」误路由**：`\b2152\b` 防不住带空格的数值，误进 IPC-2152 → 加 `(?<![\d.])` 负向断言

### 完善

- 爬电距离查表扩展至 4000V（IEC-60664-1 表 4A），修复 2.5kV 低估（材料组 II：7.1mm → 17.5mm）
- 电气间隙 / 爬电距离超出查表范围时输出「（超出表范围，取上界）」警示
- 信号波长支持 kHz / Hz 低频；趋肤深度频率提取重构，消除潜在 None 警告
- 材料组 "III" 走更保守的 III 列；清理 5 个未使用正则与 εr 冗余分支

### 验证

- PCB 计算测试 34 → 44 项，全套件 84 项自动化测试通过
- 实测：差分反推（100Ω→462μm）、波长（100MHz→1463.9mm）、安规（2.5kV 爬电 17.5mm）数值符合工程预期

## v1.4.0（2026-08-03）

PCB 设计计算技能 + 钉钉即时反馈 + rerank 生效 + 检索稳定性修复：

### 新增

- **PCB 设计计算技能**（全新技能，`skills/pcb_calc.py`）：9 类计算器，纯本地公式秒回，参数正则提取不调 LLM
  - **走线**：IPC-2221 线宽 / 载流 / 压降，含「校验」模式（给定线宽能否过目标电流）
  - **阻抗**：IPC-2141 微带线 / 带状线特征阻抗，支持**已知目标阻抗反推线宽**
  - **过孔**：载流能力 / 电阻 / 所需并联过孔数（环形截面）
  - **趋肤深度**：δ ≈ 66/√f mm（高频电流等效截面）
  - **信号**：走线传播延迟（ps/inch）与波长 / 1/4 波长
  - **热设计**：结温 TJ = TA + θJA × P，可反推允许功耗
  - **通用电路**：LED 限流电阻 / 电阻分压 / RC 时间常数
- **PCB 计算 Agent 工具**（`tools/calc_pcb_trace.py`）：口语化复杂表达由 LLM 识别参数后调用，计算逻辑与技能共用同一套公式
- **钉钉「正在处理」即时反馈**：走 Agent/LLM 的慢操作先回「⏳ 收到，正在处理中…」，文件/图片回对应提示；故障精确、PCB 计算等秒回操作不提示，避免打扰
- **错误码体系**：处理异常时返回「❌ 处理出错了（错误码：1000/1001）」，不再静默失败

### 完善

- **rerank 重排实际生效**：bge-reranker 模型下载完成并启用（v1.3.0 仅框架就绪），检索排序质量进一步提升
- **embedding 离线加载**：修复模型联网检查更新失败导致检索全空的问题，改为本地缓存加载，检索稳定性恢复
- 依赖新增 jieba / rank_bm25（混合检索），本地模型目录 models/ 不入版本管理

### 验证

- 74 项自动化测试通过（新增 PCB 计算 24 项 + 钉钉体验 7 项）
- 真实知识库实测：9 类 PCB 计算器全部路由正确；rerank 对故障检索排序有效

## v1.3.0（2026-08-03）

检索质量升级 — 混合检索 + 重排框架（对应用户最痛的「短关键词检索差」）：

### 新增

- **混合检索**（`skills/enhanced_search.py`）：语义向量检索 + BM25 关键词检索双路召回，RRF（Reciprocal Rank Fusion）融合排名
  - BM25 用 jieba 中文分词 + rank_bm25 稀疏检索，精确匹配字面词
  - 故障库与标准库的语义搜索路径统一接入，短关键词（如「过压」「硬件」）召回明显提升
  - BM25 索引带缓存（按 collection + 过滤条件），增量重建，重复查询零开销
- **重排框架**（同模块，`use_rerank=True`）：对融合后 Top-15 候选用 bge-reranker 精排，提升排序质量
  - 模型优先加载本地 `models/bge-reranker-base`（权重齐全才用），否则回退远程名
  - ⏳ 模型未下载完成（国内网速限制），代码就绪、模型到位自动启用；当前实测走混合检索路径

### 优化

- 精确匹配快速通道（故障代码 / 标准编号）不受影响，仍是不调 LLM 的毫秒级响应
- 降级链完整：jieba / rank_bm25 缺失、BM25 构建失败、rerank 模型不可用 → 自动回退纯向量检索，绝不崩溃

### 依赖

- 新增 `jieba`、`rank_bm25`（轻量纯 Python 包）
- 本地模型目录 `models/` 已加入 .gitignore（可重新下载，不退版本）

### 验证

- 33 项自动化测试通过
- 真实知识库实测：短词「过压」「硬件」混合检索全部命中相关故障，纯向量存在噪声

## v1.2.9（2026-08-03）

代码审查后的一次集中修复，重点解决多中心部门隔离失效和 /admin 安全缺口：

### 修复

- **多中心部门不再被清零**：`upsert_file` 改为「空值不覆盖」，同步路径（后台手动、批量、调度）不再把上传时指定的中心覆盖为 public；审核申请创建时把建议部门真正落库，审批通过后按真实部门入库（v1.2.8 多中心主流程缺陷）
- **/admin 读接口补齐鉴权**：collections / docs / search / upload-status 四个只读端点补密码校验，前端同步带 `getPw()`；上传表单补传 password（此前配置密码后前端上传实际不可用）
- **统计页 XSS 修复**：`/admin/stats` 用户可控字段（昵称、职位、部门、user_id）统一 HTML 转义
- **任意文件删除拦截**：sync-delete / sync-trigger 增加 data/ 目录路径白名单，防止删除或入库服务器任意路径文件
- **DeepSeek 瞬断自动重试**：对连接错误（10061）、超时、5xx、429 做指数退避重试（1s→2s），ISP 偶发波动不再导致查询全量失效
- **content 为 None 崩溃修复**：LLM 返回空 content（thinking 模式）时不再触发 `.strip()` 崩溃，Agent 与关键词提取均兼容

### 验证

- 33 项自动化测试通过（新增 4 项 department 行为回归测试）
- 全量 Python 语法检查通过

## v1.2.8（2026-07-22）

多中心部门知识库基础架构 + 用户中心管理：

### 新增

- **五中心组织架构配置**（`scripts/center_config.py`）：定义 PMO、研发、制造、商业、运营 + 公共区六类中心，提供 `resolve_center()`、`get_center_name()`、`list_centers()` 等工具方法
- **用户多中心归属**：`user_store.users` 表新增 `centers` 字段（JSON 数组），支持用户同时归属多个中心，提供 `get_user_centers()`、`set_user_centers()` 接口
- **入库部门标签**：`engine.process_file()` 新增 `department` 参数，所有新入库 chunk 自动写入 `department` metadata，按中心隔离
- **审核流程嵌部门**：钉钉上传审核通知展示上传人全部归属中心，审核人可在批准时指定或覆盖部门归属
- **多中心查询过滤**：`standards_query.search_kb()` 接受 `centers` 列表参数，自动构建 `$in` 过滤（用户中心 + 公共区），结果不足时回退到无条件搜索（兼容旧数据）
- **Web 用户管理页面**：管理后台新增「用户管理」Tab，支持勾选设置用户归属中心

### 安全

- `suggested_department` 沿审核流程逐级传递，审核人确认后写入入库管道，杜绝部门归属绕过审核
- 旧用户无 `centers` 字段时自动回退到 `center` 单字段，再回退到 `public`，不产生权限黑洞
- 旧数据无 `department` 标签时查询自动走无条件回退，存量数据不受影响

### 验证

- 29 项回归测试全部通过
- 多中心设置、读取、回退、格式化展示均通过独立验证

## v1.2.7（2026-07-22）

工具注册中心重构与上传审核工作流：

### 重构

- **工具注册中心**（`scripts/tools/`）：新增基于 `@register` 装饰器的工具注册表，替代 `agent.py` 中硬编码的 `TOOLS` 列表和 `if-elif` 工具路由。新增工具只需建文件 + 一行装饰器，无需改 `agent.py`
- **`agent.py` 精简 96 行**：`TOOLS` 定义从 36 行减为 1 行（`get_tool_definitions()`），`_execute_tool` 从 74 行减为 13 行（委托注册中心路由）
- **工具与定义同文件**：每个工具的 DeepSeek API schema 和执行逻辑放在同一模块，不再跨文件分散

### 新增

- **上传审核主动推送**：钉钉员工上传 PDF、Excel 或 Markdown 后，机器人主动单聊固定审核人，消息包含申请编号、上传人、文件名、大小和建议目标库
- **钉钉审批口令**：固定审核人可回复 `同意同步 申请编号` 或 `拒绝同步 申请编号 原因`；批准后复用现有后台任务同步知识库
- **审核人身份查询**：向机器人发送 `查看我的审核ID` 可取得配置主动推送所需的钉钉员工 ID
- **可扩展审核人解析器**：上传流程与审核人选择解耦，后续可从固定审核人切换为按上传者部门寻找主管

### 安全

- 仅申请中指定且当前配置有效的审核人可操作，申请采用一次性状态流转，重复回复不会重复入库
- 批准前校验源文件仍位于 `data/uploads/` 且扩展名受支持，主动消息不暴露服务器文件路径
- 钉钉主动消息在后台线程发送；网络失败不影响文件保存，文件继续留在待处理区

### 验证

- 新增主动消息参数、token 缓存、审核鉴权、重复审批、拒绝流程和路径越界测试
- 尚未向真实钉钉账号发送测试消息，也未对正式知识库执行批准入库

## v1.2.6（2026-07-21）

文档同步一致性与 MinerU 安全加固，避免重复入库、旧版本残留和不完整 OCR 结果进入知识库：

### 修复

- **同步状态可靠记录**：修复部分处理分支提前返回导致同步状态无法落库的问题；处理异常现在会统一记录为 `error`
- **内容变更准确识别**：使用文件 SHA-256 替代修改时间作为内容指纹，避免时间戳未变化或被复制工具改写时误判
- **Collection 路由一致**：统一在入口解析目标 Collection，PDF、Excel、Markdown 均尊重调用方指定的存储目标
- **Excel 行 ID 冲突**：行 ID 加入稳定文档身份，避免不同目录或同名工作簿的行号相互覆盖
- **MinerU 缓存失效**：缓存与源文件内容哈希绑定；强制重学会绕过缓存，不再复用过期 Markdown
- **大 PDF 分段完整性**：所有分段成功后才按顺序合并，任一段缺失即停止入库，避免半份文档进入知识库
- **查询版本漂移**：故障代码与标准查询统一复用文档管理存储实例，只返回当前可见版本

### 新增

- **稳定文档身份**：新增路径规范化文档 ID 与流式 SHA-256 工具，为版本替换和同步追踪提供统一标识
- **文档版本替换**：新版本先以 staging 状态分批写入并校验，再切换为 active；失败时清理新数据并保留旧版本可查询
- **旧版本隔离**：查询层自动隐藏 staging/retired 数据，物理删除失败时也不会把旧内容返回给用户
- **MinerU ZIP 安全校验**：拦截路径穿越、绝对路径、Windows 盘符、符号链接、异常压缩比及超限文件数量/体积
- **自动化回归测试**：新增文档引擎、同步追踪、MinerU 安全和版本替换测试，共 20 项

### 优化

- **大 PDF 临时文件隔离**：每段 MinerU 输出使用独立临时目录，合并文件采用原子替换，任务结束自动清理
- **写入并发保护**：替换、删除和查询共享可见性锁，降低同进程并发时新旧版本交叉暴露风险
- **向量存储复用**：查询模块不再各自创建 Chroma 客户端和 Embedding 实例，减少重复模型加载与状态分叉

### 验证

- `python -B -m unittest discover -s tests -q`：20 项测试通过
- 40 个脚本与测试文件通过 Python AST 语法检查
- 使用内存 Chroma 集成验证版本替换后，精确查询和语义查询均只返回新版本

## v1.2.5（2026-07-16）

用户文件上传 + Web 同步管理完善，MinerU 稳定性提升：

### 新增

- **Web 同步管理流程完善**：钉钉上传 → Web 后台「同步管理」Tab → 手动触发入库，支持进度条实时追踪
- **后台任务管理器**（`task_manager.py`）：基于 ThreadPoolExecutor 的异步任务队列，同步不阻塞 HTTP
- **同步进度追踪器**（`sync_tracker.py`）：SQLite 记录文件同步状态（pending/synced/error），含上传者信息
- **强制重学按钮**：跳过 Chroma 查重，重新走 MinerU 处理并入库
- **MinerU Token 统一管理**：`local_config.py` 新增 `MINERU_TOKEN` 配置项，优先级高于 `~/.mineru/config.yaml`

### 优化

- **目录结构重构**：源文件在哪个目录，`mineru_output/` 就建在哪个目录下
  - `data/standards/` → `standards/mineru_output/`（Web 上传文件的结果）
  - `data/uploads/` → `uploads/mineru_output/`（钉钉上传文件的结果）
  - `data/fault_codes/` → `fault_codes/mineru_output/`（预留）
- **OSS 上传自动重试**：遇到网络超时自动重试 3 次（5s → 10s → 报错），偶发抖动自动恢复
- **同步去重优化**：不再在同步前做 Chroma 预检查，每次都走 MinerU 下载最新 ZIP，入库时通过 chunk_id 自动去重
- **清理旧格式**：删除了 14 个旧格式目录 + 散落 ZIP 文件，去除 8 套标准的重复数据
- **修复 `dingtalk_bot.py` 变量 bug**：`_sync_user_info_async` 中 `sender` 未定义问题

### 技术栈

- 异步任务：`ThreadPoolExecutor`（2 个 worker），线程本地存储 + 锁保护
- 同步状态：SQLite `sync_status` 表
- OSS 上传：`requests.put()` + 自动重试，超时 180s
- 目录策略：`_get_mineru_output_dir()` 动态路径函数

## v1.2.4（2026-07-15）

用户存储结构升级 — SQLite 统一存储 + 权限系统 + 钉钉信息同步：

### 新增

- **SQLite 用户信息存储模块**（`scripts/user_store.py`）：统一存储用户信息、对话记忆、权限管理，替代原有 JSON 文件方案
  - 三张表：`users`（用户信息）、`conversations`（对话记录）、`permissions`（权限配置）
  - WAL 模式 + 线程独立连接，读写不互斥，并发安全
  - `get_store()` 单例模式，全局共享一个连接池
- **数据迁移工具**（`scripts/migrate_json_to_sqlite.py`）：一键将 `chat_memory.json` 数据迁移到 SQLite，支持 `--dry-run` 预览
- **钉钉用户信息后台同步**（`dingtalk_bot.py`）：用户首次发言或超过 24h 后自动同步钉钉用户信息（姓名、职位、部门、是否为管理者）
- **统计看板页面**（`/admin/stats`）：累计用户、今日活跃、每日消息趋势、对话量 Top 10、所有用户列表（含部门/职位/身份）
- **Web 管理页权限保护**：设置 `ADMIN_PASSWORD` 后，管理页面、上传、删除操作需要密码验证
- **权限系统**：基于 `permissions` 表的细粒度权限控制，支持 upload/delete/manage 三种操作，主管默认有上传权限
- **配置项**（`config.py`）：`MEMORY_BACKEND`（sqlite/json 后端切换）、`MAX_CONTEXT_ROUNDS`（对话轮数）、`ADMIN_PASSWORD`（管理密码）

### 重构

- **记忆模块代理层**（`memory.py`）：从直接读写 JSON 重构为统一代理层，支持 sqlite/json 双后端，默认 sqlite。JSON 路径作为兼容回退
  - 接口不变（`add()` / `get_context()` / `format_context()`），外部代码零改动
  - SQLite 操作异常时自动回退 JSON，不影响服务可用性
- **router.py 权限拦截**：upload/delete 端点增加统一权限检查，支持 password 验证和 user_id 权限判定双模式

### 技术栈

- 存储：SQLite（内置，零额外依赖）
- 钉钉 API：旧版 `oapi.dingtalk.com` 接口（`topapi/v2/user/get`），通过 appkey + appsecret 获取 token
- 线程模型：`threading.local()` 每线程独立连接 + `RLock` 写互斥

## v1.2.3（2026-07-14）

钉钉文件接收功能 + 代码结构优化：

### 新增

- **钉钉文件接收功能**（`scripts/file_handler.py`）：支持用户通过钉钉发送文件（PDF、Excel、图片等），自动下载并保存到服务器
- **文件保存目录**：`data/uploads/用户名_ID/日期/`，按用户和日期分类存储
- **System Prompt 外置**（`scripts/prompts/system_prompt.txt`）：将 LLM 提示词从代码中分离，便于维护和迭代

### 优化

- **钉钉机器人处理器**（`dingtalk_bot.py`）：支持文本、文件、图片三种消息类型
- **标准编号快速通道**（`standards_query.py`）：新增标准编号精确匹配，毫秒级响应
- **日志规范统一**（`mineru_extract.py`、`sync_mineru.py`）：所有 print 语句替换为 logging
- **搜索参数优化**（`error_query.py`、`standards_query.py`）：新增最小结果数量阈值
- **依赖说明更新**（`requirements.txt`）：补充 requests 和 MinerU 说明

### 技术栈

- 文件下载：复用 dingtalk_stream SDK 的 `get_image_download_url()` 方法，无需额外依赖
- 用户识别：通过钉钉 sender_id + sender_nick 区分不同用户

## v1.2.2（2026-07-14）

扫描 PDF OCR 技术栈升级 — MinerU 大模型视觉识别 + Markdown 切块入库：

### 新增

- **MinerU 文档提取脚本**（`scripts/mineru_extract.py`）：扫描 PDF 上传到 MinerU API → VLM 视觉识别 → 下载 Markdown/ZIP
- **MinerU 输出批量同步脚本**（`scripts/sync_mineru.py`）：扫描 `mineru_output/` 目录，批量将 MinerU 提取的 Markdown 入库到 Chroma
- **Markdown 切块器**（`doc_mgr/chunkers/markdown_chunker.py`）：基于 Markdown 标题层级（`#` `##` `###`）智能切块，支持中文标准章节号提取
- **doc_mgr 引擎支持 `.md` 文件**（`engine.py`）：新增 `_process_markdown()` 处理流程，MinerU 输出可直接入库
- **MinerU 提取结果**：9 份标准 PDF 已完成 OCR 提取（含 GB/T 34133、EN50178、IEC 60664-1 等）

### 技术栈

- OCR 路线：从传统 OCR（PaddleOCR）切换到大模型视觉识别（MinerU VLM），扫描 PDF → Markdown 质量更高
- 切块引擎：新增 MarkdownChunker，与 PyMuPDF 结构分析切块器并列，按文件类型自动选择

## v1.2.1（2026-07-13）

文档管理子系统（doc_mgr）——PDF 转换技术栈升级 + 统一上传→切块→入库：

### 新增

- **文档管理子系统**（`scripts/doc_mgr/`）：独立自洽的文档全生命周期管理模块
  - 存储抽象层（`VectorStore` ABC → `ChromaStore`，后续一行切 Qdrant）
  - PyMuPDF 结构分析切块器（替代 Unstructured，零额外依赖，对中文标准更可靠）
  - 管理 Web UI（`/admin` 路由，三 Tab：文档列表/上传/搜索测试）
  - 6 个 REST API 端点（列表/上传/搜索/删除/查询 collections/管理页面）
  - 上传文件自动保存 → 提取 → 切块 → 入库
  - 文件归属类型选择器（标准文档 / 故障代码，可扩展）
- **聊天页面新增「📂 文档管理」入口**，聊天 ↔ 管理双向导航
- **端到端增量写入**：`storage.add()` 自动跳过已存在 ID，分批写入（50/批）

### 优化

- **`sync_kb.py` / `sync_standards.py`** → 薄包装为 `process_file()` 调用层，向后兼容
- **`main.py` 统一挂载**：`app.include_router(admin_router)` 一行注册
- **PDF 切块质量**：多信号融合（章节号模式 + 字体名 + 左边界），页眉页脚自动清除
- **错误处理加固**：`list_docs` API 加 try/except，前端 fetch 加 `r.ok` 检查
- **前端安全性**：删除按钮改用 `data-*` 属性 + 事件委托，不再用内联 onclick

### 修复

- **`switchTab` 引用未声明 `event` 变量** → 改为传 `this` 参数
- **上传进度无反馈** → 2 秒后自动切换为「正在处理，请耐心等待...」

### 技术栈

- 切块引擎：PyMuPDF（已安装，零新依赖）
- 存储：Chroma（通过 `storage.py` 抽象，切换 Qdrant 只需改一处）
- 管理前端：内嵌 HTML/CSS/JS（同 `web_page.py` 模式，无模板引擎）
- 向量模型：BAAI/bge-small-zh-v1.5

## v1.1（2026-07-09）

记忆系统 + RAG Agent 升级：

### 新增

- **会话记忆模块**：用户对话记录持久化，支持跨会话上下文感知（`memory.py`）
- **用户名输入框**：Web 页面新增用户名输入，绑定对话记忆
- **RAG Agent 技能**：基于 DeepSeek Function Calling，LLM 自主决策是否查询知识库，统一故障查询 + 聊天托底（`agent.py`，替代原 `chat.py` + `error_query.py` 双技能路由）
- **搜索调参常量**：故障查询搜索参数集中管理（`K` / `n_results` / `score_threshold`）

### 优化

- **记忆去重**：防连续重复记录相同消息，节约 token
- **聊天上下文增强**：注入用户记忆，回答更贴合场景
- **Web 页面独立组件**：抽离 `web_page.py`，简化 `main.py` 入口
- **技能注册中心重构**：统一 `handle` 方法参数签名（`**kwargs`），提升可扩展性
- **Docker CPU torch**：Dockerfile 改用 CPU 版 PyTorch，省 ~13GB 镜像体积
- **查询逻辑优化**：防止重复提交，增强稳定性
- **日志清理**：钉钉机器人、增量同步脚本移除多余 print
- **用户记忆字数统计**：调试日志增加上下文字数，便于观测
- **CLAUDE.md**：新增 AI 协作规则（检查不等于修改、保持质疑主动补位）

### 修复

- **钉钉机器人处理逻辑**：提升稳定性与健壮性
- **`.pyc` 缓存坑**：修改代码后旧进程不释放端口，需强杀 + 清 `__pycache__`（开发文档已记录）

## v1.0（2026-07-09）

第一版正式上线：

- **故障查询**：PCS 故障代码精确匹配 + 语义搜索（Chroma + bge-small-zh）
- **通用聊天**：非故障问题走 DeepSeek 对话托底
- **技能注册中心**：通过 @register 装饰器注册/路由技能
- **钉钉 Stream 模式机器人**：搜索「恩特小助手」单聊使用
- **Web 页面**：FastAPI 提供 http://localhost:8000
- **云服务器部署**：Ubuntu 22.04 + 宝塔面板上线（7×24）

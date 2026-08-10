# 📋 恩特小助手 — 全部未完成事项

> 📌 **本文档是项目唯一的待办清单（单一事实源）**——CLAUDE.md、README.md、PROGRESS.md 中的待办均指向本文件。
> 更新待办请只改这里；进度看板见 [PROGRESS.md](PROGRESS.md)，版本记录见 [CHANGELOG.md](CHANGELOG.md)，项目规则见 [CLAUDE.md](CLAUDE.md)，功能说明见 [README.md](README.md)。

> 生成时间：2026-08-10｜当前版本：v1.10.2（详见 [CHANGELOG.md](CHANGELOG.md)）｜总计：**48 项**（8 完成 + 40 待办）

---

## 🔥 P0 — 阻塞级（4 项）

> 不做就卡住，发不了版本

- [ ] **完善文件上传：接入多模态识别**（用户指定最优先）
  - 目前钉钉/Web 上传文件仅自动下载保存；对上传的图片/扫描件用 VLM「看图说话」识别内容，识别结果可选入库或随回答返回
  - 与 v2.0「VLM 图片理解接入」呼应，可先行在文件上传链路落地
  - *来源：用户需求（2026-08-06）*

- [ ] **真实环境端到端验收**
  - 先备份正式 `knowledge_base/`、`data/user_store.db` 和原始文档
  - 在副本环境用真实 Excel、文本 PDF、扫描 PDF 各做一次"入库 → 查询 → 修改 → 重学 → 删除"回归
  - 验证真实 MinerU 分段、缓存失效和失败回滚
  - 验证 Web、钉钉单聊和 Docker 环境的 v1.2.8 端到端流程
  - *来源：CLAUDE.md / README / PROGRESS.md*

- [x] **崩溃恢复**：启动时识别并恢复或清理遗留 staging/retired 数据（2026-08-06 ✅，commit `8433f42` + main.py 挂载 `recover_crashed_data`）
  - *来源：CLAUDE.md / README*

- [ ] **审核流程真实钉钉端到端验证**（⛔ 已暂停，v1.10.2 起上传文件改「回复『帮我学习』」直接入库，不再走主管审核）
  - 配置固定审核人 staff\_id
  - 验证"上传 → 主动推送 → 批准/拒绝 → 同步结果"完整链路
  - **恢复方式**：取消 `dingtalk_bot._handle_file_message` 中 `queue_review_for_upload` 调用注释，并把 `knowledge_review.learn_for_user` 的 `process_file(department=...)` 改为用户主部门（见「其他待办 → 恢复部门划分与主管审核」）
  - *来源：CLAUDE.md / CHANGELOG / PROGRESS.md / v1.10.2 改造对话*

---

## ⚠️ P1 — 重要（7 项）

> 应当做，影响安全或稳定性

- [x] **管理端安全**：统一保护 `/admin` 下的列表、搜索、任务状态、同步和删除接口（2026-08-06 ✅，v1.2.9/v1.4.2 全部子项完成）
  - ✅ 列表/搜索/任务状态读接口鉴权补齐（v1.2.9）
  - ✅ stats 页 XSS 转义 + sync-delete/sync-trigger data/ 路径白名单（v1.2.9）
  - ✅ 上传文件增加路径约束、文件名净化、大小限制（50MB/413）、扩展名与 MIME 双重校验（v1.4.2）
  - ✅ 补齐 XSS 回归测试（test\_admin\_security.py 9 项，v1.4.2）
  - *来源：CLAUDE.md / README*

- [ ] **Web 身份**：移除可伪造用户名作为权限依据
  - ✅ 问答接口由 GET 迁移到 POST，避免问题内容进入 URL、代理日志和浏览器历史（v1.4.2）
  - [ ] 完成 Web 用户可信身份识别（需登录/SSO 体系，建议并入 v2.0 多中心权限一起做）
  - *来源：CLAUDE.md / README*

- [ ] **部署安全**：Docker 改为非 root 用户
  - ✅ Docker 镜像改为 appuser 非 root 运行（v1.4.2，挂载目录需 chown 1000:1000）
  - [ ] 检查镜像、挂载和部署包不包含密钥或真实业务数据（pack.sh 已排除 local\_config）
  - *来源：CLAUDE.md / README*

- [x] **DeepSeek API 瞬断自动重试**（2026-08-03 v1.2.9 ✅）
  - ISP 偶发波动导致 DeepSeek 10061 错误
  - 需加自动重试机制（计划 2026/7/31 前）
  - *来源：memory*

- [ ] **EN 50178 表格假阳性问题**
  - 不影响使用，P1 可暂放
  - *来源：checkpoint / progress-snapshot*

- [ ] **4 份扫描 PDF 全量 OCR（MinerU）**
  - 完成 OCR 提取后接入入库流程
  - *来源：checkpoint / progress-snapshot*

- [ ] **扫描 PDF 文本接入 sync\_standards.py**
  - OCR 文本接入标准入库管道
  - *来源：checkpoint / progress-snapshot*

---

## 📌 P2 — 可做可不做（5 项）

> 做了更好，不做不影响上线

- [ ] **检索评测**：建立固定评测集
  - ✅ 混合检索已实现（v1.3.0：BM25+向量 RRF 融合），短关键词召回已改善
  - [ ] 故障代码、短关键词、自然语言、标准编号、跨文档问答
  - [ ] 记录 Recall\@K、Top-1 命中率、无答案率、响应时间和 API 成本
  - [ ] **观察 bge-reranker 收益**：v1.4.0 已启用重排，持续观察「1.1GB 模型内存成本 vs 排序提升」是否划算；若收益有限可加配置开关默认关闭
  - *来源：CLAUDE.md / README*

- [ ] **CI/可观测性**：GitHub Actions 自动测试
  - 增加文档入库审计、失败原因统计、retired 数据清理和知识库容量监控
  - 增加单元测试、语法检查和敏感文件检查
  - *来源：CLAUDE.md / README*

- [ ] **IEC 60664-1 中英法三语混排**
  - 不影响使用，P2 可暂放
  - *来源：checkpoint*

- [ ] **重启服务验证端到端**（OCR 全部完成后）
  - *来源：checkpoint*

- [ ] **英文标准章节检测优化**
  - *来源：checkpoint*

- [x] **PCB 计算综合校验模式**（✅ v1.4.2 已实现为 `layout` 综合校验模式）
  - 一次输入「电流 + 铜厚 + 电压 + 材料组 + 可用宽度」，同时输出走线约束 + 安规间距 + 合计占用，判定是否冲突
  - 冲突时按优先级给设计建议：开槽 → 高 CTI 板材 → 三防漆 → 改铜厚/改布局
  - *来源：pcb\_calc 审查对话（v1.4.1）*

---

## 📖 Agent 学习落地（2026-08-05 新增，9 项）

> 学习素材：`D:\hello-agents`（Datawhale 16 章教程）+ `D:\ai-agent-book`（李博杰 10 章原理，均已 clone 最新版）
> 依据：docs/[20260805-Agent学习项目研读报告.md](docs/20260805-Agent学习项目研读报告.md) + [20260805-恩特小助手Agent落地路线报告.md](docs/20260805-恩特小助手Agent落地路线报告.md)
> 两本书交叉共识的 5 大差距，按成本/收益排序

- [ ] **上下文工程**：动态内容移出 system prompt + token 预算管控（最紧急）
  - [ ] 用户档案/短记忆/长记忆从 `system_content +=` 改为 `messages.append` 追加到消息末尾（KV Cache 前缀缓存友好，DeepSeek 支持前缀缓存）
  - [ ] 加 `build_system_context()`：token 预算 + 相关性/新近性评分贪心选择（参考 hello-agents `code/chapter9/context/builder.py`）
  - *来源：hello-agents ch9 GSSC / ai-agent-book ch2 KV Cache 三铁律；落地报告差距1*

- [ ] **Agent 状态栏 + 重复调用检测**（防白烧 token）
  - [ ] 每轮注入「第 N/5 轮、已调工具 X 次、上一轮未命中建议换关键词」
  - [ ] 同 `(工具名, 参数)` 指纹 ≥3 次直接跳出提示换问法
  - *来源：ai-agent-book ch2 实验2-8 / hello-agents ch4 Reflection；落地报告差距4*

- [ ] **标准分块补上下文前缀（Contextual Retrieval）**
  - 对 standards 块用 `_extract_std_info()` 生成 `[来源：GB/T xxx 第x.x条]` 前缀再入库（一次性重索引；失败率可降 \~49%）
  - *来源：ai-agent-book ch3 实验3-11；落地报告差距3*

- [ ] **MQE 查询扩展开关**
  - `enhanced_query()` 加 `enable_mqe` 参数，LLM 生成 3-4 个等价查询合并检索，默认关（轻量环境不增开销），LLM 失败自动退回原查询
  - *来源：hello-agents ch8 §8.3.5；落地报告差距3*

- [ ] **Agent 层评估（eval\_agent.py）**——接 TODO P2「检索评测」
  - [ ] 新建 `data/eval/agent_eval.json`：真实问法 + 期望工具名/参数 + 期望答案要点
  - [ ] DeepSeek 当 judge 按 Rubric 4 维度打分（幻觉一票否决），每次改 prompt/工具/记忆后跑
  - *来源：hello-agents ch12 BFCL+LLM Judge / ai-agent-book ch6 Rubric；落地报告差距2*

- [ ] **记忆冲突覆盖 + importance 评分**
  - [ ] 压缩提示词让 LLM 输出 `importance`；保存前查同 user 同主题 fact 做「最新覆盖旧版」
  - [ ] `format_long_term` 按 importance 排序截断
  - *来源：ai-agent-book ch3 Mem0 / hello-agents ch8；落地报告差距5*

### 第二梯队（进阶，有余力再做）

- [ ] **工具 schema 自动生成**
  - 用 ToolParameter 声明式定义 + `to_openai_schema()` 自动生成 function calling schema，替代手写 DEFINITION dict
  - *来源：hello-agents ch7 §7.5.1*

- [ ] **万物皆为工具 + 工具链**
  - 「故障→对应标准→PCB」固定多步流程固化为工具链（ToolChain），减少 LLM 串联不确定性
  - 未来经验库/钉钉能力也抽象成工具，统一注册
  - *来源：hello-agents ch7 §7.5.4 / ai-agent-book ch4*

- [ ] **Plan-and-Solve 规划层**
  - 复杂多步排查（先查代码→再查标准→再算 PCB）时先让 LLM 输出规划列表再逐步执行
  - *来源：hello-agents ch4 §4.3*

---

## 🎯 第二步 — 经验知识库（5 项）

> 三步走第二步核心。v1.4.2 已完成「搭建 + 模板 + 提交/审核/发布」主链路，待真实环境验证 + 内容补充

- [x] **经验知识库搭建**（v1.4.2 ✅）
  - 复用 RAG 架构（Chroma + Embedding + LLM）
  - 新增 `experience_kb` collection + `search_experience_kb` Agent 工具 + 上传/同步/审核全链路
  - *来源：CLAUDE.md / PROGRESS.md / memory*

- [x] **设计经验知识模板**："故障现象 → 排查步骤 → 根因 → 解决方案 → 验证结果"
  - `data/experience/` 五段式 Markdown 模板 + 示例条目（IGBT 过温排查）
  - *来源：READNE*

- [x] **建立提交/审核/发布流程**
  - 复用 knowledge\_review 审核：`.md` → experience\_kb，`同意同步 id 经验库`（v1.4.2）
  - ✅ 纠错/失效流程（v1.5.0）：同名文件重同步 = 版本替换纠错；删除文件 = 失效下线（操作指南见 docs/20260804-经验库纠错失效操作指南.md）
  - *来源：README*

- [ ] **补充排查/解决步骤**
  - 数据源本身没有这些信息，需人工整理上传（用五段式模板）
  - *来源：memory*

- [ ] **补充常见问题 + 维修记录导入**
  - 批量导入脚本可参考 `sync_experiences.py`
  - *来源：介绍页.md*

---

## 🏢 v2.0 企业知识中枢（6 项）

> 部门知识库 + 通用文件引擎 + VLM 图片理解 + 腾讯云同步

- [ ] **通用文件处理引擎重构**
  - 从硬编码 switch-case 改为注册表模式：`engine.register(".pdf", PdfProcessor)`
  - 接入 Unstructured 统一文件解析（PDF/DOCX/XLSX/PPTX/HTML/MD 等）
  - *来源：memory*

- [ ] **VLM 图片理解接入**
  - 在 MinerU 提取图片后，再加一层"看图说话"→ 图片描述转文字入库
  - *来源：memory*

- [ ] **Chroma 多中心隔离改造**
  - 新增 `dept_knowledge` collection
  - 每段 chunk 带 center/department/upload\_user metadata
  - *来源：memory*

- [ ] **上传/查询/权限全链路**
  - 用户默认只能看自己中心 + 公共区资料
  - *来源：memory*

- [ ] **腾讯云同步模块**
  - 可插拔后端设计（COS/SFTP 实现）
  - 成功入库后自动触发
  - *来源：memory / PROGRESS.md*

- [ ] **发布 v2.0.0**
  - *来源：memory*

---

## 🔭 第三步 — AI 辅助研发探索（3 项）

> 第一步走稳后再启动

- [ ] AI 辅助技术调研
  - *来源：介绍页.md*

- [ ] 方案评审辅助
  - *来源：介绍页.md*

- [ ] 文档自动生成
  - *来源：介绍页.md*

---

## 🐛 其他待办（8 项）

> 低频、远期或依赖前置条件

- [ ] **恢复部门划分与主管审核**（v1.10.2 已停用，代码保留注释）
  - v1.10.2 起上传文件「回复『帮我学习』」直接入库（department=`public`）；审核流程代码（knowledge_review 的 create_request/handle_command/notify_*）一律保留未删除
  - 未来启用时：取消 `dingtalk_bot._handle_file_message` 中 `queue_review_for_upload` 注释；`learn_for_user` 的 `process_file(department=...)` 改为用户主部门；回执文案改回提示审核
  - *来源：v1.10.2 上传学习流程改造对话*

- [x] **对话记忆升级为双层记忆（长期事实 + 短期窗口）**（2026-08-05 v1.5.3 ✅）
  - 短期窗口 5→8 轮 + 长期记忆表 `long_term_memories`：滚出窗口的旧对话由 LLM 异步压缩为「事实+摘要」，常驻注入 system prompt
  - 按 user\_id 隔离不串用户；`LONG_TERM_MEMORY_ENABLED=False` 可整体关停；首次启动幂等 backfill
  - *来源：v1.4.2 检索链路梳理对话（2026-08-04）*

- [ ] **多工具链式任务轮数验证**（加第 5 个工具时触发）
  - 现状：MAX\_AGENT\_LOOPS=5 对当前 4 个工具够用
  - 验证点：新增工具后测试「故障→标准→经验→PCB」3 步以上链式任务是否频繁触发轮数上限（日志含「达到轮数上限」标记）
  - *来源：v1.4.2 检索链路梳理对话（2026-08-04）*

- [ ] **P3 部门审核**：固定审核人实测稳定后，实现按上传者部门匹配主管、代理审核和超时升级
  - *来源：CLAUDE.md / README*

- [ ] **P3 数据权限**：按部门/角色控制文档可见范围，并同步处理来源删除
  - *来源：CLAUDE.md / README*

- [ ] **P3 业务扩展**：钉钉知识库对接、群聊能力评估（先解决身份/权限/信息泄露边界）
  - *来源：CLAUDE.md / README*

- [ ] **钉钉知识库对接需求调研**：权限范围、同步周期、上传规范
  - *来源：memory*

- [ ] **电路板设计标准查询模块**（未来方向）：确定标准优先级，确认版权/使用许可
  - *来源：memory*

---

## 📊 统计一览

| 分类 | 总数 | 状态 |
|:-----|:----:|:----:|
| 🔥 P0 阻塞级 | 4 | 3 待办 + 1 完成 |
| ⚠️ P1 重要 | 7 | 5 待办 + 2 完成 |
| 📌 P2 可做可不做 | 6 | 5 待办 + 1 完成 |
| 📖 Agent 学习落地 | 9 | 全部待办（2026-08-05 新增） |
| 🎯 第二步经验库 | 5 | 3 完成 + 2 待办 |
| 🏢 v2.0 企业中枢 | 6 | 全部待办（依赖第二步） |
| 🔭 第三步研发探索 | 3 | 全部待办（远期） |
| 🐛 其他 | 8 | 1 完成 + 7 待办 |
| **合计** | **48** | **8 完成 + 40 待办** |

---

*本文档是项目唯一的待办清单。其他文件的待办引用均指向本文件（见顶部说明）。每完成一项就勾上✅！*

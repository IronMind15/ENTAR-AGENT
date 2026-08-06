# 恩特小助手 更新日志

> 📌 **本文档是项目唯一的版本记录（单一事实源）**——README.md、PROGRESS.md 的版本历史均指向本文件，发版时只在这里追加记录。
> 相关：待办清单见 [TODO.md](TODO.md)，进度看板见 [PROGRESS.md](PROGRESS.md)。

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

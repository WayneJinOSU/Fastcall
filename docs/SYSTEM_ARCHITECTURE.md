# Fast-Call 系统架构总览

本文是当前系统的总入口，说明设计目标、模块边界、接口协作、内部事务和运行约束。模块内部细节见各分册。

## 1. 系统目标与范围

系统提供完整的自动外呼闭环：

```text
创建任务 → 加密导入联系人 → 调度与预留 → Adapter 拨号
→ Agent 语音对话 → 电话终态与重试 → 查询/API → Webhook 通知
```

核心目标：

- 外呼任务、联系人、Call、事件和结果可恢复、可追溯。
- 同一 Call 最多授权一次 originate，重启和恢复不得补发含义不明的拨号。
- 终态、重试额度、任务完成和对应 Outbox 在明确事务中处理。
- Agent 语音业务与外呼任务控制解耦，双方通过稳定协议协作。
- 本地减少控制面进程数，生产保留独立故障和部署边界。
- 默认只要求客户处理 `contact.completed`、`task.completed`、`task.cancelled`。

当前不包含多租户、预测式外呼、多活 Scheduler、多 Adapter  ownership、人工坐席扩容或新的消息基础设施。内部结果修订和 Admin review 代码保留，但当前正常客户流程不依赖人工处理。

## 2. 架构边界

系统有三个运行服务和一个共享库：

| 名称 | 类型 | 职责 |
|---|---|---|
| `fastcall` | 控制面服务 | Robot Config、Open/Admin API、任务、调度、查询、Webhook |
| `fastcall_adapter` | 电话运行面服务 | 拨号授权、单次 originate、ESL、媒体、Session、核查、挂断 |
| `collection_agents` | AI 语音服务 | Bot Pipeline、STT/LLM/TTS、语音信箱、通话记录和分析 |
| `fastcall_core` | 共享 Python 库 | 外呼领域、共享事务用例、ORM/DAO/UOW；不独立运行 |

关键理解：Fast-Call 和 Adapter 是同一外呼业务边界内的两个部署单元，不是完全独立的数据域。二者共享 core 和 MySQL 事务语义；Collection Agent 是独立业务边界，不依赖 Fast-Call 内部 Python 包。

依赖方向：

```text
fastcall ───────────┐
                    ├──> fastcall_core
fastcall_adapter ───┘

collection_agents 不依赖上述三个包
```

## 3. 当前代码地图

```text
src/
├── fastcall/
│   ├── api/                    # openapi、adminapi、console、management、health
│   ├── application/            # 任务、调度、查询、Webhook、Robot 用例
│   ├── domain/                 # Robot Config 与输入校验规则
│   ├── infrastructure/         # 控制面 DAO、HTTP clients、UOW、Webhook 安全
│   ├── main.py                 # FastAPI 应用工厂与组合根
│   ├── local_main.py           # 本地 API + Scheduler + Webhook Worker
│   ├── scheduler_main.py
│   └── webhook_main.py
├── fastcall_adapter/
│   ├── api/                    # 独立 app、依赖、Schema、错误与路由
│   ├── application/            # dispatch、media、checks、hangup、telephony
│   ├── infrastructure/         # Adapter DAO/UOW、ESL Gateway、DB/crypto
│   ├── settings.py
│   └── main.py
├── fastcall_core/
│   ├── domain/                 # Task/Contact/Call、状态、重试、时间窗、结果码
│   ├── application/            # 终态、Session、分析、版本读取、共享视图
│   └── infrastructure/         # ORM、共享 DAO、core UOW、DB/crypto
└── collection_agents/
    ├── bots/                   # standard、realtime、copilot
    ├── processors/             # 通用与模式处理器
    ├── handlers/               # 业务动作
    ├── services/               # Provider、语音信箱和音频资源
    ├── transport/              # WebSocket 协议
    ├── storage/                # Agent 自有落库
    └── server/                 # FastAPI 应用与生命周期
```

根目录继续使用统一的 `tests/`、`deploy/`、`requirements.txt`、`pyproject.toml` 和 `venv`。`assets/`、`templates/`、`data/` 仍属于 Agent 的现有资源和运行数据布局。

## 4. 运行单元与端口

| 运行单元 | 本地开发 | 生产 |
|---|---|---|
| Fast-Call 控制面 | `fastcall.local_main`，默认 8770 | API、Scheduler、Webhook 独立入口 |
| Adapter | `fastcall_adapter.main`，默认 8767 | 独立单副本、单 worker |
| Collection Agent | `main.py`，默认 8765；可按环境使用其他端口 | 独立单副本 |
| FreeSWITCH | Docker，ESL 默认 8021 | 外部电话基础设施 |
| MySQL | 本地或独立实例 | 业务事实源 |
| 测试 Webhook 接收端 | 验收时临时 HTTPS 服务 | 不属于业务服务 |

生产 Scheduler、Adapter、Webhook 和 Agent 使用单副本 `Recreate`，禁止滚动期间新旧实例并存及 HPA。本地聚合入口不得与独立 Scheduler/Webhook Worker 双跑。

## 5. 接口协作总表

| 调用方 → 接收方 | 接口 | 用途与约束 |
|---|---|---|
| 客户 → Fast-Call | `/openapi/v1/...` | 任务创建、名单、控制、查询；Bearer 认证 |
| 管理端 → Fast-Call | `/adminapi/v1/...` | 查询、delivery reset、review 兼容代理 |
| Fast-Call Scheduler → Adapter | `POST /internal/v1/calls/{call_id}/dispatch` | 提交预留 Call；Adapter token，不授予 Scheduler ESL 权限 |
| Fast-Call → Adapter | `POST /internal/v1/calls/{call_id}/hangup` | 取消后的即时幂等挂断快路径 |
| Fast-Call → Adapter | `POST /adminapi/v1/calls/{call_id}/review` | 固定上游的认证代理，转发 actor，不由控制面操作 DB |
| Adapter → FreeSWITCH | ESL | originate、事件、通道恢复、媒体和挂断 |
| FreeSWITCH → Adapter | `/internal/media/{call_id}` WebSocket | PCM16LE、单声道、8kHz 媒体流 |
| Adapter → Agent | `/ws-v2` WebSocket | start、Session 生命周期、音频和控制事件 |
| Agent → Adapter | `session.attached/ready/failed/completed` | 代际、ACK 与幂等事件；当前 completed 主要携带 voicemail，完整分析仍由 Agent 独立落库 |
| Fast-Call → 客户 | HTTPS Webhook | 稳定 event ID、HMAC-SHA256、重试与地址安全校验 |
| Agent → STT/LLM/TTS | Provider SDK/API | 真实语音交互，不经过 Fast-Call 数据模型 |

详细路径和字段见本总览的数据、接口与状态章节以及各模块分册。

## 6. 主流程

### 6.1 创建与拨号

1. Open API 创建任务并导入加密联系人。
2. 启动时固定 Robot 精确版本和配置哈希。
3. Scheduler 选择到期联系人，检查窗口、禁呼、容量和速率，创建 `reserved` Call。
4. Scheduler 通过内部 API 请求 Adapter dispatch。
5. Adapter 在同一事务中再次校验状态、容量、禁呼和拨号授权，提交 `dial_requested_at` 后才发送一次 originate。
6. 授权结果未知或重启时不自动重发，进入核查或按确定事实恢复。

### 6.2 Agent Session 与语音

1. FreeSWITCH 接听后，Adapter 建立 Agent Session 并启动媒体。
2. Agent 按 `(call_id, claim_generation)` attach，attached 持久化并 ACK 后才初始化 Pipeline。
3. ready 持久化并 ACK 后，Adapter 发送 answer 和预缓冲音频。
4. 媒体按 320B/20ms 帧传输，双方队列最多 500ms；FreeSWITCH→Agent 队列溢出时丢最旧帧，Agent 输出队列溢出时终止当前 Call。
5. Agent 的语音信箱、分析和落库独立执行；Fast-Call 可用 FreeSWITCH 技术终态完成闭环。

### 6.3 终态与通知

1. 电话事件去重并关联正确的主通道。
2. core 的终态用例更新 Call、Contact、重试、任务完成和 Outbox。
3. Webhook Worker 独立领取和投递，不影响电话状态。
4. 默认关闭 `contact.result_changed` 的生成与投递；内部 revision 和 API 查询仍保留。
5. 显式开启后，人工或迟到事件修订使用原稳定事件契约。
6. `unknown` 会保留 Contact 的活动 Call 关系并继续占用容量；任务可以先 completed，但容量需要后续核查/review 或可靠终态释放，不能把 unknown 当作无成本终态。

### 6.4 取消与挂断

1. 控制面在一个事务中取消任务、处理 pending Contact 并写任务/联系人 Outbox。
2. 未授权 `reserved` Call 直接终态化为 cancelled，不生成无效挂断指令。
3. 需要挂断的活动 Call 写入专用 `call.hangup_requested`，`hangup_active=false` 不写。
4. 提交后即时通知 Adapter；通知使用有界并发和总超时，失败不改变已提交取消事实。
5. Adapter 周期对账通过租约恢复指令，幂等挂断，只有确认终态后才将指令标记 processed。
6. 普通 EventWorker 不领取挂断指令。

### 6.5 独立 Mock Call

Console `/mock-calls` 使用当前 Release 创建 `origin=mock_call` 的单联系人 Task。浏览器媒体注册后激活 Task，随后完整经过 Scheduler、Adapter dispatch、FreeSWITCH loopback、Agent Session、终态、Outbox 和 Webhook Worker。浏览器作为 B-leg 被叫，手动接听后才出现 A `CHANNEL_ANSWER` 并启动 Agent；支持拒接和无人接听，不自动重拨。

Mock 使用独立固定 loopback target，不改变正式 gateway。A/B UUID 分别由 `outbound_call.freeswitch_uuid` 和 `mock_browser_uuid` 保存。Mock 保留完整 Agent 技术记录，但正式任务列表和正式 BI 默认排除；Webhook 只能投递到服务端固定、经过 HMAC/HTTPS 校验的 Mock Receiver。应用编辑器中的草稿 Voice Test 保持独立。

## 7. 数据、事务与内部设计规则

- MySQL 是任务、联系人、Call、事件和 Outbox 的事实源。
- Task、Contact、Call 使用 `state_version` 和条件更新；Call 操作校验 `contact.active_call_id`。
- `result_revision` 用于结果和通知版本，与内部状态版本分离。
- 每种 UOW 及其 DAO 共享同一个 SQLAlchemy Session。
- 最外层 Service/Worker 拥有 commit/rollback；Controller 和 DAO 不拥有事务。
- 嵌套 Service 使用 `commit=False`，不得把多表终态拆成多次提交。
- Worker 的租约领取与处理结果保持独立事务语义。
- core 不读环境变量、不包含 FastAPI/ESL/Scheduler/Webhook 工作流。
- 新增共享代码必须证明双方真实复用，不为假想需求建立通用框架。

Fast-Call 当前结构由 `deploy/mysql/fastcall_current_schema.sql` 唯一描述，不再维护 Alembic 基线或版本表。Fast-Call 的 `outbound_call_analysis` 与 Agent 旧 `call_analysis` 物理分离，不改旧表结构。

## 8. 安全约束

- Open API、Admin、Adapter、Agent 和媒体使用不同凭据。
- 号码和变量使用带版本认证加密，去重和禁呼使用 keyed HMAC。
- Webhook 仅 HTTPS；重新解析并校验全部 IP，连接已验证地址，同时保留 Host/SNI/证书校验。
- 禁止重定向和环境代理；私网仅按 hostname/CIDR 白名单开放，链路本地与云元数据地址始终拒绝。
- 重试保持 event ID 和 payload 不变。
- 日志、Webhook 和文档不得新增完整号码、敏感变量、完整对话、录音或密钥。
- 根 `.dockerignore` 排除 `.env*`、运行数据、虚拟环境和 Git 元数据；服务镜像使用选择性 COPY。

## 9. 验证与当前证据

截至 2026-11-15 的当前工作区：

- Console 前端 51 项测试、TypeScript 检查和生产构建通过；Mock Call 独立页面已在真实浏览器验证导航、Release 和变量加载。
- 模拟 outbound e2e、Mock Call 的 Scheduler/dispatch/手动接听/Claim/终态/Outbox e2e，以及架构边界、路由集合和 UOW 结构测试通过。真实 Docker FreeSWITCH Mock Call 已验证 B 来电、手动 answer、联合 ready、双向有效 PCM、ASR 非零文本、1 轮 LLM 对话、真实 TTS、USER_HANGUP、Task completed、两类 Webhook 各一次 204、Agent `mock_call` 落库及 A/B 通道清零。媒体修复使用 Mock-only direct playback patch，不改变正式 gateway 路径。
- 真实浏览器已验收登录、任务列表/详情、联系人、Call、通话技术分析，以及任务启动、暂停、恢复、取消；Agent 不可用与恢复、取消后子表刷新、BI 空库/有数据、日历和每日指标展开也已验证。
- Agent 的 attached 等待 ACK 断线现已纳入 slot `try/finally` 释放，并有目标回归。
- 真实 FreeSWITCH loopback 已验证当前源码：一个 Call 成功、Task 完成、两类 Webhook 各一次 204、无残留通道。
- 语音验收包含真实 STT、LLM 和 TTS，Agent 通话指标及 transcript 独立落库。
- 真实 MySQL 6 项在缺少独立测试库时不得记为已执行。
- Docker 新镜像构建曾因 Docker Hub 元数据超时受阻；loopback 使用现有 Adapter 镜像并只读挂载当前源码，不等同于新镜像构建验收。
- 统一 FreeSWITCH 镜像已在 ARM64 环境从固定源码构建成功；Mock-only、SIP/IP-auth 配置加载、SIP+Mock 同实例三组隔离冒烟通过，覆盖 ESL 强密码/ACL、Sofia profile、核心 G.711、媒体模块注册及 Mock loopback 通道创建和清理。该结果不覆盖真实 SIP INVITE/RTP、direct playback 实际音频、媒体 Header 或 Adapter/Agent 全链路。
- Adapter/Fast-Call 双能力改造的相关后端测试 147 项通过，Console 52 项测试和生产构建通过；全量后端 264 项中 263 项通过，唯一失败仍为冻结的 collection-agent `_observer` 测试。新增能力检查、拨号栅栏和媒体 Header 有单元回归，尚未据此宣称真实 SIP 或 WebSocket 媒体全链路通过。为保持 Agent 冻结，Adapter 仅外部检查其 `/state/up` 进程可达性，不把该结果描述为 Agent 业务 readiness。
- `fastcall:dev`、`fastcall-adapter:dev` 均已实际构建成功；Fast-Call 镜像内 API、Scheduler 和 Webhook 入口加载通过，且不含 Pipecat。入口检查只证明依赖与打包完整，不替代服务启动后的真实业务链路。
- `deploy/compose.yaml` 与启动器已使用本地真实 MySQL 的独立 `fastcall`、`collection_agents` 数据库完成全栈启动；Fast-Call 在当时的 Alembic 管理下曾迁移至 `0005`，当前已改为纯 SQL 快照。真实浏览器 Mock 已完成接听和双向通话，Call 为 `succeeded/USER_HANGUP`，Agent MySQL 保存通话指标和 transcript，两类 Webhook 均一次 204，FreeSWITCH 通道清零。该结果仍不替代真实运营商 SIP 验收。

## 10. 后续修改入口

- 修改 Open/Admin API、Scheduler、Webhook：阅读 [Fast-Call 控制面](modules/FASTCALL.md)。
- 修改拨号、ESL、媒体、核查、挂断：阅读 [Adapter](modules/FASTCALL_ADAPTER.md)。
- 修改状态机、终态、共享 DAO/UOW：阅读 [Core](modules/FASTCALL_CORE.md)。
- 修改 Bot、Provider、语音信箱、Agent 协议或落库：阅读 [Collection Agent](modules/COLLECTION_AGENT.md)。

任何跨模块修改都需要先确认接口、事务和部署约束，再更新对应文档与测试。

## 11. 数据所有权速查

| 数据域 | 所有者 | 核心内容 |
|---|---|---|
| Robot Config | `fastcall` | 应用、草稿、不可变版本、Release、审计、能力目录 |
| Outbound 任务 | `fastcall` 与 `fastcall_core` | 任务配置、时区窗口、重试策略、客户标识、结果版本 |
| 联系人与名单 | `fastcall_core` | 加密号码/变量、HMAC、重试、活动 Call、最终结果 |
| 电话与通道 | `fastcall_adapter` 与 `fastcall_core` | 授权、FreeSWITCH UUID、Session、核查、挂断和原始事实 |
| 事件与 Outbox | `fastcall_core` | 稳定事件 ID、payload/hash、事务 Outbox、处理租约 |
| Webhook 投递 | `fastcall` | HTTPS/HMAC 安全投递、地址快照、重试和 delivered/failed 状态 |
| Agent 通话与分析 | `collection_agents` | call_record、call_transcript、call_analysis、Provider 指标 |
| 业务分析 | `fastcall_core` | `outbound_call_analysis.business_result`，不改变电话技术结果 |

Fast-Call 与 Agent 分别使用 `deploy/mysql/fastcall_current_schema.sql` 和 `agent_current_schema.sql` 描述当前结构，不共享版本表或自动迁移。`outbound_call_analysis` 不覆盖 Agent 旧 `call_analysis`。

## 12. 基础设施与运维约束

- FreeSWITCH 负责 SIP 拨号、接听、挂机、CDR 和电话音频。统一镜像由 `deploy/freeswitch/Dockerfile` 构建，同一实例加载 Sofia、loopback 和 `mod_audio_stream`，正式任务使用 gateway，Mock Call 使用固定 loopback target。
- 镜像固定基础镜像 digest、FreeSWITCH 1.10.12、Sofia SIP、spandsp 和 `mod-audio-stream` commit `a25fb1fe530ec6a612d321ff04f70be69b1a257c`；媒体模块需满足 AGPL-3.0 或商业许可。
- 入口按 `FASTCALL_SIP_ENABLED` 和 `FASTCALL_MOCK_CALLS_ENABLED` 渲染配置。启用 SIP 时必须配置真实 gateway 和 external IP；未启用时不生成 gateway，Mock 仍可独立运行。
- ESL 必须显式配置非默认强密码，默认 ACL 只允许 `127.0.0.1/32`；实际部署需改为 Adapter 所在地址并不向宿主机发布 8021。共享网络命名空间仅属于旧 POC，不是统一镜像的默认拓扑。
- 统一镜像包含受控的 `mock_call_direct_playback.patch`，但正式 originate 不设置 `STREAM_PLAYBACK_DIRECT`，正式 SIP 仍走模块原播放路径。Mock dialplan 固定目标、不自动 answer，不增加通配路由或公网转发。
- `scripts/test_freeswitch_image.sh` 是不发布宿主机端口的镜像级冒烟，覆盖模块、ESL 认证、SIP 配置渲染和 loopback；它不替代真实 SIP、双向媒体或 Adapter/Agent 全链路验收。
- Fast-Call 与 Agent schema 均只由 SQL 文件管理：全新空库分别使用 `deploy/mysql/fastcall_current_schema.sql` 和 `agent_current_schema.sql`。Compose 不注册 `fastcall-migrate` 或 `agent-init`，不在服务启动时自动迁移。项目只保留当前 SQL 快照，旧库需在项目外备份并自行完成结构升级。
- Provider 密钥留在各服务部署环境，不进入 Robot 配置 JSON、审计、Webhook 或文档。
- 本地 `/tmp` 的运行脚本、runtime env、测试 CA 和 HTTPS 接收端只用于验收，不是生产组件。
- `FASTCALL_DISABLE_SSL_CERT_FILE=1` 仅用于本地 Agent 验收，避免测试 CA 覆盖生产 Provider 证书；Webhook Worker 不通过关闭 TLS/HMAC/私网白名单绕过校验。

## 13. 总分文档维护边界

项目只维护根 `README.md`、本总览和 `docs/modules/` 下四份分册，不并行维护其他架构或模块说明。

- 总览负责系统目标、运行单元、接口协作、跨模块事务、安全、基础设施和验证。
- `FASTCALL.md` 负责控制面、Robot Config 和 Console。
- `FASTCALL_ADAPTER.md` 负责拨号、ESL、媒体、核查和挂断。
- `FASTCALL_CORE.md` 负责共享领域、状态机、事务和数据契约。
- `COLLECTION_AGENT.md` 负责 Agent、配置模式和语音业务。

修改接口、状态机、事务或部署边界时，同步更新总览、相关分册和测试。当前行为与未来规划分开描述，验证结论注明覆盖范围，不把跳过或阻塞写成通过。

# Fast-Call 控制面设计

## 1. 职责与边界

`fastcall` 是外呼控制面，同时承载 Robot Config。负责：

- Robot 应用、草稿、校验、发布、版本和精确 Resolve。
- 外呼任务、联系人、控制、查询、禁呼和结果读取。
- Scheduler 的到期扫描、预留、容量、速率、重试和恢复。
- Webhook Outbox 投递、租约、重试和安全校验。
- 本地 API + Scheduler + Webhook Worker 聚合。

不负责：直接发送 FreeSWITCH 命令、originate、Agent Pipeline 或媒体处理。拨号和挂断执行归属 `fastcall_adapter`。

## 2. 当前内部结构

```text
src/fastcall/
├── api/
│   ├── openapi/outbound.py          # 客户任务接口
│   ├── adminapi/calls.py            # Admin 查询、reset、review 代理
│   ├── console/                     # Console API、认证与静态资源
│   ├── management.py、resolve.py    # Robot 管理与内部 Resolve
│   ├── health.py
│   ├── dependencies.py              # 认证与能力注入，不创建具体 DAO/crypto
│   ├── errors.py、requests.py、responses.py
│   └── routes.py                    # 控制面路由集合
├── application/
│   ├── outbound_tasks.py            # 创建、名单、启动、暂停、恢复
│   ├── task_control.py              # 取消事务与挂断通知
│   ├── scheduler.py                 # 调度工作流
│   ├── webhooks.py、webhook_delivery.py
│   ├── event_worker.py
│   ├── outbound_queries.py
│   ├── robot_apps.py、publication.py、resolve.py、validation.py
│   └── voice_test.py
├── domain/                          # Robot Config、变量、号码、校验和 diff
├── infrastructure/
│   ├── scheduler_repository.py      # 调度工作流 DAO 与调度子类
│   ├── query_repository.py、task_control_repository.py
│   ├── webhook_repository.py、event_worker_repository.py
│   ├── repositories.py              # Robot 管理 DAO
│   ├── dispatch_client.py            # Adapter dispatch Gateway
│   ├── hangup_client.py、review_client.py
│   ├── database.py                  # 完整 schema 启动校验
│   ├── security.py                  # 环境配置到 core crypto 的工厂
│   ├── webhooks/security.py         # HTTPS/HMAC/DNS/IP 安全投递
│   └── unit_of_work.py              # 控制面 UOW 组合
├── capabilities/、console/、migrations/
├── main.py                          # 应用工厂与控制面组合根
├── local_main.py                    # 本地聚合进程
├── scheduler_main.py、webhook_main.py
└── settings.py
```

`fastcall/infrastructure/freeswitch/` 和 `fastcall/api/internal/` 中的空遗留目录不代表 Adapter 能力；实际电话运行代码在 `fastcall_adapter`。

## 3. 启动入口与部署

| 场景 | 入口 |
|---|---|
| 生产 API | `fastcall.main:app` |
| 生产 Scheduler | `python -m fastcall.scheduler_main` |
| 生产 Webhook | `python -m fastcall.webhook_main` |
| 本地聚合 | `python -m fastcall.local_main` |

本地聚合使用同一事件循环；任一组件退出或失败时，后台组件被取消并回收，Uvicorn 通过 `should_exit` 正常关闭。不得与独立 Scheduler/Webhook Worker 双跑。

生产 Scheduler/Webhook 使用 `FASTCALL_IMAGE`，Adapter 使用 `FASTCALL_ADAPTER_IMAGE`。生产不使用本地聚合入口。

## 4. 外部接口

### Open API

主要路径：

```text
PUT  /openapi/v1/outbound-tasks/{customer_task_id}
POST /openapi/v1/outbound-tasks/{task_id}/contacts
GET  /openapi/v1/outbound-tasks/{task_id}
POST /openapi/v1/outbound-tasks/{task_id}/start|pause|resume|cancel
GET  /openapi/v1/outbound-tasks/{task_id}/contacts|calls
GET  /openapi/v1/task-contacts/{contact_id}
GET  /openapi/v1/calls/{call_id}
```

契约要点：客户任务 ID 幂等创建；任务启动固定 Robot 版本；名单启动后不可改；分页使用绑定过滤条件的 keyset cursor；完整号码和变量不出现在响应中。

### Admin 与 review 代理

- Admin 查询和 delivery reset 由控制面 Service/DAO 处理。
- `POST /adminapi/v1/calls/{call_id}/review` 保留控制面地址，但只做认证和固定 Adapter 上游代理。
- 代理转发 payload 和 actor，不透出上游地址，不跟随重定向，不在控制面操作 review 数据库事务。
- Adapter 超时/网络错误映射为结构化 504/502；其他响应状态和 body 保持。

## 5. 内部主流程

### 5.1 任务与调度

1. `OutboundTasksUseCase` 校验配置、号码、变量和回调地址。
2. 启动时 Resolve 当前发布版本并保存 `robot_version_id/config_hash`。
3. `DialScheduler` 恢复过期预留、提升到期任务和重试、选择到期联系人。
4. 调度 DAO 在同一事务中检查时间窗、禁呼、容量和速率并预留 Call。
5. `AdapterDispatchClient` 请求 Adapter dispatch；Scheduler 不持有 originate 权限。

调度专用方法留在 Fast-Call：`list_due`、重试提升、`reserve_dialable` 等。core 的共享 Call 操作不包含这些工作流。共享的未授权释放由 `ReleaseUnauthorizedReservationUseCase` 处理；任务已取消时进入 cancelled 终态而非重新 pending。

### 5.2 取消

`CancelTaskUseCase` 的顺序：

1. 按期望版本取消 Task。
2. pending Contact 直接取消并生成相应 Outbox。
3. 活动 Call 先锁定；未授权 reserved 直接终态化并写联系人 Outbox。
4. 其余需要挂断的 Call 写 `call.hangup_requested`。
5. 一次提交取消事务。
6. 即时 Adapter 通知使用最多 10 个并发和 10 秒总截止时间；失败只告警并由对账兜底。
7. 返回既有取消成功响应；相同旧版本的重复请求仍按原版本冲突处理。

### 5.3 Webhook

- 终态与 Outbox 由 core 用例在同一事务中写入。
- Worker 领取和结果更新保留独立事务与过期租约恢复。
- `FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED` 默认 false。
- 关闭时既不新生成也不领取历史 `contact.result_changed`；开启后恢复原事件路径。
- 重试保持 event ID 和 payload 不变；每次投递生成新 timestamp 并重新计算签名。

## 6. 事务与依赖规则

- `SqlAlchemyUnitOfWork` 组合 core 共享 DAO、Robot DAO 和控制面专用 DAO。
- UOW 与全部 DAO 共享一个 SQLAlchemy Session。
- 最外层 Service/Worker 提交或回滚；Controller 不调用事务方法。
- 查询不提交；DAO 不调用 Service，不决定业务结果。
- `main.py` 装配 Database、UOW factory、crypto、Adapter clients 和应用能力。
- 控制面只依赖 `fastcall_core`，不 import `fastcall_adapter` 或 `collection_agents`。

## 7. 配置与数据所有权

配置统一使用 `FASTCALL_*`（通用 `DATABASE_URL` 除外）。Adapter 地址使用 `FASTCALL_ADAPTER_INTERNAL_URL`，Agent 地址使用 `COLLECTION_AGENT_WS_URL`。

控制面启动要求 MySQL PyMySQL URL、七种互不相同的 API/内部 token，以及 Console actor、密码哈希和至少 32 字符 session secret。还必须提供 `FASTCALL_ENCRYPTION_KEYS_JSON`、`FASTCALL_ENCRYPTION_ACTIVE_VERSION`、`FASTCALL_HMAC_KEYS_JSON`、`FASTCALL_HMAC_ACTIVE_VERSION`。仅配置外呼 Open API 凭据不足以启动完整控制面。

Fast-Call 拥有 Alembic 迁移和完整 schema 校验。当前基线为 `0005`，version table 继续使用兼容历史数据库的 `robot_config_alembic_version` 名称。不得在业务测试中使用非独立业务库重建 schema。

## 8. 测试与修改指引

主要测试：

- `tests/unit/fastcall/`：API、任务、调度、Webhook、Console、启动。
- `tests/unit/architecture/`：包边界、路由集合、事务所有权、UOW 不变量。
- `tests/integration/test_outbound_e2e`：跨模块模拟闭环。
- `tests/integration/fastcall_mysql/`：真实 MySQL，仅允许独立测试库。

修改公开 API 时同步更新业务契约、总览和本分册。新增控制面 DAO 不得泄入 core；新增 Adapter 行为不得通过控制面直接操作 FreeSWITCH。

## 9. Robot Config 配置与版本契约

### 9.1 配置内容与 Runtime

配置契约版本为 `robot-app/v1`，一个应用版本保存一份完整 JSON。主要配置块包括：

- `app`：应用标识、名称和启用状态。
- `prompt`：系统 Prompt 和变量引用。
- `variables`：变量 key、类型、必填、敏感、长度、默认值等。
- `asr`：Deepgram Provider、模型、语言、采样率和 interim 行为。
- `llm`：Provider、模型、temperature、上下文轮次、max tokens、top_p、超时等。
- `tts`：Cartesia 模型、voice_id、语言、语速、音量、采样率和编码。
- `flow`：欢迎、结束、无响应、语音信箱及完整子字段。
- `call_policy`：打断和最大通话时长。

发布版本只保存变量定义，不保存真实姓名、金额等通话变量值；实际值由每次请求传入。Flow 和其他已知/未知子字段必须完整保存，不能仅保存页面展示的少数字段。Provider 密钥只存在于 Agent 部署环境，不进入配置 JSON、管理响应或审计。

当前第一阶段管理 Standard；Realtime 可识别和编辑草稿，但在远程 Adapter 启用前不得发布，不能提供“看似可用”的发布入口。

### 9.2 草稿、版本和发布

- 草稿每次保存携带旧 `draft_revision`，不匹配返回 `409 DRAFT_REVISION_CONFLICT`，禁止自动覆盖。
- Validate 返回结构化错误、警告和配置哈希。
- Publish 在同一事务中重新校验 revision、Runtime、配置和哈希，按 `(app_id, config_hash)` 幂等获取或创建不可变版本，再更新 Release 指针。
- 版本创建后不可修改；`(app_id, version_no)` 唯一。`source_draft_revision` 仅用于来源追溯和查询索引，不承担发布幂等；发布幂等以 `(app_id, config_hash)` 为准。
- 回滚只修改 Release 指针，不修改草稿、不创建版本，也不按当前能力目录重新校验历史版本。
- 能力禁用或 deprecated 后，历史版本仍可按固定 `runtime_engine/runtime_adapter/runtime_catalog_version` Resolve。

当前设计不引入多环境 Release 模型或跨环境复用规则。Robot 停用会阻止新 Resolve、新预留和新 dispatch；已授权或正在进行的 Call 不因后续停用而中断，继续使用启动时固定的版本。

### 9.3 Resolve 与精确版本

当前 Release Resolve 读取应用启停状态、Release 和版本内容；未发布或停用统一返回 `RESOLVE_FAILED`，不向调用方泄露 API Key。

Fast-Call Claim 使用 `ResolveExactVersionUseCase` 按固定 `robot_app_version_id` 读取，不与当前 Release 或最新能力目录混合。控制台 Voice Test 的内存 Session 不复用外呼 Claim 配置。

## 10. Console BFF、认证与静态托管

### 10.1 同进程 BFF

Console 使用 React SPA + 同源 FastAPI BFF，BFF 直接调用现有 Application Use Cases，不通过 HTTP 回环，也不持有 Management/Resolve Token。现有 Token 管理 API 继续服务受信任的自动化调用，与浏览器 BFF 共享业务逻辑。

主要同源接口覆盖 login/logout/session、capabilities、应用列表、草稿、启停、Validate、Publish、版本、Diff 和 Rollback；不向浏览器提供 Resolve 路由。

Console 现已增加任务列表/详情、联系人、Call、启动/暂停/恢复/取消，以及 Agent 记录读取、全局汇总、按天聚合和 facet 的同源 BFF。任务与 Call 使用显式脱敏 DTO；Agent 数据由 `AgentReportClient` 通过 `COLLECTION_AGENT_HTTP_URL` 和 Agent 内部 Bearer 凭据读取，浏览器不接触内部 Token 或 Agent 数据库。主 Call 与 Agent 记录在页面独立加载，Agent 404、超时和不可用分别映射，不掩盖电话事实。

### 10.2 浏览器认证

- 登录成功后设置带过期时间的 HttpOnly Session Cookie；`SameSite=Lax`，生产必须 `Secure`。
- 写请求要求自定义 CSRF Header，登录接口除外。
- actor 由服务端 Session 取得，浏览器不能通过 `X-Actor-Id` 冒充。
- Session 和浏览器存储不保存内部 Token 或配置草稿。
- 未认证返回 401，CSRF 失败返回 403。
- 单管理员配置为 `FASTCALL_CONSOLE_ADMIN_ACTOR_ID`、`FASTCALL_CONSOLE_ADMIN_PASSWORD_HASH`、`FASTCALL_CONSOLE_SESSION_SECRET`；只保存密码哈希，后续可用 SSO 替换认证适配器。

### 10.3 编辑器与版本管理

- 应用列表支持新建、搜索、启停和状态摘要；停用需确认，提示只影响新 Resolve。
- 编辑页按 Prompt、变量、Runtime、Flow、通话策略分段；结构化表单与完整 JSON 高级编辑共享同一草稿，切换不得丢失未知子字段。
- 保存始终提交完整配置和 revision；冲突时允许查看或重新基于最新草稿编辑，不自动覆盖。
- 历史版本在同一编辑器中只读展示；返回当前草稿时恢复之前编辑状态，回滚不覆盖当前草稿。
- 发布前调用 Validate；重复发布返回已有版本。
- Prompt/Flow 按纯文本渲染，不使用 `dangerouslySetInnerHTML`。

### 10.4 静态资源与单镜像

- `/console/api/v1` 先处理 BFF，`/console/assets` 提供哈希静态资源，`/console/{path:path}` 做 SPA fallback。
- API 404 必须返回 JSON，不能被 SPA fallback 捕获。
- 带哈希的 JS/CSS 长期缓存，`index.html` 使用 `no-cache`；不启用 Service Worker。
- Node 只用于 Docker 构建阶段，生产只有 FastAPI 一个进程和端口 8766，不运行 Vite/Node 服务，不增加前端 Deployment。
- 生产启动检查 `console/dist` 存在；构建产物不手工修改。

## 11. Review 请求与审计约束

Admin review 请求字段：

```json
{
  "expected_state_version": 7,
  "resolution": "failed",
  "result_code": "CONFIRMED_NOT_DIALED",
  "evidence_type": "CDR_AND_CHANNEL_SCAN",
  "evidence_ref": "artifact-001",
  "note": "No originated channel found",
  "force_release": false
}
```

- `resolution` 支持 `restore_active/succeeded/failed`；`unknown` 只能修正终态，不能重新激活或隐式重拨。
- 活动通道需要结束时先对实际 `freeswitch_uuid` 执行挂断并等待终态，强制释放必须带 `force_release=true` 和证据。
- 期望版本、证据、说明和操作者身份必须明确，actor 长度不得超过数据库字段限制。

Robot 版本和配置审计只追加，不允许 UPDATE/DELETE；数据库 Trigger 是 Repository 之外的第二道约束。审计仅保存操作标识、对象、版本 ID、哈希和非敏感摘要，不记录 API Key、完整 Prompt、真实通话变量或被叫号码。

## 12. 命名与兼容约定

- 控制面镜像为 `deploy/Dockerfile-fastcall`，控制面部署为 `deploy/fastcall-api-deploy.yaml`，Alembic 配置为 `deploy/fastcall-alembic.ini`。
- `FASTCALL_IMAGE`、`FASTCALL_ADAPTER_IMAGE` 和 `COLLECTION_AGENT_IMAGE` 分别对应三个独立镜像。
- 配置统一使用 `FASTCALL_*`（通用 `DATABASE_URL` 除外），不再读取旧 `ROBOT_CONFIG_*` 环境变量。
- `robot_config_alembic_version` 是已部署数据库中的历史版本表名，必须保留，不能仅为命名统一而修改。
- Kubernetes 使用 `fastcall-api` 和 `fastcall-secret`。上线新名称前需创建对应 Secret，并确认原 `robot-config-service` 的 DNS/调用方已完成迁移；不得只修改 Manifest 名称而忽略外部依赖。
- Console 仅接受 `fastcall_console_session`，旧 `robot_config_console_session` 不再用于认证。

## 13. Console 前端技术规范与目录规划

本节记录已经评审并实施的第一批 Console 能力及其目标结构。任务、Call、Agent 技术报表和任务控制已经实现；任务创建、名单导入、异常 Call、Webhook 和 transcript 属于后续范围。所有实现保持第 10 节的同源 BFF、认证和静态托管契约，不增加前端 Deployment。

### 13.1 技术边界

- 沿用 React 18、React Router、TypeScript strict、Vite、Vitest、Testing Library 和原生 CSS，不新增 UI、状态管理、图表、Tailwind、React Query 或 MSW 依赖。
- 统一 Fastcall Console 和 AppShell，但不把任务、Call、Agent 报表融入 Robot 应用或做成全系统单页。Robot 是配置对象，任务、Call 和 Agent 记录是运行对象。
- 页面使用本地 `useState/useEffect` 获取数据；共享 `api/client.ts` 继续负责同源 fetch、Session 失效和 CSRF，不建立全局 store。
- 首期手动刷新，不增加 WebSocket 推送或实时大屏；不为少量路由增加 lazy-load。
- 不预建通用 DataTable、hooks、services、constants 或格式工具集合。第二个真实调用点出现后再提取共享组件或工具，语义不同的统计卡不得为了复用强行统一。
- 组件和 API 返回内容继续纯文本渲染，不使用 `dangerouslySetInnerHTML`，不在浏览器存储内部 Token、完整号码、变量、transcript 或录音。

### 13.2 路由与导航

SPA base 保持 `/console/`：

| 目标路由 | 页面 | 说明 |
|---|---|---|
| `/tasks` | 外呼任务列表 | 默认首页；状态、Robot、搜索和分页可恢复 |
| `/tasks/:taskId` | 任务详情 | 联系人、Call 记录、只读任务配置和状态操作 |
| `/calls/:callId?record=:recordId&from=:source` | 统一 Call 详情 | 主 Call 与 Agent 记录独立加载；`record` 可选，`from=tasks|reports` 只用于导航上下文 |
| `/reports/agent` | 通话技术分析 | 全局汇总、垂直趋势图和每日报表；不含记录明细或 Call 搜索 |
| `/apps` | Robot 应用列表 | 保留现有能力 |
| `/apps/:appId/edit` | Robot 配置编辑 | 保留现有能力，应用导航保持高亮 |

- `/` 和未知路径默认跳转 `/tasks`，不改变 `/console/api/v1` 的 JSON 404。
- AppShell 使用显式路由元数据统一维护导航项、当前高亮、页面标题和面包屑，替换固定 `active` 与固定标题写法。
- Agent 报表不另建重复的记录详情页；点击记录进入统一 Call 页并通过 `record` 选中记录。
- `record` 不存在、已不可用或请求失败时，主 Call 不受影响；默认选中首条可用记录并显示记录级错误。
- 直接打开 Call 页时提供明确回退；从任务进入时可依据主 Call 的 `task_id` 返回任务，从报表进入时返回 `/reports/agent`。不得依赖不稳定的内存页面状态才能返回。

### 13.3 前端目标目录

```text
src/fastcall/console/
├── package.json
├── vite.config.ts
├── tsconfig.json
└── src/
    ├── App.tsx                       # Session、Routes、默认路由；不另建路由框架
    ├── main.tsx
    ├── api/
    │   ├── client.ts                 # 同源 fetch、ApiError、CSRF、401 事件
    │   ├── types.ts                  # 现有类型及少量真正跨 feature 的稳定 DTO
    │   └── agentRecords.ts           # 报表与 Call 页真实复用的 Agent 记录 API
    ├── components/
    │   ├── AppShell.tsx              # 导航、面包屑、工作区与页面容器
    │   ├── LoginPage.tsx
    │   └── ui/                       # 不预建；仅在真实复用出现后提取
    ├── features/
    │   ├── apps/                     # 保留现有应用列表
    │   ├── editor/                   # 保留现有编辑器与语音测试
    │   ├── tasks/
    │   │   ├── api.ts
    │   │   ├── types.ts
    │   │   ├── TaskListPage.tsx
    │   │   ├── TaskDetailPage.tsx
    │   │   ├── TaskActions.tsx
    │   │   ├── TaskStatsStrip.tsx
    │   │   ├── ContactTable.tsx
    │   │   ├── TaskCallTable.tsx
    │   │   ├── TaskConfigPanel.tsx
    │   │   ├── tasks.css
    │   │   └── *.test.tsx
    │   ├── calls/
    │   │   ├── api.ts
    │   │   ├── types.ts
    │   │   ├── CallDetailPage.tsx
    │   │   ├── TelephonyFacts.tsx
    │   │   ├── CallTimeline.tsx
    │   │   ├── AgentRecordPanel.tsx
    │   │   ├── calls.css
    │   │   └── *.test.tsx
    │   └── agent-reports/
    │       ├── api.ts                # 只放报表专属查询封装；记录读取复用 api/agentRecords
    │       ├── types.ts
    │       ├── AgentReportsPage.tsx
    │       ├── AgentReportFilters.tsx
    │       ├── AgentReportSummary.tsx
    │       ├── AgentTrendChart.tsx     # 通用垂直趋势面板，含 NULL 断线
    │       ├── AgentDailyTable.tsx     # 每日指标与 Provider 行展开
    │       ├── DateTimeRangePicker.tsx
    │       ├── calendar.ts
    │       ├── agent-reports.css
    │       └── *.test.tsx
    ├── utils/
    │   ├── datetime.ts               # 仅放真实共享的 UTC/本地时间处理
    │   └── numbers.ts                # 仅放真实共享的数字、时长和 USD 格式
    ├── styles/
    │   ├── tokens.css
    │   └── global.css
    └── test/
        └── setup.ts
```

目录约束：

- `agent-reports` 使用完整业务名称，不使用宽泛的 `reports`，避免未来混入业务分析或 Webhook 报表。
- `AgentTechnicalRecordListItem`、`AgentTechnicalRecordDetail` 是报表和 Call 页共享的稳定契约，可放入 `api/types.ts`；任务和 Call 页面专属类型留在各自 feature。
- `calls` 不得反向 import `agent-reports` 的页面组件或私有类型；共享记录读取由 `api/agentRecords.ts` 承担，不复制两份 DTO 和请求实现。
- 单 feature 使用的格式化函数就近放置，不新建 `utils/format.ts` 杂物模块。
- `components/ui` 不作为首批文件清单；EmptyState/ErrorState 等出现第二个真实调用点后再提取。

### 13.4 页面组件与状态契约

- `TaskListPage` 拥有筛选、列表、汇总和分页状态；当前页任务及聚合数据由 BFF 一次返回，前端不循环请求任务详情。
- `TaskDetailPage` 拥有任务、联系人、Call 和配置 Tab 状态。Tab 只组织同一任务的对象，不承担全局导航。
- `TaskActions` 根据服务端任务状态展示启动、暂停、恢复和取消；写操作携带当前 `expected_state_version` 和 CSRF，进行中禁用重复提交。
- 状态冲突或请求超时后重新读取任务，不自动覆盖、不盲目重发。取消默认 `hangup_active=true`，危险操作二次确认。
- `CallDetailPage` 分别维护主 Call 与 Agent 记录的 loading/success/empty/error 状态，不使用一个聚合 Promise 让任一侧失败拖垮整页。
- `AgentRecordPanel` 按 `call_uuid=call_id` 读取全部来源的记录，支持同 Call 多 `record_id` 和 `claim_generation`；记录缺失只表示暂无或尚未落库。
- `AgentReportsPage` 使用全局时间、Robot 和 Bot 模式筛选，不包含技术记录、精确 Call 或来源筛选。汇总带与垂直图表共享绝对时间边界，每日报表位于所有图之后。
- 趋势图包含会话量、语音信箱占比、平均 Agent 会话、平均轮次、成本、重试重连，以及单张 ASR/LLM/TTS 平均/P50/P80/P95 TTFB 分布图；没有指标切换控件。
- 所有空值显示 `—`，不得把缺失指标渲染成 0；成本显示 USD 估算和缺失计数，TTFB 显示有效轮次样本数、覆盖记录数和截断状态。

### 13.5 请求、URL 与类型规范

- feature API 使用共享 `api<T>()`，请求参数通过 `URLSearchParams` 构造；列表筛选进入 URL，筛选变化清除旧 cursor，分页和浏览器前进后退可恢复。
- 每次请求使用 `AbortController` 或等效请求序号防止慢响应覆盖新筛选；组件卸载后不再更新状态。
- API DTO 保持服务端 `snake_case` 和 UTC ISO 时间，不另建无收益的完整 ViewModel 映射；只有页面派生展示值才在组件内计算。
- 时间由浏览器本地化并明确时区；任务呼叫窗口同时保留任务自身 timezone。数字、时长和成本显式带单位。
- BI 看板使用绝对 UTC `[start,end)` 和固定 Asia/Shanghai 自然日，不提供用户时区选择。自定义日期时间最多 92 个自然日，结束时间不得晚于当前时间；手动刷新推进边界。
- 报表与统一 Call 页共享记录 DTO，但 Call 页必须按 `source=all` 查询，避免默认正式外呼筛选隐藏历史记录。

目标 Console BFF 接口：

```text
GET  /console/api/v1/outbound-tasks
GET  /console/api/v1/outbound-tasks/{task_id}
GET  /console/api/v1/outbound-tasks/{task_id}/contacts
GET  /console/api/v1/outbound-tasks/{task_id}/calls
POST /console/api/v1/outbound-tasks/{task_id}/start|pause|resume|cancel
GET  /console/api/v1/calls/{call_id}
GET  /console/api/v1/agent-call-records
GET  /console/api/v1/agent-call-records/summary
GET  /console/api/v1/agent-call-records/daily
GET  /console/api/v1/agent-call-records/facets
GET  /console/api/v1/agent-call-records/{record_id}
```

GET 使用 Console Session，写操作额外要求 CSRF。Agent 记录由 Fast-Call BFF 服务端调用 Agent 内部只读 API，浏览器不接触 Agent token、内部地址或 Agent 数据库。

### 13.6 样式与视觉实现

- `tokens.css` 继续维护页面、表面、文字、边框、主色和状态色；`global.css` 只放基础元素、共享布局与已有组件。
- 新 feature 样式分别放入 `tasks.css`、`calls.css`、`agent-reports.css`，使用 `tasks-`、`calls-`、`agent-reports-` 前缀，避免继续向全局样式追加无前缀类名。现有 editor 样式不做无关重写。
- 延续 Dify 式内容优先：浅灰背景、白色侧栏、淡蓝选中态、细边框、8–10px 圆角和低饱和状态色；不使用渐变、玻璃卡片、发光或深色大屏。
- 页标题约 24px，区块标题 14px，正文 13px，辅助文字 11–12px，表格 12–13px；数字右对齐，ID 使用等宽字体。
- 表格先使用语义化 HTML，不提前建立配置驱动的通用 DataTable。布局必须覆盖 1440px 桌面视口，窄屏沿用现有响应式策略，不为首期增加独立移动端应用。

### 13.7 后端配套目录

```text
src/fastcall/
├── api/console/
│   ├── routes.py                   # 现有 Robot BFF
│   ├── voice_test.py               # 现有语音测试 BFF
│   ├── outbound.py                 # 新增任务、联系人、Call BFF
│   └── agent_reports.py            # 新增 Agent 报表 BFF
├── application/outbound_queries.py # 扩展任务列表与批量聚合查询
└── infrastructure/agent_report_client.py

src/collection_agents/
├── server/report_routes.py         # 内部只读 GET，不写业务数据
└── storage/reports.py              # Agent 自有 call_record 查询与字段白名单
```

`fastcall/api/routes.py` 只负责注册新增 BFF 路由；组合根显式装配 Agent HTTP 地址和 client。不得从 WebSocket URL 推导报表地址，不新增服务或跨库查询。Agent 新字段需要 SQLite/MySQL 显式增量迁移，不能依赖 `CREATE TABLE IF NOT EXISTS` 给旧表补列。

### 13.8 测试与验收

前端测试继续与组件同目录，使用 Vitest 和 Testing Library，mock feature API 或 fetch，不新增测试框架：

- 路由：默认首页、应用导航高亮、URL 筛选恢复、非法 `record` 和返回来源。
- 任务：筛选与 cursor、状态按钮、重复提交、409 刷新、取消语义和超时处理。
- BI 报表：全局 Robot/模式、测试通话不落库、日历应用与取消、昨天边界、92 日限制、空库、空样本、样本截断、成本/轮次/重连缺失、NULL 断线和长范围刻度抽样。每日总量只执行一次条件聚合，TTFB 每日最多 1000 条采样记录。
- Call：主 Call 成功/Agent 失败、主 Call 失败/Agent 成功、双侧空态及记录切换，验证两侧互不影响；使用 `source=all` 验证同一 `call_uuid` 的正式、测试和历史 NULL 来源记录均不会因报表默认过滤而消失。
- 安全：渲染内容转义，页面和浏览器存储不出现内部 Token、完整号码、变量、transcript 或录音。

每个实施阶段至少执行：

```text
cd src/fastcall/console
npm test
npm run build
```

后端同步运行 Console、Outbound、Agent 报表、路由集合和架构边界测试。最终通过真实 Fast-Call BFF 在浏览器验收“任务列表 → 任务详情 → Call 详情 → Agent 记录 → 返回来源”，并单独验证 Agent 404、超时和不可用降级；不能只以 import、编译或静态截图代替行为验收。

### 13.9 实施状态与后续顺序

1. 已实现：数据契约与增量迁移，包括来源、claim generation、价格元数据、绝对时间边界和白名单 DTO。
2. 已实现：AppShell 路由元数据、任务只读列表/详情和批量聚合 BFF。
3. 已实现：Agent 只读 API、报表页、统一 Call 页及双侧独立降级。
4. 已实现：任务启动、暂停、恢复和取消；状态变更同时刷新联系人和 Call 子表。
5. 已实现：全局通话技术分析、日期时间日历、七张垂直趋势图、合并 TTFB 分布和每日报表；测试通话不写技术指标。
6. 后续：任务创建与名单导入；异常 Call、Webhook 和 transcript 不混入本次前端目录首批实施。

## 14. 独立 Mock Call

Mock Call 是 `/mock-calls` 的独立运行验收能力，不改变 Robot 编辑页的草稿 Voice Test。用户选择当前已发布版本并填写变量；`PrepareMockCallUseCase` 在一个事务中创建 `origin=mock_call` 的 DRAFT Task 和单个 Contact，固定版本与配置哈希。浏览器和 Adapter 媒体入口注册后，才将 Task 激活为 RUNNING，由正式 Scheduler 预留并调用 Adapter dispatch。

Mock Call 复用正式容量、速率、拨号授权、Agent Session、终态、Outbox 和 Webhook Worker。电话重试固定为零，但 Webhook 投递重试保持原语义。callback 只能使用服务端 `FASTCALL_MOCK_CALLBACK_URL` 和 `FASTCALL_MOCK_CALLBACK_SECRET_REF`，由现有服务内的 `/internal/mock-webhook` 验证 HMAC、时间戳和事件 ID 后返回 204；不得接受浏览器提供的客户地址或密钥，也不关闭 HTTPS、私网白名单或证书校验。

`outbound_task.origin` 是唯一任务来源标记，正式任务列表只查询 `outbound`。Mock 页面通过现有 Task/Call、Agent 记录和 `webhook_delivery` 读取结果，不建立第二套 Call 聚合。

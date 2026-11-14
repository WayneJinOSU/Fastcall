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

保留现有 `ROBOT_CONFIG_*` 和 `FASTCALL_*` 名称。Adapter 地址使用 `FASTCALL_ADAPTER_INTERNAL_URL`，Agent 地址使用 `COLLECTION_AGENT_WS_URL`。

控制面启动要求 MySQL PyMySQL URL、七种互不相同的 API/内部 token，以及 Console actor、密码哈希和至少 32 字符 session secret。还必须提供 `FASTCALL_ENCRYPTION_KEYS_JSON`、`FASTCALL_ENCRYPTION_ACTIVE_VERSION`、`FASTCALL_HMAC_KEYS_JSON`、`FASTCALL_HMAC_ACTIVE_VERSION`。仅配置外呼 Open API 凭据不足以启动完整控制面。

Fast-Call 拥有 Alembic 迁移和完整 schema 校验，version table 为 `robot_config_alembic_version`，当前基线 `0004`。不得在业务测试中使用非独立业务库重建 schema。

## 8. 测试与修改指引

主要测试：

- `tests/unit/fastcall/`：API、任务、调度、Webhook、Console、启动。
- `tests/unit/architecture/`：包边界、路由集合、事务所有权、UOW 不变量。
- `tests/integration/test_outbound_e2e`：跨模块模拟闭环。
- `tests/integration/robot_config_mysql/`：真实 MySQL，仅允许独立测试库。

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

### 10.2 浏览器认证

- 登录成功后设置带过期时间的 HttpOnly Session Cookie；`SameSite=Lax`，生产必须 `Secure`。
- 写请求要求自定义 CSRF Header，登录接口除外。
- actor 由服务端 Session 取得，浏览器不能通过 `X-Actor-Id` 冒充。
- Session 和浏览器存储不保存内部 Token 或配置草稿。
- 未认证返回 401，CSRF 失败返回 403。
- 单管理员配置为 `ROBOT_CONFIG_CONSOLE_ADMIN_ACTOR_ID`、`_PASSWORD_HASH`、`_SESSION_SECRET`；只保存密码哈希，后续可用 SSO 替换认证适配器。

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

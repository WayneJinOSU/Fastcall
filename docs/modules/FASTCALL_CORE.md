# Fast-Call Core 共享内核设计

## 1. 定位与目标

`fastcall_core` 是 Fast-Call 控制面与 Adapter 的共享 Python 库，不是运行服务。它解决的是当前真实共享：

- 同一套 Task/Contact/Call 领域状态和结果语义。
- 同一套终态、任务完成、Session、Analysis 和 Outbox 事务逻辑。
- 同一套 ORM、共享 DAO、数据库连接和 UOW。
- 同一套加密/HMAC 纯能力和精确 Robot 版本读取。

core 不追求通用框架，也不表示 Fast-Call 与 Adapter 已成为完全独立的数据域。二者仍属于同一外呼业务边界。

## 2. 当前结构

```text
src/fastcall_core/
├── domain/
│   ├── outbound_tasks.py          # Task/Contact 状态与转换
│   ├── calls.py                   # Call/Session 状态与活动集合
│   ├── calling_windows.py         # 时间窗、DST 与排除日期
│   ├── retry_policy.py
│   ├── telephony_results.py
│   └── errors.py
├── application/
│   ├── call_results.py            # FinalizeCallUseCase、CompleteTaskUseCase、事件 payload
│   ├── call_events.py             # AgentSessionUseCase
│   ├── call_analysis.py
│   ├── resolve_exact.py           # 精确版本读取
│   ├── reservations.py            # 未授权预留释放/取消终态
│   ├── views.py                   # Task/Contact/Call 共享响应视图
│   ├── dto.py                     # ID、UTC、时间序列化
│   ├── records.py                 # Robot 记录 DTO
│   └── outbound_ports.py          # 共享记录与 Protocol
└── infrastructure/
    ├── orm.py、outbound_orm.py
    ├── outbound_repositories.py   # 共享叶子 DAO
    ├── result_repository.py       # 多表终态原子操作
    ├── session_repository.py
    ├── analysis_repository.py
    ├── pinned_version_repository.py
    ├── outbound_unit_of_work.py   # Core UOW
    ├── database.py                # 连接/ping/close，不含控制面完整校验
    └── crypto.py                  # 纯 SecretCodec/PhoneHmac 与 key 解码
```

`call_results.py` 当前仍是终态与任务完成的共同事务模块。若未来拆成 finalize/complete/payload 文件，不得拆散其原子事务或改变提交顺序。

## 3. 明确不包含

- FastAPI、Uvicorn、Controller 或 Settings。
- Fast-Call Scheduler、查询、任务控制、Robot 管理发布和 Worker 工作流。
- Adapter dispatch/telephony/CallCheck、ESL 或媒体编排。
- Collection Agent Pipeline 或 Provider。
- 全局环境读取和 `from_env()`。

环境变量到 `SecretCodec`/`PhoneHmac` 的装配留在 Fast-Call/Adapter 入口工厂。`decode_keys()` 是纯解析能力，不读取环境。

## 4. 共享事务用例

### 4.1 Call 终态与任务完成

`FinalizeCallUseCase` 负责：

- 校验 Call 状态、代际、活动关系及必要版本。
- 计算重试、时间窗、技术结果和联系人状态。
- 更新 Call/Contact、重试额度、结果 revision 和首次终态 Outbox。
- 必要时完成任务并生成任务 Outbox。

`CompleteTaskUseCase` 的完成统计是完成时快照。嵌套调用使用 `commit=False`，最外层用例只提交一次。不同来源的重复事件只记录，不重复收敛。

### 4.2 Agent Session

`AgentSessionUseCase` 负责 Claim、recover、attached、ready、failed 和 completed：

- Claim 校验当前 Call、活动关系、代际、失败历史和固定配置哈希。
- attached/ready 持久化后才返回 ACK。
- ready 前失败可收敛 Call；ready 后仅当前代际可改变状态。
- 电话已先终态时，Agent completed 仍可保存分析并幂等 ACK，不重复转换。

精确版本读取通过 `pinned_versions` DAO 完成；Robot 管理和发布 DAO 留在 Fast-Call。

### 4.3 未授权预留释放

`ReleaseUnauthorizedReservationUseCase` 统一处理 Scheduler 和 Adapter 的释放：

- 正常任务确认未授权后释放并重新 pending。
- 已取消任务使用 cancelled 终态，不让联系人重新 pending。
- 保留 `state_version`、活动关系和 `dial_requested_at IS NULL` 条件。

## 5. DAO 与 UOW 规则

### 共享 DAO

core 提供 Task/Contact/Call、事件、Outbox、结果、Session、Analysis、Robot 版本读取、blacklist 和 `scheduler_state` 的唯一共享实现。

### 不整类搬迁混合职责

当前旧仓储曾混合共享读取、调度和 Adapter 操作。拆分按方法及完整事务职责：

- `list_due`、重试提升、调度筛选、`reserve_dialable` 归 Fast-Call。
- `authorize`、`acknowledge`、`mark_checking` 归 Adapter。
- 共享读取和 `release_unauthorized_reservation` 等共同操作留 core。
- 不把完整多表事务方法拆到不同 UOW。

### 三种组合

```text
Core UOW
└── 共享叶子 DAO

Fast-Call UOW(Core UOW)
└── Robot 管理 + 调度 + 查询 + 任务控制 + Worker DAO

Adapter UOW(Core UOW)
└── dispatch + telephony + hangup DAO
```

每个组合及其 DAO 共享同一个 SQLAlchemy Session。不得引入通用 BaseRepository，不得让专用 DAO 泄入 core。

## 6. Webhook 配置注入

core 不读取 `FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED`。core UOW 接收 `result_changed_enabled` 回调，默认 false。

Fast-Call/Adapter 组合根注入实际配置读取函数。终态和 review 用例只调用注入能力，因此：

- core 无环境依赖。
- 默认关闭在双方一致生效。
- 显式开启不改变 event ID、payload、revision 或事务。
- Worker 投递侧另有事件类型过滤，防止历史 pending 绕过默认关闭。

## 7. 测试与修改守则

- `tests/unit/fastcall_core/`：领域、时间窗、状态与共享逻辑。
- `tests/unit/architecture/`：core 无服务依赖、无环境读取、UOW 结构不变量。
- 控制面和 Adapter 原有用例继续覆盖共享调用方；不能只测试 core import。
- 修改终态时必须验证事务、Outbox、重复事件和重试，不得只做静态检查。
- 新增共享能力需有双方真实使用证据；否则放入实际所属包。
- core 不得 import `fastcall`、`fastcall_adapter` 或 `collection_agents`。

## 8. 状态机与结果语义

### 8.1 状态和容量

Task 状态：

```text
draft → scheduled → running ↔ paused
  │         │          │
  └─────────┴──────────┴──→ cancelled
running → completed
```

Contact 状态：

```text
pending → calling → succeeded/failed/unknown
  │       │       └──→ waiting_retry → pending
  └───────┴──────────→ cancelled
```

Call 状态：

```text
reserved → dialing → ringing → answered → talking
              │                         │
              │                         └──→ succeeded/failed/cancelled
              └──→ checking → succeeded/failed/unknown
```

- `reserved` 占容量但未授权拨号；只有确认 `dial_requested_at IS NULL` 才能回收。
- `answered` 表示已接听但 Agent 未 ready；`talking` 要求 `session_ready_at` 非空。
- 活动容量状态为 `reserved/dialing/ringing/answered/talking/checking/unknown`。
- `unknown` 保留 `contact.active_call_id` 和容量，不阻塞 Task completed，但需要可靠终态或 review 释放。
- Webhook 投递失败不改变 Task/Contact/Call 状态。

技术成功要求电话接听、当前代际 ready 已持久化并 ACK，以及正常挂断、结束语、无响应策略、语音信箱或最大时长结束。ready 前用户挂断为 `AGENT_READY_TIMEOUT`；ready 后普通用户挂断可为 `USER_HANGUP` 成功。Agent/媒体/Provider 异常优先于普通挂机原因。

### 8.2 标识与计次

- `task_id/contact_id/call_id` 是内部主键；`customer_task_id/customer_contact_id` 是客户幂等标识。
- `call_id` 同时用于 dispatch、Agent `callUUID`、Session registry 和 FreeSWITCH `fastcall_call_id`。
- `origination_uuid` 用于关联早到事件，`freeswitch_uuid` 用于实际媒体和挂断；正常相同但分别保存校验。
- `adapter_boot_id` 区分 Adapter 启动实例，`claim_generation` 区分同一 Call 的 Agent 代际。
- `bgapi` Job-UUID 只用于诊断，不作为业务或通道标识。
- `call_count` 按 `dial_requested_at IS NOT NULL` 的 Call 数派生；`last_call_id` 取最大 `call_no`，不重复持久化。

### 8.3 结果码与重试

| 结果 | 默认行为 |
|---|---|
| `NO_ANSWER`、`USER_BUSY`、`TEMPORARY_NETWORK_FAILURE` | 可按任务配置重试 |
| `CALL_REJECTED` | 不重试 |
| `INVALID_NUMBER/UNALLOCATED_NUMBER` | 不重试 |
| `USER_HANGUP` | ready 后成功 |
| `VOICEMAIL` | 成功，`answered_by_human=false` |
| `ORIGINATE_RESULT_UNKNOWN` | 进入 checking |
| `CONFIRMED_NOT_DIALED` | 失败，不自动重试 |
| `CALL_CHECK_TIMEOUT` | `unknown` |
| `AGENT_READY_TIMEOUT` | 失败，不自动重试 |
| `MEDIA_CONNECTION_TIMEOUT` | 失败，不自动重试 |
| `MEDIA_BACKPRESSURE_OVERFLOW` | 失败，不自动重试 |
| `AI_PROVIDER_FAILED` | 失败，不自动重试 |

重试使用新 Call，历史不覆盖。`max_retries` 不含首次呼叫，间隔数必须等于重试次数；仅当前结果属于 `retry_on` 且额度未用完时消耗额度。等待容量、健康预检、未授权系统退避、`unknown`、已接听后失败、禁呼、空号和人工修订不占自动重试额度。

## 9. 数据模型、事件与安全契约

### 9.1 数据表职责

| 表 | 职责与关键约束 |
|---|---|
| `outbound_task` | 固定 Robot 版本、时区、窗口、容量、速率、回调、状态/结果版本 |
| `task_contact` | 加密号码/变量、HMAC、重试、活动 Call、最终结果；同任务客户 ID、号码和导入序号唯一 |
| `outbound_call` | 每次呼叫历史、授权、通道、Session、核查、原始挂机事实和状态版本 |
| `call_event` | 来源稳定事件 ID、脱敏 payload/hash、处理租约；同来源 ID 唯一，同 ID 不同 hash 为冲突 |
| `call_blacklist` | 加密号码与 HMAC 版本、来源、有效期；首次/重试和最终栅栏均检查 |
| `webhook_delivery` | 事务性 Outbox、稳定事件 ID、地址/payload 快照、租约和重试状态 |
| `scheduler_state` | 单行全局 `next_dial_at`，不作为 Scheduler 所有权或 fencing |
| `outbound_call_analysis` | 独立业务分析，不改变电话技术结果或触发重试 |

号码使用 E.164 和带版本认证加密，去重/禁呼使用 keyed HMAC。HMAC 轮换时新 key 写入 active、旧 key 保留只读，查询和写入均带版本；在所有接受中的版本上查重，重建完成且旧版本无命中后才移除旧 key，冲突时停止切换而非删除数据。

### 9.2 事件 ID 与 FreeSWITCH 事实

FreeSWITCH 事件 ID 优先组合实例 UUID、boot/stream epoch、通道 UUID、事件类型和 `Event-Sequence`；缺少序号时使用规范化 payload hash。事件重放只重建状态，不重新拨号。

主通道按实际 Channel UUID 和 `origination_uuid` 关联，再校验 `fastcall_call_id`。loopback 另一腿不得覆盖主通道或创建第二个 Agent Session。

### 9.3 时间窗与重试时间

- 使用 IANA timezone，窗口为本地半开区间 `[start,end)`。
- `days` 使用 ISO 8601，跨午夜以开始日为准，重叠窗口取并集。
- DST 不存在或有歧义时该日窗口停用并告警；排除日期为本地日期。
- 窗口外重试顺延到下一窗口，不得超过任务截止时间。

### 9.4 结果修订通知

内部结果修订始终更新 `result_revision`。只有显式开启时才生成/投递 `contact.result_changed`；默认关闭不影响 API 查询和对账，但不能要求客户仅靠 Webhook 得到后续修订。

## 10. 重试策略示例

```json
{
  "max_retries": 3,
  "retry_intervals_sec": [60, 60, 60],
  "retry_on": ["NO_ANSWER", "USER_BUSY", "TEMPORARY_NETWORK_FAILURE"]
}
```

- 第 `n` 次重试使用 `retry_intervals_sec[n-1]`。
- `retry_count` 是已消耗额度和最近安排的重试序号，不等于已完成重拨数。
- `max_retries=0` 使用空数组，间隔数量必须等于 `max_retries`。
- `dial_requested_at` 是计次边界：提交前不计，提交后保守计一次，结果未知时先重读，不能据此重复发送。

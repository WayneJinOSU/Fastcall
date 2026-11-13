# Fast-Call 独立外呼服务 V1 设计

- 状态：开发基线
- 部署：单企业、独立部署、Open API 驱动
- 技术栈：MySQL、Python `asyncio`、FreeSWITCH、Collection Agent
- 规模参考：每天约 10 个任务，每任务约 500 个联系人，多任务共享有限线路

## 1. 目标与范围

V1 提供以下闭环：

```text
创建任务 → 导入联系人 → 按容量拨号 → AI 通话 → 重试或结束 → 查询/回调结果
```

功能目标：

- 创建、启动、暂停、恢复、取消外呼任务。
- 固定任务使用的 Robot 版本和联系人变量。
- 控制全局/单任务并发和拨号速率。
- 支持无人接听、忙线和临时线路故障的定时重试。
- 保存每次 Call 的电话事件、Agent 状态和标准结果。
- Scheduler/Adapter 重启后从 MySQL 恢复，未知拨号不自动重试。
- 通过 Open API 查询，通过 Webhook 推送联系人和任务结果。
- 提供未知 Call 的人工核查入口。

V1 不包含多租户、预测式外呼、人工坐席、多线路智能路由、多活跃 Scheduler、ready 后对话续传，以及 Temporal/Kafka/Redis 等额外基础设施。

## 2. 架构与部署

### 2.1 组件

```text
客户系统
  │ Open API / Webhook
  ▼
Fast-Call
  ├── Open API / Admin API
  ├── Task Service
  ├── Dial Scheduler
  ├── Event Service
  ├── Webhook Worker
  └── Robot Config
          │ MySQL
          ▼
FreeSWITCH Adapter
  │ ESL 控制与事件             ▲ FreeSWITCH 媒体 WebSocket
  ▼                            │
FreeSWITCH → SIP 线路 → 电话用户
  │
  │ Adapter 建立 Agent WebSocket
  ▼
Collection Agent → ASR / LLM / TTS
```

| 组件 | 职责 |
| --- | --- |
| Open API | 认证、任务、名单、控制和查询 |
| Robot Config | 配置、不可变版本、发布和精确版本 Resolve |
| Dial Scheduler | 到期扫描、公平性、容量和 `reserved` 创建 |
| FreeSWITCH Adapter | 最终拨号栅栏、一次 originate、事件、媒体桥和挂断 |
| FreeSWITCH | SIP 拨号、振铃、接听、挂机、CDR 和电话音频 |
| Collection Agent | Session attach 和 AI Pipeline |
| Event Service | 事件去重、状态处理和结果保存 |
| Webhook Worker | Outbox 投递和独立重试 |

Scheduler 不发送 originate；Agent 不接触任务模型或 ESL；Adapter 不决定联系人重试。

### 2.2 部署约束

- Scheduler：单副本、单调度进程、`Recreate`。
- Adapter：单副本、单 worker、`Recreate`。
- Agent：单副本、单 worker、`Recreate`。
- 禁止滚动期间新旧实例并存，禁止 HPA 和手工扩容。
- 进程崩溃后由部署系统重启；V1 不支持无缝接管。
- 同一 Call 的 dispatch、ESL 事件、媒体连接、Agent 连接和挂断由同一 Adapter 进程处理。

未来增加副本前，需要另行设计 Scheduler fencing、Adapter 路由和共享 Agent Session ownership。

## 3. 领域模型与标识

| 实体 | 含义 |
| --- | --- |
| `OutboundTask` | 一批联系人的共同配置、调度和结果 |
| `TaskContact` | 任务中的一个联系人及其当前状态 |
| `Call` | 对联系人的一次呼叫；每次电话重试创建新 Call |

标识规则：

- `task_id/contact_id/call_id`：Fast-Call 主键。
- `customer_task_id/customer_contact_id`：客户业务标识。
- `call_id` 同时作为 dispatch 幂等键、Agent `callUUID`、Agent Session registry 键和 FreeSWITCH `fastcall_call_id`。
- V1 一次 Call 最多一个 Agent Session，不设置独立 `call_session_id`。
- `origination_uuid`：Adapter 在 originate 前预分配的通道 UUID，用于关联早到事件。
- `freeswitch_uuid`：从通道事件确认的实际 UUID，用于媒体和挂断。
- 正常情况下 `origination_uuid=freeswitch_uuid`，仍分别保存并校验。
- `adapter_boot_id`：Adapter 启动实例 UUID，用于区分当前进程与重启后的恢复进程。
- `claim_generation`：同一 Call 内的 Agent 领取代际，从 1 开始，恢复时递增。
- `bgapi` Job-UUID 只写入命令事件，用于诊断，不作为业务或通道标识。

## 4. 数据设计

所有业务状态以 MySQL 为准。关键状态更新使用行锁和 `state_version` 条件更新；Call 操作还需校验 `contact.active_call_id`。

`state_version` 用于内部并发控制；`result_revision` 用于客户结果和 Outbox 顺序，两者独立。

### 4.1 `outbound_task`

| 字段 | 说明 |
| --- | --- |
| `id/customer_task_id/name` | 任务标识和名称 |
| `robot_app_id/robot_version_id/config_hash` | 启动时固定的 Robot 版本 |
| `status/health_status` | 运行和健康状态 |
| `timezone/default_country_code` | 时区和号码默认国家码 |
| `scheduled_start_at/scheduled_end_at` | 绝对开始和截止时间 |
| `calling_windows_json/excluded_dates_json` | 呼叫窗口和排除日期 |
| `max_concurrent_calls/calls_per_minute` | 单任务容量和速率 |
| `next_dial_at/last_dispatched_at` | 单任务节奏和公平轮询游标 |
| `retry_policy_json` | 重试配置 |
| `callback_url/callback_secret_ref` | 回调配置 |
| `state_version/result_revision` | 状态版本和结果版本 |
| `last_error_code` | 最近任务错误 |
| `created_at/started_at/completed_at/created_by` | 时间和创建者 |

约束：

- `customer_task_id` 唯一。
- 启动时 Resolve 并固定 Robot 版本；Robot 停用后停止新呼叫，进行中 Call 继续。
- 启动、暂停、恢复和取消使用期望 `state_version`。

### 4.2 `task_contact`

| 字段 | 说明 |
| --- | --- |
| `id/task_id/customer_contact_id/import_seq` | 标识和稳定导入顺序 |
| `phone_ciphertext/phone_last4` | 加密号码和脱敏展示 |
| `phone_hmac/hmac_key_version` | 号码指纹和 HMAC 版本 |
| `variables_ciphertext/encryption_key_version` | 加密变量和密钥版本 |
| `status` | 联系人状态 |
| `retry_count` | 已消耗的重试额度 |
| `active_call_id` | 当前活动 Call |
| `next_call_at` | 下一次最早呼叫时间 |
| `final_result_code/last_error_code` | 最终结果和最近错误 |
| `result_revision/state_version` | 结果版本和状态版本 |
| `created_at/updated_at/finished_at` | 时间 |

约束与派生字段：

- `(task_id,customer_contact_id)` 唯一。
- `(task_id,phone_hmac)` 唯一；轮换期间还需对所有接受中的 HMAC 版本查重。
- `(task_id,import_seq)` 唯一。
- `call_count` 不持久化，按 `dial_requested_at IS NOT NULL` 的 Call 数计算。
- `last_call_id` 不持久化，取该联系人最大 `call_no`。
- API 列表批量计算派生字段，避免 N+1。
- 任务启动后名单和变量不可修改。

索引：

```text
(task_id,status,next_call_at,import_seq,id)
(status,next_call_at)
(task_id,active_call_id)
```

### 4.3 `outbound_call`

| 字段 | 说明 |
| --- | --- |
| `id/task_id/contact_id/call_no` | Call 标识和联系人内序号 |
| `status/result_code/retryable` | 当前状态和标准结果 |
| `robot_version_id/config_hash` | 固定 Robot 版本 |
| `adapter_boot_id` | 拨号授权所属的 Adapter 启动实例 |
| `reserved_until` | `reserved` 恢复期限 |
| `origination_uuid/freeswitch_uuid` | 预分配和实际通道 UUID |
| `claim_generation/session_state/session_ready_at` | Agent 代际、阶段和 ready 时间 |
| `dial_phase` | `prepared/dial_requested/acknowledged/unknown` |
| `dial_requested_at/dial_acknowledged_at` | 授权和 ESL 响应时间 |
| `check_deadline_at` | 未知结果核查期限 |
| `answered_by_human` | `true/false/null` |
| `raw_hangup_cause/hangup_source` | 原始挂机事实 |
| `failure_code/failure_message/termination_reason` | 错误摘要和结束原因 |
| `reviewed_by/reviewed_at/review_evidence_ref/review_note` | 最近人工核查记录 |
| `state_version` | 状态版本 |
| `created_at/dialing_at/ringing_at/answered_at` | 拨号阶段时间 |
| `conversation_started_at/ended_at/duration_sec` | 通话阶段时间 |

约束：

- `(contact_id,call_no)` 唯一。
- `origination_uuid` 唯一且不可变。
- `freeswitch_uuid` 唯一，未拨号时允许 `NULL`。
- `dial_requested_at` 是计次边界，不证明 ESL 已收到命令。
- `send_state` 仅在 Adapter 内存中按 `call_id` 保存，不作为恢复后的发送凭据。
- `session_state` 为 `unclaimed/claimed/attached/failed`；ready 只由 `session_ready_at` 表达。
- 业务意图结果存入 `outbound_call_analysis.business_result`，不改变 Call 技术结果。

容量索引：`(status)`、`(task_id,status)`；历史索引：`(contact_id,call_no)`。

### 4.4 `call_event`

| 字段 | 说明 |
| --- | --- |
| `source/event_id/event_type` | 来源、稳定事件 ID 和类型 |
| `task_id/contact_id/call_id` | 业务关联 |
| `occurred_at/received_at` | 发生和接收时间 |
| `payload_json/payload_hash` | 脱敏内容和内容哈希 |
| `process_status` | `pending/processing/processed/retrying/dead` |
| `process_owner/process_lease_until` | 处理者和租约 |
| `process_attempts/next_process_at/processed_at/last_error` | 处理状态 |

规则：

- `(source,event_id)` 唯一。
- 同 ID 同 `payload_hash` 为重复；同 ID 不同哈希为冲突，进入错误处理。
- 事件重放只重建状态，不重新拨号。
- Worker 可领取到期的 `pending/retrying`，并回收租约过期的 `processing`。
- FreeSWITCH 事件 ID 优先组合实例 UUID、boot/stream epoch、通道 UUID、事件类型和 `Event-Sequence`；缺少序号时使用规范化内容哈希。
- 人工核查、连接断开和 `bgapi` Job-UUID 也记录为事件。

### 4.5 `call_blacklist`

字段：

```text
id
phone_ciphertext / encryption_key_version
phone_hmac / hmac_key_version
source / reason
effective_at / expires_at
created_by / created_at
```

首次呼叫、每次重试和最终拨号栅栏均查询禁呼名单。加密号码用于 HMAC 重建，不进入日志和 API。

### 4.6 `webhook_delivery`

该表同时是事务性 Outbox。

| 字段 | 说明 |
| --- | --- |
| `id/event_id` | 投递 ID 和稳定业务事件 ID |
| `aggregate_type/aggregate_id/aggregate_revision` | 聚合及结果版本 |
| `task_id/contact_id/call_id` | 业务关联 |
| `event_type` | 四类客户事件 |
| `endpoint_url/payload_json` | 地址和内容快照 |
| `status` | `pending/delivering/delivered/failed` |
| `delivery_owner/delivery_lease_until` | 投递者和租约 |
| `attempt_count/next_delivery_at` | 次数和下次时间 |
| `last_http_status/last_error/delivered_at` | 投递结果 |

约束：

- `event_id` 唯一。
- `(event_type,aggregate_type,aggregate_id,aggregate_revision)` 唯一。
- 联系人首次终态与 `contact.completed` 同事务。
- 人工结果修订与 `contact.result_changed` 同事务。
- 任务终态与 `task.completed/task.cancelled` 同事务。
- 最后一个联系人终态、任务终态及两类 Outbox 在同一锁任务的事务中完成。
- Worker 领取到期的 `pending`，并回收租约过期的 `delivering`；重投保持相同 `event_id/payload`。
- 达到上限的 `failed` 只告警，管理操作可显式重置为 `pending`。

### 4.7 `scheduler_state`

单行保存全局 `next_dial_at`。全局和任务速率均使用：

```text
dial_interval_sec = 60 / calls_per_minute
```

该表不承载 Scheduler 所有权。

## 5. 状态与结果

### 5.1 任务状态

```text
draft → scheduled → running ↔ paused
  │         │          │
  └─────────┴──────────┴──→ cancelled
running → completed
```

- `paused`：停止新呼叫，进行中 Call 继续。
- `completed`：所有联系人均为终态，不代表全部成功。
- `cancelled`：停止新呼叫，进行中 Call 按取消策略处理。
- `unknown` 联系人不阻塞任务完成，其通道容量继续保留到核查结束。
- Webhook 投递结果不影响任务状态。

### 5.2 联系人状态

```text
pending → calling → succeeded
  │       │       ├──→ failed
  │       │       ├──→ unknown
  │       │       └──→ waiting_retry → pending
  ├───────┴──────────────→ cancelled
```

预检失败原因：`INVALID_PHONE_NUMBER`、`VARIABLE_VALIDATION_FAILED`、`CALL_BLACKLISTED`、`ROBOT_DISABLED`。

重复号码在导入响应中报告，不创建第二条联系人。

### 5.3 Call 状态

```text
reserved → dialing → ringing → answered → talking
              │                         │
              │                         └──→ succeeded/failed/cancelled
              └──→ checking → succeeded/failed/unknown
```

- `reserved`：占容量，尚未授权拨号。
- `answered`：已接听，Agent 未 ready。
- `talking`：已接听且 `session_ready_at` 非空。
- `checking`：核查通道、事件和 CDR。
- `unknown`：自动核查结束，等待人工处理。

`checking/unknown` 占用容量。人工强制释放需要 `force_release=true` 和证据记录，不创建新 Call。

`reserved` 超时恢复需在同一事务确认 `dial_requested_at IS NULL`，并按 `state_version + active_call_id + status=reserved` 条件取消和重新排队。事实不明时保留容量。

### 5.4 技术成功

技术成功要求：

1. 电话已接听。
2. 当前代际 `session.ready` 已持久化并 ACK。
3. 通话以正常挂机、结束语、无响应策略、语音信箱策略或最大时长结束。

结果优先级：

- ready 前用户挂断：`failed(AGENT_READY_TIMEOUT)`，保留原始挂机原因。
- ready 后用户挂断：`succeeded(USER_HANGUP)`。
- Agent、媒体或 Provider 异常优先于普通挂机原因。
- 语音信箱完成留言：`succeeded(VOICEMAIL)`，`answered_by_human=false`。
- `outbound_call_analysis.business_result` 不影响技术成功和电话重试。

## 6. 调度与拨号

### 6.1 创建预留

Scheduler 每 200ms～1s 扫描：

```text
选择 running 任务和到期联系人
→ 锁定联系人，确认 active_call_id 为空
→ 检查任务、时间窗、禁呼和容量
→ 创建 reserved Call
→ 设置 contact.active_call_id=call_id、Contact=calling
→ 提交
→ 请求 Adapter dispatch(call_id)
```

同一 `call_id` 的 dispatch 网络重试由 Adapter 串行化和去重，不创建新 Call，也不增加发送权限。

### 6.2 公平、容量和速率

可拨号条件：

```text
全局活动 Call 数 < 全局最大并发
任务活动 Call 数 < 任务最大并发
全局 next_dial_at <= now
任务 next_dial_at <= now
```

活动状态：`reserved/dialing/ringing/answered/talking/checking/unknown`。

任务排序：

```text
(last_dispatched_at IS NULL) DESC,
last_dispatched_at ASC,
created_at ASC,
id ASC
```

联系人排序：`next_call_at,import_seq,id`。暂停、完成或无到期联系人的任务跳过，空出容量立即可供其他任务使用。

### 6.3 最终拨号栅栏

Adapter 按 `call_id` 建立进程内互斥：

```text
reserved + send_state=NONE
→ 有界健康预检
→ MySQL 事务：
   锁定任务、联系人、Call
   校验 state_version、active_call_id、status=reserved、dial_requested_at IS NULL
   再查 running、截止时间、时间窗、禁呼和容量
   写 adapter_boot_id、dial_requested_at、dial_phase=dial_requested、state_version+1
   同事务更新全局/任务 next_dial_at 和任务 last_dispatched_at
→ 提交确认
→ 当前上下文将 send_state 从 NONE 改为 STARTED
→ 发送一次 bgapi originate
```

`send_state=STARTED` 后不再发送。进程重启、恢复任务和人工核查没有 originate 发送权。

提交结果处理：

| 结果 | 处理 |
| --- | --- |
| 确定成功 | 当前上下文发送一次 |
| 确定回滚 | CAS 释放预留并重新排队，不计次 |
| 结果未知 | 用新连接按 `call_id` 重读，不直接取消、释放、重排或拨号 |

结果未知后的重读：

- `dial_requested_at IS NULL` 且版本未变：按未授权释放。
- 已授权、`adapter_boot_id` 匹配、本地仍为 `NONE`：当前顺序上下文发送一次。
- 已授权但本地已 `STARTED`、boot 不匹配或发送事实不明：进入 `checking`。
- 无法读取确定事实：保持容量并继续核查。

### 6.4 未知拨号与崩溃恢复

```text
checking
  ├── 活动通道存在 → 恢复真实电话状态
  ├── 终态事件或 CDR 存在 → 按事实结束
  ├── 有证据确认未拨号 → failed(CONFIRMED_NOT_DIALED)
  └── 超过期限 → unknown
```

- originate 响应失败、超时或写入后崩溃均不自动重发。
- 无活动通道不能单独证明未拨号。
- Adapter 重启生成新 `adapter_boot_id`，恢复进程不继承 `send_state`。
- 已接听通道因 FreeSWITCH 重启消失时，按异常中断结束。

人工核查接口支持 `restore_active/succeeded/failed`：

- `restore_active` 仅用于 Call=`checking`、Contact=`calling` 且任务未终态。
- `unknown` 联系人只能修正终态，不重新激活。
- 活动通道需要结束时，先对实际 `freeswitch_uuid` 执行 `uuid_kill`，等待终态后释放容量。
- 核查请求携带期望 `state_version`、证据和说明；结果变化时写新 revision 的 `contact.result_changed`。

### 6.5 时间窗

- 使用 IANA `timezone`；开始和截止时间带明确 offset。
- 窗口为本地时间半开区间 `[start,end)`，`start=end` 无效。
- `days` 使用 ISO 8601：1 为周一，7 为周日，不允许重复。
- `end<start` 表示跨午夜，`days` 指开始日。
- 重叠窗口取并集。
- DST 边界不存在或有歧义时，该日窗口停用并产生告警。
- `excluded_dates` 为本地日期；V1 不内置节假日日历。
- 窗口外的重试顺延到下一窗口，不得超过任务截止时间。

## 7. Agent Session 与媒体

### 7.1 Claim

V1 不设置 Session Ticket。以下接口仅允许 Adapter 内部网络和专用凭据：

```text
POST /internal/v1/calls/{call_id}/session-claim
POST /internal/v1/calls/{call_id}/session-claims:recover
```

两种 Claim 都要求 `call_event` 中不存在该 `call_id` 的任何代际 `session.failed`，覆盖 `pending/processing/processed/dead` 等所有处理状态；失败事件已入队但未处理时同样拒绝。

初始 Claim：

- 锁定 Call，校验 `active_call_id`、非终态、未 ready、非 failed 和固定 Robot 版本。
- 首次设置 `claim_generation=1`、`session_state=claimed`。
- 重复请求返回当前代际、配置和变量，不增加代际，不回退 `attached`。

恢复 Claim：

- 仅允许 `session_state=claimed/attached`、未 ready、非终态、非 failed。
- `claim_generation+1`，新代际状态改为 `claimed`。
- 保持相同 `call_id/config/variables`，不创建新 Call。

统一响应：

```json
{
  "call_id": "call_001",
  "claim_generation": 2,
  "start_payload": {
    "action": "start",
    "data": {
      "callUUID": "call_001",
      "claimGeneration": 2,
      "callee": "...",
      "config": {},
      "variables": {},
      "robotVersionId": "appver_001",
      "configHash": "sha256:..."
    }
  }
}
```

`data.config` 来自 `ResolveExactVersionUseCase.execute(robot_app_version_id)` 的精确版本结果。控制台 Voice Test 的内存 Session 不复用。

### 7.2 Attach 与生命周期

Agent 使用 `(call_id,claim_generation)` 原子 attach，并记录最高及已消费代际：

- 旧代际和已消费代际均拒绝。
- 有活动或清理中的 Session slot 时拒绝新连接，关闭码使用 `1009` 或应用错误。
- 同一代际最多一次 WebSocket attach。
- Pipeline task、Provider、媒体和清理回调全部结束后才释放 slot。
- pre-ready 断连后数据库可保持 `attached`；下一次连接前必须 recover 获取新代际。

Attach 握手：

1. Agent 收到 `start` 后保留 slot，发送 `session.attached`，暂不启动 Pipeline。
2. Fast-Call 锁定 Call，校验当前代际、`active_call_id`、未 ready 和 `session_state=claimed`。
3. 持久化 `attached` 后返回 ACK；Agent 收到 ACK 后才初始化 Pipeline。
4. ACK 丢失时在原连接以原事件 ID 和 payload 重试；仅当前代际且数据库已为 `attached` 时补 ACK。

Ready 与失败：

- ready 事件仅在当前代际、`active_call_id` 匹配、状态为 `attached` 且 `session_ready_at IS NULL` 时生效。
- 生效时写 `session_ready_at` 并增加 `state_version`，`session_state` 保持 `attached`，然后 ACK。
- pre-ready 本地失败先发送 `session.failed`，事件写入 `call_event` 并收到 ACK 后才关闭连接或允许恢复；ACK 丢失时以原事件 ID 和 payload 重试。
- ready 前的有效 `session.failed` 即使来自历史代际，也在锁 Call 的同一事务中设置 `failed` 并收敛 Call/Contact/Outbox，阻止恢复。
- ready 后仅当前代际 `failed/completed` 可改变状态。
- ready 超时或结束前再次读取 `session_ready_at`。
- ready 后 WebSocket 断开或 Agent 崩溃，当前 Call 失败并挂断，不恢复对话。

生命周期事件：`session.attached/ready/failed/completed`。

事件 ID：

```text
agent:{call_id}:{claim_generation}:attached
agent:{call_id}:{claim_generation}:ready
agent:{call_id}:{claim_generation}:failed
agent:{call_id}:{claim_generation}:completed
```

事件包含 `event_id/call_id/claim_generation/config_hash/agent_version/occurred_at`。Agent 存活期间重试至 ACK；`completed` 丢失时通过 FreeSWITCH 终态核查。

### 7.3 FreeSWITCH 媒体实现

POC 固定 [AlchemillaHQ/mod-audio-stream](https://github.com/AlchemillaHQ/mod-audio-stream) commit [`a25fb1fe530ec6a612d321ff04f70be69b1a257c`](https://github.com/AlchemillaHQ/mod-audio-stream/commit/a25fb1fe530ec6a612d321ff04f70be69b1a257c)。部署需满足其 AGPL-3.0 或商业许可。

Application/Domain 只依赖：

```text
start_media(actual_channel_uuid, adapter_wss_url, PCM参数, metadata)
stop_media(actual_channel_uuid)
kill_channel(actual_channel_uuid, cause)
```

具体命令封装在 Adapter。

originate 前设置：

```text
origination_uuid
fastcall_call_id
fastcall_task_id
fastcall_contact_id
```

事件按实际 Channel UUID 和 `origination_uuid` 关联，再校验 `fastcall_call_id`。事件可早于 `bgapi` 响应；Job-UUID 仅写入命令事件。CDR 保存 `call_id/origination_uuid/freeswitch_uuid`。

接听后的顺序：

1. Adapter 收到 `CHANNEL_ANSWER`，保存 `answered_at`。
2. 执行 Claim，建立 Agent `/ws-v2`，发送 `start`。
3. 对实际通道启动媒体：
   ```text
   uuid_audio_stream <actual_uuid> start wss://adapter/internal/media/{call_id} mono 8k <metadata>
   ```
4. 模块主动连接 Adapter；设置 `STREAM_PLAYBACK=true`、`STREAM_BUFFER_SIZE=20`，metadata 包含 `call_id/claim_generation/direction`。
5. ready 前 caller PCM 最多预缓冲 500ms，不发送给 Agent。
6. ready ACK 后先向 Agent 发送 `answer`，再发送预缓冲和实时 PCM。
7. Adapter 向模块发送二进制前发送：
   ```json
   {"type":"rawAudio","data":{"sampleRate":8000}}
   ```

媒体契约：

- signed PCM16 little-endian、单声道、8000Hz。
- 20ms 逻辑帧，即 320 bytes；无 WAV 头、无 Base64。
- FreeSWITCH 负责 SIP codec 转码；模块和 Agent 内部自行处理其他采样率。
- Adapter 按字节流重组 320B 帧，不依赖 WebSocket 消息边界。
- FreeSWITCH→Adapter 使用 caller-only `mono`，避免 TTS 回灌 STT。
- caller 实时队列最多 500ms，溢出丢旧并记录指标。
- Agent 输出队列最多 500ms，按 20ms 节拍发送；溢出以 `MEDIA_BACKPRESSURE_OVERFLOW` 结束当前 Call。

挂断：

- Agent `{"action":"hangup","data":{"code":480}}` 由 Adapter 映射为实际 `freeswitch_uuid` 的 `uuid_kill`。
- 重复挂断和通道已结束按幂等处理。
- 用户挂机后，Adapter 停止媒体、关闭 Agent 连接并等待完整清理。

## 8. 结果码与重试

### 8.1 标准结果码

| 结果码 | 处理 |
| --- | --- |
| `NO_ANSWER` | 默认可重试 |
| `USER_BUSY` | 默认可重试 |
| `TEMPORARY_NETWORK_FAILURE` | 默认可重试 |
| `CALL_REJECTED` | 默认不重试 |
| `INVALID_NUMBER/UNALLOCATED_NUMBER` | 不重试 |
| `USER_HANGUP` | ready 后成功 |
| `VOICEMAIL` | 成功 |
| `ORIGINATE_RESULT_UNKNOWN` | 进入核查 |
| `CONFIRMED_NOT_DIALED` | 失败，不自动重试 |
| `CALL_CHECK_TIMEOUT` | `unknown` |
| `AGENT_READY_TIMEOUT` | 失败，不自动重试 |
| `MEDIA_CONNECTION_TIMEOUT` | 失败，不自动重试 |
| `MEDIA_BACKPRESSURE_OVERFLOW` | 失败，不自动重试 |
| `AI_PROVIDER_FAILED` | 失败，不自动重试 |

原始 FreeSWITCH/SIP cause 单独保存。结果映射联合电话阶段、接听、ready 和 SIP cause；未接听的 `NORMAL_CLEARING` 不算成功。

### 8.2 重试配置

```json
{
  "max_retries": 3,
  "retry_intervals_sec": [60, 60, 60],
  "retry_on": ["NO_ANSWER", "USER_BUSY", "TEMPORARY_NETWORK_FAILURE"]
}
```

规则：

- `max_retries` 不含首次呼叫。
- 间隔数量必须等于 `max_retries`，否则返回 `422`。
- 间隔为大于 0 的整数秒；`max_retries=0` 时使用空列表。
- `retry_count` 是已消耗额度，也是最近安排的重试序号，不是已完成重拨数。
- 仅当前 Call 结果属于 `retry_on`、Contact 可重试且 `retry_count<max_retries` 时消耗额度。
- 第 `n` 次重试使用 `retry_intervals_sec[n-1]`。
- 重试额度、Contact 状态和 `next_call_at` 在同一事务更新。
- 等待容量、健康预检和未授权系统退避不占重试额度。
- `unknown`、已接听后失败、禁呼、空号和人工修订不自动重试。
- 系统退避需要有界并告警。

计次只依据 `dial_requested_at`：提交前不计，提交后保守计一次，提交结果未知时先重读。

## 9. API

### 9.1 Open API

```text
PUT  /openapi/v1/outbound-tasks/{customer_task_id}
POST /openapi/v1/outbound-tasks/{task_id}/contacts
GET  /openapi/v1/outbound-tasks/{task_id}
POST /openapi/v1/outbound-tasks/{task_id}/start
POST /openapi/v1/outbound-tasks/{task_id}/pause
POST /openapi/v1/outbound-tasks/{task_id}/resume
POST /openapi/v1/outbound-tasks/{task_id}/cancel
GET  /openapi/v1/outbound-tasks/{task_id}/contacts
GET  /openapi/v1/outbound-tasks/{task_id}/calls
GET  /openapi/v1/task-contacts/{contact_id}
GET  /openapi/v1/calls/{call_id}
```

规则：

- `customer_task_id` 作为创建幂等键；内容一致返回原任务，不同则 `409`。
- 联系人按 `(task_id,customer_contact_id)` 幂等；同 ID 不同内容返回冲突。
- 同任务重复号码不建联系人，导入响应 `duplicate_contacts` 返回客户联系人 ID、脱敏号码和 `DUPLICATE_PHONE`。
- 导入按全部接受中的 HMAC 版本查重。
- 联系人结果中的 `call_count/last_call` 从 Call 历史派生。
- 列表使用 keyset 分页，`limit` 默认 50、最大 200。
- 联系人支持 `status/result_code` 过滤，排序 `(import_seq,id)`；`result_code` 映射到 `final_result_code`。
- Call 支持 `status/result_code/contact_id` 过滤，排序 `(created_at,id)`。
- cursor 绑定过滤条件，条件不一致返回 `400`。

### 9.2 Admin API

```text
GET  /adminapi/v1/calls?status=checking&cursor=...&limit=...
GET  /adminapi/v1/calls?status=unknown&cursor=...&limit=...
GET  /adminapi/v1/calls/{call_id}
POST /adminapi/v1/calls/{call_id}/review
```

核查请求：

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

操作者身份来自管理凭据。核查按第 6.4 节更新 Call、Contact、容量、事件和 Outbox，不隐式重拨。

## 10. Webhook 与安全

### 10.1 客户事件

- `contact.completed`：联系人首次终态，每个联系人一次。
- `contact.result_changed`：人工修订，带新 `result_revision`、旧/新结果和独立 `event_id`。
- `task.completed`：任务完成。
- `task.cancelled`：任务取消；不再发送 `task.completed`。

联系人事件包含 `task/customer_task/contact/customer_contact` 标识、`status/result_code`、派生 `call_count/last_call_id`、`result_revision` 和时间。修订事件另含 `reason/evidence_ref/changed_at`。

投递规则：

- 使用稳定 `event_id`、时间戳和 HMAC 签名。
- `2xx` 为成功，超时和非 `2xx` 独立重试。
- 重试保持相同事件 ID 和 payload。
- 不保证联系人与任务事件的跨聚合到达顺序。
- 同一联系人按 `result_revision` 应用结果，忽略旧 revision。
- 任务事件统计为完成时快照；当前统计以查询 API 为准。
- 回调不包含完整号码、敏感变量或完整对话。

### 10.2 回调地址

仅允许 HTTPS。部署配置支持 hostname/CIDR 白名单，以允许企业内网 CRM。

每次投递：

- 重新解析并校验全部 A/AAAA，包括 IPv4-mapped IPv6。
- 默认拒绝 unspecified、loopback、private、link-local、组播、本机和云元数据地址。
- 私网地址仅在 hostname/CIDR 白名单明确允许时可用；link-local 和云元数据地址始终拒绝。
- 连接已验证 IP，保留原 hostname 用于 Host、SNI 和证书校验。
- 禁止重定向和环境代理；DNS、地址或 TLS 校验失败即停止投递。

### 10.3 数据与密钥

- Open API、Admin API、Resolve、Adapter、Agent 和媒体入口使用不同凭据。
- 号码统一为 E.164，无法唯一解析时拒绝导入。
- 号码和变量使用带版本的 AES-GCM 等认证加密。
- 去重和禁呼使用 keyed HMAC，不使用普通 SHA-256。
- 日志不输出完整号码、变量、配置、Prompt、密钥、Token 或音频。
- 对外号码默认只显示 `phone_last4`。
- 数据保留和删除周期可配置，录音默认关闭。

HMAC 轮换：

1. 新 key 成为写入 active，旧 key 保留只读；写入和查询均明确版本。
2. 去重和禁呼对所有接受中的版本联合匹配；旧 key 缺失时停止导入和拨号。
3. 分批解密号码并重建 `phone_hmac/hmac_key_version`。
4. 唯一键冲突时停止切换并处理冲突，不静默删除或合并。
5. 全部重建且旧版本无命中后再移除旧 key。

## 11. 代码结构与实施顺序

### 11.1 模块

```text
src/robot_config_service/
├── api/
│   ├── openapi/
│   ├── adminapi/
│   └── internal/
├── application/
│   ├── outbound_tasks.py
│   ├── contacts.py
│   ├── dispatch.py
│   ├── call_events.py
│   ├── retries.py
│   ├── call_checks.py
│   └── webhooks.py
├── domain/
│   ├── outbound_tasks.py
│   ├── calls.py
│   ├── retry_policy.py
│   └── telephony_results.py
└── infrastructure/
    ├── scheduler.py
    ├── freeswitch/
    └── webhooks/
```

Domain 不导入 FastAPI、SQLAlchemy 或 ESL；Application 不拼接 FreeSWITCH 命令；精确版本 Resolve 通过 Application 用例直接调用。

### 11.2 顺序

| 顺序 | 内容 | 验收重点 |
| --- | --- | --- |
| 1 | 迁移、领域状态和事务仓储 | 状态转换、唯一键和条件更新 |
| 2 | Agent Claim 和 attach | 丢响应、旧代际、pre-ready 恢复、failed/recover 并发 |
| 3 | Adapter dispatch 和 ESL 事件 | 重复 dispatch、commit unknown、重启不补发 |
| 4 | 媒体桥 POC | 双向音频、组帧、背压、挂断和 CDR 关联 |
| 5 | Scheduler、容量和重试 | 并发、速率、时间窗、重试和 `reserved` 恢复 |
| 6 | Open/Admin API 和 Outbox | 原子终态、结果修订、分页、禁呼和回调 |

## 12. 验收清单

### 状态与调度

- 非法状态转换被条件更新拒绝。
- 全局和任务并发、固定拨号间隔均不超上限。
- 暂停和取消不产生未授权新呼叫。
- 重启后任务顺序、到期联系人和重试继续执行。
- `reserved` 仅在确认未授权后回收；commit unknown 不释放、不重排。
- 同一 Call 最多发送一次 originate，恢复进程不补发。
- 跨午夜、重叠窗口、排除日期和 DST 行为符合第 6.5 节。

### Session 与媒体

- 初始 Claim 重复请求不增加代际；recover 返回更高代际。
- 旧代际、已消费代际和活动/清理中 slot 的重复连接均被拒绝。
- attached ACK 未持久化时不启动 Pipeline。
- 旧代际 ready 不改变当前 Call；failed/recover 并发不能重启失败 Session；`session.failed` 已入队但未处理时，两种 Claim 均不得成功或增加代际。
- ready 前挂断为 `AGENT_READY_TIMEOUT`，ready 后断线失败收敛。
- 固定模块的真实双向 S16LE、8kHz、320B/20ms、有限缓冲和双方挂断通过 POC。
- 早到事件、通道 UUID 和 CDR 不会错绑 Call。

### 数据、重试与回调

- 每次电话重试生成新 Call，历史不覆盖。
- 未授权系统故障不计 `call_count`，不占 `retry_count`。
- 重试间隔配置、额度和任务截止行为一致。
- 重复号码不生成冗余联系人；HMAC 双读和重建不会绕过禁呼。
- 派生 `call_count/last_call_id` 与 Call 历史一致，列表批量查询。
- 联系人/任务终态不会缺少同事务 Outbox。
- Event/Webhook Worker 崩溃后的过期租约可回收，处理保持幂等。
- 人工修订产生新 revision 的 `contact.result_changed`，不恢复已终态任务或隐式重拨。
- Webhook 重投使用相同事件 ID；失败不影响电话执行。
- 白名单内网 CRM 可回调，重定向、DNS rebinding、环境代理和云元数据地址被拒绝。

## 13. V1 实现与验证基线

本节记录实际实现补充，不改变第 1～12 节的架构和状态约束。

### 13.1 实现说明

- 电话事件允许没有振铃事件就直接接听；同来源或跨来源的重复终态均按幂等处理。FreeSWITCH 先终态、Agent 后送 `completed` 时，保存业务分析并 ACK，不重复转换 Call。
- pre-ready 断连由 Adapter 使用新代际有界恢复，避免首次连接与周期恢复并发。ready 后断线仍直接失败。
- ready 可能早于媒体 WebSocket。Adapter 保存 ready 事实，在媒体桥建立后释放预缓冲；ready 超时独立收敛为 `AGENT_READY_TIMEOUT`。
- `waiting_retry` 不是终态，不发送 `contact.completed`。取消任务的活动 Call 不再进入重试。
- 暂停期间所有活动 Call 结束时任务仍为 paused，resume 时补做任务完成和 Outbox。
- 最终拨号栅栏发现禁呼或 Robot 停用时，联系人直接进入对应失败终态，不重新排队。
- `unknown` 被迟到电话事件修订时，与人工修订一样产生新 revision 的 `contact.result_changed`。
- 人工核查和周期核查隔离单 Call 异常，不终止整个 Adapter；无 FreeSWITCH gateway 时禁止释放活动 Call。
- 业务分析由 `outbound_call_analysis` 保存，迁移基线为 `0004`。事件只保存必要业务结果，完整分析进入该表，不改变技术结果或重试。旧 Collection Agents 的 `call_analysis` 结构不同，迁移不会重命名或删除旧表。
- Scheduler 通过 Adapter Internal dispatch API 提交请求，不直接持有 originate 权限。`bgapi` Job-UUID 仅保存在命令事件。
- 只处理属于 Fast-Call originate 主通道的 ESL 事件；loopback 另一腿即使继承 `fastcall_call_id`，也不能覆盖主通道 UUID 或触发二次 Agent Session。
- ready 看门狗在 ready 后不得结束媒体任务。Agent 主动挂断后继续接收 `session.completed`，等待生命周期 ACK 后再关闭 Agent WebSocket；此期间 FreeSWITCH 的关闭或写错误不得抢先终止 Agent 生命周期。
- Fast-Call Session 使用的 Pipecat transport 在 Pipeline `EndFrame` 时不提前关闭底层 WebSocket，由 Agent 路由在生命周期和清理完成后关闭。等待 ACK 时跳过二进制音频，仅解析 ACK 文本；完成事件确实丢失时仍按 FreeSWITCH 终态兜底。

### 13.2 已执行验证

- Robot Config 全部单元测试：103 项通过。
- 模拟 FreeSWITCH、Agent、Webhook 的完整端到端测试通过。
- 真实 Docker FreeSWITCH loopback + 生产 Deepgram/DeepSeek/Cartesia：真实用户语音经 STT 识别、LLM 生成两轮回复、TTS 播放，自然挂断后 Call/Contact 成功、Task 完成，两类 Webhook 均以 HMAC + HTTPS 送达并返回 204；未修改 FreeSWITCH 配置。Collection Agent 原有通话记录和业务分析独立落库，Fast-Call 不以其完成事件作为业务成功前置条件。
- 真实 MySQL：6 项集成测试通过，包含迁移升级/降级、启动检查、并发事件去重、加密名单和任务控制。
- 真实 Docker FreeSWITCH 媒体 POC：32/32 通过，覆盖双向音频、暂停/恢复、break、停止、错误处理和重复启动阻断；完整脚本重复执行通过。
- POC 脚本为 `scripts/test_freeswitch_media_poc.sh`，固定第 7.3 节 commit，并按 ESL 就绪状态等待，不使用固定启动延迟。
- 全项目测试存在一项与 Fast-Call 无关的既有 Realtime `_observer` 错误，不属于本 V1 验收范围。

### 13.3 运行文档

进程入口、环境变量、迁移和启动步骤见 `QUICK_START.md` 的 Fast-Call 部分；组件与事务边界见 `ARCHITECTURE.md` 第 11 节。

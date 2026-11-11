# Robot 应用管理服务 MVP 实施方案

## 1. 最终范围

新建独立的 **Robot Config Service**，将当前 WebSocket 请求中的整包 Robot 配置收敛为可编辑、可校验、可发布、可回滚、可按 `robot_code` 获取的在线配置。

第一阶段只支持：

```text
Standard Bot + Deepgram ASR + DeepSeek LLM + Cartesia TTS
```

核心交付：

1. Robot Config Service：应用、草稿、校验、版本、发布、回滚、Resolve API。
2. Collection Agent 接入：远程配置客户端、旧配置编译器、变量校验和配置追溯。
3. client 管理页面：应用列表、应用编辑、版本发布。

不再建设：

- `provider_connection` 表、供应商连接页面及对应 API。
- 独立供应商测试和 TTS 试听。
- `secret_ref`、Secret Manager、多供应商账号和在线 API Key 录入。
- Realtime、Copilot 改造。
- 共享 Profile 资源中心、审批、灰度、A/B 和自部署模型平台。
- `recording_enabled`；录音由实际通信服务负责。

### 实施默认值

以下默认值用于直接开始开发；如实际业务不符，需要在编码前调整：

- `robot_code` 全局唯一，MVP 不引入运行时 `tenant_id`。
- 所有 Robot 共用当前部署环境中的供应商账号。
- 新服务在当前仓库中实现为独立 FastAPI 应用，管理页面由 client 侧接入。
- 环境只使用 `dev`、`test`、`prod`。
- 每次新通话都调用 Resolve API；MVP 不做配置缓存，保证发布和回滚立即生效。

## 2. 架构与边界

```text
client 管理页面
      │
      ▼
Robot Config Service
      ├── 应用与草稿
      ├── 校验、版本、发布、回滚
      ├── Resolve API
      └── 审计日志
             │
             └── DATABASE_URL：本地 SQLite，dev/test/prod 使用 MySQL

外呼系统 / 测试 client
      │ data.robotCode + data.variables
      ▼
Collection Agent
      ├── Config Resolve Client
      ├── Legacy Config Compiler
      ├── 通话变量校验与渲染
      ├── 现有 CallConfigManager
      └── 环境变量中的供应商 API Key
             │
             ▼
      Deepgram / DeepSeek / Cartesia
```

职责：

- Robot Config Service 只管理配置，不直接调用供应商；不接收、保存或展示 `callee` 等被叫信息。
- 外呼系统仍负责拨号、被叫号码、频控、黑名单、线路和重试。
- Collection Agent 负责消费配置并执行 Standard Bot。
- API Key 只存在 Collection Agent 部署环境：

| 供应商 | 环境变量 |
| --- | --- |
| Deepgram | `DEEPGRAM_API_KEY` |
| DeepSeek | `DEEPSEEK_API_KEY` |
| Cartesia | `CARTESIA_API_KEY` |

密钥轮换只修改部署环境并滚动重启，不修改应用版本。

## 3. 配置契约

契约版本为 `robot-app/v1`。一个应用版本保存一份完整配置 JSON，不拆共享资源版本。

### 3.1 必须保留的现有配置

- `config.code`：Robot 稳定标识。
- `config.variables`：每次通话传入的平铺变量值。
- `modelConfig.config`：完整保留 `temperature`、`contextRounds`、`maxTokens`。
- `flowConfig`：完整保留欢迎、结束、无响应、语音信箱和延迟挂断配置及其子字段。

发布版本只保存变量定义，不保存真实姓名、金额等通话变量值。实际值由每次请求通过 `data.variables` 传入。

其他已有业务字段（例如 `intent`）允许原样保存在应用 JSON 并由编译器透传；MVP 不为它们增加专门编辑页面。

### 3.2 应用配置示例

```json
{
  "schema_version": "robot-app/v1",
  "app": {
    "code": "example-standard-en",
    "name": "Standard English",
    "runtime_engine": "standard",
    "locale": "en-US",
    "enabled": true
  },
  "prompt": {
    "system_template": "..."
  },
  "variables": [
    {
      "key": "user_name",
      "type": "string",
      "required": true,
      "sensitive": true,
      "max_length": 100
    },
    {
      "key": "app_type",
      "type": "string",
      "required": true,
      "max_length": 100
    },
    {
      "key": "remaining_amount",
      "type": "decimal",
      "required": true,
      "format": "money"
    },
    {
      "key": "contact_time",
      "type": "datetime",
      "required": false,
      "default": ""
    }
  ],
  "asr": {
    "provider": "deepgram",
    "model": "nova-2",
    "locale": "en-US",
    "sample_rate": 8000,
    "interim_results": true,
    "endpointing_ms": 100
  },
  "llm": {
    "provider": "deepseek",
    "model": "deepseek-flash",
    "temperature": 0.7,
    "top_p": 0.9,
    "context_rounds": 10,
    "max_tokens": 500,
    "timeout_ms": 6000
  },
  "tts": {
    "provider": "cartesia",
    "model": "sonic-2",
    "voice_id": "voice-xxx",
    "locale": "en-US",
    "speed": 0.9,
    "volume": 80,
    "sample_rate": 8000,
    "encoding": "pcm_s16le"
  },
  "flow": {
    "welcomeNode": {
      "content": "Am I speaking with {{user_name}}?",
      "timeoutSec": 10,
      "timeoutReply": "Am I speaking with {{user_name}}?"
    },
    "endNode": {
      "content": "Okay, thank you for your time. Have a good day!",
      "hangUpConfig": {
        "delayHangUp": {
          "enabled": true,
          "delaySecond": 1
        },
        "otherHangUp": {
          "minInteractions": 15,
          "minCallDurationSec": 300,
          "content": "Okay, thank you for your time. Have a good day!"
        }
      }
    },
    "noResponse": {
      "timeoutSec": 6,
      "noResponseReply": "Hello, are you still there?",
      "maxNoResponseCount": 3,
      "noResponseHangUp": {
        "enabled": true,
        "hangupMessage": "Thank you for taking the call. Goodbye!"
      }
    },
    "voicemailNode": {
      "enabled": true,
      "action": "playThenHangup",
      "content": "Hello, I'm calling from the app. Goodbye"
    }
  },
  "call_policy": {
    "allow_interruptions": true,
    "max_call_duration_sec": 180
  }
}
```

### 3.3 旧配置编译规则

| 新配置 | 现有运行时配置 |
| --- | --- |
| `app.code/name` | `config.code/name` |
| 本次请求 `data.variables` | `config.variables` |
| `prompt.system_template` | `promptConfig.content` |
| `asr` | `config.asr`，保留 `provider/model/locale` |
| `llm.provider/model` | `modelConfig.provider/name` |
| `llm.temperature/context_rounds/max_tokens` | `modelConfig.config.temperature/contextRounds/maxTokens` |
| `llm.top_p` | `modelConfig.config.topP` |
| `llm.timeout_ms` | `modelConfig.config.llmTimeoutSecs`，毫秒转秒 |
| `tts.voice_id` | `tts.name` |
| `tts.speed/volume` | `ttsConfig.rate/volume` |
| 其余 `tts` 字段 | `config.tts` |
| `flow` | `flowConfig`，节点和子字段无损保留 |
| 应用版本 ID | `config.robotVersionId` |

新配置链路中的 `allow_interruptions` 不再读取 `ALLOW_INTERRUPTIONS`。旧请求链路在迁移期保持原行为。

### 3.4 配置保存与行为生效的区别

不能只完成字段保存和编译。Standard 新链路需要逐项检查实际消费点：

| 配置 | 当前基础 | 新链路工作 |
| --- | --- | --- |
| `config.code` | 已用于 Robot 标识和记录 | 保持 |
| `config.variables` | 已用于模板渲染 | 增加严格校验，禁止残留模板 |
| `temperature/maxTokens` | 已读取 | 保持并验证编译等价 |
| `contextRounds` | 未看到 Standard 实际消费 | 接入上下文轮次裁剪 |
| `welcomeNode.content` | 已读取 | 保持 |
| `welcomeNode.timeoutSec/timeoutReply` | 未看到 Standard 实际消费 | 接入首句超时回复 |
| `endNode.content/hangUpConfig` | 未看到 Standard 实际消费 | 接入结束语、延迟及条件挂断 |
| `noResponse` | 已读取主要字段 | 验证完整行为 |
| `voicemailNode` | 已读取 | 验证完整行为 |
| `allow_interruptions` | 当前读取环境变量 | 新链路改为远程配置 |
| `max_call_duration_sec` | 当前使用函数默认值 | 新链路改为远程配置 |

如果某项行为本轮不实现，必须在页面和验收中明确标为未支持，不能展示为已生效配置。

## 4. 数据模型

MVP 只有四张表。

### 4.1 `robot_app`

- `id`
- `app_code`，全局唯一
- `name`、`runtime_engine`、`locale`、`enabled`
- `draft_json`、`draft_revision`
- `created_at`、`updated_at`

草稿每次保存递增 `draft_revision`，更新接口必须携带旧 revision。

### 4.2 `robot_app_version`

- `id`、`app_id`、`version_no`
- `source_draft_revision`
- `content_json`、`config_hash`、`schema_version`
- `validation_report_json`、`validated_at`
- `created_by`、`created_at`

约束：

- `(app_id, version_no)` 唯一。
- `(app_id, source_draft_revision)` 唯一，用于重复发布幂等。
- `config_hash` 建普通索引。

版本创建后不可更新。

### 4.3 `robot_release`

- `id`、`app_id`、`environment`
- `robot_app_version_id`
- `updated_by`、`updated_at`

`(app_id, environment)` 唯一。`environment` 只允许 `dev/test/prod`。

发布和回滚只修改对应环境的版本指针；进行中的通话继续使用启动时获取的配置。

### 4.4 `config_audit_log`

- `id`、`actor`、`action`
- `object_type`、`object_id`
- `app_version_id`、`config_hash`
- `detail_json`、`created_at`

只追加，不记录 API Key、通话变量值或其他通话数据。

## 5. API

### 5.1 管理 API

```text
GET    /api/v1/robot-apps
POST   /api/v1/robot-apps
GET    /api/v1/robot-apps/{app_id}
PUT    /api/v1/robot-apps/{app_id}/draft
POST   /api/v1/robot-apps/{app_id}/validate
POST   /api/v1/robot-apps/{app_id}/publish
GET    /api/v1/robot-apps/{app_id}/versions
GET    /api/v1/robot-apps/{app_id}/diff?from=1&to=2
POST   /api/v1/robot-apps/{app_id}/rollback
```

`PUT draft`：

- 请求携带完整草稿和 `draft_revision`。
- revision 不匹配返回 `409 Conflict`。

`POST validate`：

- 请求携带 `draft_revision`。
- 返回配置哈希及结构化错误、警告列表。

`POST publish`：

```json
{
  "draft_revision": 12,
  "config_hash": "sha256:...",
  "environment": "test"
}
```

在一个事务中：

1. 再次确认当前草稿 revision 和哈希未变化。
2. 再执行服务端校验。
3. 按 `(app_id, source_draft_revision)` 获取或创建不可变版本。
4. 将指定环境的 release 指向该版本。

重复发布同一草稿返回同一版本，不产生重复版本。`test` 验证通过后，可以使用相同草稿发布到 `prod`，复用已创建的版本。

`POST rollback`：

```json
{
  "environment": "prod",
  "robot_app_version_id": "appver_16"
}
```

目标版本必须属于该应用。回滚到当前版本时为无操作。

### 5.2 Resolve API

```http
POST /internal/v1/runtime/resolve
```

```json
{
  "robot_code": "example-standard-en",
  "environment": "prod"
}
```

响应：

```json
{
  "robot_app_version_id": "appver_17",
  "version_no": 17,
  "config_hash": "sha256:...",
  "runtime_engine": "standard",
  "app_config": {
    "schema_version": "robot-app/v1",
    "app": {},
    "prompt": {},
    "variables": [],
    "asr": {},
    "llm": {},
    "tts": {},
    "flow": {},
    "call_policy": {}
  }
}
```

不存在的 Robot、未发布环境、已停用应用均返回明确错误。响应不包含 API Key。

## 6. Collection Agent 接入

### 6.1 新请求与旧请求必须显式区分

远程配置请求：

```json
{
  "action": "start",
  "data": {
    "callUUID": "call-123",
    "robotCode": "example-standard-en",
    "variables": {
      "user_name": "Wayne",
      "app_type": "PlaPla",
      "remaining_amount": "5537",
      "contact_time": "2026-07-09 04:43:20"
    }
  }
}
```

判断规则：

- `data.robotCode` 存在：远程配置模式。
- 只有 `data.config`：旧配置模式。
- 两者同时存在或都不存在：拒绝请求并返回明确协议错误。

不能因为旧请求的 `config.code` 存在就自动切换成远程模式。

环境由 Collection Agent 服务端的 `APP_ENV`/`MODE` 确定，客户端不能指定环境。服务地址和内部认证配置使用服务端环境变量，例如 `ROBOT_CONFIG_SERVICE_URL`、`ROBOT_CONFIG_SERVICE_TOKEN`。

### 6.2 调用顺序

```text
接收 start
  → 识别远程/旧配置模式
  → 远程模式调用 Resolve API
  → 校验 runtime_engine=standard
  → 校验并注入 data.variables
  → 编译成现有 config
  → 创建 CallConfigManager
  → 启动 Standard Bot
```

远程配置不能与请求中的其他局部配置合并。Resolve 失败时直接返回错误，不使用代码里的默认供应商、模型或音色。

### 6.3 变量规则

- 发布配置定义变量；通话请求提供平铺变量值。
- 校验类型、必填、最大长度和敏感字段。
- 必填变量缺失或非法：不启动 Bot，记录 `ROBOT_VARIABLE_INVALID`，向客户端返回错误并结束当前测试/通话。
- 可选变量使用 `default`；未配置默认值时使用空字符串。
- 渲染后仍出现 `{{...}}` 按配置错误处理。
- MVP 不承诺“阻止拨号”：Collection Agent 收到请求时呼叫可能已经建立。真正的拨号前阻断由外呼系统后续接入前置校验实现。

### 6.4 通话追溯

每通电话记录：

- `robot_code`
- `robot_version_id`
- `config_hash`
- 实际 ASR、LLM、TTS 供应商和模型
- 配置解析或变量错误码

## 7. 校验与完整应用测试

发布校验只做：

- JSON Schema、必填字段和字段类型。
- Prompt/flow 中引用变量是否已定义。
- 变量定义是否重复，必填变量规则是否合法。
- Standard、Deepgram、DeepSeek、Cartesia 组合是否受支持。
- 模型、`voice_id`、语言和音频参数是否完整。
- 新链路需要执行的配置是否存在。

不提供独立供应商连接测试或 TTS 试听。

完整应用测试流程：

1. 将应用发布到 `test`。
2. client 使用 `data.robotCode + data.variables` 连接现有 WebSocket。
3. 验证 ASR、DeepSeek、Cartesia、Prompt、变量和完整 `flowConfig` 行为。
4. 测试通过后，将同一应用版本发布到 `prod`。

## 8. 管理页面与权限

client 只增加三个页面：

1. **应用列表**：`robot_code`、名称、语言、各环境版本、启停状态。
2. **应用编辑**：基本信息、Prompt、变量、ASR、LLM、TTS、音色、`flowConfig` 和通话策略。
3. **版本发布**：校验结果、JSON Diff、发布到指定环境、版本历史和回滚。

权限只区分查看、编辑、发布，复用 client 现有认证。Resolve API 使用独立的服务间认证，不接受公网匿名访问。

安全要求：

- API Key 不进入配置服务、数据库、版本 JSON、接口响应或日志。
- 配置和通话日志不打印完整敏感变量。
- 管理操作记录审计。
- 远程配置请求只能解析配置，不能覆盖配置。

## 9. 开发顺序

### 第一步：配置服务

- 四张表和 migration。
- 草稿、校验、发布、版本、Diff、回滚。
- Resolve API 和内部认证。
- 使用 `examples/voice_chat_v2.html` 的配置作为首个导入样例。

### 第二步：运行时最小接入

- 新旧请求显式分流。
- Resolve Client 和 Legacy Config Compiler。
- 变量校验、渲染和通话版本追溯。
- 远程 `allow_interruptions`、`max_call_duration_sec`。

### 第三步：必要行为补齐

- 对照第 3.4 节逐项检查 `contextRounds`、欢迎超时、结束和挂断配置。
- 未实现的行为不得在管理页面标为已支持。

### 第四步：client 与完整测试

- 完成三个管理页面。
- 在 `test` 环境完成完整 WebSocket 测试和真实测试电话。
- 先迁移一个 Robot，再逐个迁移其他应用。

## 10. 测试与验收

至少覆盖：

- 草稿乐观锁、重复发布、发布原子性、跨环境复用版本和回滚。
- 新旧请求识别；两者同时存在时拒绝。
- 配置编译与 client 样例等价，必要字段不丢失。
- 变量缺失、类型错误、默认值和残留模板检查。
- Resolve 失败、Robot 不存在、环境未发布和应用停用。
- 远程打断和通话时长配置实际生效。
- 第 3.4 节标记为已支持的流程行为都有测试。
- API Key 和敏感变量不进入接口响应与普通日志。
- Deepgram + DeepSeek + Cartesia 完整 WebSocket 通话。

最终验收：

1. client 创建一个 Standard Robot 并发布到 `test`。
2. 测试请求只传 `robotCode` 和 `variables`，即可完成完整 WebSocket 通话。
3. 必填变量缺失时不启动 Bot，并返回明确错误。
4. 修改 Prompt 后发布新版本，只影响新通话。
5. 同一版本可以从 `test` 发布到 `prod`，也可以回滚。
6. 历史通话能追溯到准确的应用版本和配置哈希。
7. 标记为已支持的配置均有真实运行行为，不存在只在页面保存的“假配置”。

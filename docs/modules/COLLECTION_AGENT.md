# Collection Agent 模块设计

## 1. 职责与独立性

`collection_agents` 是独立 AI 语音服务，基于 Pipecat 提供：

- 标准轮转 Bot、实时 Bot 和 Copilot Bot。
- STT、LLM、TTS Provider 装配与指标。
- 中断、无响应、挂断、语音信箱和多语言处理。
- 通话记录、完整 transcript、业务分析和独立落库。
- Fast-Call 所需 Agent Session slot 与生命周期协议。

Agent 不操作外呼 Task/Contact/Call，不连接 ESL，不决定重试，也不依赖 Fast-Call Webhook 或内部 Python 包。

## 2. 当前内部结构

```text
src/collection_agents/
├── bots/
│   ├── standard/                 # 标准轮转编排、生命周期、服务
│   ├── realtime/                 # 实时资源、Provider、指标和观察器
│   ├── copilot/                  # 事件协议与工具调用
│   ├── helpers.py、observers.py、telephony.py
├── processors/
│   ├── common/                   # 无响应、语音信箱、挂断等通用处理器
│   ├── standard/、realtime/、copilot/
├── handlers/                     # 各模式业务动作，不组装 Bot
├── services/                     # Provider、配置、语音信箱和音频资源
├── transport/                    # Telephony/Copilot WebSocket 协议
├── notes/                        # 通话分析与工作流
├── metrics/                      # 通话与 Provider 指标
├── storage/                      # Agent 自有 DB 与通话数据
├── server/                       # FastAPI app、routes、lifespan、warmup
├── infra/                        # 日志、环境、路径、Sentry
├── session_lifecycle.py          # Fast-Call 生命周期客户端协议
├── session_registry.py           # 单 Call slot 与代际占用
└── events.py                     # 内部领域事件
```

根目录资源保持原位：`assets/` 存音频，`templates/streams.xml` 提供 TwiML，`data/` 存本地运行数据。`infra/paths.py` 统一推导这些路径。

## 3. 服务接口

| 接口 | 用途 |
|---|---|
| `WS /ws-v2` | 标准/实时电话 Bot；Fast-Call 也使用该入口 |
| `WS /ws-ai-copilot` | Copilot 事件驱动交互 |
| `GET /state/up` | 健康检查 |
| `GET /`、`POST /` | 服务信息与 TwiML 响应 |

Fast-Call 使用的 start payload 包含 `callUUID`、`claimGeneration`、`callee`、固定 `config`、`variables`、`robotVersionId` 和 `configHash`。只有带 `claimGeneration` 的请求启用 Fast-Call Session 协议；普通 `/ws-v2` 请求保持原流程。

## 4. Fast-Call Session 协议

### 4.1 Slot 与代际

- `(call_id, claim_generation)` 原子 attach。
- 旧代际、已消费代际、活动或清理中的重复 slot 均被拒绝。
- 同一代际最多一次 WebSocket attach。
- Pipeline、Provider、媒体和清理回调全部结束后才释放 slot。

**当前已知限制**：`server/routes.py` 在 slot 的 `try/finally` 释放范围之前等待 `session.attached` ACK；如果此时断线，slot 可能残留并导致后续 recover 被拒绝。这是当前实现缺陷，不应把该断线场景误写成已经可靠恢复。后续修复需把 attach/ACK 纳入明确清理范围，并增加“attached 后断线、无 ACK、随后 recover”的回归测试。

### 4.2 生命周期

1. Agent 收到 start 后保留 slot，发送 `session.attached`，暂不初始化 Pipeline。
2. Fast-Call 持久化 attached 并返回 ACK。
3. Agent 收到 ACK 后才初始化 Provider 和 Pipeline。
4. ready 后发送 `session.ready` 并等待 ACK。
5. pre-ready 本地失败先发送 `session.failed`；ACK 后才关闭或允许 Adapter recover。
6. ready 后断线由 Adapter 判定失败并挂断，不做对话恢复。

事件使用稳定 ID：

```text
agent:{call_id}:{claim_generation}:attached
agent:{call_id}:{claim_generation}:ready
agent:{call_id}:{claim_generation}:failed
agent:{call_id}:{claim_generation}:completed
```

Agent 存活期间按协议重试至 ACK。Fast-Call 的技术终态由 FreeSWITCH 兜底，不把 `session.completed` ACK 当作成功前置条件。

## 5. Bot 与 Provider 内部协作

标准链路：

```text
WebSocket 音频 → Telephony Serializer → VAD/轮次与中断
→ STT → LLM → TTS → 音频/文本事件
→ 挂断或无响应策略 → 指标、记录、分析、落库
```

- Provider 创建集中在 `services/`，不进入 Fast-Call 任务模型。
- 通用处理器只暴露检测事件，不包含具体响应动作。
- Handler 不反向依赖 Bot 装配。
- 语音信箱检测与留言播放分离，业务结果不改变 Fast-Call 的电话技术结果。
- Agent 的原有落库独立执行，不依赖 Fast-Call WebSocket 存活。

## 6. 数据边界

Agent 自有主要表：

- `call_record`：技术指标、模型、时长和成本。
- `call_transcript`：完整对话消息。
- `call_analysis`：Agent 原有业务分析结构。

三张表通过 `call_uuid` 关联。Fast-Call 使用独立的 `outbound_call_analysis.business_result`，不得复用或改写 Agent 旧 `call_analysis` 的结构。

完整对话、号码、变量和录音不得因为 Fast-Call 集成而进入客户 Webhook。

## 7. 启动、资源与配置

- 本地入口为根 `main.py`，默认端口 8765；部署可按环境使用其他端口。
- 常用模块入口为 `collection_agents.server.app:app`。
- Provider 密钥、Fast-Call Agent token 和 DB 配置沿用现有环境变量。
- 静态资源和运行数据不得通过包迁移改变位置；镜像和文档不得包含 `.env` 或本地运行数据。
- 默认数据库为项目根目录下的 `data/collection_agents.db`，路径由 `PROJECT_ROOT` 决定，不随启动 CWD 变化。生产须持久化该目录或配置外部 DB；当前 `/mnt/data` 挂载主要用于模型资源，不能假定它自动保存 SQLite 数据。

## 8. 测试与修改约束

- Agent 单元测试主要位于 `tests/unit/test_*.py`，保持现有根级布局。
- Provider 集成测试和 load 测试分别位于 `tests/integration/`、`tests/load/`。
- 当前已有一项既有 Realtime `_observer` 错误：`test_realtime_pre_session_audio`。除非任务专门修复该问题，不得把它混入 Fast-Call 改造。
- 修改 `session_lifecycle.py` 或 `session_registry.py` 时必须覆盖 ACK、代际、重复 slot、pre-ready recover 和清理顺序。
- 修改 Bot 业务时不得破坏普通 `/ws-v2` 流程，也不得要求 Fast-Call 为业务分析复制分类逻辑。

真实 loopback 已验证 Agent 能完成真实 STT、LLM、TTS、结束语和独立通话落库；详细结果见系统总览。

## 9. 配置模式与运行行为

### 9.1 新旧配置分流

- `data.config.schema_version == "robot-app/v1"` 进入新契约编译和严格变量校验。
- 其他完整配置进入旧链路，保留默认 Provider、模型、语言和音色的兼容行为。
- `robotCode` 只用于标识和追溯，不参与模式选择；不得因为旧请求存在 `config.code` 就切换为远程配置。
- Fast-Call 直接下发固定配置、变量和版本信息，Agent 不持有 Resolve Token，不调用 Robot Config Service。

新契约的编译顺序：

```text
校验版本元数据与 configHash
→ 校验 data.variables
→ 应用可选变量默认值
→ 渲染 Prompt 和完整 Flow
→ 检查残留 {{...}}
→ 无损编译为现有运行配置
→ 创建 CallConfigManager 和 Pipeline
```

变量校验失败、配置哈希不匹配、残留模板或本地部署资源不匹配时，不得创建 Pipeline。Managed 模式禁止回退到默认 Provider、模型或音色；旧请求才使用原兼容默认值。

### 9.2 三种 Bot 模式

| 模式 | 交互与适用场景 | 关键内部能力 |
|---|---|---|
| Standard | 用户说→模型思考→机器人回应的轮转式对话 | 完整上下文、工具调用、打断、无响应、留言和指标 |
| Realtime | 流式边说边听，适合低延迟自然交互 | RNNoise、Realtime Provider、轮次监控、优雅挂断 |
| Copilot | 事件驱动的 AI 助手 | `action.create`、文本/音频/心跳/done、动态配置和工具调用 |

配置消费必须与声明能力一致：上下文轮次、欢迎超时、结束与挂断、无响应、语音信箱、打断和最大时长不能只保存到页面而没有实际行为。未实现的配置行为应在页面和验收中明确标为未支持。

### 9.3 记录与可追溯

- `config.code`、版本 ID 和 config hash 用于通话追溯。
- 记录实际 ASR、LLM、TTS Provider/模型及配置或变量错误码，避免记录完整敏感变量。
- 默认无响应流程为检测静默→生成并播放对应语言留言→按配置挂断；最大次数、时长和结束语以运行配置为准。
- `allow_interruptions` 和 `max_call_duration_sec` 由远程配置驱动，不再读取进程环境中的旧同名默认值。

## 10. 基础设施与验收约束

- FreeSWITCH 与 Adapter 当前可共享容器网络命名空间，避免为本地验收修改 ESL ACL。不得在测试中修改 FreeSWITCH 配置、ACL 或 dialplan。
- `mod-audio-stream` 固定 commit `a25fb1fe530ec6a612d321ff04f70be69b1a257c`，部署需满足 AGPL-3.0 或商业许可。
- 本地 `/tmp` 下的运行脚本、runtime env、测试 CA 和 Webhook 接收端只用于验收，不属于生产组件；敏感配置不得输出或提交。
- `FASTCALL_DISABLE_SSL_CERT_FILE=1` 仅用于本地 Agent 验收，防止测试 CA 覆盖生产 Provider 证书；Webhook Worker 仍按既有安全链路使用测试 CA。
- 语音验收应使用明确的 8kHz 单声道 PCM 输入，不能只验证音频文件存在；还需确认 STT 转写、LLM 回复、TTS 播放、通话指标和 transcript 落库。

## 11. 新契约到现有运行配置的映射

| `robot-app/v1` 配置 | 现有运行配置 |
|---|---|
| `app.code/name` | `config.code/name` |
| 本次请求 `data.variables` | `config.variables` |
| `prompt.system_template` | `promptConfig.content` |
| `llm.provider/model` | `modelConfig.provider/name` |
| `llm.temperature/context_rounds/max_tokens` | `modelConfig.config.temperature/contextRounds/maxTokens` |
| `llm.top_p` | `modelConfig.config.topP` |
| `llm.timeout_ms` | `modelConfig.config.llmTimeoutSecs`（毫秒转秒） |
| `tts.voice_id` | `tts.name` |
| `tts.speed/volume` | `ttsConfig.rate/volume` |
| 其余 `tts` 字段 | `config.tts` |
| `flow` | `flowConfig`，节点和子字段无损保留 |
| 应用版本 ID | `config.robotVersionId` |

`allow_interruptions` 和最大时长由新配置链路驱动；旧请求链路在兼容期保持原行为。新配置不得与请求中的其他局部配置合并，Resolve/编译失败时直接使用错误，不回退到代码默认供应商、模型或音色。

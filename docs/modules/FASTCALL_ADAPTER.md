# Fast-Call Adapter 设计

## 1. 职责与核心不变量

`fastcall_adapter` 是独立电话运行面服务和镜像，负责：

- 对预留 Call 做最终拨号授权并发送一次 originate。
- FreeSWITCH ESL 命令、事件、通道关联、恢复和核查。
- Agent Claim、attach/ready/failed/completed 及媒体和挂断。
- Admin review 的实际通道检查与结果事务。
- 取消挂断指令的即时处理、租约恢复和周期对账。

最重要约束：**只有当前进程内已完成 `reserved + send_state=NONE → 数据库提交 → STARTED` 的上下文才能发送一次 originate。重启、核查和恢复没有补发权限。**

Adapter 不决定联系人重试，不拥有 Open API、Console、Robot 管理或 Scheduler。

## 2. 当前内部结构

```text
src/fastcall_adapter/
├── api/
│   ├── app.py                    # 独立 FastAPI 工厂和 UOW 组合根
│   ├── dependencies.py           # Admin/Adapter/Agent 认证与 UOW 注入
│   ├── errors.py、schemas.py
│   └── routes/
│       ├── health.py
│       ├── dispatch.py
│       ├── sessions.py
│       ├── media.py
│       ├── review.py
│       └── hangup.py
├── application/
│   ├── dispatch.py               # 拨号栅栏与单次发送上下文
│   ├── media.py                  # Agent/FreeSWITCH 会话和媒体编排
│   ├── media_bridge.py           # PCM 组帧、队列、背压和事件协调
│   ├── telephony_events.py       # 电话事件去重与状态推进
│   ├── call_checks.py            # 恢复、过期核查和 Admin review
│   └── hangup.py                 # 即时挂断与持久化对账
├── infrastructure/
│   ├── dispatch_repository.py    # 授权、acknowledge、checking
│   ├── telephony_repository.py   # 通道关联与状态更新
│   ├── hangup_repository.py      # 专用事件领取、租约与完成状态
│   ├── gateways/esl.py           # FreeSWITCH ESL Gateway
│   ├── database.py               # Adapter 所需 schema 启动检查
│   ├── crypto.py                 # 环境配置到 core crypto 的工厂
│   └── uow.py                    # core + dispatch/telephony/hangup
├── settings.py                   # 仅 Adapter 所需配置
└── main.py                       # 组合根、ESL/媒体/核查任务监护
```

Application 不依赖 FastAPI 或 SQLAlchemy。`media_bridge.py` 只处理通用媒体编排，不是 DAO；ESL 和外部客户端归 Gateway/Infrastructure。

## 3. 独立应用与接口

Adapter 使用 `fastcall_adapter.api.app.create_app()`，不 import 控制面应用。主要业务路由如下，另有 FastAPI 默认文档端点：

| 路由 | 用途 | 凭据 |
|---|---|---|
| `/up`、`/ready`、`/state/up`、`/state/ready` | 健康与数据库就绪 | 内部健康检查 |
| `POST /internal/v1/calls/{call_id}/dispatch` | 预留 Call 拨号请求 | Adapter token |
| `POST /internal/v1/calls/{call_id}/session-claim` | 初始 Claim | Adapter token |
| `POST /internal/v1/calls/{call_id}/session-claims:recover` | pre-ready 代际恢复 | Adapter token |
| `POST /internal/v1/calls/{call_id}/session-events/{action}` | Agent 生命周期事件 | Agent token |
| `POST /internal/v1/calls/{call_id}/analysis` | 预留的分析保存接口；当前 Agent 完整分析仍独立落库，未接入该调用 | Agent token |
| `WS /internal/media/{call_id}` | FreeSWITCH 媒体 | Media token |
| `POST /adminapi/v1/calls/{call_id}/review` | 实际 Admin review | Admin token + actor |
| `POST /internal/v1/calls/{call_id}/hangup` | 幂等挂断 | Adapter token |

不注册 `/openapi/`、`/console`、Robot 管理或控制面路由。

## 4. 拨号内部设计

### 4.1 最终栅栏

1. 按 `call_id` 获取进程内锁和发送上下文。
2. 检查当前 Call 仍为未授权 reserved。
3. 执行有界健康检查。
4. DAO 在同一事务中锁定 Task/Contact/Call，校验版本、活动关系、任务状态、窗口、禁呼、容量和速率。
5. 写 `adapter_boot_id/dial_requested_at/dial_phase` 并提交。
6. 当前上下文从 NONE 改为 STARTED。
7. 仅该上下文调用一次 originate。

重复 dispatch 使用现有上下文和数据库事实幂等处理。上下文保留当前进程的重复响应语义；不得为清理内存擅自改变 `already_started` 等响应。

### 4.2 授权未知与恢复

- 提交失败且确定未授权时，释放预留并重新排队；任务已取消则由共享 UseCase 终态化。
- 提交结果未知时重新读取，不直接重发。
- originate 失败、超时或进程崩溃不自动补发。
- 无通道不能单独证明未拨号；进入 checking/unknown 核查。

## 5. Agent Session 与媒体

- `call_id` 同时用于 dispatch、Agent `callUUID`、Session registry 和 FreeSWITCH 关联。
- Claim 幂等返回当前代际；pre-ready recover 增加代际。
- 任何代际的 `session.failed` 都阻止后续 Claim。
- attached 持久化并 ACK 后才初始化 Agent Pipeline；旧代际 ready 无效。
- Agent slot 在 Pipeline、Provider、媒体和清理结束后才释放。
- ready 后断线直接失败并挂断，不做对话续传。
- Agent 在 attached 等 ACK 时断线的 slot 残留是当前已知限制，详见 Collection Agent 分册；不得把该场景视为已由现有测试覆盖。

媒体契约：signed PCM16 little-endian、单声道、8000Hz、320B/20ms 逻辑帧。FreeSWITCH→Adapter 使用 caller-only mono；双方队列最多 500ms。FreeSWITCH→Agent 队列溢出时丢最旧帧并记录指标，Agent 输出队列溢出时终止当前 Call。ready 前的音频只预缓冲，ready ACK 后先 answer 再释放。

## 6. 核查、review 与挂断

### 周期核查

`main.py` 周期执行通道恢复、核查过期、Session 恢复和挂断对账。单 Call 异常不得静默终止整个 Adapter；进程内长驻组件失败仍由入口监护并整体退出。

### Admin review

review 在 Adapter 中完成 gateway 检查、必要挂断、结果修订、事件和 Outbox。控制面代理不参与该事务。无 gateway 时不得释放活动 Call。

`unknown` 会保留 Contact 的活动 Call 关系并继续占用容量，任务却可能已 completed。后续需要可靠终态或 review 释放容量；挂断指令也只有观察到终态后才算 processed。

### 取消挂断

- 专用 `call.hangup_requested` 使用 `pending/retrying → processing → processed`。
- 普通 EventWorker 显式排除该类型。
- 即时 `HangupCallUseCase` 对终态幂等返回，非终态必须有实际 `freeswitch_uuid`。
- 对账只有在 Call 进入 succeeded/failed/cancelled 后才标记 processed；发送 kill 不等于完成。
- 控制面或 Adapter 崩溃后由持久化指令和过期租约恢复，不重拨、不重复取消。

## 7. ESL 故障处理

- 命令的连接、写入和响应读取具有总超时。
- Timeout、Cancel、ESL 协议错误、OSError、EOF 和 ValueError 都关闭并清空连接。
- `close()` 先清空引用，`wait_closed()` 有 1 秒上限。
- 事件监听使用独立连接并在 `finally` 关闭。
- 不自动重试含义不明的 originate；后续命令才可安全使用新连接。

## 8. 配置、镜像与测试

- 使用现有 `ROBOT_CONFIG_DATABASE_URL`/`DATABASE_URL`、`FASTCALL_*`、FreeSWITCH 和 Agent 地址配置。
- Settings 只校验 Admin/Adapter/Agent/Media 等自身凭据，不要求 Console 凭据；完整运行入口还要求加密/HMAC 的 key JSON 与 active 版本，以及 `FREESWITCH_ESL_PASSWORD`、`FREESWITCH_SIP_GATEWAY`。
- `deploy/Dockerfile-fastcall-adapter` 只复制 `fastcall_adapter` 和 `fastcall_core`，入口为 `python -m fastcall_adapter.main`。
- Kubernetes 使用 `FASTCALL_ADAPTER_IMAGE`，单副本、单 worker、`Recreate`。

主要测试位于 `tests/unit/fastcall_adapter/`，覆盖 dispatch、Session、媒体、ESL、核查、review、挂断和 API 认证。共享事务另由 core 测试、架构测试和模拟 e2e 保护。

修改 Adapter 时不得把控制面应用或专用 DAO 重新引入，也不得为“恢复”增加 originate 权限。

## 9. FreeSWITCH 媒体实现细节

### 9.1 通道变量与事件关联

originate 前设置：

```text
origination_uuid
fastcall_call_id
fastcall_task_id
fastcall_contact_id
```

事件可能早于 `bgapi` 响应，必须先按实际 Channel UUID 和 `origination_uuid` 关联，再校验 `fastcall_call_id`。只处理 Fast-Call originate 主通道，loopback 另一腿即使继承相同 Call 标识，也不得覆盖主通道或触发第二个 Agent Session。Job-UUID 只写入命令事件用于诊断。

### 9.2 接听后的顺序

1. 收到 `CHANNEL_ANSWER`，保存 `answered_at`。
2. 执行 Claim 并建立 Agent `/ws-v2`，发送 start。
3. 对实际通道启动 `uuid_audio_stream`，参数为 caller-only mono、8kHz 和 Call metadata。
4. FreeSWITCH 模块主动连接 Adapter；播放开关和缓冲区使用现有配置，metadata 带 Call、代际和方向。
5. ready 前 caller PCM 最多预缓冲 500ms，不发送给 Agent。
6. ready ACK 后先发送 `answer`，再发送预缓冲和实时 PCM。
7. 向模块发送二进制音频前发送 `{"type":"rawAudio","data":{"sampleRate":8000}}`。

FreeSWITCH 负责 SIP codec 转码；模块与 Agent 内部处理其他采样率。Adapter 按字节流重组 320B 帧，不依赖 WebSocket 消息边界。

### 9.3 挂断与清理

- Agent `{"action":"hangup","data":{"code":480}}` 映射为实际 `freeswitch_uuid` 的 `uuid_kill`。
- 重复挂断和通道已结束按幂等处理。
- Agent 主动挂断后丢弃不可播放的尾部音频；完成事件能收到则处理并 ACK，但不把完成事件作为业务成功前置条件。
- 用户挂机后停止媒体、关闭 Agent 连接并等待完整清理；FreeSWITCH 终态到达后即可开始资源清理。

### 9.4 基础设施约束

`mod-audio-stream` 固定 commit `a25fb1fe530ec6a612d321ff04f70be69b1a257c`，部署需满足 AGPL-3.0 或商业许可。Adapter 与 FreeSWITCH 可共享容器网络命名空间，验收不得通过修改 ESL ACL 或 dialplan 绕过网络约束。

## 10. 媒体启动参数补充

当前模块启动时设置 `STREAM_PLAYBACK=true` 和 `STREAM_BUFFER_SIZE=20`。metadata 包含 `call_id`、`claim_generation` 和 `direction`。这些参数应与现有 FreeSWITCH 配置保持一致，不得为目录或进程合并而随意修改。

Ready 看门狗在 ready 后不得结束正常媒体任务。等待 Session ACK 时仅解析文本 ACK，跳过二进制音频；完成事件丢失时仍按 FreeSWITCH 终态兜底。

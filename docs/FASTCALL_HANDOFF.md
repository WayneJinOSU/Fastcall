# Fast-Call 简化调整与新窗口 Handoff

本文同时包含下一阶段简化方案和当前实现交接。服务合并与 Webhook 简化**尚未实施**，不要把计划当成已完成能力。

# 第一部分：修复与简化调整计划

## 1. 目标与边界

已完成的全自动外呼闭环、媒体链路、MySQL 事务和现有生产部署职责不回退。调整目标是：

1. 降低本地开发进程管理成本。
2. 当前阶段不考虑人工坐席、人工核查和人工修订。
3. 简化客户需要处理的 Webhook 事件，不扩大 Collection Agent 的业务修改。
4. 保持现有 FreeSWITCH、Agent Session、任务状态机和安全约束。

这不是一次架构重写，也不删除已经验证的 Adapter、Session 或 Outbox 能力。

## 2. 当前问题

合并前，本地运行 4 个宿主机业务进程、2 个 Docker 容器和 MySQL：

- Fast-Call / Robot Config API；
- Scheduler；
- Webhook Worker；
- Collection Agent；
- Adapter 容器；
- FreeSWITCH 容器；
- MySQL。

其中 API、Scheduler 和 Webhook 都是单实例 Python 异步任务，本地分别启动的收益较低，进程管理成本较高。

Webhook 当前有 4 种事件：

- `contact.completed`；
- `contact.result_changed`；
- `task.completed`；
- `task.cancelled`。

`contact.result_changed` 可来自人工修订，也可来自迟到电话事件对 `unknown` 的自动修订。当前阶段不希望把人工处理或结果修订通知作为客户接入的必要概念。

## 3. 服务合并方案

### 3.1 本地目标形态

合并为 5 个运行单元：

| 运行单元 | 内容 |
|---|---|
| Fast-Call 本地应用 | API + Scheduler + Webhook Worker |
| Collection Agent | 保持现有独立服务 |
| Adapter | 保持独立进程或容器 |
| FreeSWITCH | 保持 Docker 服务 |
| MySQL | 保持本地数据库 |

测试用 HTTPS Webhook 接收端只在验收时启动，不属于业务服务。

### 3.2 实施方式

新增开发专用入口，建议为：

```text
src/robot_config_service/local_main.py
```

建议命令：

```bash
PYTHONPATH=src venv/bin/python -m robot_config_service.local_main
```

该入口在同一个 `asyncio` 进程中运行：

- FastAPI / Uvicorn；
- 现有 `run_scheduler()`；
- 现有 `run_webhook_worker()`。

要求：

- 仅用于本地开发，不改变 `scheduler_main.py`、`webhook_main.py` 和生产部署。
- 复用现有 Settings、`.env` 加载、数据库校验和业务用例，不复制 Scheduler 或 Webhook 逻辑。
- 任一组件退出或失败时，停止其他组件并返回非零状态，不能静默失去拨号或回调能力。
- 停止时取消后台任务、等待清理，再关闭 Uvicorn 和数据库资源。
- 禁止同一环境同时启动聚合入口与独立 Scheduler/Webhook Worker，避免重复执行。
- 不在本地入口中嵌入 Adapter 或 Collection Agent。

生产环境继续独立部署 Scheduler、Adapter 和 Webhook Worker，保留现有单副本 `Recreate` 约束。

## 4. Webhook 简化方案

### 4.1 当前阶段客户必需事件

客户只需处理：

| 事件 | 含义 |
|---|---|
| `contact.completed` | 联系人首次进入终态 |
| `task.completed` | 所有联系人处理完成 |
| `task.cancelled` | 任务被中途取消，不再发送 `task.completed` |

`contact.result_changed` 暂不作为当前客户接入契约的一部分。

### 4.2 修订事件处理

建议增加配置：

```text
FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED=false
```

默认不生成或不投递 `contact.result_changed`。已有测试需要覆盖默认关闭和显式开启两种行为；显式开启只用于兼容后续自动修订需求，不代表现阶段引入人工流程。

边界必须明确：

- 不删除 `result_revision`，它仍用于幂等和版本判断。
- 不删除内部结果修订或人工核查代码，但当前正常业务流程不使用人工核查。
- 内部结果若因迟到证据发生修订，仍可用于 API 查询和对账；默认不再主动推送修订事件。
- 客户以首次 `contact.completed` 为通知基线；需要内部最新修订结果时主动查询 API。
- 文档必须说明这一选择，不能声称客户仅依赖 Webhook 就一定获得后续修订结果。

### 4.3 保持不变的内容

- `contact.completed` 仍包含任务/联系人标识、状态、结果码、Call 次数、最后 Call、revision 和完成时间。
- `task.completed` 仍包含完成时的联系人统计快照。
- `task.cancelled` 与 `task.completed` 继续区分，不能混为自然完成。
- 继续保留稳定事件 ID、HMAC-SHA256、HTTPS、DNS/IP 校验和失败重试。
- 不在回调中增加完整号码、敏感变量、完整对话、录音或完整业务分析。
- Collection Agent 原有语音信箱、业务分析和数据库落库保持独立，不为 Webhook 简化而修改。

## 5. 明确不做的事情

- 不撤回 `session_lifecycle.py` 或 `session_registry.py`。
- 不修改 Collection Agent 的语音信箱和业务分析逻辑。
- 不为本地测试修改 FreeSWITCH 配置、ESL ACL 或 dialplan。
- 不把 Adapter 合并进 FastAPI 主应用。
- 不删除 `unknown`、内部修订、Admin review 或现有数据库字段。
- 不引入 Redis、Kafka、Temporal 或新的进程管理框架。
- 不将生产多职责进程合并列为本阶段目标。

## 6. 推荐实施顺序

1. 新增 `local_main.py`，组合现有三个异步入口，并补充启动、失败传播和关闭测试。
2. 更新本地启动文档，明确不得与独立 Scheduler/Webhook Worker 同时运行。
3. 增加 `FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED=false` 的兼容开关和测试。
4. 更新 Open API / Webhook 文档，只把三个当前必需事件作为客户接入契约。
5. 运行完整单元测试、模拟端到端和真实 MySQL 回归。
6. 使用聚合入口替换本地独立 API、Scheduler、Webhook Worker，再执行一次 FreeSWITCH loopback 验收。
7. 确认只创建一个 Call、只产生预期事件、Webhook 无重复投递。

## 7. 验收标准

### 服务合并

- 一个命令启动 API、Scheduler 和 Webhook Worker。
- 健康检查、任务创建、自动拨号、终态和回调均正常。
- 关闭聚合进程后无残留 Scheduler 或 Webhook 任务。
- 生产独立入口的现有测试和部署配置不变。

### 回调简化

- 默认只产生当前需要的三个事件。
- 结果修订在默认配置下不产生 `contact.result_changed`。
- 显式开启时，原修订事件路径仍按既有测试工作。
- Webhook 重试保持事件 ID 和 payload 不变，HTTPS/HMAC 校验不降级。

### 回归基线

- Robot Config 现有 104 项单元测试全部通过，新增测试同步通过。
- 模拟端到端测试通过。
- 真实 MySQL 6 项集成测试通过。
- FreeSWITCH loopback 中 Call/Contact 成功、Task 完成、Webhook 返回 204，且无残留通道。

# 第二部分：当前实现 Handoff

## 8. 下一窗口先读什么

按顺序阅读：

1. 本文第一部分：下一阶段要实施的服务合并与 Webhook 简化。
2. `docs/FASTCALL_OUTBOUND_SERVICE_DESIGN.md`：V1 契约、架构和已执行验证。
3. `docs/ARCHITECTURE.md` 第 11 节：组件和事务边界。
4. `docs/QUICK_START.md` 的 Fast-Call 部分：启动、测试和 loopback 验收。

可直接给新窗口的任务：

> 阅读 `docs/FASTCALL_HANDOFF.md`。先实现本地 API + Scheduler + Webhook Worker 聚合入口，再实现默认关闭 `contact.result_changed` 的兼容配置。保持生产部署、Collection Agent 业务和 FreeSWITCH 配置不变，完成测试与真实 loopback 回归。

## 9. 已完成的基线

Fast-Call V1 已实现并完成真实全链路验证：

- Agent Session、代际和防重复占用；
- Scheduler、任务控制、重试和拨号栅栏；
- Adapter 拨号、ESL 事件、媒体桥和挂断；
- Open API、Admin API 和 MySQL 事务；
- 事务性 Outbox、HMAC + HTTPS Webhook 和重试；
- Alembic 当前基线为 `0004`，业务分析表为 `outbound_call_analysis`，不覆盖旧 `call_analysis`。

真实 Docker FreeSWITCH loopback 中已使用生产 Deepgram、DeepSeek 和 Cartesia：

- Cartesia 生成用户语音并注入 loopback；
- Deepgram 返回真实转写；
- DeepSeek 产生过两轮真实对话和 token 记录；
- Cartesia 生成并播放回复；
- Call/Contact 为 `succeeded`，Task 为 `completed`；
- `contact.completed`、`task.completed` 均为一次 `delivered`，HTTP 204；
- 最终无 FreeSWITCH 残留通道。

最终清理回归：

```text
task_id = task_350d35d6a9f84668868dc3f0af92b8a5
call_id = call_985da6c90abc40f8a30e7d845fe16d19
```

## 10. 验证基线

最近执行结果：

- Robot Config：104 项单元测试通过。
- `tests.integration.test_outbound_e2e`：通过。
- 真实 MySQL：6 项集成测试通过。
- FreeSWITCH 媒体 POC：32/32 通过，脚本为 `scripts/test_freeswitch_media_poc.sh`。
- `git diff --check`：通过。
- 全项目 190 项测试中有一项既有 Realtime `_observer` 错误：`tests.unit.test_realtime_pre_session_audio`。它不属于 Fast-Call 本次改动，不要为了本任务顺手修改。

常用命令：

```bash
PYTHONPATH=src venv/bin/python -m unittest discover -s tests/unit/robot_config_service -t .
PYTHONPATH=src venv/bin/python -m unittest tests.integration.test_outbound_e2e
```

真实 MySQL 测试只能使用独立测试库，禁止指向业务 `collection_agents` 数据库。

## 11. 运行环境与当前停止状态

合并前的运行方式：

| 组件 | 运行方式 | 地址或标识 |
|---|---|---|
| Fast-Call / Robot Config API | 宿主机进程 | `http://localhost:8770` |
| Scheduler | 宿主机后台进程 | 无监听端口 |
| Collection Agent | 宿主机进程 | `http://localhost:8768` |
| Webhook Worker | 宿主机后台进程 | 无监听端口 |
| Adapter | `fastcall-adapter` 容器 | `http://localhost:8767` |
| FreeSWITCH | `fastcall-fs` 容器 | ESL `localhost:8021` |
| MySQL | 本地服务 | `localhost:3306/collection_agents` |
| 测试 Webhook 接收端 | 宿主机临时进程 | HTTPS `9443/callback` |

按用户要求，本次启动的 API、Scheduler、Collection Agent、Webhook Worker 和测试 Webhook 接收端已停止，`fastcall-adapter`、`fastcall-fs` 容器已停止但未删除。本地 MySQL 未停止，业务数据未删除。

Docker CLI 若不在 PATH：

```bash
export PATH="/Applications/Docker.app/Contents/Resources/bin:$PATH"
docker ps -a
```

开始新工作前先检查端口和容器，**不要重复启动服务**：

```bash
lsof -nP -iTCP:8770 -iTCP:8768 -iTCP:8767 -iTCP:9443 -sTCP:LISTEN
docker ps -a
```

恢复独立进程验收时，先启动 `fastcall-fs`，再启动使用相同配置的 `fastcall-adapter`，然后按 `QUICK_START.md` 启动其他服务。实现聚合入口后，只启动聚合控制面，不再同时启动独立 Scheduler/Webhook Worker。

## 12. 本地配置与敏感信息

- `.env` 中有本地 MySQL 和生产 Provider 配置，不要输出、复制或提交其内容。
- Scheduler、Adapter 和 Webhook Worker 已支持读取 `.env`，不要恢复成只依赖 shell 环境。
- 当前验收使用 `/tmp/fastcall_run.py` 合并 `.env` 与 `/tmp/fastcall-runtime.env`；这些文件含敏感配置，只能使用，不要打印。
- Adapter 当前使用 `/tmp/fastcall-adapter-full.env`。
- 当前 Webhook 测试接收端及测试 CA 配置位于 `/tmp`，不是生产组件。
- `/tmp/fastcall_webhook_run.py` 包含本次本地 `host.docker.internal` 到局域网地址的测试解析映射。聚合入口验收时需要保留等效测试解析或提供有效的本地 DNS；不得通过关闭 TLS、HMAC 或私网白名单绕过。
- `FASTCALL_DISABLE_SSL_CERT_FILE=1` 只用于 Collection Agent，防止测试 CA 覆盖生产 Provider 证书；Webhook Worker 仍使用测试 CA。

## 13. 必须保持的约束

### Collection Agent

- 不再扩大其语音信箱、业务分析或落库逻辑修改。
- `session_lifecycle.py` 和 `session_registry.py` 是 Fast-Call 接入协议，负责 attached/ready、ACK、代际和防重复会话，不要直接撤回。
- 只有带 `claimGeneration` 的请求启用该协议，普通 `/ws-v2` 请求保持原流程。
- Agent 原有语音信箱、业务分析和数据库落库独立执行，不依赖 Fast-Call WebSocket 的存活。
- Fast-Call 以 FreeSWITCH 技术终态完成业务闭环，不把 `session.completed` ACK 当作成功前置条件。

### FreeSWITCH

- 无 SIP 线路时继续使用现有 FreeSWITCH loopback 能力。
- 不修改 FreeSWITCH 配置、ESL ACL 或 dialplan。
- Adapter 与 FreeSWITCH 当前共享容器网络命名空间，避免修改 ESL 访问控制。
- `mod-audio-stream` 固定 commit `a25fb1fe530ec6a612d321ff04f70be69b1a257c`，部署需满足 AGPL-3.0 或商业许可。

### 业务范围

- 当前阶段不考虑人工坐席、人工核查和人工修订流程。
- `contact.result_changed` 除了人工修订，当前也可由迟到电话事件自动修订触发；简化时不能只删除人工分支。
- 当前客户回调不包含完整号码、敏感变量、完整对话或完整业务分析。

## 14. 当前仓库状态提醒

编写本 Handoff 时，HEAD 为：

```text
3aca824 freeswitch + agents + app manage 跑通
```

已有未提交修改包括：

- `docs/ARCHITECTURE.md`
- `docs/FASTCALL_OUTBOUND_SERVICE_DESIGN.md`
- `src/robot_config_service/infrastructure/freeswitch/esl.py`
- `src/robot_config_service/infrastructure/freeswitch/media.py`
- `tests/unit/robot_config_service/test_outbound_esl.py`
- `tests/unit/robot_config_service/test_outbound_media.py`

这些是最近的真实媒体收尾修复和文档更新，不要回退。另有 `.kimi-code/` 本地技能配置，保持不动。

本次只新增本文档 `docs/FASTCALL_HANDOFF.md`，简化方案和交接内容已合并在同一文件中。

开始修改前重新执行 `git status --short`，以实际状态为准，不要覆盖用户或其他窗口的新改动。

## 15. 推荐的下一窗口执行清单

1. 阅读简化计划并核对当前服务、Git 状态和测试基线。
2. 新增 `local_main.py`，只组合现有 API、`run_scheduler()`、`run_webhook_worker()`。
3. 为聚合入口补充失败传播、取消清理和启动测试。
4. 增加 `FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED=false`，兼容现有显式开启路径。
5. 更新测试与文档，明确默认关闭修订通知后客户需要通过 API 查询内部修订结果。
6. 运行全部相关单元测试、模拟端到端和真实 MySQL 回归。
7. 以聚合入口替换旧的三个本地控制面进程，执行真实 loopback 验收。
8. 核对无重复拨号、无重复 Webhook、无残留 FreeSWITCH 通道，再报告完成。

不要把“计划文档已写”当成“简化已实现”。

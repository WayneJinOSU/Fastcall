# Fast-Call 服务包与本地控制面改造计划

- 状态：已确立（四包目录及分层可读性规范经 Codex 评审，复核两项 P1 已纳入）
- 依据：`docs/FASTCALL_HANDOFF.md`、当前代码入口与部署文件
- 范围：本地控制面合并、Webhook 默认契约简化、`src/` 内服务包拆分
- 当前进度：本地聚合、Webhook 开关、core 提取、Adapter 独立包/镜像配置和 Fast-Call 改名已实施；最终评审与真实环境验收另列结果
- 目录纠偏：不采用 `services/` 或服务级打包布局；撤回残留已逐项核对并清理，未移动 Agent 的正常包、资源或数据

## 1. 背景与当前事实

### 1.1 运行边界

生产部署单元保持 Fast-Call/Robot Config API、Scheduler、Webhook Worker、Adapter、Collection Agent、FreeSWITCH 和 MySQL。本地开发只将 API、Scheduler、Webhook Worker 合并为一个 `asyncio` 进程，Adapter、Agent 和基础设施不合并。

生产继续独立运行 Scheduler、Webhook Worker 和 Adapter，保留单副本、单 worker、`Recreate`，不引入多活、HPA 或滚动期间新旧实例并存。

### 1.2 代码边界

- `src/collection_agents` 与 `src/robot_config_service` 没有直接 Python import 依赖，Agent 包无需迁移。
- Adapter 已有独立入口、Dockerfile 和 Kubernetes Deployment，但代码仍位于 `robot_config_service`。
- Adapter 当前复用完整控制面 `create_app()`，因此装配了 Open API、Console、Management 等无关路由。
- API、Scheduler、Webhook Worker 和 Adapter 已共享 `Database`、UOW、ORM、事务仓储和部分安全配置。
- Adapter 的 dispatch、媒体、电话事件和 Session 使用 `FinalizeCallUseCase`；控制面任务路径使用同一文件中的 `CompleteTaskUseCase`，终态与 Outbox 不能复制成两套。
- `AgentSessionUseCase` 依赖精确版本 Resolve；`CallCheckUseCase` 同时服务 Adapter 周期核查和 Admin review。

### 1.3 构建与路径约束

- 根 `pyproject.toml`、`requirements.txt` 和 `venv` 继续统一服务整个仓库。
- 根 `tests/`、`deploy/`、`docs/`、`assets/`、`templates/` 和 `data/` 大结构保持。
- 只需同步调整 Alembic、Console package data、Dockerfile `COPY/CMD`、Kubernetes 模块入口和受影响测试 import。
- 根 `.dockerignore` 已加入，后续根上下文构建必须继续排除 `.env*`、运行数据、虚拟环境和 Git 元数据。

## 2. 架构决策

1. 保留当前单仓、单根 `src` 布局，不建立 `services/`、根 `packages/` 或 `src/packages/`。
2. 在 `src/` 下设置四个平级 Python 包：`fastcall`、`collection_agents`、`fastcall_adapter`、`fastcall_core`。
3. Python import 包使用下划线；服务显示名称和容器仍可使用 `fastcall-adapter`、`collection-agent`。
4. Adapter 是独立代码包、独立进程和独立镜像，不再作为控制面包中的入口。
5. `fastcall_core` 是共享 Python 库，不是运行服务，不增加进程或网络跳数。
6. Collection Agent 包、资源、数据路径和业务逻辑保持原位。
7. Fast-Call 控制面继续包含 Robot Config，不再拆成独立运行服务。
8. 根依赖和打包体系统一，不为每个包建立独立 pyproject、requirements 或虚拟环境。
9. 不改变外部 Open API 路径、Webhook 安全协议、Agent WebSocket 协议、FreeSWITCH 配置、数据库表名和现有环境变量名称。
10. 不拆物理数据库，不做数据迁移，不改变 Alembic version table 和 `0004` 基线。
11. 不撤回 Session 生命周期、内部结果修订、Admin review、Outbox 或 Adapter 恢复能力。
12. 不引入 Redis、Kafka、Temporal、新进程管理框架或新包管理框架。

## 3. 目标目录

```text
Fastcall/
├── src/
│   ├── fastcall/                       # 控制面 + Robot Config
│   │   ├── api/                        # Open/Admin/Management/Resolve/Console
│   │   ├── application/                # 控制面任务、Scheduler、Webhook Worker
│   │   ├── capabilities/
│   │   ├── console/
│   │   ├── infrastructure/             # 控制面专用仓储与 HTTP client
│   │   ├── migrations/
│   │   ├── main.py
│   │   ├── local_main.py
│   │   ├── scheduler_main.py
│   │   ├── webhook_main.py
│   │   └── settings.py
│   ├── fastcall_adapter/               # 电话运行面独立包
│   │   ├── api/                        # health/dispatch/session/media/review/hangup
│   │   ├── application/                # dispatch/media/telephony/checks
│   │   ├── infrastructure/             # ESL、dispatch/telephony 仓储
│   │   ├── main.py
│   │   └── settings.py
│   ├── fastcall_core/                  # 共享领域与事务内核
│   │   ├── domain/
│   │   ├── application/
│   │   └── infrastructure/
│   └── collection_agents/              # 保持现有 Agent 包
│       ├── bots/
│       ├── handlers/
│       ├── processors/
│       ├── server/
│       ├── session_lifecycle.py
│       └── session_registry.py
├── tests/
│   ├── unit/
│   │   ├── fastcall/                   # 原 robot_config_service 测试随包改名
│   │   ├── fastcall_adapter/
│   │   ├── fastcall_core/
│   │   └── test_*.py                   # Agent 测试保持现有根级布局
│   ├── integration/                    # 保持现有集成测试结构
│   └── load/
├── deploy/                             # 保持根级部署目录
│   ├── Dockerfile                      # Agent 镜像
│   ├── Dockerfile-gpu
│   ├── Dockerfile-robot-config         # Fast-Call 镜像
│   ├── Dockerfile-fastcall-adapter     # Adapter 镜像
│   ├── robot_config_alembic.ini
│   └── ...
├── assets/                             # Agent 资源保持原位
├── templates/
├── data/
├── docs/
├── examples/
├── scripts/
├── main.py                             # Agent 启动入口保持原位
├── pyproject.toml                      # 单根打包配置
└── requirements.txt                    # 单根依赖清单
```

不新增 `COLLECTION_AGENT_DATA_DIR`，不改变 Agent 资源查找，不移动现有数据库或音频资源。

## 4. 代码结构、分层与可读性规范

### 4.1 三个改造包的内部结构

`application/` 本身就是 Service 层，不再嵌套语义重复的 `services/`。现有 `*UseCase` 继续作为 Service 命名，避免为了套用三层概念全面改名。

```text
fastcall_core/
├── domain/                            # 纯领域规则，无框架依赖
│   ├── outbound_tasks.py
│   ├── calls.py
│   ├── calling_windows.py
│   ├── retry_policy.py
│   ├── telephony_results.py
│   └── errors.py
├── application/                       # 双方共享的 Service 与接口
│   ├── ports/
│   │   ├── records.py                 # 跨层记录 DTO
│   │   ├── repositories.py            # 沿用现有 Protocol
│   │   └── unit_of_work.py
│   ├── finalize_call.py
│   ├── complete_task.py
│   ├── event_payloads.py
│   ├── agent_session.py
│   ├── call_analysis.py
│   ├── resolve_exact.py
│   └── views.py                       # 先集中短视图函数，出现独立职责后再拆
└── infrastructure/
    ├── database.py                    # 连接基础设施，不含完整控制面校验
    ├── orm/                           # 共享 ORM
    ├── dao/                           # 共享叶子 Repository
    ├── uow.py                         # SqlAlchemyCoreUnitOfWork
    └── crypto.py                      # 纯 SecretCodec/PhoneHmac

fastcall/
├── api/                               # Controller 与 HTTP 协议
│   ├── app.py                         # 只创建 FastAPI、注册路由和错误处理
│   ├── errors.py
│   ├── schemas/
│   ├── dependencies/
│   │   ├── auth.py
│   │   └── injection.py               # 只读取 app.state 中已装配能力
│   └── routes/
│       ├── management.py
│       ├── resolve.py
│       ├── console/
│       ├── openapi/
│       └── admin/
├── application/
│   ├── robot_config/                  # apps、publication、resolve
│   ├── outbound/                      # tasks、cancel、queries、delivery reset
│   └── workers/                       # scheduler、event_worker、webhook_worker
├── domain/robot_config/               # contract、variables、validation、diff
├── infrastructure/
│   ├── dao/                           # 调度、查询、任务控制、Worker、Robot 管理
│   ├── gateways/                      # Adapter HTTP client
│   └── uow.py                         # SqlAlchemyFastcallUnitOfWork
├── capabilities/
├── console/
├── migrations/                        # 唯一 schema 迁移所有者
├── main.py                            # 控制面组合根
├── local_main.py                      # 本地进程组合根
├── scheduler_main.py
├── webhook_main.py
└── settings.py

fastcall_adapter/
├── api/
│   ├── app.py                         # 独立应用工厂，不复用控制面 app
│   ├── errors.py
│   ├── schemas.py
│   ├── dependencies.py
│   └── routes/                        # health、dispatch、sessions、media、review、hangup
├── application/
│   ├── dispatch.py                    # 单次拨号状态机保持内聚
│   ├── media.py
│   ├── telephony/                     # events、restore、expire
│   └── calls/                         # review、hangup、reconcile
├── infrastructure/
│   ├── dao/                           # dispatch、telephony、hangup
│   ├── gateways/                      # esl.py、media_bridge.py
│   └── uow.py                         # SqlAlchemyAdapterUnitOfWork
├── main.py                            # Adapter 组合根与任务监护
└── settings.py
```

`collection_agents` 保持当前结构，不为统一外观机械套用上述目录。

### 4.2 三层概念映射

| 概念 | 代码位置 | 核心职责 |
|---|---|---|
| Controller | `api/routes/` 中的无状态路由函数 | 协议解析、认证、参数校验、调用 Service、响应 |
| Service | `application/` 中的 `*UseCase` 和 Worker | 业务编排、状态转换、事务边界、调用端口 |
| DAO | `infrastructure/dao/` 中的 `*Repository` | ORM 读写、条件更新、查询和租约持久化 |
| Gateway | `infrastructure/gateways/` 中的 `*Gateway`/client | Adapter HTTP、ESL、媒体等外部系统适配 |
| Domain | `domain/` | 业务模型、规则、结果码和不变量 |
| 组合根 | `main.py` 和各进程入口 | 装配 Settings、Database、UOW、Gateway，再交给应用工厂注册路由 |

DAO 只处理持久化；ESL、HTTP 和媒体不是 DAO。Domain 不是 DAO 模型，ORM 与领域记录的转换由基础设施负责。

### 4.3 允许与禁止依赖

| 层 | 允许 | 禁止 |
|---|---|---|
| Controller | Schema、认证依赖、Application Service | 访问 `uow.*`、commit/rollback、具体 DAO、直接读环境变量 |
| Service | Domain、现有 Protocol、UOW/Gateway 抽象 | FastAPI、SQLAlchemy、Settings、具体 DAO、全局环境读取 |
| DAO | Session、ORM、领域记录和领域错误 | Service、Controller、Gateway、commit/rollback |
| Gateway | 网络/ESL/HTTP client、DTO | UOW、数据库事务、调用其他 Service |
| Domain | 标准库和同层 Domain | API、Application、Infrastructure、环境配置 |

入口和组合根可以导入各层进行装配，是唯一允许的跨层装配点。`api/dependencies` 不直接实例化 DAO/UOW/crypto，只从 `app.state` 获取组合根已装配的能力。

Controller 暂时可保留 `uow=Depends(get_uow)`，但必须立即传给 Service，不得访问其仓储或事务方法。优先修正现有 Admin delivery reset 的直接仓储与提交，以及 Open API 中直接加载 `PhoneHmac` 的行为。

Service 沿用现有 Protocol，不为每个类新增平行接口，不引入 DI 容器。`fastcall` 与 `fastcall_adapter` 只能共同依赖 core，二者不得互相 import；`collection_agents` 不依赖三者。

### 4.4 core allowlist 与 UOW 组合

core 只保留共享领域、终态/任务完成、Session、Analysis、精确版本读取、共享视图、共享 ORM/DAO/UOW 和纯安全能力。完整事件 payload 与 `CompleteTaskUseCase` 等现有依赖必须纳入同一迁移闭包。

共享叶子数据能力包括 Outbound Task/Contact/Call、事件、Outbox、结果、Session、Analysis、Robot 版本读取、blacklist 和 `scheduler_state`。它们只有一份实现，不得复制或跨服务内部 import。

现有仓储类不能按整类搬迁。例如 `SqlAlchemyOutboundCallRepository` 同时含共享读取和 Scheduler reserve/release，`list_due` 也属于调度工作流。阶段 2A 按方法和事务职责拆分类：

- core：共享读取、原子状态操作及双方共同调用的事务方法；`release_unauthorized_reservation` 可保留在 core。
- Fast-Call：`list_due`、重试提升、调度筛选、容量判断和 `reserve_dialable`。
- Adapter：`authorize`、`acknowledge`、`mark_checking` 和恢复扫描。

UOW 保持三个粗粒度组合，不按端点或每张表创建 UOW：

```text
CoreUnitOfWork
├── tasks、contacts、calls
├── call_events、webhook_deliveries
├── sessions、results、analysis
└── blacklist、scheduler_state、pinned_versions

FastcallUnitOfWork(CoreUnitOfWork)
├── robot_apps、versions、releases、audits
└── scheduling、queries、task_control、webhook_worker、event_worker

AdapterUnitOfWork(CoreUnitOfWork)
└── dispatch、telephony、checks、hangup
```

这里的成员是职责聚焦的 Repository，不是把当前粗粒度类原样挂到 core。每种 UOW 及其全部 DAO 必须共享同一个 SQLAlchemy Session；完整多表事务方法是拆分的最小单元，不能把其中的关联更新分散到不同 UOW。不得引入通用 `BaseRepository`，也不得让 Fast-Call/Adapter 专用 DAO 泄入 core。

明确不进入 core：Scheduler 工作流、控制面查询、TaskControl、Webhook/Event Worker、Console、Robot 管理发布、普通 Resolve、完整 schema 校验、dispatch/telephony 状态转换、CallCheck、FastAPI、Uvicorn、ESL 和 Agent Pipeline。

数据库连接可共享；完整 `Database.verify()` 和 Alembic 归 Fast-Call。Adapter 只检查自身需要的连接与 schema 兼容性。core 的 `SecretCodec`/`PhoneHmac` 只接收显式配置，不保留 `from_env()`；环境读取和 JSON 装配留在两个入口，变量名不变。

### 4.5 事务所有权

- 最外层 Service 或 Worker 拥有一个工作单元的 commit/rollback；Controller 和 DAO 永不提交。
- 查询 Service 不提交；嵌套 Service 沿用 `commit=False`，最外层成功时只提交一次，异常由最外层回滚。
- Worker 的租约领取与处理结果仍是两个独立事务，不为“简化”合并。
- originate、Webhook HTTP 和内部命令等外部副作用不得夹在未提交数据库写入之间。
- 取消挂断继续使用已确定的“持久化指令并提交 → 即时命令 → 周期对账”。
- Admin review 的通道检查和结果事务仍由 Adapter 同一用例处理，不跨服务拆成两次数据库提交。

拆分前后必须用事务测试证明提交次数、回滚、Outbox 和外部调用顺序不变，不能只靠 import 检查。

### 4.6 文件拆分与保留边界

优先处理有实际职责冲突的文件：

1. `main.py`/`adapter_main.py`：分离应用工厂、路由集合和进程组合根。
2. `api/dependencies.py`：拆分认证与能力注入；具体实例创建移到组合根。
3. `outbound_unit_of_work.py`：拆成 Core/Fast-Call/Adapter 三个组合。
4. `outbound_repositories.py`：按共享叶子、调度工作流和 Adapter 工作流拆 DAO。
5. `outbound_tasks.py`：拆出 views 和输入归一化；create/start/pause/resume 仍留在同一任务 Service。
6. `call_results.py`：拆为 finalize、complete 和共享 payload 模块，但不拆散 FinalizeCall 的原子事务。
7. `call_checks.py`：按恢复、过期和 review 三条工作流拆分。

不为美观拆分纯规则 `domain/validation.py`、单次拨号 `DispatchService`、短小的 `local_main.py`/`settings.py` 或内聚 capabilities。只有出现第二职责、不同事务边界或新的外部依赖时才继续拆文件。

不引入通用事务中间件、CQRS、DI 容器、每动词一个服务或全仓格式化。新模块不得使用职责不明的 `helpers.py`、`utils.py`、`misc.py`、`common.py` 名称；现有 Agent 同名目录不在本次改名范围内。

### 4.7 命名与静态架构测试

命名保持业务语义：

- 路由模块按“访问面＋资源”命名。
- Service 使用 `*UseCase`，DAO 使用 `*Repository`。
- 外部端口与实现使用 `*Gateway`，事务组合使用 `*UnitOfWork`。
- 布尔、时间和容量名称表达真假或单位，不为了简短牺牲含义。

新增 `tests/unit/architecture/`，使用 AST 和行为测试维护边界：

- `test_import_boundaries.py`：禁止反向及跨服务 import。
- `test_layer_dependencies.py`：禁止 Domain 向上、Controller 到具体基础设施、DAO 到 Service。按已迁移包启用：阶段 2D 检查 Fast-Call/core；Adapter 在阶段 3B 消除 `application/media.py` 的具体 FreeSWITCH 依赖后再纳入，不设置长期通用豁免清单。
- `test_transaction_ownership.py`：通过 AST 检查 commit/rollback 调用位置，不用简单字符串匹配。
- `test_app_route_sets.py`：断言两个应用必需和禁止的路由，Adapter 不含 Console/Robot 管理/Open API。
- `test_uow_composition.py`：检查结构不变量而非成员快照：UOW 与 DAO 共享 Session、core 不包含专用成员或反向 import；跨表提交一次与完整回滚由行为测试验证。

这些测试用于保护真实契约，不设置函数行数、参数个数或复杂度分数门槛。

## 5. 接口与路由迁移

### 5.1 保持不变的契约

- Open API 路径、请求响应、状态版本和幂等规则不变。
- `contact.completed`、`task.completed`、`task.cancelled` payload 和安全投递机制不变。
- `contact.result_changed` 默认关闭；显式开启后保持 event ID、payload 和 revision 行为。
- Adapter dispatch、Agent Session、媒体 WebSocket 的 URL 路径、凭据和消息协议不变。
- `call_id` 继续贯穿 dispatch、Agent Session 和 FreeSWITCH 通道。

### 5.2 路由归属

- Fast-Call 保留公开 Open API、Admin 查询/投递重置、Management、Resolve、Console，以及 Admin review 的兼容代理入口。
- Adapter 装配 health、internal dispatch、internal session、internal media、Admin review 实际用例和内部挂断。
- 不再通过完整控制面 `create_app()` 间接注册无关路由。

**Admin review 业务归属 Adapter，原外部基址由控制面代理保留。** 两个服务都保留 `POST /adminapi/v1/calls/{call_id}/review`、原请求字段和 Admin 凭据。控制面路由只做认证和固定 Adapter 地址的 HTTP 代理，转发 actor 与请求，不直接访问数据库或 gateway；Adapter 执行实际通道检查、挂断、结果修订、事件和 Outbox 单事务。代理不得接受调用方提供的上游地址，不得关闭认证或跟随重定向。这样既有控制面基址和当前可用的 Adapter 基址都不失效。新增控制面代理契约测试，以及“Adapter 应用 + fake gateway”的路由级事务测试。

**活动 Call 挂断采用“持久化指令 + Adapter 即时命令 + 周期对账”。**

1. Open API 取消任务仍由控制面完成原有一次数据库事务。
2. `hangup_active=true` 时，为每个活动 Call 写入带稳定 ID 的 `call.hangup_requested` 持久化指令，复用现有 `call_event` 和租约字段，不新增表；`false` 不写指令，活动 Call 继续执行。
3. 专用仓储只领取该 event type，普通 EventWorker 必须显式排除它，不能误领。状态流为 `pending → processing → processed`，失败进入 `retrying`；只有观察到通道终态或可靠结束证据后才能 `processed`。
4. 提交后控制面通过 Adapter internal client 调用幂等挂断命令，作为即时快路径。
5. Adapter 根据当前 Call 的实际 `freeswitch_uuid` 执行 `uuid_kill`；重复或已终态请求幂等返回，不获得 originate 权限。
6. Adapter 周期对账读取未完成指令，对相应非终态 Call 幂等挂断，直到满足完成条件。
7. 控制面提交后崩溃、命令结果未知或 Adapter 重启均能恢复；不得重拨、重复取消或重复写 Outbox。
8. 即时命令失败时保留取消事实并告警，由对账兜底；首个取消请求仍返回既有成功响应，明确表示取消已持久化，不把已提交事务伪装成失败。相同旧 `expected_state_version` 的重复请求继续按现有版本冲突处理，不新增重放响应或改变既有幂等契约。

## 6. 实施阶段

每个阶段可拆多个独立提交，测试通过后进入下一阶段。目录移动、包改名和功能变化不得混为同一提交；review/cancel 修正与 Adapter 包迁移也必须分别提交、分别回归。

### 恢复实施前核对（待完成）

- 逐项比较撤回残留的嵌套目录与正常目录，确认内容来源后合并或删除，不直接覆盖现有文件。
- 核对 `src/collection_agents/collection_agents/`、`assets/assets/`、`templates/templates/`、`deploy/migrations/migrations/`、`tests/load/load/` 是否只含重复内容，并保留用户期间新增或修改的文件。
- 重新执行 `git status --short` 和当前测试，以实际工作树重新建立基线；规划评审不代替该核对。

### 阶段 0：基线与构建保护（已完成）

- 已记录 104 项 Robot Config 基线、191 项全量测试中的既有 Realtime `_observer` 错误、模拟 e2e 和三服务路由。
- 已新增根 `.dockerignore`。
- 真实 MySQL 6 项因未配置独立测试库而跳过，不计作已验证。

### 阶段 1A：本地聚合（已完成）

- 已新增本地聚合入口（当前为 `fastcall.local_main`），复用 API、Scheduler 和 Webhook Worker。
- 已实现任一组件退出后的整体关闭、异常传播和取消清理。
- 聚合入口 4 项生命周期测试已通过；当前工作树保留入口和测试。

### 阶段 1B：Webhook 简化（已完成）

- 已加入 `FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED`，同时控制修订事件生成与历史待办投递。
- 默认关闭不影响内部 revision 与 API 查询；自动修订和 Admin review 显式开启路径均有测试。
- 相关单元测试、模拟 e2e 和文档更新已完成。

### 阶段 2：提取 `src/fastcall_core`

**提交 2A：leaf 模型与持久化基础。** 迁移共享 domain、DTO、ports、ORM、数据库连接和 core UOW；控制面及 Adapter 专用仓储暂留原包。

**提交 2B：共享事务用例。** 迁移 call results、Session、analysis 和精确版本 Resolve，拆出共享视图及完整依赖闭包；CallCheck 和 dispatch/telephony 仓储不进入 core。

**提交 2C：调用方切换。** 更新 API、Scheduler、Webhook、Adapter import，暂不改变进程拓扑和路由；分别组合控制面与 Adapter UOW。

**提交 2D：Controller 与组合根清理。** 将 delivery reset 改为 Service，移除路由直接 commit；把 `default_uow_factory`、`SecretCodec.from_env()`/`PhoneHmac.from_env()` 等具体实例创建同时迁到进程组合根，`api/dependencies` 只注入已装配能力。分离控制面应用工厂与组合根，保持路由、响应和事务行为不变，并按包范围加入分层、事务所有权和 UOW 不变量测试。

每个提交保持原入口可运行并执行相关回归。增加 core 单元测试和静态边界测试。

完成标准：四个现有入口只通过 core 获取共享能力；单元测试、模拟 e2e 通过；真实 MySQL 在具备独立测试库时执行。

### 阶段 3：修正 review/cancel，再拆 `src/fastcall_adapter`

**提交 3A：能力修正。** 在现包内先完成控制面 review 兼容代理、Adapter 路由级事务测试、专用挂断事件过滤与状态流、即时幂等命令和周期对账。验证即时失败后仍返回既有取消成功响应、旧版本重复请求仍冲突，以及控制面/Adapter 崩溃恢复。

**提交 3B：包迁移。** 新建 `src/fastcall_adapter`，迁移 Adapter 入口、dispatch、媒体、电话事件、ESL、核查、专用 Settings 和应用工厂；控制面不再被 import。同步更新 review 代理、双基址和内部挂断的 runbook，不推迟到最终阶段。

**提交 3C：独立镜像。** `Dockerfile-fastcall-adapter` 只复制 `fastcall_adapter`、`fastcall_core` 及所需共享资源，不包含 Agent、Console、Scheduler 或 Webhook Worker；不得通过根项目 `pip install .` 将四包全部装入。

部署变量拆为 `FASTCALL_IMAGE` 与 `FASTCALL_ADAPTER_IMAGE`：Scheduler/Webhook 使用前者，Adapter 使用后者。保持单 worker、单副本、`Recreate` 和端口。同步更新镜像变量、模块入口和启动命令文档。

完成标准：Adapter 独立构建与启动，review 双基址、cancel、事件隔离、重复 dispatch、commit unknown、重启不补发、Session 代际、媒体背压测试通过；模拟 e2e 和真实 loopback 通过；镜像无敏感配置。

### 阶段 4：机械改名 `robot_config_service` → `fastcall`

1. 将剩余控制面包移到 `src/fastcall`，测试目录同步改名。
2. 更新 import、Console、capabilities package data、Alembic 和部署入口。
3. 保留 `ROBOT_CONFIG_*` 环境变量、数据库名、表名和 version table。
4. 保留 API、`scheduler_main.py`、`webhook_main.py` 生产入口及 `local_main.py`。
5. 删除旧包，避免双包并存和误启动。

完成标准：Fast-Call 镜像独立构建，Open/Admin/Console 路由与迁移检查正常，生产三个入口独立启动，真实 loopback 通过。

### 阶段 5：文档与最终清理

1. 更新 ARCHITECTURE、OUTBOUND_SERVICE_DESIGN、QUICK_START 和 README。
2. 将旧计划中 `services/`、服务级打包及推迟 Adapter 拆包的决策标记为由本计划替代。
3. 清理旧模块路径和无用引用，不移动 Agent 资源或删除业务数据。
4. 执行完整单测、模拟 e2e、独立镜像检查和真实 loopback。

## 7. 依赖、镜像与发布

### 7.1 单根依赖和打包

- 根 `pyproject.toml` 使用 setuptools 发现 `src` 下四个包，按需要保留 Fast-Call package data。
- 根 `requirements.txt` 和 `venv` 继续统一第三方依赖；不引入内部包版本解析或 wheel 构建流程。
- 本地继续使用 `PYTHONPATH=src` 或现有 editable 安装。
- 包独立通过 import 边界和镜像内容实现，不要求独立 Python distribution。

### 7.2 Docker

- 构建上下文仍为仓库根目录，强制使用 `.dockerignore`。
- Fast-Call 镜像选择性复制 `fastcall` 与 `fastcall_core`。
- Adapter 镜像选择性复制 `fastcall_adapter` 与 `fastcall_core`。
- Agent Dockerfile 和业务资源保持现状；本计划不顺手修改 GPU 构建。
- 镜像分别执行 import、启动和敏感文件检查，不只验证 Dockerfile 语法。

### 7.3 数据库和发布

- Fast-Call 拥有 Robot Config/Outbound Alembic；Agent 自有 SQL 保持原位。
- 不改 `robot_config_alembic_version`、数据库名、表名和迁移内容。
- 真实 MySQL 测试只能使用独立测试库，禁止指向业务 `collection_agents`。
- 不并行运行新旧 Scheduler、Webhook Worker 或 Adapter；Adapter 继续使用 `Recreate`。
- 本地回退独立 Worker 前必须停止聚合入口；结构和镜像通过 Git/镜像版本回滚，无需数据库回滚。

## 8. 验证矩阵

### 自动验证

- 各包单元测试和静态 import 边界。
- Fast-Call、Adapter、Agent 的 HTTP/WebSocket 路由矩阵。
- Robot Config/Fast-Call 全部单元测试及新增本地入口、Webhook 测试。
- Agent 既有测试；既有 Realtime 错误单独记录，不顺手修复。
- 模拟 outbound e2e；真实 MySQL 在独立测试库可用时执行。
- Fast-Call、Adapter 独立镜像构建、启动和敏感文件检查。
- Adapter 拆分后及 Fast-Call 改名后分别执行真实 loopback，最终再完整验收。

### 核心业务回归

- 同一 Call 最多一次 originate，重启和恢复不补发。
- Task/Contact/Call 状态版本、重试、取消和容量语义不变。
- Admin review 在 Adapter 通过 fake gateway 验证路由和事务，路径、字段、revision、Outbox 不变；控制面代理保留原外部基址并转发认证与 actor。
- `hangup_active=false` 不产生挂断指令；`true` 时即时命令和对账幂等，普通 EventWorker 不会领取挂断指令。
- 即时挂断失败仍返回既有取消成功响应，旧版本重复请求仍冲突；控制面提交后崩溃、命令失败及 Adapter 重启后仍能完成挂断，不重复取消、Outbox 或拨号。
- Session ACK、代际、防重复占用和 pre-ready 恢复不变。
- 终态与 Outbox 同事务，重复事件不重复收敛。
- Webhook 默认只生成三个必需事件；显式开启 result_changed 时原路径正常。
- Webhook 重试保持 event ID/payload，HTTPS、HMAC、DNS/IP 校验不降级。
- Agent 语音信箱、分析和落库不依赖 Fast-Call WebSocket 存活。

### 最终真实验收

1. 只启动本地聚合控制面，不启动独立 Scheduler/Webhook Worker。
2. 使用既有 FreeSWITCH loopback，不修改配置、ESL ACL 或 dialplan。
3. 验证任务创建、自动拨号、真实 Agent 对话、终态、API 查询和 Webhook 204。
4. 核对只创建一个 Call，预期事件各一次，无重复拨号或投递。
5. 停止聚合进程，确认无残留 Scheduler/Webhook 任务和 FreeSWITCH 通道。
6. 检查 `git status --short`，清理测试产物，不回退用户已有修改。

## 9. 风险与控制

| 风险 | 控制措施 |
|---|---|
| core 变成另一个控制面 | allowlist、分离 UOW、静态禁止 API/Scheduler/Webhook/ESL 进入 core |
| Adapter 复制状态机 | 必须依赖 core，复用同一事务测试 |
| 路由拆分丢失能力 | 三服务路由矩阵，review/cancel 单独契约测试 |
| 取消后崩溃留下活动通道 | 持久化指令和 Adapter 周期对账，覆盖双进程恢复 |
| 共享 UOW 带入无关实现 | core 只组合共享叶子仓储，专用仓储归各包 |
| 包改名破坏迁移与静态资源 | 单独机械提交，检查 Alembic、Console、package data |
| 独立镜像带入敏感文件 | `.dockerignore`、选择性 COPY、镜像文件检查 |
| 新旧 Adapter 并行拨号 | 单副本 `Recreate`，禁止滚动双实例 |
| 大爆炸迁移 | 小提交、分阶段回归和中间 loopback |

## 10. 明确不做

- 不采用 `services/`、根 `packages/` 或 `src/packages/`。
- 不拆服务级 pyproject、requirements、venv 或根测试树。
- 不移动 Agent 包、assets、templates、data、SQL 或启动入口。
- 不把 Adapter 或 Agent 合并进 FastAPI 主应用。
- 不在生产合并 Scheduler、Adapter 和 Webhook Worker。
- 不修改 Agent 业务、FreeSWITCH 配置或数据库结构。
- 不删除内部修订、Admin review、Session 生命周期或 Outbox。
- 不为目录独立复制共享领域与事务代码。
- 不顺手修复无关 Realtime 测试或 GPU 构建问题。

## 11. 文档关系与完成定义

本计划确立后，替代 `FASTCALL_LOCAL_CONSOLIDATION_PLAN.md` 中 `services/`、服务级打包和推迟 Adapter 包级拆分的相关决策；handoff 的业务、安全和验证约束继续有效。handoff 中“尚未实施”的历史实现状态以本计划记录的当前进度为准，不回写或篡改 handoff 的历史交接内容。

阶段 0、1 的完成状态以当前代码和已执行测试为准，其余内容仍是计划。只有全部剩余阶段实施、验证和真实 loopback 完成后，才能将目标架构描述为已完成。

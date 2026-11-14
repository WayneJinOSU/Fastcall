# Fast-Call 本地聚合入口与 Webhook 简化实施计划

依据 `docs/FASTCALL_HANDOFF.md` 第一部分制定。本文保留早期实施思路，不是当前状态说明。

> 当前目录、Adapter 拆包和分层方案以 `FASTCALL_SERVICE_RESTRUCTURING_PLAN.md` 为准。本文关于 `services/` 归档、推迟 Adapter 独立包及旧模块路径的内容已被替代。

## 0. 前置变更：包改名 `robot_config_service` → `fastcall`

服务既然合并为 Fast-Call 单一应用，包名与品牌对齐。改名作为独立提交先于下列全部变更完成。

### 0.1 新名与边界

- 新包名：`fastcall`（与 `FASTCALL_*` 环境变量、`fastcall-adapter` / `fastcall-fs` 容器、`deploy/fastcall-outbound-deploy.yaml` 命名一致；Python 包名不带连字符）。
- **必须改**：`src/robot_config_service/` 包目录及内部全部 import；`tests/unit/robot_config_service/` 测试目录及其 import；`pyproject.toml` 的 `package-data` 键；`deploy/robot_config_alembic.ini` 的 `script_location`；`deploy/Dockerfile-robot-config`、`deploy/Dockerfile-fastcall-adapter` 的 COPY 路径与 CMD 模块路径；`deploy/fastcall-outbound-deploy.yaml` 的三条 `python -m` command；`docs/QUICK_START.md`、`docs/ARCHITECTURE.md`、`docs/FASTCALL_OUTBOUND_SERVICE_DESIGN.md` 等文档中的模块路径与启动命令。
- **不改**（配置与数据契约，不随包名迁移）：`ROBOT_CONFIG_*` 环境变量名（`ROBOT_CONFIG_DATABASE_URL` 等，动了会破坏 `.env`、Settings 与部署）；数据库名 `collection_agents` 与全部表名；`src/collection_agents/` 独立服务包；OpenAPI 路由路径；`tests/integration/robot_config_mysql/` 测试目录名。
- **可选、本阶段默认不动**：`deploy/robot_config_alembic.ini`、`Dockerfile-robot-config` 等文件名（文件名不含 `_service` 后缀，保留不影响功能）；FastAPI `title="Robot Config Service"`（Swagger 可见，如需改列为独立小提交）。

### 0.2 机械步骤

1. `git mv src/robot_config_service src/fastcall`，`git mv tests/unit/robot_config_service tests/unit/fastcall`。console 前端在包内（`console/dist` 由 `api/console/static.py` 按文件相对路径解析），随包目录整体移动即可，但 Dockerfile 中的显式 COPY 路径必须同步改。
2. 全量替换字符串 `robot_config_service` → `fastcall`（src、tests、docs、deploy、pyproject.toml）。注意只匹配完整单词，不得波及 `ROBOT_CONFIG_*` / `robot_config_alembic.ini` 等不含 `_service` 的名称。
3. 删除 `__pycache__`、`src/collection_agents.egg-info`，重新安装 editable 包。
4. 单独提交，随后立即回归：104 项单元测试 + `tests.integration.test_outbound_e2e` 全绿，作为后续 handoff 工作的基线。

### 0.3 风险

- 与既有未提交修改（`esl.py` / `media.py` 及测试）叠加：改名提交只含改名，功能修复留在工作区或先行提交，避免一个提交里混入两类变更。
- deploy 文件中的 `python -m robot_config_service.*` command 不改会导致生产镜像起不来——这是机械跟随项，与"生产部署拓扑不变"不冲突。

### 0.4 服务目录边界（为 Docker 部署与服务分离铺路）

前提已核实：`src/collection_agents` 与 `src/robot_config_service` 之间零 import 依赖，代码层服务边界已经干净；Fast-Call 镜像（`Dockerfile-robot-config`、`Dockerfile-fastcall-adapter`）已用选择性 COPY 按服务打包。因此目录调整只做"边界显式化"，不做全量单仓改造。

**边界的两层视角**（文档和目录各归其位）：

- 部署单元（7 个）：Fast-Call API、Scheduler、Webhook Worker、Adapter、Collection Agent、FreeSWITCH、MySQL。其中 Adapter 是独立部署（独立入口 `adapter_main.py`、独立镜像与 k8s Deployment、单副本 `Recreate`），但**不是独立代码包**——它与 API/Scheduler/Webhook 共享 `fastcall` 包的领域模型、数据库与 `create_app()` 工厂，通过 internal HTTP API 交互。
- 代码服务（2 个）：`src/collection_agents`、`src/fastcall`。Adapter 属 `fastcall` 代码服务的一个部署入口，不单独成包。

**本次做**（跟随改名提交或紧随其后的独立提交）：

- `src/` 保持两包平级：`src/collection_agents`（Agent 服务）与 `src/fastcall`（Fast-Call 服务，含 API / Scheduler / Webhook / Adapter 四个入口），与既有约定一致。
- `deploy/` 按代码服务归档为子目录：
  - `deploy/fastcall/`：`Dockerfile-robot-config`、`Dockerfile-fastcall-adapter`、`fastcall-outbound-deploy.yaml`（内含 Scheduler / Adapter / Webhook Worker 三个独立 Deployment）、`robot-config-deploy.yaml`、`robot_config_alembic.ini`；
  - `deploy/collection_agent/`：`Dockerfile`、`Dockerfile-gpu`、`migrations/*.sql`（业务表 SQL，属 Agent 侧）；
  - `deploy/freeswitch/` 原地不动。
  - 同步更新相对引用：`alembic.ini` 的 `%(here)s/../src/...`、docker build 的 `-f` 路径、文档中的构建命令。
- `docs/ARCHITECTURE.md` 补两层边界图：代码服务 2 个、部署单元 7 个；明确 Adapter 是 `fastcall` 代码服务下的独立部署单元，不与其余三个入口合并进程（生产与本地的聚合入口均不含 Adapter）。

**明确推迟**（出现独立发布节奏或独立依赖需求时再启动，避免当下无收益的大迁移）：

- `services/<name>/` 全量单仓布局（每服务独立 pyproject / requirements / venv / tests / CI）。
- **Adapter 包级拆分**：将 Adapter 提为独立包/服务目录，需先提取共享领域层与持久化层（calls/tasks/events/outbox 模型、ORM、UOW）为共享内核包或复制归边，工作量大且与"同节奏演进"现状不匹配。
- Agent 镜像瘦身（`COPY . .` 改选择性 COPY）——与目录迁移解耦，可随时单独做。
- 共享层（requirements、tests、docs）拆分。

**保持不动**：数据库名 `collection_agents` 与表名、DB 迁移 SQL 的内容、`deploy/migrations` 中 SQL 的执行顺序约定、`tests/integration/robot_config_mysql/` 目录名。

## 1. 目标与边界

- 本地开发从 3 个控制面进程（API、Scheduler、Webhook Worker）合并为 1 个聚合进程，降低进程管理成本。
- Webhook 客户接入契约收敛为 3 个事件：`contact.completed`、`task.completed`、`task.cancelled`；`contact.result_changed` 默认关闭，可用配置显式开启。
- 不改变 `scheduler_main.py`、`webhook_main.py`、生产部署、Collection Agent 业务逻辑和 FreeSWITCH 配置。
- 不撤回 `result_revision`、内部修订、Admin review 等既有能力。

## 2. 代码现状核对结论

已核对的关键事实（计划据此设计）：

- `scheduler_main.run_scheduler()` 与 `webhook_main.run_webhook_worker()` 都是自包含协程：各自 `load_dotenv()`、`Settings.from_env()`、`Database.create()` 后进入无限循环，可直接作为 asyncio 任务被第三方组合，无需修改。
- `DialScheduler.run(stop_event=None)` 与 `WebhookWorker.run(stop_event=None)` 已支持 `stop_event` 优雅停止。
- `adapter_main.run_adapter()` 已有成熟的多组件组合先例：`asyncio.wait(FIRST_COMPLETED)` → `server.should_exit = True` → 取消其余任务 → `gather(return_exceptions=True)` → `task.result()` 重抛异常。聚合入口沿用同一写法。
- `contact.result_changed` 有两个生成点，都必须被开关覆盖：
  - 自动修订路径：`application/call_results.py` `FinalizeCallUseCase.execute`（迟到电话事件修订 `unknown` 等，`revision_event=True` 分支）。
  - 人工修订路径：`application/call_checks.py` `CallCheckUseCase.review`（`revision_event=False`，自行入队）。只删人工分支是 handoff 明确禁止的。
- 现有测试 `tests/unit/robot_config_service/test_outbound_dispatch.py::test_late_terminal_event_revising_unknown_emits_result_changed` 断言自动修订产生 `contact.result_changed`，需要改为显式开启开关。
- 仓库现有未提交修改（esl/media 修复与文档）不回退；基线为 104 项单元测试 + e2e + 真实 MySQL 6 项集成测试。

## 3. 变更一：本地聚合入口 `local_main.py`

### 3.1 新增文件

`src/fastcall/local_main.py`（改名后路径；若改名未先行完成则暂用 `src/robot_config_service/local_main.py`，改名提交后机械跟随），仅本地开发使用。

```bash
PYTHONPATH=src venv/bin/python -m robot_config_service.local_main
```

### 3.2 结构

- `load_dotenv()` 后导入并复用 `robot_config_service.main:app`（模块级已 `create_app()`），用 `uvicorn.Server(uvicorn.Config(app, host, port))` 承载 API。绑定地址取 `FASTCALL_API_HOST` / `FASTCALL_API_PORT`，默认 `0.0.0.0:8770`，与 handoff §11 的本地地址表一致。
- 组合三个任务：`server.serve()`、`run_scheduler()`、`run_webhook_worker()`，全部 `asyncio.create_task`。
- 监护逻辑照抄 `adapter_main` 先例：
  - `done, pending = await asyncio.wait(tasks, return_when=FIRST_COMPLETED)`；
  - 任一任务完成（无论正常还是异常）→ `server.should_exit = True`，取消其余任务并 `gather(return_exceptions=True)` 等待清理；
  - 若完成任务的结束不是由进程关闭信号触发，则视为失去拨号或回调能力：记录日志并以非零状态退出（实现为 `task.result()` 重抛异常，或在正常返回时抛 `RuntimeError`）；信号驱动的整体关闭正常退出。
- 数据库资源由各组件现有 `finally: database.close()` 自行清理，聚合层不重复持有连接。
- 入口内不嵌入 Adapter、Collection Agent。

### 3.3 明确不做

- 不修改 `scheduler_main.py`、`webhook_main.py`、`main.py`。
- 不改变生产部署（Kubernetes 仍独立运行 Scheduler、Adapter、Webhook Worker，保留单副本 `Recreate`）。
- 不引入新的进程管理框架。

## 4. 变更二：`FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED` 开关

### 4.1 实现

- 在 `application/webhooks.py`（或同层新建小模块）增加读取函数：

```python
def result_changed_events_enabled():
    return os.getenv("FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED", "").strip().lower() in ("1", "true", "yes", "on")
```

  默认 `false`，与 `webhook_main.py` 现有的 `_csv()` 环境变量风格一致；`.env.example` 增加该变量说明。

- `call_results.py` 的 `revision_event` 分支和 `call_checks.py` review 入队分支各加一个条件：开关关闭时不入队 `contact.result_changed`。两处只加门控，不改 payload、event_id 和 revision 逻辑。

### 4.2 保持不变

- `result_revision` 字段、内部修订写库、API 查询与对账能力不受影响。
- 开关显式开启时，两条原有事件路径行为与现有测试一致。
- 三个必需事件的 payload、稳定 event_id、HMAC-SHA256、HTTPS、DNS/IP 校验、失败重试全部不变。

### 4.3 测试调整

- `test_outbound_dispatch.py` 现有用例改为在显式开启开关的环境下断言（patch 环境变量）。
- 新增默认关闭用例：自动修订路径与人工 review 路径在默认配置下均不产生 `contact.result_changed` 投递记录，但联系人结果修订本身（`final_result_code`、revision）仍生效。
- 新增显式开启用例：覆盖 review 路径，保证兼容行为被锁住。

## 5. 文档更新

| 文档 | 更新内容 |
|---|---|
| `docs/QUICK_START.md` | Fast-Call 本地启动改为聚合入口一条命令；明确禁止与独立 Scheduler / Webhook Worker 同时运行 |
| `docs/FASTCALL_OUTBOUND_SERVICE_DESIGN.md` | Webhook 契约章节只列 3 个必需事件；说明 `contact.result_changed` 默认关闭、开启方式、以及"客户以首次 `contact.completed` 为基线，修订结果需主动查 API" |
| `docs/ARCHITECTURE.md` §11 | 补充本地聚合入口为开发专用形态，生产形态不变 |
| `.env.example` | 增加 `FASTCALL_WEBHOOK_ENABLE_RESULT_CHANGED`（注释说明默认关闭） |

## 6. 新增测试

- `tests/unit/fastcall/test_local_main.py`：
  - 组合启动：三个组件任务均被创建并运行（mock `run_scheduler` / `run_webhook_worker` / uvicorn Server，避免真实网络和数据库）。
  - 失败传播：任一组件任务抛异常 → 其余任务被取消，`run_local_app` 抛出同一异常（非零退出）。
  - 取消清理：`run_local_app` 被取消时，三个任务均被取消且无残留。
  - 正常返回传播：长驻组件提前正常返回 → 停止其余组件并视为失败退出。
- 上述 webhook 开关的默认关闭 / 显式开启用例（见 4.3）。

## 7. 实施顺序

0. 包改名 `robot_config_service` → `fastcall`，独立提交并回归锁定基线；随后（或同一窗口的独立提交）完成 `deploy/` 按服务归档（见 0.4）。
1. 新增 `local_main.py` 与 `test_local_main.py`。
2. 增加 webhook 开关与相关测试，调整既有 `result_changed` 用例。
3. 更新 `.env.example` 与三份文档。
4. 回归：104 项单元测试 + `tests.integration.test_outbound_e2e` + 真实 MySQL 6 项集成测试（独立测试库，禁止指向业务库）。
5. 本地以聚合入口替换三个独立控制面进程，按 `QUICK_START.md` 恢复 FreeSWITCH loopback 验收；需要保留 `/tmp/fastcall_webhook_run.py` 的等效测试解析映射，不关闭 TLS/HMAC/私网白名单。
6. 验收核对：一个 Call、一次 `contact.completed`、一次 `task.completed`、Webhook 204、无重复投递、无残留 FreeSWITCH 通道；关闭聚合进程后无残留任务。

## 8. 风险与对策

- **双跑风险**：聚合入口与独立 Scheduler/Webhook Worker 同时运行会导致重复拨号/重复投递。对策：文档明示 + 验收前先 `lsof` 检查 8770 等端口。
- **uvicorn 信号处理**：`server.serve()` 自行安装 SIGINT/SIGTERM 处理器，与 `asyncio.run` 的组合沿用 `adapter_main` 已验证的写法，不另造信号逻辑。
- **既有未提交修改**：`esl.py` / `media.py` 及其测试的修改不回退、不覆盖；动手前重新 `git status --short`。

# Robot Config Service 服务目录、模块边界与接口分层规划

## 1. 规划目标与范围

本规划以“Robot Config Service 最终方案（直接下发配置版）”为唯一依据，替代旧设计中的以下做法：

- Collection Agent 调用 Resolve。
- 按 `robotCode` 与 `config` 是否同时存在区分新旧模式。
- 在数据库、API 或 Release 中保存 `environment`。
- 跨环境复用版本 ID。
- 通过应用名称判断 Runtime。
- 在配置服务中保存 Runtime Profile 之外的运行表冗余字段。

第一阶段交付边界：

```text
Robot Config Service
  管理 Standard/Realtime 草稿
  校验并发布 Standard
  识别 Realtime，但在远程 Adapter 完成前禁止发布

外呼系统（外部仓库）
  每次通话调用 Resolve
  直接向 Collection Agent 下发完整配置、变量和版本信息

Collection Agent（本仓库）
  不持有 Resolve Token
  不调用 Robot Config Service
  执行收到的 robot-app/v1 或旧格式完整配置

React 管理控制台（与 Robot Config Service 同进程部署）
  通过同源 Console BFF 访问管理用例，不直接接触两个内部 Token
```

## 2. 服务目录规划

```text
src/fastcall/
├── __init__.py
├── main.py                         # ASGI 入口，只做应用装配
├── settings.py                     # APP_ENV、MySQL、双 Token 配置及启动校验
│
├── api/                            # HTTP 协议层
│   ├── __init__.py
│   ├── routes.py                   # 路由汇总，不包含业务逻辑
│   ├── management.py               # 应用、草稿、校验、发布、版本、回滚 API
│   ├── resolve.py                  # 外呼系统 Resolve API
│   ├── health.py                   # up / ready
│   ├── dependencies.py             # 双 Token 认证、Actor、UoW 依赖注入
│   ├── requests.py                 # 请求 DTO，禁止未知请求字段
│   ├── responses.py                # 响应 DTO 与统一响应结构
│   └── errors.py                   # 领域错误到 HTTP 状态码的映射
│
├── application/                    # 用例与事务编排层
│   ├── __init__.py
│   ├── robot_apps.py               # 创建、查询、保存草稿、启停
│   ├── validation.py               # 草稿校验用例
│   ├── publication.py              # 发布、幂等、版本查询、Diff、回滚
│   ├── resolve.py                  # 当前 Release 解析用例
│   ├── dto.py                      # 用例输入和输出对象
│   └── ports.py                    # UnitOfWork、Repository 抽象接口
│
├── domain/                         # 纯业务规则层
│   ├── __init__.py
│   ├── contract.py                 # robot-app/v1 契约与配置块规则
│   ├── runtime.py                  # RuntimeProfile、RuntimeResolution 值对象
│   ├── validation.py               # 校验编排及结构化 ValidationReport
│   ├── variables.py                # 变量定义、模板引用和敏感信息规则
│   ├── hashing.py                  # 规范化 JSON 与 SHA-256
│   ├── diff.py                     # 不可变版本内容 Diff
│   └── errors.py                   # 稳定的业务错误码
│
├── capabilities/                   # 服务端静态能力目录
│   ├── __init__.py
│   ├── catalog.json                # 唯一能力数据源，含 catalog_version
│   ├── loader.py                   # 加载、结构自检、只读访问
│   └── runtime_resolver.py         # provider + model 精确匹配
│
├── infrastructure/                 # 技术实现层
│   ├── __init__.py
│   ├── database.py                 # Engine、Session、MySQL/Schema 可用性检查
│   ├── orm.py                      # 四张表的 SQLAlchemy 映射
│   ├── repositories.py             # MySQL Repository 实现
│   └── unit_of_work.py             # 事务、行锁、提交和回滚
│
└── migrations/
    ├── env.py                      # 独立 Alembic 配置
    └── versions/
        └── 0001_initial.py         # 表、约束、索引和不可变 Trigger

tests/
├── unit/
│   └── fastcall/       # 契约、能力、哈希、用例和 API 测试
└── integration/
    └── robot_config_mysql/         # 真实 MySQL 并发、事务和 Trigger 测试

deploy/
├── migrations/                     # Collection Agent 原有 SQL 迁移，保持独立
├── Dockerfile-robot-config         # Robot Config Service 独立镜像
├── robot-config-deploy.yaml        # 独立 Deployment/Service/Probe
└── robot_config_alembic.ini        # 独立版本表配置
```

### 目录约束

- `fastcall` 不导入 `collection_agents`。
- Collection Agent 运行链路不导入配置服务的 API、数据库或 Repository。
- 两个服务通过 JSON 契约交互，不通过共享 Python 运行对象交互。
- 哈希和契约使用跨服务一致性测试，避免因复制实现而产生规范漂移。
- 新服务不与 Collection Agent 共用 Alembic 版本表、启动 Schema 检查或存储模块。

## 3. 分层与依赖规则

允许的依赖方向：

```text
main
  → api
  → application
  → domain

main / api 的依赖装配
  → infrastructure
  → application.ports

application
  → domain
  → capabilities 的只读抽象
  → application.ports

infrastructure
  → application.ports
  → domain 的纯值对象

capabilities
  → domain.runtime / domain.errors

domain
  → Python 标准库
```

禁止的依赖：

- `domain` 导入 FastAPI、Pydantic、SQLAlchemy、环境变量或文件系统。
- `application` 编写 SQL、操作 ORM 对象或直接读取 HTTP Request。
- `api` 直接调用 Repository、拼接业务响应或决定发布结果。
- `infrastructure` 决定 Runtime、语言音色合法性或配置哈希。
- 能力目录在 Resolve 时重新校验、替换或修补历史版本。

### 各层职责

| 层 | 负责 | 不负责 |
| --- | --- | --- |
| API | Token、Actor、请求 Schema、状态码、响应序列化 | 发布规则、SQL、哈希 |
| Application | 用例流程、事务边界、锁顺序、幂等和审计编排 | 字段级业务校验 |
| Domain | 契约、Runtime 规则、校验、哈希、Diff、错误码 | 数据库和 HTTP |
| Capabilities | 静态选项、精确 Runtime 解析、启禁用状态 | 供应商 API 调用 |
| Infrastructure | MySQL 持久化、行锁、事务、Trigger、Schema 检查 | 业务决策 |

## 4. 核心抽象接口

以下接口表示规划契约，不要求按示例逐字实现。

### 4.1 UnitOfWork 与 Repository

```python
class RobotConfigUnitOfWork(Protocol):
    apps: RobotAppRepository
    versions: RobotAppVersionRepository
    releases: RobotReleaseRepository
    audits: AuditLogRepository

    def commit(self) -> None: ...
    def rollback(self) -> None: ...
```

```python
class RobotAppRepository(Protocol):
    def get(self, app_id: str) -> RobotAppRecord | None: ...
    def get_for_update(self, app_id: str) -> RobotAppRecord | None: ...
    def get_by_code(self, app_code: str) -> RobotAppRecord | None: ...
    def add(self, record: RobotAppRecord) -> None: ...

class RobotAppVersionRepository(Protocol):
    def get(self, app_id: str, version_id: str) -> RobotVersionRecord | None: ...
    def get_by_hash(self, app_id: str, config_hash: str) -> RobotVersionRecord | None: ...
    def next_version_no(self, app_id: str) -> int: ...
    def add(self, record: RobotVersionRecord) -> None: ...

class RobotReleaseRepository(Protocol):
    def get(self, app_id: str) -> RobotReleaseRecord | None: ...
    def get_for_update(self, app_id: str) -> RobotReleaseRecord | None: ...
    def save(self, record: RobotReleaseRecord) -> None: ...

class AuditLogRepository(Protocol):
    def append(self, entry: AuditEntry) -> None: ...
```

Record 是持久化边界对象，不进入 HTTP 层。Repository 不提供版本和审计的更新、删除接口；数据库 Trigger 是第二道强制约束。

### 4.2 领域接口

```python
resolve_runtime(provider: str, model: str, catalog: Catalog) -> RuntimeResolution
validate_app_config(config: dict, catalog: Catalog) -> ValidationReport
calculate_config_hash(config: dict, resolution: RuntimeResolution) -> str
diff_versions(source: dict, target: dict) -> ConfigDiff
```

`ValidationReport` 至少包含：

```text
valid
errors[]
warnings[]
catalog_version
runtime_resolution（精确解析成功后）
config_hash（完整校验成功后）
```

错误项统一为 `code + path + message`，HTTP 层不重新解释字段错误。

### 4.3 应用用例接口

```python
create_app(command, actor) -> RobotAppView
update_draft(command, actor) -> RobotAppView
set_enabled(command, actor) -> RobotAppView
validate_draft(command) -> ValidationReport
publish(command, actor) -> RobotVersionView
rollback(command, actor) -> RobotVersionView
resolve(robot_code) -> ResolvedRuntimeView
```

所有写用例在一个 UnitOfWork 中完成。Resolve 是只读用例，不创建审计、不更新 Release、不访问最新能力目录。

## 5. HTTP 接口分层

### 5.1 管理接口

| 路由 | Application 用例 | 事务 |
| --- | --- | --- |
| `GET /api/v1/capabilities/runtime-profiles` | 读取静态 Catalog | 无数据库事务 |
| `GET /api/v1/robot-apps` | 查询应用及当前 Release 摘要 | 只读 |
| `POST /api/v1/robot-apps` | 创建应用和初始草稿 | 写事务 + 审计 |
| `GET /api/v1/robot-apps/{app_id}` | 查询完整草稿 | 只读 |
| `PUT .../draft` | 乐观锁保存完整草稿 | 锁应用行 + 审计 |
| `PUT .../enabled` | 启停应用 | 锁应用行 + 审计 |
| `POST .../validate` | 校验当前 revision 草稿 | 只读 |
| `POST .../publish` | 校验、版本幂等、更新 Release | 单写事务 + 审计 |
| `GET .../versions` | 版本摘要列表 | 只读 |
| `GET .../versions/{version_id}` | 不可变版本完整内容 | 只读 |
| `GET .../diff?from=1&to=2` | 按版本号比较内容 | 只读 |
| `POST .../rollback` | 修改当前 Release 指向 | 单写事务 + 审计 |

### 5.2 Resolve 接口

```http
POST /internal/v1/runtime/resolve
Authorization: Bearer <ROBOT_CONFIG_RESOLVE_TOKEN>
```

API 层只负责 Token 和请求字段；Application 依次读取应用、启停状态、Release 和版本。Robot 不存在、未发布、已停用或 Release 数据异常均返回统一 `RESOLVE_FAILED`，不泄露存在性。

### 5.3 健康检查

- `up`：进程和 HTTP 服务可用，不表示数据库就绪。
- `ready`：启动校验已完成，且当前可执行 `SELECT 1`。
- 启动时依次检查 `APP_ENV`、双 Token、MySQL 方言、连接、必需表和 Alembic revision；失败则拒绝启动。

## 6. 关键事务与一致性边界

### 6.1 保存草稿

1. 锁定应用行。
2. 比较 `draft_revision`。
3. 只检查草稿基本结构和 `app.code/name` 与运行表一致。
4. 保存完整 JSON 并递增 revision。
5. 追加审计后提交。

草稿保存允许未完成配置和 Realtime 配置；完整校验属于 Validate/Publish。

### 6.2 发布

1. 锁定应用行，串行化同一应用的并发发布。
2. 检查 revision。
3. 重新精确解析 Runtime，并执行完整校验。
4. 重新计算并核对哈希。
5. 按 `(app_id, config_hash)` 查询版本。
6. 不存在时分配 `version_no` 并创建不可变版本。
7. 创建或更新该应用唯一 Release。
8. 追加审计，一次性提交。

任一步失败均回滚版本、Release 和审计。唯一约束作为并发兜底，不能只依赖应用层查询。

### 6.3 回滚

- 锁应用和 Release，确认目标版本属于当前应用。
- 只更新 Release，不修改草稿、不创建版本。
- 当前版本重复回滚直接返回，不产生无意义审计。
- 不根据当前能力目录校验历史版本。

### 6.4 Resolve

- 读取版本上固定的 `runtime_engine/runtime_adapter/runtime_catalog_version`。
- 原样返回不可变 `content_json`。
- 即使能力已禁用或 deprecated，也允许历史版本 Resolve。

## 7. Collection Agent 接口边界

WebSocket 收到 `start` 后按 `data.config.schema_version` 判断契约：

- `robot-app/v1`：进入新契约编译和严格变量校验。
- 其他完整配置：进入旧链路，保留原有默认和兼容行为。
- `robotCode` 仅用于标识和追溯，不参与模式选择。

新契约编译流程：

```text
校验版本元数据与 configHash
→ 校验 data.variables
→ 应用可选变量默认值
→ 渲染 Prompt 和完整 Flow
→ 检查残留 {{...}}
→ 无损编译为现有运行配置
→ 创建 CallConfigManager
→ 创建 Standard Pipeline
```

编译失败、变量失败、配置与本地部署资源不匹配均不得创建 Pipeline。

严格模式通过显式运行上下文传递，不通过全局环境变量切换：

```text
managed=True
runtime_engine
runtime_adapter
runtime_catalog_version
robot_version_id
config_hash
```

Provider 工厂在 `managed=True` 时禁止默认 Provider、默认模型、默认语言和默认音色；旧请求仍调用原兼容路径。

## 8. 配置、审计与日志边界

Robot Config Service 环境只包含：

```text
APP_ENV
ROBOT_CONFIG_DATABASE_URL
ROBOT_CONFIG_MANAGEMENT_TOKEN
ROBOT_CONFIG_RESOLVE_TOKEN
```

Collection Agent 环境只包含供应商密钥、连接信息和自身服务配置，不增加 Resolve 地址或 Token。

审计只保存操作标识、对象、版本 ID、哈希和非敏感变更摘要。以下内容不得进入审计和普通日志：

- Token、API Key。
- 完整 Prompt。
- 真实通话变量值。
- 被叫号码及敏感通话内容。

通话追溯信息通过现有 `call_record` 和结构化技术日志记录；配置或变量错误只记录错误码与字段路径。

## 9. 测试分层规划

### 9.1 纯单元测试

不依赖 MySQL，覆盖：

- 契约字段、Provider 组合、语言音色和变量引用。
- Runtime 精确匹配及未知组合拒绝。
- 哈希规范化、数组顺序和 Runtime Profile 敏感性。
- 变量类型、必填、默认值和残留模板。
- 新旧配置识别、完整 Flow 无损编译和严格消费。
- API 认证、统一错误结构和 Resolve 失败不泄露存在性。

### 9.2 应用用例测试

使用测试 Repository/UoW，覆盖：

- 草稿乐观锁。
- 相同内容发布幂等。
- 回滚不修改草稿。
- 发布只影响当前 Release。
- 历史版本保留原 Runtime Profile。
- 用例失败时调用回滚而不是提交。

纯单元测试不能替代 MySQL 的并发与 Trigger 验收。

### 9.3 真实 MySQL 集成测试

必须覆盖：

- 非 MySQL、数据库不可用、Schema/revision 不匹配时拒绝启动。
- 并发发布只产生一个 `(app_id, config_hash)` 版本。
- 发布中途失败时版本、Release、审计全部回滚。
- Trigger 禁止版本和审计 UPDATE/DELETE。
- 禁用能力后历史版本仍可 Resolve。
- 四张表及 Alembic 版本表互不影响 Collection Agent 数据。

### 9.4 端到端验收

在外部外呼系统仓库或联调环境验证：

1. 每次通话都重新 Resolve。
2. Resolve 失败不发起 Bot 通话，不使用旧配置。
3. 下发的配置、版本 ID、哈希与 Resolve 响应一致。
4. 发布和回滚只影响新通话。
5. Standard 上下文轮次、欢迎、无响应、结束、挂断、语音信箱、打断和最大通话时长实际生效。
6. 完成一次真实 Standard WebSocket 通话。

## 10. 实施顺序与 PR 边界

| PR | 目录与模块 | 完成标准 |
| --- | --- | --- |
| PR1 | `settings`、`infrastructure`、`migrations`、健康检查和最小 ASGI 装配 | 真实 MySQL 迁移及启动检查通过 |
| PR2 | `capabilities`、`domain`、`application/validation` | 校验、Runtime、哈希和不可变版本规则有测试 |
| PR3 | `application/robot_apps`、`publication`、管理 API | 草稿到发布、Diff、回滚、审计闭环通过 |
| PR4 | `application/resolve`、Resolve API、双 Token、独立部署 | 环境隔离和统一 Resolve 失败通过 |
| PR5 | `collection_agents/services/robot_app` 与 Standard 严格运行链路 | 直接下发、变量、适配、追溯和行为测试通过 |
| PR6 | `console` React 页面、Console BFF、FastAPI 静态托管和首个 Robot 迁移 | 单 FastAPI 进程下三页面管理闭环及首个应用上线 |

每个 PR 保持可审查和可独立验收，不在 PR1 提前实现发布、Resolve 或通话适配。Realtime 远程 Adapter 继续作为后续独立 PR。

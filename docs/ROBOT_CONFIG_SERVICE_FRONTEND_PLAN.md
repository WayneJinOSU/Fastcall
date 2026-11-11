# Robot Config Service React 管理前端规划

## 1. 目标与约束

为 Robot Config Service 增加 React 管理控制台，采用 Dify 式简约、克制、工具型界面风格。

硬性约束：

- 使用 React，不引入 Next.js 等需要独立 Node 服务的框架。
- 前端与 FastAPI 同进程、同端口、同镜像部署。
- 生产环境不运行 Vite/Node 服务。
- 浏览器不持有 `ROBOT_CONFIG_MANAGEMENT_TOKEN` 或 `ROBOT_CONFIG_RESOLVE_TOKEN`。
- 现有 `/api/v1` 管理 API 和 `/internal/v1/runtime/resolve` 保持 Token 认证，不为浏览器放开。
- 第一阶段只支持 Standard 编辑和发布；Realtime 只读展示或明确提示不可发布，不提供“看似可用”的编辑入口。
- 完整 Flow 必须无损编辑，不能只保存页面展示的少数字段。

## 2. 总体结构

```text
浏览器 React SPA
  │
  │ 同源 /console/api/v1，HttpOnly Session Cookie + CSRF
  ▼
FastAPI Console BFF
  │ 直接调用 Application Use Cases
  │ 不通过 HTTP 回环，不使用 Management Token
  ▼
Robot Config Service Application / Domain / MySQL

浏览器静态资源
  │
  ▼
FastAPI StaticFiles + SPA fallback
```

Console BFF 是面向浏览器的管理后端。现有 Token 管理 API 保留给受信任的管理后端或自动化调用，两套入口共享 Application 层，不复制业务逻辑。

## 3. 前端与静态资源目录

前端源码只属于 `robot_config_service`，不放入 `collection_agents`。

```text
src/robot_config_service/
├── console/                        # React 源码和构建配置
│   ├── package.json
│   ├── package-lock.json
│   ├── tsconfig.json
│   ├── vite.config.ts              # base=/console/，开发代理只用于本机
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx                # React 入口
│   │   ├── App.tsx                 # 路由和全局布局
│   │   ├── api/
│   │   │   ├── client.ts           # fetch、Cookie、CSRF、统一错误
│   │   │   └── types.ts            # 与后端 DTO 对应的 TypeScript 类型
│   │   ├── components/             # 通用组件
│   │   │   ├── AppShell.tsx
│   │   │   ├── PageHeader.tsx
│   │   │   ├── StatusBadge.tsx
│   │   │   ├── ConfirmDialog.tsx
│   │   │   ├── EmptyState.tsx
│   │   │   └── FormField.tsx
│   │   ├── features/
│   │   │   ├── apps/
│   │   │   │   ├── AppListPage.tsx
│   │   │   │   ├── CreateAppDialog.tsx
│   │   │   │   └── api.ts
│   │   │   └── editor/
│   │   │       ├── AppEditorPage.tsx       # 草稿编辑、校验和发布
│   │   │       ├── HistoryDrawer.tsx       # 历史版本列表
│   │   │       ├── PromptSection.tsx
│   │   │       ├── VariablesSection.tsx
│   │   │       ├── RuntimeSection.tsx
│   │   │       ├── FlowEditor.tsx
│   │   │       ├── CallPolicySection.tsx
│   │   │       └── api.ts
│   │   ├── hooks/
│   │   │   └── useUnsavedChanges.ts
│   │   ├── styles/
│   │   │   ├── tokens.css
│   │   │   ├── global.css
│   │   │   └── components.css
│   │   └── test/
│   │       └── setup.ts
│   └── dist/                       # 构建产物，不手工修改
│
├── api/
│   └── console/                    # FastAPI 浏览器 BFF
│       ├── routes.py
│       ├── auth.py
│       ├── requests.py
│       └── static.py               # 静态资源、SPA fallback、缓存头
│
└── main.py                         # 注册 Console API 和静态页面
```

依赖选择：

- React + TypeScript + Vite。
- React Router 只负责应用列表和应用编辑两个核心页面路由。
- 原生 `fetch` 和 React hooks 管理请求，MVP 不引入大型状态管理库。
- 原生 CSS + CSS Variables 实现风格，不引入重型 UI 组件库。
- 图标统一使用轻量 SVG 图标库，不使用 emoji 作为功能图标。

`node_modules`、`dist`、前端测试覆盖率文件不提交。Docker 构建阶段生成 `dist`。

## 4. 页面与路由规划

### 4.1 应用列表 `/console/apps`

页面内容：

- 顶部标题“Robot 应用”和主按钮“新建应用”。
- 搜索框按 `app_code` 或名称过滤，状态筛选只使用 `enabled` 和是否已发布。
- 桌面端使用每排 4 张的紧凑卡片网格，窄屏逐级降为 2 张或 1 张；每张卡片只展示列表接口已有的名称、应用代码、Release 摘要、草稿 revision、启停状态和更新时间。
- 概览仅统计总数、已启用、已停用和未发布数量，全部由当前列表响应在客户端派生。
- 列表页不展示 Runtime、模型、语言、标签、应用描述或版本号；这些信息需要额外请求，不属于列表管理必需信息。
- 不提供工作区切换、环境标识、全局发布记录、审计日志、设置、Resolve 延迟或其他无对应接口的入口。
- 启停开关需要二次确认；停用提示“仅影响新的 Resolve，不影响进行中的通话”。“已启用”不得写成“运行中”。
- 每张卡片只提供“编辑配置”入口，历史版本和发布在编辑器内完成。
- 空状态、加载状态、请求失败状态均有明确界面。

新建应用只填写 `name`。服务端生成 `robot_<32 位 UUID 十六进制>` 作为稳定外部 `app_code`，创建后只读且不可修改；数据库唯一约束兜底。创建后进入编辑页完善草稿，不要求在列表页填写完整 JSON。

### 4.2 应用编辑与版本管理 `/console/apps/:appId/edit`

采用左侧分段导航、右侧表单的工具型布局：

1. 基本信息：只读 `app_code`，名称通过草稿中的 `app.name` 展示。
2. Prompt：多行编辑器，显示已定义变量，支持插入 `{{variable}}`。
3. 变量：表格编辑 `key/type/required/sensitive/max_length/default`。
4. Runtime：模型、ASR/TTS 语言、音色和语速；选项来自只读能力目录。采样率、编码、interim_results、endpointing 和音量等固定或工程参数不在页面开放。音色显示目录中的 `name`，`voice_id` 仅作为底层值保存。
5. Flow：欢迎、无响应、结束、语音信箱的结构化编辑，同时提供完整 JSON 高级编辑。
6. 通话策略：打断开关和最大通话时长。

编辑与发布规则：

- 页面加载后保存原始草稿和 `draft_revision`。
- 保存时提交完整配置和当前 revision，不做局部字段 PATCH。
- `409 DRAFT_REVISION_CONFLICT` 时禁止自动覆盖，提供“查看最新草稿”和“重新基于最新版本编辑”。
- 离开未保存页面时显示确认。
- 模型改变后只更新草稿中的 `llm.provider/model`；Runtime 由服务端派生，页面不能提交 `runtime_engine/adapter`。
- 能力禁用项不出现在新配置选项中；历史值只读展示并提示，不允许另存为新发布版本。
- Flow 结构化表单与 JSON 高级编辑使用同一份草稿状态，切换时不丢失未知子字段。
- Realtime 模型选中后显示“远程 Adapter 尚未启用”，允许保存草稿，但发布按钮不可用。
- 顶部操作区只有“历史版本”“保存草稿”“发布草稿”。发布前调用 Validate，并携带校验返回的 revision 与 config_hash。
- 重复发布由后端幂等返回同一版本，页面提示“内容未变化，沿用已有版本”。

历史版本交互：

- 点击“历史版本”打开右侧抽屉，只显示版本号、发布时间、操作人和当前 Release 标识。
- 点击历史版本后关闭抽屉，通过 `GET versions/{version_id}` 在同一编辑器中加载完整不可变配置。
- 历史配置使用与草稿完全相同的分段和表单，全部只读；顶部显示“正在查看历史版本”“返回当前草稿”和“回滚到此版本”。
- 查看历史版本不修改草稿或 revision；返回时恢复进入历史视图前的草稿编辑状态。
- 回滚需要二次确认，只影响新通话；成功后保持当前草稿不变。
- 不提供独立版本页面、版本详情页面或默认 Diff 区域。Diff API 保留，但不进入第一阶段主交互。

登录页是辅助页面，不计入两个核心管理页面。

## 5. Dify 式简约视觉规范

设计关键词：白色工作区、浅灰页面底色、细分隔线、克制阴影、清晰层级、紧凑表格、少量蓝色强调。

建议 Token：

```text
页面背景       #F7F8FA
卡片/侧栏      #FFFFFF
主文字         #1F2937
次文字         #6B7280
边框           #E5E7EB
主色           #155EEF
主色浅背景     #EFF4FF
成功           #067647
警告           #B54708
错误           #B42318
```

- 字体使用系统中文/英文无衬线字体。
- 圆角以 6–8px 为主，不使用大面积渐变和重阴影。
- 内容最大宽度约 1440px，表单列宽稳定，长 Prompt 和 JSON 使用等宽字体。
- 表格操作固定在右侧，状态统一使用 Badge。
- 危险操作仅回滚确认和停用提示使用红色，普通页面保持低饱和。
- 支持键盘焦点、表单 label、对话框焦点管理和足够对比度。
- 桌面优先，窄屏下侧栏折叠、表格横向滚动，不隐藏关键操作。

## 6. Console BFF 与认证规划

### 6.1 浏览器接口

新增同源接口：

```text
POST /console/api/v1/login
POST /console/api/v1/logout
GET  /console/api/v1/session

GET  /console/api/v1/capabilities/runtime-profiles
GET  /console/api/v1/robot-apps
POST /console/api/v1/robot-apps
GET  /console/api/v1/robot-apps/{app_id}
PUT  /console/api/v1/robot-apps/{app_id}/draft
PUT  /console/api/v1/robot-apps/{app_id}/enabled
POST /console/api/v1/robot-apps/{app_id}/validate
POST /console/api/v1/robot-apps/{app_id}/publish
GET  /console/api/v1/robot-apps/{app_id}/versions
GET  /console/api/v1/robot-apps/{app_id}/versions/{version_id}
GET  /console/api/v1/robot-apps/{app_id}/diff
POST /console/api/v1/robot-apps/{app_id}/rollback
```

BFF 直接构造 `RobotAppsUseCase / PublicationUseCase / ResolveUseCase` 等已有用例。Resolve 不暴露给浏览器，因此 Console 不提供 Resolve 路由。

### 6.2 会话认证

浏览器认证与内部 Token 完全分离：

- 登录成功后，FastAPI 设置签名、带过期时间的 `HttpOnly` Session Cookie。
- Cookie 使用 `SameSite=Lax`；prod 必须 `Secure`。
- 写请求要求自定义 CSRF Header，登录接口除外。
- `actor` 从服务端 Session 取得，不能由浏览器通过 `X-Actor-Id` 冒充。
- Session 中不保存 Management Token、Resolve Token 或配置草稿。
- React 不把认证信息写入 `localStorage/sessionStorage`。
- 未认证返回 401，CSRF 失败返回 403；前端收到 401 跳转登录页。

当前本地环境提供单管理员登录适配器：

```text
ROBOT_CONFIG_CONSOLE_ADMIN_ACTOR_ID
ROBOT_CONFIG_CONSOLE_ADMIN_PASSWORD_HASH
ROBOT_CONFIG_CONSOLE_SESSION_SECRET
```

只保存密码哈希，不保存明文密码。后续接入企业 SSO 时替换 `ConsoleAuthenticator`，页面和 Application 用例不变。

## 7. FastAPI 静态托管

路由顺序：

1. 现有健康检查、Token API、Resolve API。
2. `/console/api/v1` BFF。
3. `/console/assets` 静态资源。
4. `/console/{path:path}` SPA fallback。
5. `/` 和 `/console` 重定向到 `/console/apps`。

要求：

- API 404 必须返回 JSON，不能被 SPA fallback 捕获并返回 HTML。
- Vite 构建使用 `base=/console/`，刷新深层路由仍返回 `index.html`。
- 带内容哈希的 JS/CSS 使用长期缓存；`index.html` 使用 `no-cache`。
- 不启用 Service Worker，避免配置管理页面使用旧缓存。
- FastAPI 启动时检查生产构建存在；开发模式可只启动 API，由 Vite 开发服务器代理 `/console/api`。

## 8. 单镜像构建与部署

`Dockerfile-robot-config` 改为多阶段构建：

```text
node 阶段
  复制 console/package*.json
  npm ci
  复制 console 源码
  npm run build

python 阶段
  安装 Python 依赖
  复制后端源码
  从 node 阶段复制 console/dist
  启动同一个 uvicorn 进程
```

最终结果：

- 只暴露 `8766`。
- 不包含 Node 运行时。
- 不增加前端 Deployment、Service 或 Ingress。
- 现有 `/state/up`、`/state/ready`、MySQL 和双 Token 配置不变。
- Console Session 配置通过现有 Secret 注入。
- 多副本使用相同的 Session Secret，保证请求落到任意副本均可认证。

## 9. 状态、错误与安全处理

API Client 统一处理：

- 401：清理页面敏感状态并跳转登录。
- 403：提示会话校验失败，允许刷新后重试。
- 409：草稿冲突或哈希变化，不自动重试写操作。
- 422：展示结构化字段错误和发布校验报告。
- 网络/500：保留用户未保存草稿，不清空表单。

安全要求：

- 前端源码、Source Map、接口响应和浏览器存储中不出现内部 Token。
- 生产构建关闭 Source Map，或确保 Source Map 不公开。
- 审计中的 Actor 来自 Session。
- Prompt 和 Flow 文本按纯文本渲染，不使用 `dangerouslySetInnerHTML`。
- 版本 ID 和哈希默认显示摘要，复制时提供完整值。
- 停用、发布、回滚不自动重复提交；请求进行中禁用对应按钮。

## 10. 测试与验收

### 10.1 前端单元和组件测试

- 列表加载、空状态、错误状态。
- 新建应用和草稿表单字段更新。
- 变量增删改、Prompt 变量插入。
- Flow 结构化编辑与 JSON 切换不丢字段。
- 未保存离开提示、revision 冲突处理。
- 校验错误定位、发布禁用、回滚确认。
- API Client 的 Cookie、CSRF 和错误处理。

### 10.2 FastAPI 集成测试

- 未登录不能调用 Console API。
- Session、CSRF 和服务端 Actor 生效。
- 浏览器接口不需要也不接受内部 Token 代替会话。
- Console 与 Token API 调用相同 Application 用例，结果一致。
- 首页、深层 SPA 路由和静态资源返回正确。
- API 404 不返回 HTML，缓存头正确。
- 构建产物缺失时生产启动给出明确错误。

### 10.3 浏览器端到端验收

使用真实 FastAPI + MySQL + React 构建产物完成：

1. 登录并创建一个 Standard 应用。
2. 编辑 Prompt、变量、模型、语言、音色、完整 Flow 和通话策略。
3. 保存草稿并验证冲突提示。
4. 在编辑器内校验并发布，看到新的当前 Release。
5. 打开历史版本抽屉，在第一版本中只读查看完整配置。
6. 回滚第一版本并返回当前草稿，确认草稿内容和未保存状态未被覆盖。
7. 停用后 Resolve 失败，启用后恢复。
8. 刷新深层路由和重启浏览器后会话行为符合预期。
9. 浏览器网络请求、存储和构建资源中不存在两个内部 Token。
10. 只启动 FastAPI 一个进程即可完成全部操作。

## 11. 实施拆分

建议在 PR6 内按以下顺序实施：

1. **Console BFF 与认证**：先打通浏览器安全访问 Application 用例。
2. **React 骨架与视觉系统**：Vite、路由、AppShell、API Client、设计 Token。
3. **应用列表和新建**：完成第一个只读/创建闭环。
4. **完整草稿编辑器**：Prompt、变量、能力选项、Flow 和通话策略。
5. **编辑器内版本管理**：实现历史抽屉、同窗口只读历史配置、发布与回滚。
6. **FastAPI 静态托管和单镜像构建**：移除生产环境对 Node 的依赖。
7. **组件测试、FastAPI 集成测试和浏览器验收**。

完成标准是：单 FastAPI 进程、单端口、单镜像下，应用列表和统一编辑器即可完成从创建草稿到发布、查看历史版本和回滚的完整流程，且浏览器始终不持有内部 Token。

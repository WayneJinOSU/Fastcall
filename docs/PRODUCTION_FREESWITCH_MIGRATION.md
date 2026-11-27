# FreeSWITCH 生产线路改造执行计划

> 状态：代码与配置改造、ARM64 官方包镜像构建及镜像 smoke 已完成；通过原生 `send_silence_when_idle=-1` 解决 `park()` 缺少下行写帧的问题，真实 SIP App 双向 AI 与挂断清理已验收。真实运营商验收未完成，生产 NO-GO。本文保留一次性改造任务与验收口径，当前实现和验证事实以根 README、`docs/SYSTEM_ARCHITECTURE.md` 及相关模块文档为准。
> 执行过程中必须把已实现的当前事实同步到根 README、系统总览和相关模块文档；不得把计划、构建成功或配置加载写成真实线路验收通过。

## 1. 执行者入口

在新窗口中按以下要求工作：

1. 先完整阅读本文，再阅读 `README.md`、`docs/SYSTEM_ARCHITECTURE.md`、`docs/modules/FASTCALL_ADAPTER.md` 及本文列出的实际文件。
2. 先记录 `git status --short`、目标测试和全量单元测试基线；保留用户已有修改，不覆盖、不提交、不回退无关文件。
3. 按阶段实施和验收，不一次性同时修改 Dockerfile、Sofia、NAT、Adapter、Agent 和数据库。
4. 遇到本文的 BLOCKER 或关键契约不清时停止扩大修改，报告阻塞；不得自行缩小验收口径后宣称生产可上线。
5. 不读取、打印或提交 `.env`、`deploy/.env.compose`、Provider 密钥、SignalWire Token、运营商密码或完整电话号码。
6. 未经用户明确要求，不执行 git commit/push/reset/rebase，不对真实生产线路发起呼叫。

## 2. 最终决策

本次只建设一种交付方式：**Linux Docker Compose + 官方预编译 FreeSWITCH 包的薄扩展镜像**。不再同时建设 Linux 主机直装、systemd 或第二套部署脚本。

目标运行链路：

```text
运营商 SIP Trunk
    │ SIP UDP/TCP + RTP，优先 PCMU/PCMA
    ▼
官方预编译 FreeSWITCH + Sofia
    │
    ├─ ESL ───────────────────────→ 当前 Fastcall Adapter
    │                                      │
    └─ 未打补丁 mod_audio_stream            │
              │ PCM16LE mono 8kHz WebSocket │
              └───────────────────────────→ 当前 Adapter
                                                 │
                                                 ▼
                                       当前 Collection Agent
                                                 │
                                                 ▼
                                          STT / LLM / TTS
```

明确决策：

- FreeSWITCH、Sofia SIP 和官方依赖来自 SignalWire 官方预编译包，不从源码编译或替换核心。
- `mod_audio_stream` 继续使用当前 `AlchemillaHQ/mod-audio-stream` 固定 commit，源码不打补丁，可在独立构建阶段针对完全相同的官方包版本和 ABI 编译。
- 生产业务呼叫只接运营商 Trunk。SIP App/WSS 常开作为内部测试能力，不是生产用户入口。
- Mock Call 功能已完整移除；历史 `origin=mock_call` 数据、存量数据库字段、旧草稿取消和拒绝拨号保护保留。本生产改造不再维护 Mock 配置或媒体链路。
- direct-playback C++ 补丁和 Mock dialplan 已删除。
- 保留当前 Adapter/Agent 媒体协议和业务状态机，不引入独立 RTP 媒体网关。

## 3. 目标与不变量

### 3.1 必须实现

- 生产镜像不再包含 PCRE、spandsp、Sofia SIP、FreeSWITCH 的源码编译链。
- 最终镜像只增加固定版本、未修改的第三方媒体模块及项目配置。
- 生产 `APP_ENV=prod` 时必须显式配置 Trunk gateway/host、external IP 和 ESL 强密码，缺失即启动失败。
- 生产 originate 不得回退到测试 App 的 `user/{号码}` 接入方式，即使该号码已有测试注册。
- WSS、7443、证书和单个强密码内部测试 SIP 用户保持可用；不得将其扩展成生产 App 用户体系。
- 不恢复已删除的 Mock 功能、配置、补丁或媒体路径；历史 Mock 记录保持可读。
- 官方包目录、日志/state 卷、配置渲染和镜像冒烟全部一致。

### 3.2 必须保持不变

- Open API、Admin API、Adapter API、Agent `/ws-v2` 和内部媒体路径。
- `call_id`、origination UUID、FreeSWITCH UUID 和 Agent claim generation 的关联方式。
- 单次 originate 授权栅栏；结果未知时不得自动补发。
- `CHANNEL_*` 事件处理、终态、重试、Outbox、容量和挂断事务。
- Agent attached/ready/failed/completed 与 ACK 顺序。
- PCM16LE、mono、8kHz、320B/20ms、最多 500ms 队列、ready 前预缓冲和输出溢出失败策略。
- 媒体 Bearer Header，不把 token 放入 URL。
- Fast-Call 与 Agent 数据库结构、共享 core/UOW 和现有部署单元。

## 4. 明确非目标

本次不做以下工作：

- 不开发正式 App 用户体系、账号 provisioning、PushKit/CallKit，也不把常开的内部 WSS 扩展成生产 App 用户入口。
- 不恢复或验收已删除的 Mock 功能；历史 Mock 数据、存量字段和安全清理不在本次删除范围内。
- 不更换 `mod_audio_stream` 上游，不切换到 amigniter 商业版或 Bytedesk 镜像。
- 不引入 RTPengine、Janus、LiveKit 或自研 RTP 网关。
- 不拆分 `FreeSwitchESL`、Adapter 控制面与媒体面，不重构 Agent Pipeline。
- 不改数据库 schema、公开 API、Webhook、Robot 配置或任务状态机。
- 不把 SIP 端口机械改成 5060；沿用当前可配置端口，按运营商要求设置。
- 不同时建设主机直装和 Docker 两套部署。
- 不用 Mock、loopback、SIP App、数据库 `answered`、容器健康检查代替真实运营商验收。

## 5. 当前差距与改造原因

1. `deploy/freeswitch/Dockerfile` 仍需从源码编译核心改为官方预编译包；Mock 补丁已经删除，不得恢复。
2. `entrypoint.py`、Compose、模板和 smoke 将配置、日志、DB 和证书固定在 `/usr/local/freeswitch`，与官方 DEB 分拆目录不同。
3. 当前 WSS 和单测试用户应保留，但证书源路径需要适配官方包目录，且现有生产路由可能被测试注册影响，必须显式隔离。
4. 当前 gateway 可为空并回退 `user/{号码}`；生产缺少 Trunk 配置时必须 fail closed。
5. 镜像配置和 WSS 冒烟不证明真实运营商 SIP/RTP 或未打补丁模块的双向 AI 音频。

## 6. 文件级改造边界

| 文件 | 允许且需要的改动 |
|---|---|
| `deploy/freeswitch/Dockerfile` | 删除核心及依赖源码编译；用官方包安装固定版本 FreeSWITCH/Sofia/G.711；多阶段构建固定 commit 的原版 `mod_audio_stream`；不得恢复已删除的 patch；保存无敏感信息的包/模块构建清单。 |
| `deploy/freeswitch/entrypoint.py` | 将配置、日志、DB、证书目录改为官方包布局的显式常量；生产强制 Trunk 配置；保留 WSS 和单个内部测试用户现有行为。 |
| `deploy/freeswitch/templates/external.xml.template` | 保留 WSS listener 和 TLS 行为，只把证书运行路径调整到官方包配置目录，不顺带修改 WSS 协商。 |
| `deploy/compose.yaml` | 调整官方包日志/DB 卷；向 FreeSWITCH 传入 `APP_ENV`；生产 dial mode 固定为 gateway；保留 7443 映射和 WSS 证书挂载；保留 ESL 不发布；开放 Trunk 所需 SIP/RTP 及测试所需 WSS/SRTP。 |
| `deploy/compose.env.example` | 改为生产 Trunk 示例：SIP=true；gateway/host 为必填占位；保留单个内部测试用户和 WSS 配置，并明确其不参与生产被叫路由。 |
| `scripts/bootstrap_compose.py` | 强制执行生产组合硬门禁；保留现有 WSS 证书、单测试用户密码和 App 注册信息输出，不新增 WSS 开关或 override。 |
| `src/fastcall/settings.py` | 生产只要求 SIP 能力；不恢复 Mock 配置，不改其他业务配置。 |
| `src/fastcall_adapter/settings.py` | 校验生产 gateway 非空及 dial mode 只能为 gateway；SIP=true 是合法生产组合。 |
| `src/fastcall_adapter/main.py` | 仅把生产环境/Trunk 约束传入现有 ESL Gateway 组合根，不调整媒体和 Session 装配。 |
| `src/fastcall_adapter/infrastructure/gateways/esl.py` | **仅允许窄范围路由改动**：生产始终使用 `sofia/gateway/{gateway}/{callee}`，不做 registration lookup，拒绝 user/loopback dial mode；非生产保留 SIP App/user 测试路由。正式媒体启动、挂断和 originate 变量不变。 |
| `scripts/test_freeswitch_image.sh` | 按官方包路径重写；增加生产负向门禁、无补丁检查和模块加载检查；保留 WSS 注册/证书测试，但不以 WSS 替代 Trunk 验收。 |
| 相关单元测试 | 更新 entrypoint、bootstrap、Fast-Call/Adapter settings、生产 ESL 路由和镜像配置断言；历史 Mock 来源过滤测试继续保留。 |
| `README.md`、`docs/SYSTEM_ARCHITECTURE.md`、`docs/modules/FASTCALL_ADAPTER.md` | 同步当前生产架构、配置、路径和验证事实；旧 Mock/WSS 结果只作为历史或测试证据，不宣称真实 Trunk 已验收。 |
| `docs/examples/local-sip-test/README.md` | 标明 WSS/App 是常开的内部测试能力；说明其与生产 Trunk 路由隔离，不写成生产用户入口或 App 用户体系。 |

### 明确不修改

- `src/fastcall_adapter/infrastructure/gateways/esl.py` 的正式媒体启动、挂断、媒体 Header 和 originate 业务变量；只允许上表所述的生产路由选择修正。
- Adapter/Agent 媒体协议、Session 状态机、数据库 DAO/UOW。
- 已删除的 Mock 业务源码、配置、补丁和媒体路径不得恢复；历史数据及安全清理保持现状。

## 7. 官方包与目录契约

实现时以实际官方包清单为准，预期采用以下布局：

| 类型 | 目标位置 |
|---|---|
| 配置 | `/etc/freeswitch` |
| 日志 | `/var/log/freeswitch` |
| DB/state | `/var/lib/freeswitch` |
| 模块 | `/usr/lib/freeswitch/mod` |
| 可执行文件 | `/usr/bin/freeswitch`、`/usr/bin/fs_cli` |
| WSS 证书只读源目录 | `/run/fastcall-wss-certs`，由基础 Compose 常设挂载 |
| WSS 证书运行目录 | `/etc/freeswitch/certs`，由 entrypoint 从独立源目录复制 |

要求：

- 在 entrypoint 中使用清晰的目录常量，必要时允许测试覆盖；不得用一个 `/usr/local/freeswitch` 前缀猜测所有路径。
- 不用软链接把整个 `/usr/local/freeswitch` 映射到 `/usr`，避免掩盖包布局错误。
- 构建阶段用 `dpkg -L`、`test -x`、`test -d` 和实际模块加载验证布局。
- `mod_audio_stream.so` 必须安装到实际模块目录，`module_exists mod_audio_stream` 必须成功。
- 构建模块时使用与运行镜像完全相同的 Debian 发行版、CPU 架构、FreeSWITCH 包版本、头文件和库。
- 优先使用与运行包同版本的官方开发包；若必须取匹配头文件，只能在构建阶段使用，最终运行核心仍必须来自官方预编译包。
- 固定基础镜像 digest、精确包版本和模块 commit，记录包清单及 SHA-256；不得使用会漂移的 `latest` 或未固定 apt 版本作为已验证制品。
- SignalWire Token 只通过构建 secret 使用，不进入 Dockerfile ARG、镜像层、构建日志、Compose env 或文档。

## 8. 配置契约

不新增 WSS 开关或 Compose override。WSS、7443 和单个内部测试账号随 SIP 能力常开。

生产基线：

```dotenv
APP_ENV=prod
FASTCALL_SIP_ENABLED=true
FREESWITCH_SIP_GATEWAY=<运营商gateway名称>
FREESWITCH_SIP_HOST=<运营商地址>
FREESWITCH_EXTERNAL_IP=<运营商可见的本机地址>
FREESWITCH_LOCAL_USER=<单个内部测试账号>
FREESWITCH_LOCAL_PASSWORD=<独立强密码>
FREESWITCH_WSS_PORT=7443
```

行为要求：

- 生产硬门禁按组件职责分别执行，而不是给所有组件传递所有 FreeSWITCH 配置：bootstrap 与 FreeSWITCH entrypoint 校验 SIP、gateway/host 和 dial mode；Adapter 校验 SIP、gateway 和 dial mode；Fast-Call 只校验 SIP。`APP_ENV=prod` 的统一目标为 SIP=true、gateway/host 非空、dial mode=gateway，任一组件发现自身相关配置非法都必须启动失败。Fast-Call 不需要读取 WSS 证书、gateway/host 或 dial mode。
- Compose 必须把 `APP_ENV` 传给 FreeSWITCH，不能只传给 Fast-Call/Adapter。
- 保留现有 WSS/TLS listener、7443 映射、证书生成/挂载、单测试用户和 App 注册信息输出。证书只读挂载到 `/run/fastcall-wss-certs`，entrypoint 复制到 `/etc/freeswitch/certs`，源目录与目标目录不得相同。
- 内部测试账号必须使用独立强密码；在可行时通过防火墙限制 7443 的测试来源。该账号不参与生产用户体系，也不代表允许生产 originate 使用 `user/{号码}`。
- 即使测试 App 已注册被叫号码，生产 originate 仍只能使用 Trunk gateway；非生产测试路由保持现有行为。
- Mock 已删除，启动时仅保留删除旧 dialplan 的兼容清理；不得重新引入 Mock 配置或运行入口。
- 不新增配置别名或长期双轨；文档统一描述 WSS 常开和 Trunk-only 生产路由。

## 9. 分阶段实施与验收

### 阶段 0：基线与前置条件

执行：

```bash
git status --short
venv/bin/python -m pytest -q
```

记录真实通过、失败和跳过情况；既有失败不得归因于本次改造。确认：

- 可用的 SignalWire 官方包仓库凭据和受支持 Debian/架构。
- 当前模块 commit、许可证及构建依赖。
- 运营商测试线路类型：注册型或 IP-auth。
- Trunk 地址、端口、传输、号码格式、主叫规则、codec、RTP 地址段和测试终端。
- 真实 Provider 凭据与配额。

没有运营商线路时可以继续阶段 1—4，但最终必须保持生产 `NO-GO/UNVERIFIED`，不得宣称上线完成。

### 阶段 1：官方包薄镜像

只改 Docker 构建、目录布局和必要测试，不同时修改 Adapter/Agent。

完成证据：

- Dockerfile 中不存在 FreeSWITCH/Sofia/spandsp 源码编译，不存在 `git apply` 或 C++ patch。
- 模块 checkout 后无源码差异，固定 commit 与当前生产选择一致。
- `ldd` 无缺失运行库，`freeswitch -version` 和 `fs_cli status` 正常。
- `mod_sofia`、`mod_audio_stream`、PCMU、PCMA 可用。
- 最终镜像不包含构建工具链、Token、`.env` 或运营商/Provider 凭据。
- 构建清单能够证明核心包版本、模块 commit、基础镜像 digest 和架构。

### 阶段 2：生产配置与常开测试入口隔离

保留 WSS 和单测试账号，实施生产组合硬门禁、生产 Trunk 路由约束，以及 Compose/bootstrap/文档联动；不恢复 Mock。

完成证据：

- `APP_ENV=prod` 与 SIP=false、空 gateway/host 或非 gateway dial mode 的每种非法组合都被明确拒绝；FreeSWITCH、Fast-Call 和 Adapter 不会形成混合环境。
- WSS listener、7443 映射、证书和单个内部测试账号可用，证书源/目标目录分离，TLS handshake 和现有 App 注册测试通过；除该显式测试账号外不加载默认 directory 用户。
- 即使被叫号码曾有测试注册记录，生产 originate 仍只使用 `sofia/gateway/{gateway}/{callee}`；生产不执行 registration lookup，`FREESWITCH_DIAL_MODE=user/loopback` 被拒绝，并有对应 ESL 单元测试。
- 遗留 Mock origin 明确拒绝拨号，旧 dialplan 在启动时被清理；不存在 Mock 配置或运行入口。
- WSS 测试通过不改变生产 Trunk 路由；7443 的测试来源限制和测试账号强密码配置已检查。
- ESL 仅允许 Adapter 地址，8021 不向宿主机发布。

### 阶段 3：回归与文档收敛

至少执行：

```bash
venv/bin/python -m pytest -q tests/unit/test_freeswitch_entrypoint.py
venv/bin/python -m pytest -q tests/unit/test_deploy_bootstrap.py
venv/bin/python -m pytest -q tests/unit/fastcall_adapter
venv/bin/python -m pytest -q tests/unit/fastcall
venv/bin/python -m pytest -q
./scripts/test_freeswitch_image.sh
```

要求：

- 相关测试和全量单元测试通过；既有无关失败单列。
- 镜像 smoke 覆盖生产配置负向门禁及 WSS 测试入口，不以配置加载替代真实媒体验收。
- 文档、配置示例、代码默认值和实际渲染一致。
- `git diff` 不包含无关格式化、生成物、日志、真实凭据或测试电话号码。
- 保留历史 Mock 来源过滤测试，但不存在 Mock 运行功能或其专用业务测试。

### 阶段 4：预生产部署门禁

在 Linux Docker 环境使用实际生产配置启动，但不恢复正式任务调度：

- 本次发布制品是一组匹配版本：`FREESWITCH_IMAGE` digest、`FASTCALL_ADAPTER_IMAGE` digest、`FASTCALL_IMAGE` digest、对应 Compose/env 快照和模块 commit。API、Scheduler、Webhook 必须确认使用同一个候选 `FASTCALL_IMAGE` digest。
- 容器启动、模块加载、ESL 鉴权、目录权限和数据卷正常。
- Adapter/Agent/Fast-Call readiness 符合现有语义。
- 运营商 SIP/RTP 防火墙只开放必要地址段，ESL/MySQL/Agent/Adapter 不公网暴露。
- 保存整组候选制品及配置，验证其模块、卷路径、Trunk 配置和通道恢复。
- 若存在上一套 Trunk-only、WSS 常开的已验收生产制品，保存其三类镜像 digest 和配置作为回滚点；首次迁移没有该制品时必须明确记录“无合规旧版本”，不得把当前开发镜像伪装成合规回滚点。整组候选制品未通过上述验证时不得进入阶段 6。

### 阶段 5：真实运营商与双向 AI 验收

只有阶段 1—4 通过且取得测试线路后执行。

#### 5.1 先做无 AI 的 Trunk 基线

验证真实 INVITE、180/183、200 OK、ACK、BYE，以及 SDP、Contact 和 in-dialog 请求往返；确认真实双向 RTP、号码和 codec。不得先靠 AI 日志推断电话成功。

#### 5.2 再启用完整链路

至少验证：

- 注册型 Trunk 为 `REGED`，或 IP-auth 线路确实接受请求。
- 真实振铃和接听，主叫/被叫格式正确。
- 用户说话经 Carrier、FreeSWITCH、模块、Adapter 到 STT，得到非空且语义正确的 transcript。
- Agent LLM/TTS 音频经原始模块播放路径返回 Carrier，测试人员实际听到，且开场白和结束语完整。
- 用户打断有效，未打补丁模块无需 `STREAM_PLAYBACK_DIRECT`。
- 应答、拒接、无应答、忙和运营商拒绝映射到现有结果码。
- 至少一次用户挂机和一次 Agent 主动挂机，终态、Outbox、Agent 落库和通道清理正确。
- Provider 超时/断连、Carrier 断链和 Adapter 重启不产生重复 originate、静默挂死或残留媒体任务。
- 从低并发开始逐步增加，记录 RTP 丢包/抖动、媒体断流、Provider TTFB 和终态分布。

任何一项未执行都标记 `UNVERIFIED`。Mock、loopback、SIP App 或单元测试不能替代以上证据。

### 阶段 6：灰度与恢复调度

1. 暂停 Scheduler 新派发，排空在途 Call。
2. 确认 FreeSWITCH 通道为零后，以单实例 Recreate 更新整组候选制品：FreeSWITCH、Adapter，以及使用候选 `FASTCALL_IMAGE` 的 API、Scheduler、Webhook；不得形成新旧程序混合版本。
3. 依次验证模块、Trunk、Fast-Call 和 Adapter readiness，再恢复 Scheduler。
4. 先单通、少量测试号码、低并发，再逐步扩大。
5. 保存每阶段证据；未达到真实线路门禁时不得标记生产 GO。

## 10. 回滚与在途 Call

保留业务数据库与 FreeSWITCH DB/log 卷。本任务不应产生数据库迁移。

**首次迁移与后续升级必须区分：**

- 首次迁移不存在 Trunk-only、WSS 常开的已验收旧制品时，没有可宣称的生产回滚点。阶段 4/5 或首次灰度失败时，保持 Scheduler 暂停并判定 `NO-GO`，不得通过恢复旧开发镜像来伪装回滚成功。首次候选制品通过全部门禁后才允许首次 GO，并保存为后续合规基线。
- 后续升级必须保存上一套 Trunk-only、WSS 常开的已验收 `FREESWITCH_IMAGE`、`FASTCALL_ADAPTER_IMAGE`、`FASTCALL_IMAGE` 三类 digest、匹配模块和 Compose/env 快照；没有整组合规回滚制品时不得切版。

后续标准回滚窗口：

1. 暂停 Scheduler 新建和派发正式 Call。
2. 等待活动通话自然结束，处理 `reserved`、`dialing`、`checking`、`unknown`，确认通道清零。
3. 整组回滚 FreeSWITCH（含匹配模块）、Adapter，以及 API/Scheduler/Webhook 共用的 Fast-Call 镜像；禁止新旧核心、模块或应用镜像混用。
4. 验证 Trunk、Fast-Call 和 Adapter readiness 后再恢复调度。

首次灰度失败或后续应急切版时：

- 不手工补发 originate，不直接删除 `checking/unknown` Call 或释放容量。
- 使用现有核查和 Admin review 按实际通道证据收敛。
- 记录受影响 Call ID 和技术原因，不记录完整号码或对话。
- 后续回滚目标必须是 Trunk-only、WSS 常开的已验收生产制品；首次迁移没有此制品时只能停止发布并保持 NO-GO。

## 11. 停止条件与主要风险

| 级别 | 风险 | 必须动作 |
|---|---|---|
| BLOCKER | 官方包不支持目标 Debian/架构，或模块 ABI 不兼容 | 更换受支持发行版并重新验证；禁止回退源码编译核心。 |
| BLOCKER | 生产缺少 Trunk gateway/host | 启动失败并补齐配置；禁止回退测试用户路由。 |
| BLOCKER | 没有真实运营商线路 | 可完成构建和配置工作，但整体保持 NO-GO。 |
| BLOCKER | 真实 SIP/RTP、ACK/BYE 或 Docker UDP 返回路径失败 | 在同一 Compose 交付中修网络；不以 Mock 结果替代，不另建部署体系。 |
| BLOCKER | 未打补丁模块无法完成真实双向播放 | 停止生产发布并报告证据；不得恢复私有补丁，是否换模块/媒体路线由用户另行决策。 |
| MAJOR | 官方包目录与旧硬编码不一致 | 全量清理 Dockerfile、entrypoint、Compose、模板和 smoke 的旧路径。 |
| MAJOR | Mock 被恢复、WSS 测试账号弱密码或生产路由被测试注册影响 | 完成生产负向配置、WSS 安全和 Trunk-only 路由测试后再进入线路验收。 |
| MAJOR | apt 包或基础镜像版本漂移 | 固定版本/digest，保存包清单和校验值。 |
| MAJOR | AGPL-3.0 或商业许可未确认 | 发布前完成许可证确认。 |
| MAJOR | 在途 Call 切版导致 checking/unknown 和容量占用 | 默认排空；应急只走现有核查/review，不改状态机。 |

## 12. 新窗口最终交付格式

执行完成后，结果必须分为两类，不得混写：

### A. 代码与构建交付

- 实际修改文件和关键行为。
- 构建制品的镜像 digest、官方包版本、模块 commit。
- 已运行的单元测试、镜像 smoke 和配置负向测试。
- 文档更新和许可证状态。
- 未解决的代码/构建问题。

### B. 真实线路上线状态

- `GO` 或 `NO-GO`。
- 实际运营商测试场景及证据。
- 双向 ASR/TTS、挂断、终态和清理结果。
- 所有 `UNVERIFIED` 项及阻塞原因。
- 灰度、回滚制品和在途 Call 处理结果。

没有真实线路证据时，正确结论是“代码/镜像改造完成，生产线路未验收，NO-GO”，而不是“上线改造完成”。

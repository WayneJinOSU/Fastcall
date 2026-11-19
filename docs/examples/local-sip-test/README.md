# 内部 SIP App 测试（默认 WSS，无运营商线路）

SIP App/WSS、7443 和单个测试账号随 SIP 能力常开，只用于内部测试，不是生产用户入口或 App 用户体系。本指南要求显式使用 `APP_ENV=test`；生产 `APP_ENV=prod` 必须使用 Trunk gateway，且 originate 不会查询或回退到已注册的 `user/{号码}`。

用普通 SIP App 验证真实 SIP 注册、INVITE、RTP/SRTP 音频和 AI 对话，全程经过通用 outbound-task，不依赖运营商线路，也不需要 `USER_TARGET`、`ext:`、`FREESWITCH_DIAL_MODE`、dummy gateway 或手工修改 XML。

## 先分清：每一步由谁在哪里操作

| 操作者 | 在哪里 | 要做什么 |
|---|---|---|
| 你 | **部署电脑的终端**，进入 Fastcall 项目根目录 | 执行 `./scripts/bootstrap_compose.sh`；在同一张终端窗口查看 SIP 输出 |
| Bootstrap | **部署电脑** | 生成 SIP 密码和证书，保存到 `deploy/.env.compose` 与 `deploy/runtime/tls/`，然后调用 Docker Compose |
| Docker build | **部署电脑** | 只构建程序镜像，不会替你注册手机 App，也不会把某次生成的密码永久固化到镜像中 |
| FreeSWITCH 容器启动入口 | **容器内** | 根据环境变量生成 SIP 账号配置并加载证书 |
| 你 | **手机上的 SIP App** | 手动填写账号、密码、域和 WSS 地址，信任证书并确认注册成功 |
| 你 | **部署电脑的浏览器和终端** | 在 Console 发布 Robot，再执行 quickstart 发起呼叫 |

因此，不要只在 `docker build` 的输出中找 SIP 账号。直接运行 `./scripts/bootstrap_compose.sh` 的终端才是查看这些输出的位置。

## 先分清：SIP 登录账号和外呼联系人

这两个值在首验中使用同一个号码，但职责不同：

| 概念 | 用途 | 由谁决定 |
|---|---|---|
| SIP 登录账号 | App 向 FreeSWITCH 证明身份，并登记“这个账号当前在哪台设备上” | bootstrap 根据 `FREESWITCH_LOCAL_USER` 创建一个账号 |
| 外呼联系人 | 告诉通用任务这次要呼叫哪个号码 | 创建任务时导入的 `phone` |

没有运营商线路时，FreeSWITCH 不能直接通过手机号找到一台 App。必须先发生一次注册：

```text
SIP App 使用账号、密码、域和 WSS 地址
→ FreeSWITCH 验证账号密码
→ FreeSWITCH 保存账号与设备当前位置的对应关系
```

之后才是服务主动外呼：

```text
任务联系人 phone=+8615908280801
→ Adapter 读取联系人号码并生成 user/8615908280801
→ FreeSWITCH 查询该账号的注册位置
→ 向已注册的 SIP App 发送 INVITE
→ App 响铃，接听后建立 RTP/SRTP 和 AI 双向音频
```

**`FREESWITCH_LOCAL_USER` 不参与 Adapter 的路由决策。** 它只是 bootstrap 用来预置一个 SIP 登录账号。quickstart 默认把该账号作为联系人，只是为了少输入一个参数；真正拨号时仍读取任务联系人。

当前首验是**单个预置账号模式**。玩家输入任意号码后由系统自动创建 SIP 账号的能力尚未实现，因此 `--phone` 不能指向一个从未创建并注册的账号。

## 1. 准备数据库和 AI Provider

先按根目录 `README.md` 的“准备宿主机依赖”和“创建数据库、用户并导入 SQL”完成操作，不要共用 Fast-Call 与 Agent 数据库。

首验固定使用 Console 默认组合，在 `deploy/.env.compose` 填写三项真实凭据：

```dotenv
DEEPGRAM_API_KEY=<你的Deepgram密钥>
DEEPSEEK_API_KEY=<你的Deepseek密钥>
CARTESIA_API_KEY=<你的Cartesia密钥>
```

不要只配置 OpenAI 密钥：本教程的默认 Robot 使用 `deepseek/deepseek-flash`。

## 2. 配置并启动服务

在**部署电脑**打开终端，进入项目根目录，不要进入 Docker 容器：

```bash
cd /path/to/Fastcall
```

如果还没有 `deploy/.env.compose`，复制配置模板；已经存在时不要覆盖：

```bash
cp deploy/compose.env.example deploy/.env.compose
```

至少检查以下值：

```dotenv
APP_ENV=test
FASTCALL_SIP_ENABLED=true
FREESWITCH_DIAL_MODE=gateway
FREESWITCH_EXTERNAL_IP=192.168.1.100
FREESWITCH_DOMAIN=
FREESWITCH_LOCAL_USER=8615908280801
FREESWITCH_LOCAL_PASSWORD=
FREESWITCH_WSS_PORT=7443
FREESWITCH_SIP_GATEWAY=
FREESWITCH_SIP_HOST=
```

- `APP_ENV=test`：显式选择非生产测试路由；`prod` 下空 gateway/host 会拒绝启动，生产 originate 也绝不会使用这里的 App 注册记录。
- `FREESWITCH_EXTERNAL_IP`：运行 Fastcall 的 Mac/服务器局域网 IP；公开部署使用公网 IP。
- `FREESWITCH_DOMAIN`：SIP 注册域和 WSS 证书 SAN。留空时自动使用 external IP；公开部署建议填写 `sip.example.com` 一类域名。
- `FREESWITCH_LOCAL_USER`：要创建的 SIP 登录账号，使用 6–15 位数字，默认 `8615908280801`。它不是外呼路由配置。
- `FREESWITCH_LOCAL_PASSWORD`：该账号的登录密码。留空时由 bootstrap 生成至少 12 位的随机密码并保存，重启会复用。
- `FREESWITCH_WSS_PORT`：宿主机 WSS TCP 端口，默认 `7443`。
- `FREESWITCH_SIP_GATEWAY`：本地 App 首验留空；接运营商线路时再按线路配置。

启动：

```bash
./scripts/bootstrap_compose.sh
```

在**同一张终端窗口**等待命令执行完成，不要提前按 Ctrl+C。首次启动会生成缺失的 Console/WSS 证书和 SIP 密码，并在启动成功后输出：

```text
SIP username: 8615908280801
Generated SIP password: <随机密码>
SIP domain: 192.168.1.100
SIP WSS URL: wss://192.168.1.100:7443
Contact phone: +8615908280801
```

如果错过了输出，在部署电脑打开 `deploy/.env.compose`，查看 `FREESWITCH_LOCAL_USER`、`FREESWITCH_LOCAL_PASSWORD`、`FREESWITCH_DOMAIN` 和 `FREESWITCH_WSS_PORT`。后续重启复用这些值，不会再显示原来的随机密码。证书过期或与当前 domain 不匹配时，bootstrap 会明确报错，不会静默替换。

## 3. 配置 SIP App 并完成注册

以 bootstrap 的实际输出为准：

| App 字段 | 示例 |
|---|---|
| 用户名 / Username | `8615908280801` |
| 密码 / Password | bootstrap 生成的随机密码 |
| 域 / Domain / Realm | `192.168.1.100` |
| 传输协议 | `WSS` / `Secure WebSocket` |
| WSS 地址 | `wss://192.168.1.100:7443` |

保持 App 前台并开启自动注册。iOS 后台或锁屏可能挂起 WebSocket，导致注册失效。

### 证书必须能被 App 信任

WSS 在 SIP REGISTER 之前先进行 TLS 握手。账号密码正确也不能绕过证书检查。

**局域网自签名方式：** bootstrap 生成的证书 SAN 与 `FREESWITCH_DOMAIN` 一致，但由本地 CA 签发。App 或操作系统必须信任：

```text
deploy/runtime/tls/ca.crt
```

先用 AirDrop、数据线或文件共享把这个文件传到手机，再按平台操作：

- **iPhone/iOS**：打开下载的 CA 描述文件，到“设置 → 通用 → VPN与设备管理”安装；然后到“设置 → 通用 → 关于本机 → 证书信任设置”，启用 `Fastcall Local CA` 的完全信任。菜单名称以当前 iOS 版本为准。
- **Android**：在系统安全/加密与凭据设置中选择“安装证书”或“CA 证书”，导入 `ca.crt`。不同品牌菜单不同，而且部分 App 不信任用户 CA。
- **App 使用独立证书库时**：还需要在 App 内导入 CA，不能只安装到系统。

如果 App 没有导入私有 CA 的入口，或只接受系统公共 CA，就使用下面的公共证书方式，不要关闭证书校验。

**公开部署的公共证书方式：** 准备解析到服务器的域名，并使用 Let’s Encrypt 等公共 CA 签发证书。将 full chain 和私钥放到以下位置：

```bash
mkdir -p deploy/runtime/tls
cp /path/to/fullchain.pem deploy/runtime/tls/wss.crt
cp /path/to/privkey.pem deploy/runtime/tls/wss.key
cat deploy/runtime/tls/wss.crt deploy/runtime/tls/wss.key > deploy/runtime/tls/wss.pem
chmod 600 deploy/runtime/tls/wss.key deploy/runtime/tls/wss.pem
```

然后设置并重新启动：

```dotenv
FREESWITCH_DOMAIN=sip.example.com
```

```bash
./scripts/bootstrap_compose.sh
```

App 使用 `wss://sip.example.com:7443`。文件齐全且匹配时 bootstrap 会复用它们。公开环境还需放行 WSS TCP 端口和配置的 RTP UDP 范围，并妥善保护 SIP 密码。

自签名证书的域名或 IP 改变后，需要重新生成 `wss.crt`、`wss.key`、`wss.pem` 并重启。App 连接的地址必须与证书 SAN 一致。

如果 bootstrap 提示旧版本地证书缺少必需的 X.509 扩展，先备份整个目录再生成新 CA 和证书：

```bash
mv deploy/runtime/tls deploy/runtime/tls.before-upgrade
./scripts/bootstrap_compose.sh
```

这会生成新的本地 CA，手机需要重新安装并信任新的 `ca.crt`。不要把备份目录重新覆盖回去。

**先确认 App 显示注册成功，再继续。** 如果无法注册，优先看第 7 节的排查顺序，不要直接发任务。

## 4. 创建并发布 Robot

服务启动后打开：

```text
http://localhost:8080/console/
```

首次启动前，终端会显示：

```text
Generated Console password for admin: <Console随机密码>
```

使用 `admin` 和该密码登录。如果忘记复制，不能从 `.env.compose` 的哈希中找回明文，直接在宿主机项目根目录重设：

```bash
FASTCALL_CONSOLE_ADMIN_PASSWORD='<新的强密码>' ./scripts/bootstrap_compose.sh
```

然后使用 `admin` 和刚设置的新密码登录。接着按下面内容创建一个**不使用联系人变量**的首验 Robot：

1. 在“Robot 应用”中点击“新建应用”，名称填 `SIP Demo English`，点击“创建并编辑”。系统会自动生成 `app_code`。
2. 在“Prompt 与变量”中填写 system prompt，不添加任何变量：
   ```text
   You are a friendly Fastcall test assistant. Keep every answer short.
   ```
3. 在“Runtime”中确认以下默认组合：
   - LLM：`deepseek / deepseek-flash`
   - ASR：`deepgram / nova-2`，语言 `en-US`
   - TTS：`cartesia / sonic-2`，语言 `en-US`
   - 音色：选择列表中一个启用的音色；如果列表为空，填写你 Cartesia 账号中有效的 Voice ID。
4. 在“Flow”中填写：
   - `welcomeNode.content`：`Hello, this is your Fastcall test call.`
   - `endNode.content`：`Thank you for testing Fastcall. Goodbye.`
   - `noResponse.noResponseReply`：`Are you still there?`
   - 其余配置先保持默认。
5. 保存草稿，点击“校验”，确认没有错误后点击“发布”。在“基本信息”中复制自动生成的 `app_code`。

这些字段不能留空，否则发布校验会失败。默认示例不需要联系人变量，因此后面的 quickstart 不要传 `--variables`。如果自行添加 `required` 的 `user_name`，则必须传 `--variables '{"user_name":"Alice"}'`；如果 Robot 没定义这个变量，传入也会被拒绝为 `Unknown contact variable`。

## 5. 通过通用任务发起呼叫

最简单的命令：

```bash
./scripts/sip_quickstart.py --robot-code YOUR_APP_CODE
```

脚本默认联系人为 `+{FREESWITCH_LOCAL_USER}`，也可以显式填写，但仍必须是已经创建并注册的 SIP 账号：

```bash
./scripts/sip_quickstart.py \
  --robot-code YOUR_APP_CODE \
  --phone +8615908280801
```

脚本只执行现有通用 Open API：

1. `PUT /openapi/v1/outbound-tasks/{customer_task_id}` 创建普通外呼任务。
2. `POST /openapi/v1/outbound-tasks/{task_id}/contacts` 导入联系人。
3. `POST /openapi/v1/outbound-tasks/{task_id}/start` 启动任务。
4. 查询任务和 Call，输出状态与 Console 详情地址。

Scheduler 调度后，Adapter 根据联系人号码直拨 `user/{callee去+}`。没有专用 SIP 测试任务类型，也没有 dummy gateway 或隐藏号码映射。

## 6. 确认真机验收结果

App 响铃后接听，至少确认：

- WSS 证书受信任，REGISTER 成功，错误密码会被拒绝。
- App 收到 INVITE，媒体协商成功，双方都能听到声音。
- 开场白完整，用户转写和 AI 回应正确。
- 结束语完整播完后再挂断。
- Console 中的 transcript、Call 终态和 Agent 技术记录正确。
- 通话结束后 FreeSWITCH 通道、媒体流和 Agent Session 被清理。

每次发布候选版本记录实际结果，不要把“待验证”写成“通过”：

| 项目 | 本次记录 |
|---|---|
| App 名称与版本 / 手机 OS | 待真机填写 |
| 网络：局域网或公网 / 证书模式 | 待真机填写 |
| REGISTER / INVITE / 双向 RTP 或 SRTP | 待真机填写 |
| 开场白 / 双向 AI 对话 / 结束语 | 待真机填写 |
| Call 终态 / transcript / 资源清理 | 待真机填写 |

UDP/TCP 客户端可使用 `服务器IP:5080`，但其他客户端成功不能证明目标 WSS App 可用；应使用目标 App 完成真机验收。

## 7. 按阶段排查

1. **WSS/TLS 握手失败**：检查 7443 端口、证书信任、证书有效期、SAN 与 App 地址是否一致。
2. **REGISTER 失败**：检查账号、密码、域、WSS 地址，保持 App 前台并开启自动注册。
3. **任务启动但没有响铃**：检查 App 是否仍注册、联系人是否与注册账号一致，以及 Adapter 是否拨出 `user/{联系人去+}`。
4. **响铃但无法建立 AI 对话**：检查 Agent Provider、Agent Session 和媒体流是否 ready。
5. **接通后无声或超时**：检查 RTP/SRTP、ICE/DTLS（若客户端使用）、服务器 external IP 和 RTP UDP 端口范围。

仅容器健康、WSS 端口开放或 TLS 握手成功，都不等于 SIP 注册、双向媒体和 AI 全链路已经通过。

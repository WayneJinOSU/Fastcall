# Collection Agents 项目完整概览

## 📋 项目概述

**项目名称**: Collection Agents (催收电话机器人系统)  
**项目类型**: AI 语音交互系统 / 语音机器人平台  
**仓库地址**: https://github.com/SuperWuli/collection-agents.git  
**最后更新**: 2025-07-08  

这是一个基于 Pipecat 框架的多种模式语音机器人系统，用于自动催收电话和业务咨询。支持多语言、多LLM服务商、实时语音交互等功能。

---

## 🏗️ 项目架构

### 核心架构设计

```
FastAPI 服务器
    ↓
WebSocket 端点 (3种)
    ├─ /ws-v2              (通用Bot - standard)
    ├─ /ws-ai-copilot      (AI助手 - copilot)
    └─ 回调端点 /callback/call_record
    
Bot Pipeline 架构:
    ├─ STT (语音识别) → Deepgram
    ├─ LLM (语言模型) → OpenRouter/Azure/OpenAI
    ├─ TTS (语音合成) → Cartesia/ElevenLabs
    └─ 3种 Bot 实现
        ├─ 标准Bot (standard.py) - 异步轮转式
        ├─ 实时Bot (realtime.py) - 边说边听
        └─ Copilot Bot (copilot.py) - 事件驱动
```

---

## 📁 目录结构

```
collection-agents/
├── main.py                           # FastAPI 主服务入口
├── app_logging.py                    # 日志配置系统
├── sentry_config.py                  # Sentry 错误追踪配置
│
├── 【核心Bot实现】
├── standard.py                       # 标准Bot (异步轮转)
├── realtime.py                 # 实时Bot (流式交互)
├── copilot.py               # Copilot Bot (事件驱动)
│
├── 【处理器模块】
├── processors_beta.py                # Beta模式自定义处理器
├── processors_realtime.py            # 实时模式自定义处理器
├── processors_common.py              # 通用处理器 (SilenceDetector等)
│
├── 【业务模块】
├── standard_modules/
│   ├── config.py                     # 配置管理 (CallConfigManager)
│   ├── services.py                   # LLM/STT/TTS 服务创建
│   ├── notes.py                      # 通话记录生成和分析 (collection_note)
│   └── __init__.py
│
├── 【工具库】
├── my_utils/
│   ├── mysql.py                      # MySQL 数据库连接
│   ├── utc2tz.py                     # 时区转换工具
│   ├── telephony_websocket.py             # WebSocket 协议序列化
│   ├── websocket_safe_send.py        # WebSocket 安全发送
│   ├── copilot_websocket.py  # Copilot WebSocket协议
│   ├── text_normalization.py         # 文本规范化
│   ├── words_process.py              # 数字转文字转换
│   ├── datetime_with_tz.py           # 时间处理
│   └── env.py                        # 环境变量管理
│
├── 【指标收集】
├── call_metrics/
│   ├── collector.py                  # 通话指标收集
│   └── realtime_collector.py         # 实时通话指标
│
├── 【外部集成】
├── collection_note/                  # 通话记录分析引擎
├── dify/                             # Dify工作流集成
├── n8n_helper/                       # N8N自动化集成
├── scheduler/                        # 调度系统
│
├── 【其他资源】
├── templates/
│   └── streams.xml                   # TwiML 流媒体模板
├── assets/                           # 音频资源 (WAV文件)
├── deploy/
│   ├── Dockerfile                    # CPU版本镜像
│   ├── Dockerfile-gpu                # GPU版本镜像
│   └── deploy.yaml                   # Kubernetes部署配置
│
├── 【配置文件】
├── .env                              # 环境变量配置
├── requirements.txt                  # 依赖列表
├── .python-version                   # Python版本
├── README.md                         # 项目说明
├── request.json                      # 请求示例
└── voice_chat_v2.html                # 语音聊天前端
```

---

## 🔌 主要依赖

### 核心框架
```
pipecat-ai[daily,cartesia,openai,silero,deepgram,azure,websocket,rnnoise]==0.0.104
- 语音管道框架，支持实时流式处理
- 包含多种TTS/STT/LLM集成
```

### Web 框架
```
fastapi          # 高性能异步Web框架
uvicorn          # ASGI服务器
```

### 语音/音频
```
av==17.0.1       # PyAV 音频处理
pipecat[rnnoise] # 噪音消除
```

### LLM 服务
```
langgraph                    # LangGraph 工作流 (集成Dify)
openai                       # OpenAI API
pipecat[azure,openai,...]   # Azure LLM, OpenRouter支持
```

### 数据库
```
SQLAlchemy==1.4.27
PyMySQL==1.0.2
```

### 其他工具
```
python-dotenv    # 环境变量管理
loguru           # 日志库
apscheduler      # 定时任务调度
pandas           # 数据处理
requests/httpx   # HTTP请求
sentry-sdk       # 错误追踪
```

---

## 🚀 服务启动流程

### 1. 主入口 (main.py)

**FastAPI 服务器配置**:
- 监听: `0.0.0.0:8765`
- CORS: 允许所有来源
- 中间件: 请求追踪码注入

**3个WebSocket端点**:

#### a) `/ws-v2` - 标准通话模式
```python
# 接收JSON协议:
{
  "action": "start",
  "data": {
    "callee": "15551234567",      # 被叫号码
    "businessID": "16592232",      # 业务ID
    "agentID": "xxx",              # Agent ID
    "config": {                    # Bot配置
      "name": "agent_name",
      "modelConfig": {...},        # LLM配置
      "promptConfig": {...},       # 提示词配置
      "flowConfig": {...}          # 业务流程配置
    }
  }
}
```
→ 执行 `run_standard_bot()`

#### b) `/ws-ai-copilot` - AI助手模式 (事件驱动)
```python
# 接收事件协议:
{
  "event_type": "action.create",
  "session_create": {...}
}
```
→ 执行 `run_copilot_bot()`

#### c) `/callback/call_record` - 回调端点
- 接收任务完成回调
- 接收通话记录回调
- 存储到数据库

### 2. 服务器启动命令

```bash
python main.py
# 或者
uvicorn main:app --host 0.0.0.0 --port 8765
```

---

## 🤖 三种Bot实现方式

### Bot 1: 标准Bot (`standard.py`) - 异步轮转模式

**特点**:
- 经典的"用户说→LLM思考→机器人回应"轮转模式
- 完整的上下文管理
- 支持中断、挂断、留言等

**主流程**:
```
WebSocket 连接
    ↓
接收语音流 (InputAudioRawFrame)
    ↓
STT 转录 (Deepgram)
    ↓
LLM 处理 (上下文感知)
    ↓
TTS 合成 (Cartesia)
    ↓
发送音频 + 文本事件
    ↓
检测用户中断/无响应/挂断
```

**关键处理器与轮次策略**:
- `HangupGate` - 检测挂断信号
- `MinWordsUserTurnStartStrategy` - 收到至少一个词的转录后才触发打断
- `SpeechTimeoutUserTurnStopStrategy` - 根据 VAD 和转录状态结束用户轮次
- `SilenceDetector` - 无响应检测 (24+秒)
- `VoicemailDetector` - 语音信箱检测

**核心配置参数**:
```python
LLM_TIMEOUT_SECS = 6.0          # LLM超时时间
LLM_MAX_RETRIES = 3             # 重试次数
LLM_RETRY_ON_TIMEOUT = True     # 超时重试
NO_RESPONSE_TIMEOUT = 24 sec    # 无响应超时
```

---

### Bot 2: 实时Bot (`realtime.py`) - 流式交互模式

**特点**:
- 实时流式处理，边说边听
- 使用 Azure Realtime LLM 或 OpenAI Realtime API
- 更自然的对话体验
- 更低的延迟

**主流程**:
```
WebSocket (流式)
    ↓
实时音频流处理 (PCM格式)
    ↓
RNNoise 降噪
    ↓
Realtime LLM (边听边说)
    ↓
实时音频输出
    ↓
Graceful 优雅挂断
```

**Azure Realtime 配置**:
```python
# 来自 .env.temp:
AZURE_LLM_1_ENDPOINT = "https://jinle-openai-us-east.openai.azure.com/"
AZURE_LLM_1_API_KEY = "..."
AZURE_LLM_1_DEPLOYMENT = "gpt-5.4-mini"
AZURE_LLM_1_API_VERSION = "2025-04-01-preview"
# 共8个 Azure LLM 目标，支持负载均衡
```

**关键处理器**:
- `GracefulHangupController` - 优雅挂断
- `RealtimeBotSpeakingStateTracker` - Bot说话状态
- `RealtimeInputTranscriptionMonitor` - 输入转录监控
- `VoicemailDetector(mode="realtime")` - 语音信箱检测

---

### Bot 3: Copilot Bot (`copilot.py`) - 事件驱动模式

**特点**:
- 基于事件的协议 (类似OpenAI Realtime)
- 心跳机制保活
- 支持动态配置更新
- 适合多Agent协作

**事件协议**:
```python
# 客户端发送:
{
  "event_type": "action.create",
  "session_create": {...}
}

# 服务器发送:
{
  "event_type": "response.text",
  "text": "..."
}

{
  "event_type": "response.audio",
  "audio": "base64..."
}

{
  "event_type": "heartbeat"
}

{
  "event_type": "response.done"
}
```

**核心特性**:
- 自定义工具调用 (Tool-use integration)
- 上下文管理
- 动态配置切换
- 错误处理和重试

---

## ⚙️ 配置系统

### CallConfigManager 配置管理器 (config.py)

从 `call_data` 中提取配置：

```python
class CallConfigManager:
    
    # 1. ASR配置 (语音识别)
    get_asr_config() -> {
        "provider": "deepgram",           # 固定使用 Deepgram
        "locale": "zh-CN",                # 语言代码
        "name": "中文"                    # 显示名称
    }
    
    # 2. TTS配置 (语音合成)
    get_tts_config() -> {
        "provider": "cartesia",           # Cartesia或ElevenLabs
        "voice_id": "...",                # 语音ID
        "model": "sonic-2",               # 模型版本
        "locale": "zh-CN"
    }
    
    # 3. LLM配置 (语言模型)
    get_llm_config() -> {
        "provider": "openrouter",         # openrouter/azure/openai
        "model": "openai/gpt-4o",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "...",
        "prompt": "...",                  # 系统提示词
        "temperature": 0.2,               # 创意度
        "max_completion_tokens": 512,
        "llm_timeout_secs": 6.0,
        "retry_on_timeout": True
    }
    
    # 4. 流程配置
    get_flow_config() -> {
        "welcomeNode": {...},             # 欢迎节点
        "endNode": {...},                 # 结束节点
        "voicemailNode": {...},           # 留言节点
        "noResponse": {...}               # 无响应处理
    }
```

---

## 📞 服务创建 (services.py)

### STT 服务 (Speech-to-Text)

```python
def create_stt_service(asr_config, robot_code, robot_name):
    # 部分Robot使用 nova-3，其他使用 nova-2
    if robot_code in ["019d479f40eb78c6ab208597d643c90d", ...]:
        asr_model = "nova-3"
    else:
        asr_model = "nova-2"
    
    return DeepgramSTTService(
        api_key=os.environ.get("DEEPGRAM_API_KEY"),
        live_options=LiveOptions(
            model=asr_model,
            language=locale,
            smart_formatting=True,
            # ...
        )
    )
```

### TTS 服务 (Text-to-Speech)

支持多个提供商:
- **Cartesia**: `CartesiaTTSService` (sonic-2)
- **ElevenLabs**: (通过API)
- **PlayHT**: (备选)

### LLM 服务 (Language Model)

支持的提供商：
1. **OpenRouter** (默认)
   ```python
   OpenAILLMService(
       api_key="sk-or-v1-...",
       base_url="https://openrouter.ai/api/v1",
       model="openai/gpt-4o"
   )
   ```

2. **Azure LLM** (负载均衡)
   ```python
   AzureLLMService(
       endpoint="https://jinle-openai-us-east.openai.azure.com/",
       api_key="...",
       deployment="gpt-5.4-mini",
       api_version="2025-04-01-preview"
   )
   # 支持8个独立的Azure终端点，加权轮转
   ```

3. **OpenAI** (直连)
   ```python
   OpenAILLMService(
       api_key=os.getenv("OPENAI_API_KEY"),
       model="gpt-4o"
   )
   ```

4. **其他**:
   - DeepSeek (`DeepSeekLLMService`)
   - Qwen (`QwenLLMService`)

---

## 📊 通话记录生成 (notes.py)

### 功能流程

```
对话结束
    ↓
提取消息历史 (build_context_messages_with_timestamps)
    ↓
分析 (analyze_collection_dialogs)
    ↓
生成意图标签 (deepseek-flash 模型)
    ↓
保存到数据库
```

### 支持的分析引擎

- **OpenRouter** (默认) - 使用 Deepseek-v4-flash
- **Dify** - 集成Dify工作流

### 保存内容

```python
# 保存到数据库:
- 完整对话历史 (带时间戳)
- 提取的意图标签
- 通话指标 (时长、转录等)
- 关键信息提取
```

---

## 🔍 处理器架构

### 通用处理器 (processors_common.py)

1. **ContextMessageTimestampTracker**
   - 为每条消息分配唯一的毫秒级时间戳
   - 支持显式时间戳覆盖

2. **SilenceDetector**
   - 检测用户无响应窗口
   - 可配置超时时间 (默认6秒)
   - 检测间隔 (默认0.5秒)
   - 事件回调: `on_silence_detected`

### Standard 处理器与轮次策略

1. **TranscriptionLogger**
   - 记录STT结果

2. **HangupGate**
   - 监听挂断信号
   - 支持优雅关闭

3. **MinWordsUserTurnStartStrategy**
   - 收到至少一个词的 interim/final 转录后开始用户轮次并触发打断

4. **SpeechTimeoutUserTurnStopStrategy**
   - 根据 VAD 停止信号和转录状态结束用户轮次

5. **HangupPhraseDetector**
   - 检测助手结束语并触发优雅挂断

6. **VoicemailDetector**
   - 检测语音信箱

### 实时处理器 (processors_realtime.py)

1. **GracefulHangupController**
   - 优雅处理通话结束

2. **RealtimeBotSpeakingStateTracker**
   - 跟踪Bot说话状态

3. **RealtimeInputTranscriptionMonitor**
   - 监控用户输入转录

4. **VoicemailDetector(mode="realtime")**
   - 检测语音信箱

---

## 📈 指标收集 (call_metrics/)

### CallMetricsCollector

收集的关键指标：
- STT/TTS/LLM 调用次数和耗时
- Token 使用量
- 成本计算
- 通话时长
- 响应时间 (TTFB)

### RealtimeMetricsCollector

实时模式的轻量级指标收集

---

## 🌐 WebSocket 协议

### Beta 模式 (telephony_websocket.py)

**自定义协议序列化**:
```python
TelephonyFrameSerializer
    ↓
TelephonyEventRouter (电话事件路由)
```

### Copilot 模式 (copilot_websocket.py)

**事件驱动协议**:
```python
AICopilotEventRouter
    ↓
AICopilotFrameSerializer
    ↓
InnerEventRouter
```

支持的事件类型:
- `action.create` - 创建会话
- `response.text` - 文本响应
- `response.audio` - 音频响应
- `response.done` - 响应完成
- `heartbeat` - 心跳

---

## 🗄️ 数据库 (`storage/db.py`)

### DBClient

```python
class DBClient:
    @classmethod
    def execute(cls, sql, *params):
        """执行 SQL 查询或更新"""

    @classmethod
    def get_one(cls, sql, *params):
        """查询一行"""

    @classmethod
    def get_many(cls, sql, *params):
        """查询多行"""
```

`MysqlClient` 保留为兼容别名。默认使用 SQLite，也可以通过 `DATABASE_URL` 配置 MySQL。

### 核心表

| 表 | 用途 |
|---|---|
| `call_record` | 通话技术指标、模型、Token、耗时和成本 |
| `call_transcript` | 完整对话消息 |
| `call_analysis` | 智能催记、摘要等分析结果，业务字段保存在 `result_json` |

三张表通过 `call_uuid` 关联。MySQL 迁移脚本位于
`deploy/migrations/20261110_call_record_analysis.sql`。

---

## 🔐 安全和日志

### 日志系统 (app_logging.py)

**AppLogFilter**:
- 环境标志控制日志级别
- `APP_DEBUG_VOICE=1` - 记录Pipecat调试日志
- `APP_DEBUG_LLM=1` - 记录LLM提示词日志
- `APP_DEBUG_HTTP=1` - 记录HTTP请求日志

### Sentry 错误追踪 (sentry_config.py)

```python
# 配置项:
SENTRY_DSN = "http://..."
SENTRY_ENVIRONMENT = "prod"
SENTRY_SEND_DEFAULT_PII = true
SENTRY_TRACES_SAMPLE_RATE = 0.0
SENTRY_PROFILES_SAMPLE_RATE = 0.0

# 智能过滤:
- 过滤 WebSocket 连接断开噪音
- 只保留有意义的错误信息
```

---

## 🌍 多语言支持

### 支持的语言

```python
_FIRST_QUESTION_BY_LANGUAGE = {
    "zh": "你好",           # 中文
    "en": "Hello",          # 英文
    "es": "Hola",           # 西班牙文
    "tl": "Kumusta",        # 菲律宾语
}

# 默认无应答留言也根据语言定制
build_no_response_hangup_prompt(locale: str) -> str
```

### 语言特定功能

- 数字转文字 (中文、西班牙文、英文)
- 地名规范化
- 文化适配的提示词

---

## 🚀 部署

### Docker 镜像

**CPU 版本** (Dockerfile):
```dockerfile
FROM python:3.10-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["python", "main.py"]
```

**GPU 版本** (Dockerfile-gpu):
- 支持 CUDA 推理加速
- 用于本地STT/VAD模型

### Kubernetes 部署 (deploy.yaml)

- Service 配置
- Deployment 配置
- ConfigMap/Secret 管理

### 部署环境

```bash
# 开发环境
MODE=dev

# 测试环境
MODE=test

# 生产环境
MODE=prod FLY_API_KEY=... FLY_APP_NAME=risk-collection-agents
```

---

## 🔄 Git 工作流

### 最近提交记录

```
71613a8 - Merge remote-tracking branch 'origin/test' into test
b649fb3 - 沿用原有丢弃历史上下文的机制
2a9eefa - Merge branch 'prod' into test
5bc7881 - deepseek官方服务商增加user_id实现collection-agent服务调用隔离
2905502 - 文字可截断
e846d6f - 增加首句前置静音
0ffae93 - Merge remote-tracking branch 'origin/test' into test
aa5dc0c - 增加首句前置静音
```

### 分支策略

- **main** - 主分支
- **test** - 测试分支
- **prod** - 生产分支

---

## 📋 环境变量配置 (.env)

### 关键配置

```bash
# ===== LLM 服务 =====
OPENAI_API_KEY=sk-or-v1-...
OPENAI_API_URL=https://openrouter.ai/api/v1
OPENROUTER_API_KEY=...

# Azure (8个负载均衡终端点)
AZURE_LLM_1_ENDPOINT=https://...
AZURE_LLM_1_API_KEY=...
AZURE_LLM_1_DEPLOYMENT=gpt-5.4-mini
# ... AZURE_LLM_2 到 AZURE_LLM_8

# 其他LLM
DEEPSEEK_API_KEY=sk-...
ALIYUN_API_KEY=sk-...

# ===== 语音服务 =====
DEEPGRAM_API_KEY=...          # STT
CARTESIA_API_KEY=...          # TTS
ELEVENLABS_API_KEY=...        # TTS备选
PLAYHT_USER_ID=...            # TTS备选

# ===== 实时通话 =====
DAILY_API_URL=api.daily.co/v1
DAILY_API_KEY=...
DAILY_SAMPLE_ROOM_URL=...
TWILIO_ACCOUNT_SID=...
TWILIO_AUTH_TOKEN=...

# ===== 监控和日志 =====
SENTRY_DSN=...
SENTRY_ENVIRONMENT=prod
SENTRY_SEND_DEFAULT_PII=true

# ===== 部署 =====
FLY_API_KEY=...
FLY_APP_NAME=risk-collection-agents
MODE=prod

# ===== 调试开关 =====
APP_DEBUG_VOICE=1
APP_DEBUG_LLM=1
APP_DEBUG_HTTP=1
ALLOW_INTERRUPTIONS=1
```

---

## 🎯 关键业务流程

### 完整通话流程

```
1. 客户端连接 WebSocket
    ↓
2. 发送开始信号 (action: "start")
    ↓
3. 服务器加载配置 (CallConfigManager)
    ↓
4. 初始化 STT/LLM/TTS 服务
    ↓
5. 构建 Pipeline (语音处理管道)
    ↓
6. 欢迎语 (welcomeNode)
    ↓
7. 主对话循环:
   ├─ 接收用户语音
   ├─ STT 转录
   ├─ LLM 思考
   ├─ TTS 合成
   ├─ 发送回复
   └─ 检测中断/无响应/挂断
    ↓
8. 通话结束逻辑:
   ├─ 保存通话记录
   ├─ 分析对话内容
   ├─ 提取意图标签
   └─ 上报指标
    ↓
9. 结束语 (endNode)
    ↓
10. 断开连接
```

### 无响应处理

```
用户无响应 > 24 秒
    ↓
发送 SilenceDetector 事件
    ↓
执行无响应节点 (noResponse config)
    ↓
生成留言内容 (build_no_response_hangup_prompt)
    ↓
LLM 生成最终留言
    ↓
TTS 播放留言
    ↓
挂断电话
```

### 语音信箱检测

```
VoicemailDetector 监听
    ↓
检测到语音信箱特征
    ↓
根据 voicemailNode 配置:
├─ action == "hangup" → 立即挂断
└─ action != "hangup" → 继续流程
```

---

## 💡 核心特性

### 1. 多模式支持
- ✅ 标准轮转模式 (standard)
- ✅ 流式实时模式 (realtime)
- ✅ 事件驱动模式 (copilot)

### 2. 多语言支持
- ✅ 中文、英文、西班牙文、菲律宾语
- ✅ 地域和语言特定的提示词

### 3. 多 LLM 集成
- ✅ OpenRouter (GPT-4o, Claude等)
- ✅ Azure (8个负载均衡终端点)
- ✅ OpenAI (直接API)
- ✅ Deepseek/Qwen/Aliyun

### 4. 智能通话管理
- ✅ 自动中断检测
- ✅ 无响应自动处理
- ✅ 语音信箱识别
- ✅ 优雅挂断

### 5. 完整分析和记录
- ✅ 通话记录生成
- ✅ 意图标签提取
- ✅ 通话指标收集
- ✅ 成本追踪

### 6. 企业级特性
- ✅ Sentry 错误追踪
- ✅ 结构化日志记录
- ✅ MySQL 数据持久化
- ✅ Kubernetes 容器化部署
- ✅ 请求链路追踪

---

## 🔧 快速开始

### 安装依赖
```bash
pip install -r requirements.txt
```

### 配置环境
```bash
cp .env.temp.example .env.temp
# 编辑 .env.temp，填入必要的API Key
```

### 启动服务
```bash
python main.py
# 或使用 Uvicorn
uvicorn main:app --host 0.0.0.0 --port 8765
```

### 测试连接
```bash
# WebSocket 连接示例
wscat -c ws://localhost:8765/ws-v2

# 发送开始信号
{"action": "start", "data": {"callee": "1234567890", "config": {...}}}
```

---

## 📚 文件大小参考

| 文件 | 大小 | 说明 |
|-----|-----|------|
| standard.py | 39 KB | 标准Bot实现 |
| copilot.py | 49 KB | Copilot Bot |
| realtime.py | 84 KB | 实时Bot (最复杂) |
| processors_beta.py | 36 KB | Beta处理器 |
| standard_modules/notes.py | 31 KB | 通话记录处理 |
| call_metrics/collector.py | 22 KB | 指标收集 |

---

## 🎓 项目特色

1. **先进的语音AI架构**
   - 支持流式边说边听（Realtime）
   - 支持传统轮转式（Beta）
   - 支持事件驱动（Copilot）

2. **生产级可靠性**
   - 完整的错误处理和重试机制
   - Sentry 实时监控
   - 结构化日志追踪

3. **灵活的配置系统**
   - 动态支持多种LLM服务商
   - 流程节点可配置
   - 语言和地区适配

4. **完整的业务闭环**
   - 从通话初始化到结束分析
   - 自动意图提取
   - 成本计算和追踪

5. **企业集成就绪**
   - Dify工作流集成
   - N8N自动化集成
   - Kubernetes部署

---

这是一个非常成熟的企业级AI语音交互系统！

---

## Fast-Call 独立外呼服务 V1（2026-11-13）

项目现支持完整外呼任务闭环：创建任务、加密导入名单、按容量拨号、Agent 通话、失败重试、结果查询和 Webhook。该能力与原有三种 Bot 模式复用同一 Collection Agent Pipeline，但任务、调度和拨号状态由独立 Fast-Call 模块管理。

| 模块 | 主要实现 |
| --- | --- |
| Open/Admin API | `src/fastcall/api/openapi/`、`adminapi/` |
| 任务与名单 | `application/outbound_tasks.py`、`outbound_queries.py` |
| Scheduler | `application/scheduler.py`、`scheduler_main.py` |
| Adapter | `application/dispatch.py`、`media.py`、`adapter_main.py` |
| Agent Session | `application/call_events.py`、`collection_agents/session_registry.py` |
| 事件与结果 | `application/telephony_events.py`、`call_results.py` |
| Outbox 与 Webhook | `application/webhooks.py`、`infrastructure/webhooks/` |
| 数据迁移 | `migrations/versions/0002_outbound_service.py`、`0003_call_analysis.py`、`0004_outbound_call_analysis.py` |

关键保证包括单次 originate、重启不补发、MySQL 条件更新、Agent 代际控制、双向音频有界缓冲、原子终态与 Outbox、结果修订及安全 Webhook。详细组件图和事务边界见 `ARCHITECTURE.md` 第 11 节，启动步骤见 `QUICK_START.md` 的 Fast-Call 部分。

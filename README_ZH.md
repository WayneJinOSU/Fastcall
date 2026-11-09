# Collection Agents - 智能语音机器人系统

**[中文] [English]** | **[项目概览](PROJECT_OVERVIEW.md) | [系统架构](ARCHITECTURE.md) | [快速开始](QUICK_START.md)**

## 🎯 项目简介

Collection Agents 是一个基于 **Pipecat** 框架的企业级 **AI 语音交互系统**，主要用于自动催收电话、客服咨询和语音助手场景。

### 核心特性

- ✅ **三种运行模式**：标准Bot (轮转式) / 实时Bot (流式) / Copilot Bot (事件驱动)
- ✅ **多LLM支持**：OpenRouter / Azure / OpenAI / Deepseek / Qwen 等
- ✅ **完整的语音处理**：Deepgram STT + Cartesia TTS + LLM推理
- ✅ **多语言支持**：中文、英文、西班牙文、菲律宾语等
- ✅ **智能通话管理**：自动中断检测、无响应处理、语音信箱识别、优雅挂断
- ✅ **完整分析和记录**：通话记录生成、意图标签提取、成本计算、指标追踪
- ✅ **企业级可靠性**：Sentry 错误追踪、结构化日志、SQLite/MySQL 持久化、Kubernetes 部署
- ✅ **自主部署友好**：默认 SQLite 零配置存储，LLM 支持任意 OpenAI 兼容端点（可接本地 vLLM/Ollama）

---

## 🚀 快速开始

### 1️⃣ 环境准备

```bash
# 克隆仓库
git clone https://github.com/SuperWuli/collection-agents.git
cd collection-agents

# 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Linux/macOS

# 安装依赖
pip install -r requirements.txt
```

### 2️⃣ 配置API密钥

编辑 `.env` 文件，配置以下必需项：

```bash
# LLM 服务
OPENAI_API_KEY=sk-or-v1-xxxx          # OpenRouter
DEEPGRAM_API_KEY=xxxxx                # 语音识别
CARTESIA_API_KEY=sk_car_xxxxx         # 语音合成
```

详见 [.env 配置说明](QUICK_START.md#2-配置api密钥)

### 3️⃣ 启动服务

```bash
# 直接运行
python main.py

# 或使用 Uvicorn
uvicorn main:app --host 0.0.0.0 --port 8765
```

### 4️⃣ 测试连接

```bash
# 使用 WebSocket 客户端连接
wscat -c ws://localhost:8765/ws-v2

# 发送启动信号
{
  "action": "start",
  "data": {
    "callee": "15551234567",
    "config": {
      "modelConfig": {
        "provider": "openrouter",
        "name": "openai/gpt-4o"
      }
    }
  }
}
```

更详细说明见 [快速开始指南](QUICK_START.md)

---

## 📚 文档导航

| 文档 | 说明 |
|------|------|
| **[项目概览](PROJECT_OVERVIEW.md)** | 📋 完整的项目介绍、架构设计、核心模块说明 |
| **[系统架构](ARCHITECTURE.md)** | 🏗️ 详细的架构图、数据流、处理流程等 |
| **[快速开始](QUICK_START.md)** | 🚀 安装、配置、使用示例、常见问题 |
| **[本文件](README_ZH.md)** | 📖 项目总览和导航 |

---

## 🏗️ 项目结构

```
collection-agents/
├── main.py                    # FastAPI 入口
├── bot_beta.py                # 标准Bot (轮转式)
├── realtime_alpha.py          # 实时Bot (流式)
├── bot_copilot_beta.py        # Copilot Bot (事件驱动)
│
├── bot_beta_modules/          # 核心业务模块
│   ├── config.py              # 配置管理
│   ├── services.py            # 服务创建
│   └── notes.py               # 通话记录
│
├── my_utils/                  # 工具库
│   ├── mysql.py               # 数据库
│   ├── websocket_*.py         # WebSocket 支持
│   ├── text_normalization.py  # 文本处理
│   └── ...
│
├── processors_*.py            # 处理器
├── call_metrics/              # 指标收集
├── app_logging.py             # 日志系统
├── sentry_config.py           # 错误追踪
│
├── deploy/                    # 部署配置
├── requirements.txt           # 依赖清单
├── .env                       # 环境变量
└── README_ZH.md              # 本文件
```

详见 [项目概览](PROJECT_OVERVIEW.md#-目录结构)

---

## 🤖 三种运行模式对比

### 1. 标准Bot (`bot_beta.py`) - 轮转式交互

- **工作模式**：用户说 → 识别 → LLM思考 → 回复
- **适用场景**：催收电话、客服咨询等通用场景
- **优点**：完整的上下文管理、支持中断和优雅退出
- **延迟**：2-6 秒

```
WebSocket → STT → LLM → TTS → 音频输出
```

### 2. 实时Bot (`realtime_alpha.py`) - 流式交互

- **工作模式**：PCM 音频流 → Realtime LLM → 实时音频输出
- **适用场景**：高端应用、需要低延迟的场景
- **优点**：流式处理、边说边听、更自然的对话
- **延迟**：< 1 秒

```
音频流 → RNNoise降噪 → Realtime LLM → 实时TTS
```

### 3. Copilot Bot (`bot_copilot_beta.py`) - 事件驱动

- **工作模式**：事件驱动的协议 (类似 OpenAI Realtime)
- **适用场景**：AI助手、多Agent协作
- **优点**：支持工具调用、心跳保活、动态配置
- **延迟**：低

```
事件流 → Frame Pipeline → 事件响应
```

详见 [系统架构 - 三种Bot模式](ARCHITECTURE.md#-三种bot模式对比)

---

## 🔌 核心功能详解

### 1️⃣ 配置管理系统

**BotV3ConfigManager** 管理四个配置区域：

```python
config = BotV3ConfigManager(call_data)

# ASR 配置
asr = config.get_asr_config()
# → {provider: "deepgram", locale: "zh-CN"}

# TTS 配置
tts = config.get_tts_config()
# → {provider: "cartesia", voice_id: "...", model: "sonic-2"}

# LLM 配置
llm = config.get_llm_config()
# → {provider: "openrouter", model: "openai/gpt-4o", ...}

# 流程配置
flow = config.get_flow_config()
# → {welcomeNode: {...}, endNode: {...}, noResponse: {...}}
```

### 2️⃣ LLM 服务集成

支持多个 LLM 提供商，智能选择和负载均衡：

```python
# OpenRouter (默认)
OpenAILLMService(
    api_key="sk-or-v1-...",
    base_url="https://openrouter.ai/api/v1",
    model="openai/gpt-4o"
)

# Azure (8个负载均衡终端点)
select_azure_llm_target()  # 加权轮转

# 其他: OpenAI, Deepseek, Qwen, Aliyun
```

### 3️⃣ 无响应自动处理

系统自动检测用户无响应 (>24秒)，并执行：

1. 生成对应语言的留言提示词
2. LLM 生成最终留言内容
3. TTS 播放留言
4. 自动挂断

支持多语言（中英西菲）

### 4️⃣ 通话记录生成

自动保存完整的通话记录，包括：

- 对话历史 (带时间戳)
- 意图标签提取 (使用 Deepseek-v4-flash)
- 关键信息提取
- 通话指标 (时长、成本等)

### 5️⃣ 完整的指标收集

```python
metrics = CallMetricsCollector()
# 收集: STT调用 / Token使用 / 成本计算 / TTFB / 通话时长
```

---

## ⚙️ 环境变量配置

### 必需配置

```bash
# LLM 服务
OPENAI_API_KEY=sk-or-v1-xxxxx           # OpenRouter
# 或 Azure 配置
AZURE_LLM_1_ENDPOINT=https://...
AZURE_LLM_1_API_KEY=...

# 语音服务
DEEPGRAM_API_KEY=xxxxx                  # STT
CARTESIA_API_KEY=sk_car_xxxxx           # TTS
```

### 可选配置

```bash
# 错误追踪
SENTRY_DSN=http://...

# 调试开关
APP_DEBUG_VOICE=1
APP_DEBUG_LLM=1
APP_DEBUG_HTTP=1

# 部署
MODE=prod|test|dev
FLY_API_KEY=...
```

详见 [完整配置说明](QUICK_START.md#-api-请求示例)

---

## 📊 性能指标

### 标准Bot性能

| 指标 | 值 | 说明 |
|------|-----|------|
| 平均响应延迟 | 2-6s | STT + LLM + TTS |
| 单次通话成本 | $0.02-0.05 | 取决于模型和使用量 |
| Token 利用率 | 70-90% | 高效的上下文管理 |
| 通话成功率 | >95% | 含重试机制 |

### 实时Bot性能

| 指标 | 值 | 说明 |
|------|-----|------|
| 平均响应延迟 | <1s | 流式处理 |
| 单次通话成本 | $0.03-0.06 | 实时LLM计费 |
| 并发能力 | 取决于硬件 | 单机支持数十并发 |

---

## 🛠️ 开发指南

### 添加自定义处理器

```python
from pipecat.processors.frame_processor import FrameProcessor

class MyProcessor(FrameProcessor):
    async def process_frame(self, frame, direction):
        # 处理逻辑
        await self.push_frame(frame, direction)
```

### 集成新的LLM服务

```python
from pipecat.services.base_llm import BaseLLMService

class MyLLMService(BaseLLMService):
    async def process(self, context):
        # 实现 LLM 调用
        pass
```

### 自定义WebSocket协议

继承 `FrameSerializer` 实现自定义序列化。

更多开发示例见 [快速开始 - 代码片段](QUICK_START.md#-常用代码片段)

---

## 🚀 部署指南

### Docker 部署

```bash
# CPU 版本
docker build -f deploy/Dockerfile -t collection-agents .
docker run -p 8765:8765 collection-agents

# GPU 版本
docker build -f deploy/Dockerfile-gpu -t collection-agents-gpu .
```

### Kubernetes 部署

```bash
kubectl apply -f deploy/deploy.yaml
```

详见 `deploy/` 目录

---

## 📞 常见问题

### Q1: WebSocket 连接失败？

检查服务是否运行：
```bash
curl http://localhost:8765/state/up
```

### Q2: LLM 响应缓慢？

调整超时和重试设置：
```python
"config": {
  "llmTimeoutSecs": 10,
  "retryOnTimeout": true
}
```

### Q3: 如何支持新语言？

编辑 `bot_beta_modules/config.py` 中的 `_FIRST_QUESTION_BY_LANGUAGE`

### Q4: 通话小结分析用的是什么引擎？

默认使用内置的本地后处理工作流（`collection_note/` 模块），
通过 OpenAI 兼容接口完成意图分析，无需外部工作流平台。

更多问题见 [快速开始 - 故障排除](QUICK_START.md#-故障排除)

---

## 🔗 相关资源

- **Pipecat**: https://github.com/pipecat-ai/pipecat
- **Deepgram**: https://www.deepgram.com/
- **Cartesia**: https://www.cartesia.ai/
- **OpenRouter**: https://openrouter.ai/
- **Azure**: https://azure.microsoft.com/

---

## 📝 许可证

根据项目设置，此仓库使用相应许可证。

---

## 👥 贡献指南

欢迎贡献代码！请：

1. Fork 本仓库
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 开启 Pull Request

---

## 📧 联系方式

- GitHub: https://github.com/SuperWuli/collection-agents
- Issues: https://github.com/SuperWuli/collection-agents/issues

---

## 🎉 致谢

感谢所有贡献者和使用者的支持！

---

**最后更新**: 2025-07-09  
**项目版本**: v0.0.104 (基于 Pipecat)  
**推荐Python版本**: 3.10+

**快速链接**：[项目概览](PROJECT_OVERVIEW.md) | [系统架构](ARCHITECTURE.md) | [快速开始](QUICK_START.md)

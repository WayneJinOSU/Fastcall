# Collection Agents 快速参考指南

## 🚀 5分钟快速开始

### 1. 环境准备

```bash
# 克隆项目
git clone https://github.com/SuperWuli/collection-agents.git
cd collection-agents

# 创建Python虚拟环境 (Python 3.10+)
python -m venv venv
source venv/bin/activate  # macOS/Linux
# 或 venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt
```

### 2. 配置API密钥

编辑 `.env` 文件，至少配置以下必需项：

```bash
# 必需：LLM
OPENAI_API_KEY=sk-or-v1-xxxx                    # OpenRouter
# 或
AZURE_LLM_1_ENDPOINT=https://...
AZURE_LLM_1_API_KEY=...
AZURE_LLM_1_DEPLOYMENT=gpt-5.4-mini
AZURE_LLM_1_API_VERSION=2025-04-01-preview

# 必需：语音
DEEPGRAM_API_KEY=xxxxx                          # STT
CARTESIA_API_KEY=sk_car_xxxxx                   # TTS

# 可选：错误追踪
SENTRY_DSN=http://...
```

### 3. 启动服务

```bash
# 方法1：直接运行
python main.py

# 方法2：使用Uvicorn
uvicorn main:app --host 0.0.0.0 --port 8765

# 方法3：启用调试模式
APP_DEBUG_VOICE=1 APP_DEBUG_LLM=1 python main.py
```

### 3.1 配置通话数据主动回调

当 `outbound_source` 为 `collection_harness` 时，通话结束后服务会向
Harness 发送一次 `POST` 请求：回调地址通过环境变量配置——`MODE=test` 时使用
`HARNESS_CALLBACK_URL_TEST`（缺省回退 `HARNESS_CALLBACK_URL`），其他 `MODE` 使用
`HARNESS_CALLBACK_URL`；未配置时跳过回调，其他来源也不会发送该回调。
超时时间和重试次数已在代码中固定配置。

外呼来源通过 `data.config.variables.outbound_source` 传入，可选值为
`hermes`、`collection_harness`、`agent_reach`，缺省为 `hermes`。

请求体为 JSON，主要字段如下：

```json
{
  "schemaVersion": "1.0",
  "eventType": "call.completed",
  "callUuid": "...",
  "businessId": "...",
  "workOrderId": "...",
  "outboundSource": "collection_harness",
  "call": {"...": "完整通话指标"},
  "dialogs": [{"role": "user", "content": "...", "timestamp": 0}],
  "prompt": {"content": "...", "model": "...", "parameters": {}},
  "collectionNote": {"repaymentWish": "...", "collectionResult": "...", "remark": "..."}
}
```

成功状态码为 2xx；5xx、网络异常和超时会按配置重试，4xx 不重试。请求失败只记录日志，不会阻断本地 `process_record` / `context_log` 保存。

### 4. 测试连接

```bash
# 安装WebSocket客户端
npm install -g wscat

# 连接到服务器
wscat -c ws://localhost:8765/ws-v2

# 发送开始信号
{
  "action": "start",
  "data": {
    "callee": "15551234567",
    "businessID": "test_id",
    "agentID": "test_agent",
    "config": {
      "name": "test_bot",
      "modelConfig": {
        "provider": "openrouter",
        "name": "openai/gpt-4o",
        "config": {
          "temperature": 0.2,
          "maxTokens": 512
        }
      },
      "promptConfig": {
        "content": "You are a helpful assistant."
      }
    }
  }
}
```

---

## 📚 核心概念速览

### 三种运行模式

| 模式 | 文件 | 特点 | 何时使用 |
|------|------|------|---------|
| **标准Bot** | `bot_beta.py` | 轮转式对话，完整上下文 | 一般场景、催收电话 |
| **实时Bot** | `realtime_alpha.py` | 流式处理，边说边听，低延迟 | 高端应用、需要快速响应 |
| **Copilot** | `bot_copilot_beta.py` | 事件驱动，支持工具调用 | AI助手、多Agent场景 |

### 配置四大块

```python
# config.py 中的 BotV3ConfigManager 管理四个配置：

1. asr_config = get_asr_config()
   - provider: "deepgram"
   - locale: "zh-CN" (语言)
   - name: "显示名称"

2. tts_config = get_tts_config()
   - provider: "cartesia"
   - voice_id: "语音ID"
   - model: "sonic-2"
   - locale: "zh-CN"

3. llm_config = get_llm_config()
   - provider: "openrouter" | "azure" | "openai"
   - model: "模型名称"
   - prompt: "系统提示词"
   - temperature: 0.2
   - max_completion_tokens: 512

4. flow_config = get_flow_config()
   - welcomeNode: {...}      # 欢迎语
   - endNode: {...}          # 结束语
   - voicemailNode: {...}    # 语音信箱处理
   - noResponse: {...}       # 无响应处理
```

---

## 🔧 常用代码片段

### 1. 启动标准Bot

```python
from bot_beta import run_bot_beta

# 在 main.py 的 WebSocket 端点中
await run_bot_beta(websocket, callee, call_data)
```

### 2. 启动实时Bot

```python
from realtime_alpha import run_bot_realtime_alpha

await run_bot_realtime_alpha(websocket, callee, call_data)
```

### 3. 自定义配置加载

```python
from bot_beta_modules.config import BotV3ConfigManager

config_manager = BotV3ConfigManager(call_data)
asr_config = config_manager.get_asr_config()
tts_config = config_manager.get_tts_config()
llm_config = config_manager.get_llm_config()
flow_config = config_manager.get_flow_config()
```

### 4. 创建LLM服务

```python
from bot_beta_modules.services import create_llm_service

llm_service = create_llm_service(llm_config, robot_code="xxx")
```

### 5. 保存通话记录

```python
from bot_beta_modules.notes import save_conversation

await save_conversation(
    context=llm_context,
    call_data=call_data,
    call_metrics=metrics,
    route=route  # 通话路由/质量标签
)
```

### 6. 检测无响应

```python
from processors_common import SilenceDetector

detector = SilenceDetector(
    timeout_sec=24,
    check_interval_sec=0.5
)
detector.on_silence_detected += async_callback
```

### 7. 记录指标

```python
from call_metrics.collector import CallMetricsCollector

collector = CallMetricsCollector()
# 自动监听所有 Frame 事件
# 通过 get_metrics() 获取结果
metrics = collector.get_metrics()
```

---

## 📱 API 请求示例

### WebSocket 请求格式 (Beta模式)

```json
{
  "action": "start",
  "data": {
    "callee": "15551234567",
    "businessID": "16592232",
    "agentID": "agent_001",
    "config": {
      "name": "collection_bot_v1",
      "asr": {
        "provider": "deepgram",
        "locale": "zh-CN"
      },
      "tts": {
        "provider": "cartesia",
        "voice_id": "...",
        "model": "sonic-2",
        "locale": "zh-CN"
      },
      "modelConfig": {
        "provider": "openrouter",
        "name": "openai/gpt-4o",
        "config": {
          "temperature": 0.2,
          "maxTokens": 512,
          "topP": 0.9,
          "frequencyPenalty": 0,
          "presencePenalty": 0,
          "seed": 42
        }
      },
      "promptConfig": {
        "content": "你是一个催收代理。请礼貌地联系客户..."
      },
      "flowConfig": {
        "welcomeNode": {
          "content": "你好，请问有几分钟时间吗？"
        },
        "endNode": {
          "content": "感谢您的时间，再见。"
        },
        "voicemailNode": {
          "action": "hangup"
        },
        "noResponse": {
          "hangupMessage": "我稍后再联系您"
        }
      }
    }
  }
}
```

### 回调请求格式

```bash
# POST /callback/call_record

# 任务完成回调
{
  "code": "callback_id",
  "orgCode": "org_id",
  "orgName": "组织名",
  "name": "任务名",
  "status": 7,              # 完成
  "startDate": "2025-08-10",
  "numberCount": 1546,
  "dialedCount": 1546,
  "completedCount": 1546
}

# 通话记录回调
{
  "uuid": "call_uuid",
  "orgCode": "org_id",
  "caller": "caller_id",
  "callee": "15551234567",
  "status": 4,              # 已接
  "beginTime": "2025-08-10 21:42:52",
  "answerTime": "2025-08-10 21:43:01",
  "endTime": "2025-08-10 21:43:47",
  "recordPath": "s3://...",
  "hangupCode": "200",
  "talkDuration": 46,       # 秒
  "callDuration": 55,
  "businessId": "16592232",
  "intention": "I"
}
```

---

## 🛠️ 故障排除

### 问题1：WebSocket连接失败

```bash
# 检查服务是否运行
curl http://localhost:8765/state/up

# 检查防火墙
netstat -an | grep 8765

# 查看日志
tail -f logs/app.log
```

### 问题2：LLM超时

```python
# 调整超时配置 (在config中)
"config": {
  "llmTimeoutSecs": 10,      # 改大一点
  "retryOnTimeout": true     # 启用重试
}
```

### 问题3：STT识别不准确

```bash
# 切换模型版本
# services.py 中修改 asr_model = "nova-3"

# 或调整语言设置
"asr": {
  "locale": "en-US",         # 改为目标语言
  "smart_formatting": true
}
```

### 问题4：内存泄漏

```bash
# 启用调试
APP_DEBUG_VOICE=1 python main.py

# 查看是否有未关闭的连接
# 检查日志中的 "pipeline_started" 和 "pipeline_stopped"
```

---

## 📊 监控和调试

### 启用调试模式

```bash
# 启用所有调试信息
APP_DEBUG_VOICE=1 \
APP_DEBUG_LLM=1 \
APP_DEBUG_HTTP=1 \
python main.py
```

### 查看日志

```python
# app_logging.py 使用 loguru
# 日志将输出到 stdout

# 级别：DEBUG -> INFO -> WARNING -> ERROR -> CRITICAL

# 查看Sentry错误
# 访问你自己部署的 Sentry 仪表板（地址取决于你的 Sentry 部署）
```

### 性能指标

```python
# 获取通话指标
metrics = collector.get_metrics()

print(f"STT: {metrics.get('stt_calls')} 次")
print(f"LLM: {metrics.get('llm_input_tokens')} + {metrics.get('llm_output_tokens')} tokens")
print(f"TTS: {metrics.get('tts_calls')} 次")
print(f"总成本: ${metrics.get('total_cost'):.4f}")
print(f"通话时长: {metrics.get('duration_sec')}s")
print(f"TTFB: {metrics.get('ttfb_ms')}ms")
```

---

## 🎯 常见用例

### 用例1：部署催收电话机器人

```python
# 1. 配置中文提示词
config = {
  "asr": {"locale": "zh-CN"},
  "tts": {"locale": "zh-CN"},
  "promptConfig": {
    "content": "你是友好的催收代理..."
  }
}

# 2. 配置无响应处理
"flowConfig": {
  "noResponse": {
    "hangupMessage": "我会稍后再给您来电"
  }
}

# 3. 启动Bot
await run_bot_beta(websocket, callee, {
  "data": {"config": config}
})
```

### 用例2：实时客服助手

```python
# 1. 使用实时Bot模式
await run_bot_realtime_alpha(websocket, callee, call_data)

# 2. 配置低延迟
config = {
  "modelConfig": {
    "config": {
      "llmTimeoutSecs": 3,     # 快速响应
      "maxTokens": 256         # 保持简洁
    }
  }
}
```

### 用例3：多语言支持

```python
# 自动检测语言
from bot_beta_modules.config import normalize_language_code

locale = "es-MX"
lang_code = normalize_language_code(locale)  # "es"

# 配置对应语言的首句
from bot_beta_modules.config import build_first_question_content
first_q = build_first_question_content(locale)  # "Hola"
```

---

## 🚀 高级配置

### 使用Azure LLM负载均衡

系统会自动从8个Azure LLM终端点中选择：

```python
# services.py 中的 select_azure_llm_target()
# 权重配置：
# LLM_1-4: 权重=1 (低优先级)
# LLM_5-7: 权重=4 (高优先级)  
# LLM_8:   权重=0 (禁用)

# 加权轮转算法会根据权重选择
```

### 自定义处理器

```python
from pipecat.processors.frame_processor import FrameProcessor, FrameDirection
from pipecat.frames.frames import Frame

class MyCustomProcessor(FrameProcessor):
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        # 自定义处理逻辑
        await super().process_frame(frame, direction)
        # 修改或监控frame
        await self.push_frame(frame, direction)
```

### 通话小结分析引擎

```python
# bot_beta_modules/notes.py

# 默认使用内置的本地后处理工作流（collection_note/），
# 通过 OpenAI 兼容接口完成意图分析，无需外部工作流平台。
# 如需调整，设置环境变量：
COLLECTION_NOTE_ENGINE=post_process
```

---

## 📞 支持和资源

### 文档
- 📄 `PROJECT_OVERVIEW.md` - 完整项目概览
- 🏗️ `ARCHITECTURE.md` - 系统架构详解
- 📖 `QUICK_START.md` - 本文件

### 代码入口
- `main.py` - 服务入口点
- `bot_beta.py` - 标准Bot实现
- `realtime_alpha.py` - 实时Bot实现
- `bot_copilot_beta.py` - Copilot Bot实现

### 关键模块
- `bot_beta_modules/config.py` - 配置管理
- `bot_beta_modules/services.py` - 服务创建
- `bot_beta_modules/notes.py` - 通话记录
- `processors_*.py` - 处理器实现
- `my_utils/` - 工具库

### 日志和监控
- `app_logging.py` - 日志配置
- `sentry_config.py` - Sentry错误追踪
- `call_metrics/` - 指标收集

---

## 🔍 快速导航

### 我想...

| 目标 | 执行 |
|------|------|
| 启动服务 | `python main.py` |
| 测试WebSocket | `wscat -c ws://localhost:8765/ws-v2` |
| 更改LLM | 编辑 `config.get_llm_config()` |
| 更改语言 | 编辑 `config.get_asr_config()` 的 locale |
| 添加自定义处理器 | 继承 `FrameProcessor` 在 `processors_*.py` |
| 查看通话记录 | 调用 `save_conversation()` |
| 收集指标 | 使用 `CallMetricsCollector` |
| 调试问题 | 启用 `APP_DEBUG_*=1` |
| 部署到生产 | 使用 `deploy/Dockerfile` 和 `deploy.yaml` |

---

祝您使用愉快！🎉

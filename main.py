import os
import json
import time
import traceback
import asyncio
import uvicorn
import logging

from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import HTMLResponse

from app_logging import configure_logging, set_request_trace_code
from sentry_config import init_sentry
from my_utils.mysql import MysqlClient
configure_logging()
init_sentry()

from bot_beta import run_bot_beta
from realtime_alpha import run_bot_realtime_alpha
from bot_copilot_beta import run_copilot_bot_beta


import ssl

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

# from n8n_helper.get_push_list import router as get_push_list_router

# import ssl
# import certifi
#
# # ... 在 _connect 方法中 ...
#
# # 1. 创建 SSL 上下文
# ssl_context = ssl.create_default_context(cafile=certifi.where())

# Default Vosk model path used by create_stt_service (services.py). Override via
# VOSK_MODEL_PATH env if the deployment mounts the model elsewhere.
DEFAULT_VOSK_MODEL_PATH = "/mnt/data/vosk-model-small-es-0.42"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Preheat the Vosk model at startup so the first call doesn't drop audio.

    Vosk's Model() cold load takes ~15s locally (longer on network storage). The
    model is cached process-wide in vosk_stt._vosk_model_cache, so loading it once
    here means every subsequent call hits the cache and is ready instantly. Runs as
    a background task to avoid blocking server startup; failures are logged, not
    raised, so a missing model/vosk never prevents the app from serving traffic.
    """

    async def _preheat_vosk():
        model_path = os.getenv("VOSK_MODEL_PATH", DEFAULT_VOSK_MODEL_PATH)
        if not os.path.isdir(model_path):
            logging.info(f"[Vosk preheat] skipped, path not found: {model_path}")
            return
        try:
            from bot_beta_modules.vosk_stt import _get_or_load_model

            logging.info(f"[Vosk preheat] loading model from {model_path} ...")
            t0 = time.perf_counter()
            await _get_or_load_model(model_path)
            logging.info(f"[Vosk preheat] model ready in {time.perf_counter() - t0:.2f}s")
        except Exception as exc:
            logging.warning(f"[Vosk preheat] failed (will retry on first call): {exc}")

    preheat_task = asyncio.create_task(_preheat_vosk())
    try:
        yield
    finally:
        if not preheat_task.done():
            preheat_task.cancel()


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# app.include_router(get_push_list_router, prefix="/n8n")


@app.middleware("http")
async def inject_trace_code(request: Request, call_next):
    # Allow upstream systems to pass through a stable request trace id.
    trace_code = (
        request.headers.get("x-trace-code")
        or request.headers.get("trace_code")
        or request.query_params.get("trace_code")
    )
    set_request_trace_code(trace_code)
    response = await call_next(request)
    return response


@app.get("/")
async def start_call():
    return {"message": "server is running"}


@app.post("/")
async def start_call():
    logging.info("POST TwiML")
    return HTMLResponse(content=open("templates/streams.xml").read(), media_type="application/xml")


@app.get("/state/up")
async def state_up():
    return {"code": 200, "data": {}, "msg": "success"}


@app.websocket("/ws-v2")
async def websocket_endpoint_v3(websocket: WebSocket):
    await websocket.accept()
    set_request_trace_code(
        websocket.headers.get("x-trace-code")
        or websocket.headers.get("trace_code")
        or websocket.query_params.get("trace_code")
    )
    logging.info("WebSocket connection accepted for v3")

    async for message in websocket.iter_text():
        if isinstance(message, str):
            try:
                call_data = json.loads(message)

                if call_data.get("action") == "start":
                    data = call_data.get("data", {})
                    callee = str(data.get("callee", ""))[-10:]
                    business_id = data.get("businessID")
                    agent_id = data.get("agentID", "")

                    logging.info(f"call_uuid:{call_data.get("data", {}).get("callUUID", "")}, callee: {callee}, business_id: {business_id}, agent_id: {agent_id}")

                    config = data.get("config", {})
                    agent_name = config.get("name", "")

                    bot_type = "general_bot"
                    if "realtime" in agent_name:
                        bot_type = "realtime_bot"

                    if callee or True:  # Allow testing even without callee
                        logging.info(f"server run with {bot_type}")
                        if bot_type == "realtime_bot":
                            await run_bot_realtime_alpha(websocket, callee or 'test', call_data)
                        else:
                            await run_bot_beta(websocket, callee or 'test', call_data)

                        break
            except json.JSONDecodeError:
                logging.info("Received non-JSON message, skipping...")
        else:
            logging.info("Received non-string message, skipping...")


@app.websocket("/ws-ai-copilot")
async def websocket_copilot_endpoint(websocket: WebSocket):
    """
    WebSocket endpoint for AI Copilot - Event-driven protocol
    Implements CollectionAgent & VoiceAgent interaction
    """
    await websocket.accept()
    set_request_trace_code(
        websocket.headers.get("x-trace-code")
        or websocket.headers.get("trace_code")
        or websocket.query_params.get("trace_code")
    )
    logging.info("WebSocket connection accepted for AI Copilot")

    try:
        # Wait for initial action.create event with session configuration
        message = await websocket.receive_text()
        event = json.loads(message)

        if event.get("event_type") != "action.create":
            expected_type = event.get('event_type')
            logging.error(
                f"Expected action.create event, got: {expected_type}"
            )
            await websocket.close(
                code=1008,
                reason="Expected action.create event"
            )
            return

        session_create = event.get("session_create", {})

        if not session_create:
            logging.error(
                "Missing session_create data in action.create event"
            )
            await websocket.close(
                code=1008,
                reason="Missing session_create data"
            )
            return

        # Run the copilot bot with event-driven protocol
        await run_copilot_bot_beta(websocket, event)

    except asyncio.CancelledError:
        # Handle task cancellation when WebSocket is forcibly closed
        logging.warning("Copilot websocket task was cancelled")
        # Don't re-raise, let finally execute
    except json.JSONDecodeError as e:
        logging.error(f"JSON decode error: {e}")
        try:
            await websocket.close(code=1007, reason="Invalid JSON")
        except Exception:
            pass  # WebSocket may already be closed
    except Exception as e:
        logging.error(f"Error in copilot websocket: {e}")
        logging.error(traceback.format_exc())
    finally:
        logging.info("Copilot WebSocket connection closed")
        # Ensure WebSocket is properly closed
        try:
            await websocket.close()
        except Exception:
            pass  # Already closed or error closing


@app.post("/callback/call_record")
async def call_record(req: dict):
    '''
    外呼平台回调入口，支持两类负载：

    1) 任务回调：{"code": "...", "name": "...", "status": 7, "numberCount": ..., ...}
    2) 通话记录回调：{"uuid": "...", "caller": "...", "callee": "...", "status": 4,
       "beginTime": "...", "recordPath": "<录音文件 URL>", "businessId": "...", ...}

    完整字段示例见 request.json / 文档。
    '''

    logging.info("call_record")
    logging.info(f"call_record_raw: {req}")

    def etl_callback_log(data):
        data_copy = data.copy()
        data_copy['data'] = json.dumps(data)
        return data_copy

    callback_log = etl_callback_log(req)

    insert_sql = """
        INSERT INTO callback_log
        (callee, business_id, data, ctime1, uuid)
        VALUES
        (:callee, :business_id, :data, :ctime1, :uuid)
    """
    params = {
        "callee": callback_log.get("callee"),
        "business_id": callback_log.get("businessId"),
        "data": callback_log["data"],
        "ctime1": int(time.time() * 1000),
        "uuid": callback_log.get("uuid"),
    }

    MysqlClient.execute(insert_sql, params)

    return {'code': 200, 'msg': 'success', 'data': None}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)
    # uvicorn.run(app, host="localhost", port=8765)

"""HTTP / WebSocket 路由注册。

普通函数 ``register_routes(app)`` 显式注册全部端点：
  - GET  ``/``                      健康探活
  - POST ``/``                      返回 TwiML（templates/streams.xml）
  - GET  ``/state/up``              健康检查
  - WS   ``/ws-v2``                 外呼机器人入口（standard / realtime 按 agent 名分流）
  - WS   ``/ws-ai-copilot``         人工坐席 AI Copilot（事件驱动协议）
  - POST ``/callback/call_record``  外呼平台通话记录回调
"""

import asyncio
import json
import logging
import time
import traceback

from fastapi import FastAPI, Request, WebSocket
from starlette.responses import HTMLResponse

from collection_agents.bots.copilot import run_copilot_bot_beta
from collection_agents.bots.realtime import run_bot_realtime_alpha
from collection_agents.bots.standard import run_bot_beta
from collection_agents.infra.logging import set_request_trace_code
from collection_agents.infra.paths import PROJECT_ROOT
from collection_agents.storage.db import MysqlClient

_TWIML_PATH = PROJECT_ROOT / "templates" / "streams.xml"


def register_routes(app: FastAPI):
    @app.get("/")
    async def index():
        return {"message": "server is running"}

    @app.post("/")
    async def twiml():
        logging.info("POST TwiML")
        return HTMLResponse(content=_TWIML_PATH.read_text(), media_type="application/xml")

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

                        logging.info(f"call_uuid:{call_data.get('data', {}).get('callUUID', '')}, callee: {callee}, business_id: {business_id}, agent_id: {agent_id}")

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

        完整字段示例见 examples/request.json / 文档。
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

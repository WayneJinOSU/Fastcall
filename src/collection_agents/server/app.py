"""FastAPI 应用装配：实例 + 中间件 + 路由。"""

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from collection_agents.infra.logging import configure_logging, set_request_trace_code
from collection_agents.infra.sentry import init_sentry
from collection_agents.server.lifespan import lifespan
from collection_agents.server.routes import register_routes

configure_logging()
init_sentry()

app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for testing
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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


register_routes(app)

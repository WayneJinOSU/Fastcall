import asyncio
import os

import uvicorn
from dotenv import load_dotenv

from fastcall.main import app
from fastcall.scheduler_main import run_scheduler
from fastcall.webhook_main import run_webhook_worker


load_dotenv()


async def run_local_app():
    server = uvicorn.Server(uvicorn.Config(
        app,
        host=os.getenv("FASTCALL_API_HOST", "0.0.0.0"),
        port=int(os.getenv("FASTCALL_API_PORT", "8770")),
        log_level=os.getenv("FASTCALL_LOG_LEVEL", "info").lower(),
    ))
    server_task = asyncio.create_task(server.serve())
    tasks = {
        server_task,
        asyncio.create_task(run_scheduler()),
        asyncio.create_task(run_webhook_worker()),
    }
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        shutdown_requested = server.should_exit
    finally:
        server.should_exit = True
        background_tasks = tasks - {server_task}
        for task in background_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*background_tasks, return_exceptions=True)
        await asyncio.gather(server_task, return_exceptions=True)

    for task in done:
        task.result()
    if done != {server_task} or not shutdown_requested:
        raise RuntimeError("A local Fast-Call component stopped unexpectedly")


if __name__ == "__main__":
    asyncio.run(run_local_app())

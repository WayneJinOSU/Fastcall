"""服务启动入口。

免安装直接运行（自动把 src/ 加入 import 路径）：

    python main.py

或交给 uvicorn / gunicorn：

    uvicorn main:app --host 0.0.0.0 --port 8765
    uvicorn collection_agents.server.app:app   # 已 pip install -e . 时
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import uvicorn

from collection_agents.server.app import app  # noqa: E402,F401

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8765)

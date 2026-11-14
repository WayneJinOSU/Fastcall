from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse


DEFAULT_STATIC_DIR = Path(__file__).resolve().parents[2] / "console" / "dist"


def verify_console_static(static_dir: Path) -> None:
    index = static_dir / "index.html"
    assets = static_dir / "assets"
    if not index.is_file() or not assets.is_dir() or not any(assets.iterdir()):
        raise RuntimeError("Fast-Call Console production build is missing; run npm run build")


def register_console_static(app: FastAPI, static_dir=None) -> None:
    static_root = Path(static_dir or DEFAULT_STATIC_DIR).resolve()
    app.state.console_static_dir = static_root

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse("/console/apps")

    @app.get("/console", include_in_schema=False)
    async def console_root():
        return RedirectResponse("/console/apps")

    @app.get("/console/assets/{asset_path:path}", include_in_schema=False)
    async def asset(asset_path: str):
        path = (static_root / "assets" / asset_path).resolve()
        if static_root / "assets" not in path.parents or not path.is_file():
            return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "Asset not found"}})
        return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})

    @app.get("/console/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path == "api" or path.startswith("api/"):
            return JSONResponse(status_code=404, content={"error": {"code": "NOT_FOUND", "message": "API route not found"}})
        return FileResponse(static_root / "index.html", headers={"Cache-Control": "no-cache"})

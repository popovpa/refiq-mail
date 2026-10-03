from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.core.logging import configure_logging
from app.health import readiness

configure_logging()

app = FastAPI(title="RefIQ Mail", version="0.1.0", docs_url=None, redoc_url=None)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ready")
async def ready():
    report = await readiness()
    if report["ready"]:
        return {"status": "ready"}
    return JSONResponse(status_code=503, content={"status": "unavailable"})

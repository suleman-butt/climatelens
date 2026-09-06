from fastapi import FastAPI

from climatelens import __version__

app = FastAPI(title="ClimateLens", version=__version__)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}

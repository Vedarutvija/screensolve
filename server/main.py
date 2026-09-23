from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from server.database import Base, engine
from server.routers import captures

Base.metadata.create_all(bind=engine)

app = FastAPI(title="ScreenSolve", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(captures.router)


@app.get("/health")
def health():
    return {"status": "ok"}


DASH = Path(__file__).resolve().parent.parent / "dashboard"

app.mount("/assets", StaticFiles(directory=DASH / "assets"), name="assets")


@app.get("/")
def index():
    return FileResponse(DASH / "index.html")

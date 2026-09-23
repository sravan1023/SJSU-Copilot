import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

load_dotenv()

from logging_config import setup_logging

setup_logging()

from runtime import lifespan
from routers import chat, professors, jobs, telemetry, guest

app = FastAPI(title="SJSU Copilot API", lifespan=lifespan)

# allow_origins=["*"] together with allow_credentials=True is rejected by
# browsers for credentialed requests and is far wider than this app needs. Set
# ALLOWED_ORIGINS to a comma-separated list in deployment.
DEFAULT_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173"
allowed_origins = [
    origin.strip()
    for origin in os.getenv("ALLOWED_ORIGINS", DEFAULT_ORIGINS).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(chat.router, prefix="/api")
app.include_router(professors.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(telemetry.router, prefix="/api")
app.include_router(guest.router, prefix="/api")


@app.get("/")
def health():
    return {"status": "ok", "service": "sjsu-copilot-backend"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)

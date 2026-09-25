"""Municipal assistant API.

    uv run uvicorn app.main:app --reload --port 8000
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .schemas import AskRequest, AskResponse

app = FastAPI(title="Chișinău Municipal Assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

NOT_FOUND = {
    "ro": "Informația nu a fost găsită în documentele disponibile.",
    "ru": "В доступных документах информация не найдена.",
}


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/ask")
def ask(req: AskRequest) -> AskResponse:
    # Stub until retrieval over the offline_indexation corpus is wired in.
    lang = req.lang or "ro"
    return AskResponse(status="not_found", lang=lang, answer=NOT_FOUND[lang])

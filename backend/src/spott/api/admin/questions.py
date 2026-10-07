"""Admin → what people asked: the low ratings, the quick questions, the gaps (questions without a full answer)."""

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool

from ..deps import service
from ..errors import ApiException
from ..schemas import (
    FeedbackItem,
    FeedbackList,
    FeedbackStats,
    GapList,
    GapRecheck,
    Suggestion,
    SuggestionCreate,
    SuggestionList,
)
from .auth import require_admin
from .sources import admin_store
from .views import iso

router = APIRouter(prefix="/api/admin", dependencies=[Depends(require_admin)])


@router.get("/feedback", response_model=FeedbackList)
async def low_rated(request: Request, max_rating: int = Query(2, ge=1, le=5),
                    limit: int = Query(50, ge=1, le=500)) -> FeedbackList:
    rows = await run_in_threadpool(admin_store(request).feedback, max_rating, limit)
    return FeedbackList(items=[FeedbackItem(**{k: iso(r.get(k)) for k in FeedbackItem.model_fields}) for r in rows])


@router.get("/feedback/stats", response_model=FeedbackStats)
async def feedback_stats(request: Request) -> FeedbackStats:
    return FeedbackStats(**await run_in_threadpool(admin_store(request).feedback_stats))


@router.post("/suggestions", response_model=Suggestion, status_code=201)
async def pin_suggestion(req: SuggestionCreate, request: Request) -> Suggestion:
    """A question the admin wants among the quick questions; shown once the next re-check answers it well."""
    suggestions = service(request, "suggestions")
    return await run_in_threadpool(suggestions.add, req.question, req.lang, req.pinned,
                                   getattr(request.app.state, "translate", None))


@router.get("/suggestions", response_model=SuggestionList)
async def admin_suggestions(request: Request) -> SuggestionList:
    """Every quick question once, with its texts in every language and whether it is shown yet."""
    suggestions = service(request, "suggestions")
    return SuggestionList(items=await run_in_threadpool(suggestions.admin_list))


@router.delete("/suggestions/{suggestion_id}")
async def hide_suggestion(suggestion_id: int, request: Request) -> dict:
    if not await run_in_threadpool(service(request, "suggestions").hide, suggestion_id):
        raise ApiException(404, "not_found", f"No quick question {suggestion_id}")
    return {"ok": True}


@router.get("/gaps", response_model=GapList)
async def list_gaps(request: Request, status: str = "not_found,partial", lang: str | None = None,
                    days: int = Query(30, ge=1, le=3650), limit: int = Query(50, ge=1, le=500),
                    hidden: bool = False) -> GapList:
    """Similar not_found / partial questions grouped (local embeddings, no LLM), biggest groups first."""
    statuses = [x for x in status.split(",") if x in ("not_found", "partial")]
    if not statuses or lang not in (None, "ro", "ru"):
        raise ApiException(422, "validation_error", "status: not_found and/or partial; lang: ro or ru")
    return GapList(**await run_in_threadpool(service(request, "gaps").list, statuses, lang, days, limit, hidden))


@router.post("/gaps/{gap_id}/recheck", response_model=GapRecheck)
async def recheck_gap(gap_id: str, request: Request) -> GapRecheck:
    """Asks the group's example again (one model call, only on this click). Answered now → the group leaves the
    default list."""
    result = await run_in_threadpool(service(request, "gaps").recheck, gap_id, service(request, "ask_once"))
    if result is None:
        raise ApiException(404, "not_found", f"No gap {gap_id}")
    return GapRecheck(**result)


@router.post("/gaps/{gap_id}/hide")
async def hide_gap(gap_id: str, request: Request) -> dict:
    if not await run_in_threadpool(service(request, "gaps").hide, gap_id, True):
        raise ApiException(404, "not_found", f"No gap {gap_id}")
    return {"ok": True}


@router.post("/gaps/{gap_id}/unhide")
async def unhide_gap(gap_id: str, request: Request) -> dict:
    if not await run_in_threadpool(service(request, "gaps").hide, gap_id, False):
        raise ApiException(404, "not_found", f"No gap {gap_id}")
    return {"ok": True}

"""Schemas of the API without models or a database."""

from spott.api.schemas import HealthResponse


def test_health_response():
    health = HealthResponse(
        status="ok",
        device="mps",
        models_loaded=True,
        chunk_count=2269,
    )
    assert health.status == "ok"
    assert health.device == "mps"
    assert health.chunk_count == 2269

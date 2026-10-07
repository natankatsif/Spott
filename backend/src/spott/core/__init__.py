"""Retrieval package for Chișinău Municipal Assistant."""

from .config import (
    EMBEDDING_MODEL_NAME,
    RRF_K,
    TOP_CANDIDATES,
    TOP_K,
)
from .db import (
    INIT_SQL,
    get_connection,
    get_pool,
    init_db,
)
from .embeddings import (
    free_device_cache,
    get_device,
    get_embedding_model,
    uses_half_precision,
)
from .pipeline import (
    RetrievalResult,
    retrieve,
)
from .search import (
    HybridSearcher,
    build_fts_query,
    clean_tsquery_term,
    deduplicate_results,
    execute_fts_query,
    execute_line_fts_query,
    execute_line_vector_query,
    execute_vector_query,
    get_chunks_by_ids,
    kind_priority,
    rrf_fuse,
    weighted_rrf_fuse,
)

__all__ = [
    "EMBEDDING_MODEL_NAME",
    "HybridSearcher",
    "INIT_SQL",
    "RRF_K",
    "RetrievalResult",
    "TOP_CANDIDATES",
    "TOP_K",
    "build_fts_query",
    "clean_tsquery_term",
    "deduplicate_results",
    "execute_fts_query",
    "execute_line_fts_query",
    "execute_line_vector_query",
    "execute_vector_query",
    "free_device_cache",
    "get_chunks_by_ids",
    "get_connection",
    "get_device",
    "get_embedding_model",
    "get_pool",
    "init_db",
    "kind_priority",
    "retrieve",
    "rrf_fuse",
    "uses_half_precision",
    "weighted_rrf_fuse",
]

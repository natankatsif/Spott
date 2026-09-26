"""Retrieval package for Chișinău Municipal Assistant."""

from .config import (
    EMBEDDING_MODEL_NAME,
    NOT_FOUND_THRESHOLD,
    RERANK_TOP_K,
    RERANKER_MODEL_NAME,
    RRF_K,
    TOP_CANDIDATES,
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
from .rerank import (
    get_reranker_model,
    rerank_candidates,
)
from .search import (
    HybridSearcher,
    build_fts_query,
    clean_tsquery_term,
    deduplicate_results,
    execute_fts_query,
    execute_vector_query,
    kind_priority,
    rrf_fuse,
)

__all__ = [
    "EMBEDDING_MODEL_NAME",
    "HybridSearcher",
    "INIT_SQL",
    "NOT_FOUND_THRESHOLD",
    "RERANK_TOP_K",
    "RERANKER_MODEL_NAME",
    "RRF_K",
    "TOP_CANDIDATES",
    "build_fts_query",
    "clean_tsquery_term",
    "deduplicate_results",
    "execute_fts_query",
    "execute_vector_query",
    "free_device_cache",
    "get_connection",
    "get_device",
    "get_embedding_model",
    "get_pool",
    "get_reranker_model",
    "init_db",
    "kind_priority",
    "rerank_candidates",
    "rrf_fuse",
    "uses_half_precision",
]

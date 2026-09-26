"""Search interface and CLI (re-exported from retrieval)."""

from retrieval.search import (
    RESULT_COLUMNS,
    RRF_K,
    STOP_WORDS,
    HybridSearcher,
    build_fts_query,
    clean_tsquery_term,
    deduplicate_results,
    execute_fts_query,
    execute_vector_query,
    kind_priority,
    main,
    parse_args,
    rrf_fuse,
)

__all__ = [
    "RESULT_COLUMNS",
    "RRF_K",
    "STOP_WORDS",
    "HybridSearcher",
    "build_fts_query",
    "clean_tsquery_term",
    "deduplicate_results",
    "execute_fts_query",
    "execute_vector_query",
    "kind_priority",
    "main",
    "parse_args",
    "rrf_fuse",
]

if __name__ == "__main__":
    main()

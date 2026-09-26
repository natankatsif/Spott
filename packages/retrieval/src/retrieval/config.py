"""Central configuration for retrieval and reranking."""

TOP_CANDIDATES: int = 30
RERANK_TOP_K: int = 8
NOT_FOUND_THRESHOLD: float = 0.0093
RRF_K: int = 60
RERANKER_MODEL_NAME: str = "BAAI/bge-reranker-v2-m3"
EMBEDDING_MODEL_NAME: str = "BAAI/bge-m3"
RERANKER_MAX_LENGTH: int = 512
RERANKER_BATCH_SIZE: int = 16

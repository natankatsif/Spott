import os

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

TOP_CANDIDATES: int = 30
RERANK_TOP_K: int = 8
NOT_FOUND_THRESHOLD: float = 0.0093
RRF_K: int = 60
RERANKER_MODEL_NAME: str = "BAAI/bge-reranker-v2-m3"
EMBEDDING_MODEL_NAME: str = "BAAI/bge-m3"
RERANKER_MAX_LENGTH: int = 1024
RERANKER_BATCH_SIZE: int = 16

# Reranker flag: defaults to false (disabled on memory-constrained machines)
RERANKER_ENABLED: bool = os.getenv("RERANKER_ENABLED", "false").lower() in ("true", "1", "yes")

# Weighted RRF scores: W_VECTOR for chunk vector, W_FTS for full text, W_LINE for line vector
W_VECTOR: float = float(os.getenv("RRF_W_VECTOR", "1.0"))
W_FTS: float = float(os.getenv("RRF_W_FTS", "0.1"))
W_LINE: float = float(os.getenv("RRF_W_LINE", "1.0"))

# Tool response character limits (to prevent agent context overflow)
MAX_TOOL_SEARCH_CHARS: int = int(os.getenv("MAX_TOOL_SEARCH_CHARS", "12000"))
MAX_TOOL_GREP_CHARS: int = int(os.getenv("MAX_TOOL_GREP_CHARS", "8000"))
MAX_TOOL_TOC_CHARS: int = int(os.getenv("MAX_TOOL_TOC_CHARS", "10000"))
MAX_TOOL_OPEN_CHARS: int = int(os.getenv("MAX_TOOL_OPEN_CHARS", "10000"))


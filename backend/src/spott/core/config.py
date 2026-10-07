import os

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv())

TOP_CANDIDATES: int = 30
TOP_K: int = 8  # results retrieve() returns by default
RRF_K: int = 60
EMBEDDING_MODEL_NAME: str = "BAAI/bge-m3"

# Weighted RRF scores: W_VECTOR for chunk vector, W_FTS for full text, W_LINE for line vector
W_VECTOR: float = float(os.getenv("RRF_W_VECTOR", "1.0"))
W_FTS: float = float(os.getenv("RRF_W_FTS", "0.1"))
W_LINE: float = float(os.getenv("RRF_W_LINE", "1.0"))


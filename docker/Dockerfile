# One image for the API and the admin worker (docker-compose.yml, profile "server").
# Build context: the repo root (backend depends on packages/retrieval through the uv workspace).
FROM python:3.14-slim

# pg_restore for the index dump; Tesseract for OCR (Docling on Linux); GL/GLib for OpenCV; headless LibreOffice,
# which Docling runs to open .doc / .rtf / .xls / .ppt (without it those files fail to parse).
RUN apt-get update && apt-get install -y --no-install-recommends \
        postgresql-client tesseract-ocr tesseract-ocr-ron tesseract-ocr-rus libgl1 libglib2.0-0 curl \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONUTF8=1 HF_HOME=/app/.hf

# Dependencies first: this layer stays cached while only the code changes.
COPY pyproject.toml uv.lock ./
COPY backend/pyproject.toml backend/
COPY offline_indexation/pyproject.toml offline_indexation/
COPY packages/retrieval/pyproject.toml packages/retrieval/
# torch without CUDA: the lock's Linux torch pulls ~5 GB of nvidia-* packages no CPU server uses. Everything
# else comes from the lock; torch and torchvision of the same versions from PyTorch's CPU index.
ARG NO_CUDA="--no-install-package torch --no-install-package torchvision --no-install-package triton \
    --no-install-package cuda-bindings --no-install-package cuda-pathfinder --no-install-package cuda-toolkit \
    --no-install-package nvidia-cublas --no-install-package nvidia-cuda-cupti --no-install-package nvidia-cuda-nvrtc \
    --no-install-package nvidia-cuda-runtime --no-install-package nvidia-cudnn-cu13 --no-install-package nvidia-cufft \
    --no-install-package nvidia-cufile --no-install-package nvidia-curand --no-install-package nvidia-cusolver \
    --no-install-package nvidia-cusparse --no-install-package nvidia-cusparselt-cu13 \
    --no-install-package nvidia-nccl-cu13 --no-install-package nvidia-nvjitlink --no-install-package nvidia-nvshmem-cu13 \
    --no-install-package nvidia-nvtx"
RUN uv sync --frozen --no-dev --no-install-workspace $NO_CUDA \
    && uv pip install --no-deps --index-url https://download.pytorch.org/whl/cpu torch==2.14.0 torchvision==0.29.0

COPY packages packages
COPY backend backend
COPY offline_indexation offline_indexation
COPY docker docker
RUN uv sync --frozen --no-dev --inexact $NO_CUDA  # --inexact: keep the CPU torch installed above

# The embedding model (~2.3 GB) goes into the image, so the server doesn't download it on start.
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"
ENV HF_HUB_OFFLINE=1

EXPOSE 8000
CMD ["sh", "docker/start-api.sh"]

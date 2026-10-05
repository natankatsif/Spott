"""Spott: the municipal assistant's backend.

    spott.core    the database, embeddings and search, shared by the other two
    spott.ingest  crawl, download, parse, chunk and index the sources (CLI stages and the admin worker)
    spott.api     the FastAPI app the frontend talks to

`api` and `ingest` both build on `core` and never import each other (import-linter, pyproject.toml).
"""

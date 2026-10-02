"""Runtime configuration.

Everything environment-specific resolves here, and every cloud dependency has
a local fallback so the system runs and tests without a single API key. That
fallback is not a convenience: it is what makes `make test` meaningful in CI.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"

    # --- LLM: Groq primary, OpenRouter fallback -----------------------------
    groq_api_key: str = ""
    # One model for both jobs: openai/gpt-oss-120b is the strongest available on
    # the free tier and is reliable at JSON extraction as well as prose.
    groq_model_extract: str = "openai/gpt-oss-120b"
    groq_model_explain: str = "openai/gpt-oss-120b"
    groq_base_url: str = "https://api.groq.com/openai/v1"

    # Groq's free tier rate-limits hard, which is exactly the condition that
    # makes a second provider worth having. Only consulted after Groq fails.
    #
    # The OpenRouter model is the one free endpoint that reliably honours
    # `response_format: json_object`, which extraction depends on. Verified
    # against the live catalogue: `qwen/*:free` and `gemma/*:free` both returned
    # 429 or ignored the parameter when checked.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # These must be `:free` endpoints. Verified against the live catalogue:
    # only these honoured `response_format: json_object` on the free tier, which
    # profile extraction depends on. Others returned 429, 403, 400, or ignored
    # the parameter and answered with prose.
    openrouter_model_extract: str = "nvidia/nemotron-3-super-120b-a12b:free"
    openrouter_model_explain: str = "nvidia/nemotron-3-super-120b-a12b:free"
    llm_timeout_s: float = 30.0

    # --- Embeddings ----------------------------------------------------------
    # all-MiniLM-L6-v2 runs locally via fastembed. No API key, no cost.
    # Override only to force a deterministic embedder in tests.
    embed_backend: str = "auto"  # auto | fastembed | openai | hash
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-ada-002"
    embed_dim: int = 512  # only used by the hash fallback

    # --- Vector store --------------------------------------------------------
    qdrant_url: str = ""
    qdrant_api_key: str = ""
    qdrant_collection: str = "bank_sathi_policies"
    qdrant_local_path: str = ""

    # --- Database ------------------------------------------------------------
    database_url: str = ""
    db_pool_size: int = 5
    db_max_overflow: int = 5
    db_statement_timeout_ms: int = 15_000

    # --- Data ----------------------------------------------------------------
    cards_json_path: str = "data/cards.json"
    synthetic_seed: int = 20_240_607
    policy_docs_dir: str = "data/policies"

    # --- Retrieval -----------------------------------------------------------
    rag_top_k: int = 5
    rag_min_score: float = 0.15

    # --- Explanation ---------------------------------------------------------
    max_explain_retries: int = 2
    enable_verifier_llm_check: bool = False

    @property
    def has_llm(self) -> bool:
        """True if any provider is configured. Callers degrade on a per-call
        basis, so this only gates the question of whether to try at all."""
        return bool(self.groq_api_key or self.openrouter_api_key)

    @property
    def has_groq(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def has_openrouter(self) -> bool:
        return bool(self.openrouter_api_key)

    @property
    def has_remote_embeddings(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def has_qdrant(self) -> bool:
        return bool(self.qdrant_url) or bool(self.qdrant_local_path)

    @property
    def has_postgres(self) -> bool:
        return self.database_url.startswith(("postgres://", "postgresql://"))

    def resolve(self, relative: str) -> Path:
        p = Path(relative)
        return p if p.is_absolute() else REPO_ROOT / p


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

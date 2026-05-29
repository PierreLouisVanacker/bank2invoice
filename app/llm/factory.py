"""Factory : instancie le bon LLMClient selon la config."""

from __future__ import annotations

from app.config import settings
from app.llm.base import LLMClient


def get_llm_client() -> LLMClient:
    """Renvoie le client LLM configuré dans .env (settings.llm_provider)."""
    provider = settings.llm_provider.lower()

    if provider == "ollama":
        from app.llm.ollama import OllamaClient

        return OllamaClient(host=settings.ollama_host, model=settings.ollama_model)

    if provider == "stub":
        from app.llm.stub import StubLLM

        return StubLLM()

    if provider == "anthropic":
        # À implémenter au besoin
        raise NotImplementedError(
            "Le client Anthropic n'est pas encore implémenté. "
            "Utilise LLM_PROVIDER=ollama ou stub."
        )

    raise ValueError(
        f"LLM_PROVIDER inconnu : {provider!r}. "
        "Valeurs attendues : 'ollama', 'stub', 'anthropic'."
    )

"""Configuration centralisée chargée depuis .env."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings chargées depuis .env, avec defaults raisonnables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Database
    database_url: str = "sqlite:///data/db.sqlite"

    # Stockage
    pdfs_dir: Path = Path("data/pdfs")
    factures_dir: Path = Path("data/factures")

    # LLM
    llm_provider: str = "ollama"
    ollama_host: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    # LaTeX
    latex_binary: str = "xelatex"

    # Auth : secret pour signer les cookies de session.
    # OBLIGATOIRE en prod, générer avec `python -c "import secrets; print(secrets.token_urlsafe(32))"`
    session_secret: str = ""

    # App
    debug: bool = True

    def ensure_dirs(self) -> None:
        """Crée les dossiers nécessaires s'ils n'existent pas."""
        self.pdfs_dir.mkdir(parents=True, exist_ok=True)
        self.factures_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BACKEND_DIR.parent / ".env", BACKEND_DIR / ".env"), extra="ignore"
    )

    database_url: str = "postgresql+asyncpg://interviewbud:interviewbud@localhost:5544/interviewbud"

    # Azure OpenAI (v1 GA endpoint: https://<resource>.openai.azure.com/openai/v1/)
    azure_openai_endpoint: str = ""
    azure_openai_api_key: str = ""
    # Low-latency model that speaks as the interviewer (e.g. gpt-4.1 / gpt-4.1-mini / gpt-5-chat).
    azure_openai_chat_deployment: str = "gpt-4.1"
    # Model for offline work: resume extraction, planning, scorecards, self-critique.
    azure_openai_reasoning_deployment: str = ""
    # For a chat deployment that always reasons (grok-4.x, gpt-5): "minimal"/"low" keeps live replies fast.
    azure_openai_chat_reasoning_effort: str = ""
    # Preparation-journey steps (extraction, coverage, plan, review) are mostly careful copying and mapping; on an
    # always-reasoning model, "low" cuts minutes off each step. Blank = the model's default.
    prep_reasoning_effort: str = "low"
    azure_openai_transcribe_deployment: str = "gpt-4o-mini-transcribe"

    fish_api_key: str = ""
    fish_tts_model: str = "s2.1-pro"
    fish_asr_model: str = "transcribe-1-pro"
    fish_voice_id: str = ""
    tts_sample_rate: int = 24000
    tts_latency: str = "balanced"

    # fish = Fish Audio cloud; self-hosted servers speaking fish-speech's HTTP API:
    # fish_local = fish-speech (infra/fish-speech), chatterbox = Chatterbox-Turbo (infra/chatterbox),
    # pocket = Kyutai Pocket TTS on CPU (infra/pocket-tts)
    tts_provider: str = "fish"
    tts_server_url: str = "http://127.0.0.1:8790"
    tts_server_api_key: str = ""
    tts_server_sample_rate: int = 24000  # 24000 Chatterbox, 44100 fish-speech S2-Pro

    # local = faster-whisper in-process; model per https://huggingface.co/Systran
    local_stt_model: str = "small.en"

    stt_provider: str = "fish"  # fish | azure | local

    sandbox_python_image: str = "interviewbud-sandbox:py312"
    sandbox_timeout_s: float = 10.0
    sandbox_memory: str = "256m"

    cors_origins: str = "http://localhost:5199,http://127.0.0.1:5199"

    # Job-page reading fallbacks for JS-rendered pages (optional): Firecrawl /v2/scrape, Browserless /content.
    firecrawl_api_key: str = ""
    browserless_api_key: str = ""
    browserless_url: str = "https://production-sfo.browserless.io"

    # Auth. SECRET_KEY signs agent tokens; generated once into backend/data/.secret_key if unset.
    secret_key: str = ""
    cookie_secure: bool = False  # set true behind HTTPS
    session_remember_days: int = 30
    session_hours: int = 12  # sliding lifetime of a session the user didn't ask to remember

    @property
    def azure_base_url(self) -> str:
        base = self.azure_openai_endpoint.rstrip("/")
        if not base.endswith("/openai/v1"):
            base += "/openai/v1"
        return base + "/"

    @property
    def tts_self_hosted(self) -> bool:
        return self.tts_provider in ("fish_local", "chatterbox", "pocket")

    @property
    def tts_configured(self) -> bool:
        return bool(self.tts_server_url) if self.tts_self_hosted else bool(self.fish_api_key)

    @property
    def tts_output_rate(self) -> int:
        return self.tts_server_sample_rate if self.tts_self_hosted else self.tts_sample_rate

    @property
    def reasoning_deployment(self) -> str:
        return self.azure_openai_reasoning_deployment or self.azure_openai_chat_deployment


@lru_cache
def get_settings() -> Settings:
    return Settings()

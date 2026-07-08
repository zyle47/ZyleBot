from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    lmstudio_base_url: str = "http://localhost:1234/v1"
    lmstudio_model: str = "qwythos-9b-claude-mythos-5-1m"

    agent_max_steps: int = 12
    agent_request_timeout_s: float = 120.0

    tool_max_file_read_bytes: int = 1_000_000
    tool_search_max_results: int = 200

    # --- Web access (Phase F) ---
    # Pluggable search backend. "duckduckgo" needs no API key; brave/tavily
    # (key-based) can be added later by filling the reserved keys below.
    search_provider: str = "duckduckgo"
    tool_max_fetch_chars: int = 8000
    web_request_timeout_s: float = 20.0
    # Reserved for future key-based providers (empty = unused).
    brave_api_key: str = ""
    tavily_api_key: str = ""

    # --- Action tools (write/exec, all confirm_required) ---
    command_timeout_s: float = 30.0
    command_max_output_chars: int = 10_000

    db_path: str = Field(default="data/zylebot.db", validation_alias="ZYLEBOT_DB_PATH")

    host: str = Field(default="127.0.0.1", validation_alias="ZYLEBOT_HOST")
    port: int = Field(default=8000, validation_alias="ZYLEBOT_PORT")

    # The one globally-shared fact injected into every conversation's system
    # prompt. Empty = the model knows nothing personal about the user.
    user_name: str = Field(default="", validation_alias="USER_NAME")


settings = Settings()

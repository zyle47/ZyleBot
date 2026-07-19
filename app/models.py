from pydantic import BaseModel, Field, field_validator


class ChatRequest(BaseModel):
    message: str = ""
    # base64 data URLs (e.g. "data:image/jpeg;base64,...") for vision-capable models.
    images: list[str] = []


class ConfirmRequest(BaseModel):
    approved: bool


class ModelRequest(BaseModel):
    model: str


class ProviderConnectRequest(BaseModel):
    # Empty = reuse the key already saved (runtime or .env).
    api_key: str = ""


class ScoreSubmit(BaseModel):
    """Breakout high-score submission (POST /api/scores). Bounds keep a buggy
    client from writing garbage; abuse doesn't matter on a localhost app."""

    initials: str = Field(min_length=1, max_length=3)
    score: int = Field(ge=0, le=1_000_000)
    level: int = Field(default=1, ge=1, le=99)

    @field_validator("initials")
    @classmethod
    def _normalize_initials(cls, v: str) -> str:
        normalized = "".join(char for char in v.upper() if char.isalnum())[:3]
        if not normalized:
            raise ValueError("initials must contain letters/digits")
        return normalized

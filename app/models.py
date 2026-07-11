from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str = ""
    # base64 data URLs (e.g. "data:image/jpeg;base64,...") for vision-capable models.
    images: list[str] = []


class ConfirmRequest(BaseModel):
    approved: bool


class ModelRequest(BaseModel):
    model: str

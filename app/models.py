from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str


class ConfirmRequest(BaseModel):
    approved: bool


class ModelRequest(BaseModel):
    model: str

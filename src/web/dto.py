from typing import Literal

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64, description="대화 식별자(브라우저 생성 uuid)")
    message: str = Field(min_length=1, max_length=4000, description="사용자 질문")


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    rag: bool
    ollama: bool

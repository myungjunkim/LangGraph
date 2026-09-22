"""LLM 객체 생성 단일 지점. 사내 서버 이전 시 이 파일만 교체한다."""
from langchain_ollama import ChatOllama

from src.config.settings import Settings


def create_chat_model(settings: Settings) -> ChatOllama:
    return ChatOllama(
        model=settings.llm_model,
        base_url=settings.ollama_base_url,
        temperature=settings.temperature,
        num_ctx=settings.num_ctx,
        reasoning=False,  # qwen3 thinking 모드 비활성화
        client_kwargs={"timeout": settings.llm_timeout},
    )

"""브라우저용 FastAPI 앱. 에이전트 한 턴을 SSE 로 흘려보낸다."""
import json
from typing import AsyncIterator

import httpx
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, HumanMessage

from src.agent import RUNTIME_ERRORS
from src.config.settings import PROJECT_ROOT, Settings
from src.web.dto import ChatRequest, HealthResponse

STATIC_DIR = PROJECT_ROOT / "resources" / "static"


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def check_rag(base_url: str, timeout: float) -> bool:
    try:
        return httpx.get(f"{base_url.rstrip('/')}/check", timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def check_ollama(base_url: str, model: str, timeout: float) -> bool:
    try:
        response = httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout)
        if response.status_code != 200:
            return False
        return model in {m.get("name", "") for m in response.json().get("models", [])}
    except (httpx.HTTPError, ValueError):
        return False


async def stream_events(graph, config: dict, text: str) -> AsyncIterator[tuple[str, object]]:
    """그래프 한 턴을 (event, data) 로 흘린다. RUNTIME_ERRORS 는 호출자가 잡는다."""
    async for mode, payload in graph.astream({"messages": [HumanMessage(text)]}, config=config,
                                             stream_mode=["updates", "messages"]):
        if mode == "updates":
            for message in payload.get("agent", {}).get("messages", []):
                for call in getattr(message, "tool_calls", None) or []:
                    yield "search", {"tool": call["name"], "query": call["args"].get("query", "")}
        elif mode == "messages":
            # ChatOllama 는 AIMessageChunk 를, 가짜 모델은 완성된 AIMessage 를 흘린다(AIMessageChunk 는 하위 타입).
            # tool_call 만 있는 조각은 content 가 비어 있어 걸러진다.
            chunk, meta = payload
            if meta.get("langgraph_node") == "agent" and isinstance(chunk, AIMessage) and chunk.content:
                yield "token", chunk.content
    yield "done", ""


def create_app(graph, settings: Settings) -> FastAPI:
    app = FastAPI(title="KUDOS RAG Agent", version="1.0")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @app.get("/check", response_model=HealthResponse, tags=["health check"])
    def check():                                  # async 제거 — 블로킹 HTTP 2회를 스레드풀에서 실행
        rag = check_rag(settings.rag_base_url, settings.rag_timeout)
        ollama_ok = check_ollama(settings.ollama_base_url, settings.llm_model, settings.rag_timeout)
        return HealthResponse(status="ok" if rag and ollama_ok else "degraded", rag=rag, ollama=ollama_ok)

    @app.post("/v1/chat/stream", tags=["대화"])
    async def chat_stream(request: ChatRequest):
        config = {"configurable": {"thread_id": request.thread_id},
                  "recursion_limit": settings.recursion_limit}

        async def generate():
            try:
                async for event, data in stream_events(graph, config, request.message):
                    yield _sse(event, data)
            except RUNTIME_ERRORS as e:
                # 첫 바이트 전송 후에는 상태 코드를 바꿀 수 없으므로 error 프레임으로 알린다
                yield _sse("error", f"{type(e).__name__}: {e}")

        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return app

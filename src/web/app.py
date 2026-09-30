"""브라우저용 FastAPI 앱. 에이전트 한 턴을 SSE 로 흘려보낸다."""
import json
import re
from typing import AsyncIterator
from urllib.parse import urlsplit, urlunsplit

import httpx
from fastapi import FastAPI, Path as PathParam
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agent import RUNTIME_ERRORS, SYSTEM_FACTS_TOOL, tool_call_summary
from src.config.settings import PROJECT_ROOT, Settings
from src.web.dto import ChatRequest, HealthResponse, ThreadMessage, ThreadMessagesResponse

STATIC_DIR = PROJECT_ROOT / "resources" / "static"
# 백틱·별표(마크다운)와 한글(붙은 조사)에서 URL 을 끊는다. 출처 쪽도 같은 규칙으로 잘려 비교가 일관된다
URL_PATTERN = re.compile(r"""https?://[^\s)\]>"'`*ㄱ-ㆎ가-힣]+""")


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


def to_thread_messages(messages) -> list[ThreadMessage]:
    """복원용 대화 기록. 질문과 최종 답변만 남기고 도구 호출·청크 원문은 버린다."""
    result = []
    for message in messages:
        if isinstance(message, HumanMessage):
            result.append(ThreadMessage(role="user", content=message.content))
        elif isinstance(message, AIMessage) and not message.tool_calls and message.content:
            result.append(ThreadMessage(role="assistant", content=message.content))
    return result


def extract_urls(text: str) -> set[str]:
    """비교용으로 정규화한 URL 집합. 후행 구두점·슬래시·대소문자(스킴+호스트) 차이를 흡수한다."""
    urls = set()
    for raw in URL_PATTERN.findall(text):
        parts = urlsplit(raw.rstrip(".,;:!?\u201d\u2019\"'"))
        urls.add(urlunsplit((parts.scheme.lower(), parts.netloc.lower(),
                             parts.path.rstrip("/"), parts.query, "")))
    return urls


def unverified_urls(answer: str, sources: str) -> list[str]:
    """답변에는 있는데 이번 턴 검색 결과에는 없는 URL. 출처 위조를 사용자에게 알리기 위한 것이다."""
    known = extract_urls(sources)
    return sorted(url for url in extract_urls(answer) if url not in known)


async def stream_events(graph, config: dict, text: str) -> AsyncIterator[tuple[str, object]]:
    """그래프 한 턴을 (event, data) 로 흘린다. RUNTIME_ERRORS 는 호출자가 잡는다."""
    sources, answer = [], []          # 이번 턴 도구 결과 원문 / 흘려보낸 답변 토큰
    async for mode, payload in graph.astream({"messages": [HumanMessage(text)]}, config=config,
                                             stream_mode=["updates", "messages"]):
        if mode == "updates":
            # agent 뿐 아니라 모든 노드 갱신을 본다. force_search 가 만든 합성 tool_calls 도
            # 화면에 [검색] 줄로 드러나야 한다(tools 노드 갱신에는 tool_calls 가 없어 부작용 없음)
            for node_update in payload.values():
                for message in node_update.get("messages", []):
                    if isinstance(message, ToolMessage):
                        sources.append(str(message.content))
                    for call in getattr(message, "tool_calls", None) or []:
                        if call["name"] == SYSTEM_FACTS_TOOL:
                            continue          # 검색이 아니라 시스템이 넣은 사실이라 표시하지 않는다
                        # UI 와 같은 규칙: 도구 호출 직전까지의 본문(강제 검색이 버릴 임시 답변 포함)은
                        # 최종 답변이 아니므로 누적에서 지운다. 안 그러면 출처 대조가 거짓 경고를 낸다
                        answer.clear()
                        yield "search", {"tool": call["name"], "query": tool_call_summary(call["args"])}
        elif mode == "messages":
            # ChatOllama 는 AIMessageChunk 를, 가짜 모델은 완성된 AIMessage 를 흘린다(AIMessageChunk 는 하위 타입).
            # tool_call 만 있는 조각은 content 가 비어 있어 걸러진다.
            chunk, meta = payload
            if meta.get("langgraph_node") == "agent" and isinstance(chunk, AIMessage) and chunk.content:
                answer.append(str(chunk.content))
                yield "token", chunk.content

    # 답변이 인용한 URL 이 이번 턴 검색 결과에 없으면 알린다(본문은 고치지 않고 표시만 한다)
    unverified = unverified_urls("".join(answer), "\n".join(sources))
    if unverified:
        yield "warning", {"kind": "unverified_source", "urls": unverified}
    yield "done", ""


def create_app(graph, settings: Settings) -> FastAPI:
    app = FastAPI(title="KUDOS RAG Agent", version="1.0")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        # 캐시 헤더가 없으면 브라우저가 Last-Modified 로 신선도를 추정해 UI 수정 후에도 옛 화면을 띄운다.
        # FileResponse 는 304 를 주지 않아 매번 전체(약 12KB)를 받지만 로컬 단일 페이지라 무시할 수준이다
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html",
                            headers={"Cache-Control": "no-cache"})

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

    @app.get("/v1/threads/{thread_id}/messages", response_model=ThreadMessagesResponse, tags=["대화"])
    async def thread_messages(thread_id: str = PathParam(min_length=1, max_length=64)):
        # 없는 대화면 상태가 비어 있어 빈 목록이 된다(404 가 아니라 200)
        state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
        return ThreadMessagesResponse(messages=to_thread_messages(state.values.get("messages", [])))

    return app

# agent-02: FastAPI + 웹 채팅 UI (팀원 브라우저 접근)

- 작성일: 2026-09-22
- 작성자: 리드
- 상태: READY FOR REVIEW (2026-09-22, 리드 확인) — 커밋 대기(사용자). W8 렌더링 3항목은 사용자 육안 확인 필요. **2026-09-25 추가**: `index.html` `ask()` 스트림 처리 2건이 다른 세션에서 사후 수정됨 → `agent-05-stream-ui-fixes.md` 에서 설계 편입·재검증(SSE `token` 설명 보정 포함)
- 선행 티켓: agent-01 (종결). 저장소: `/Users/mjkim/workspace/LangGraph` 만 변경. RAG 저장소는 변경하지 않는다.

## 목표

agent-01 의 검색 에이전트를 터미널 없이 **브라우저에서** 쓸 수 있게 FastAPI 서버와 단일 파일 채팅 UI 를 얹는다. 답변은 SSE 로 토큰 스트리밍하고, 검색이 일어나면 `[검색] 도구(검색어)` 를 답변 앞에 보여준다. `src/` 의 에이전트 코드는 그대로 재사용한다.

## 사용자 확정 사항 (2026-09-22)

| 항목 | 결정 |
|---|---|
| 목적 | 팀원에게 브라우저로 열어주기 (사용자 선택 "2") |
| 형태 | RAG 서버(`RAG/resources/static/index.html`)와 같은 모양의 FastAPI + 단일 HTML |
| 스트리밍 | **포함** (리드 결정 — 아래 "리드 판단 기록 (1)") |

## 확인된 현재 상태 (`[검증]`)

- LangGraph `main.py`: `run_turn(graph, config, text, out)`, `warn_if_rag_down`, `build_repl_graph(settings)`, `RUNTIME_ERRORS = (httpx.HTTPError, ollama.ResponseError, ConnectionError, GraphRecursionError)`. 그래프는 `graph.stream(..., stream_mode="values")` 로 돌리고 `AIMessage.tool_calls` 로 `[검색]` 줄을 만든다.
- `Settings` 는 10 필드 frozen dataclass, `load_settings` 는 `[rag] [ollama] [agent]` 3섹션만 읽는다. `test/conftest.py` 의 `CONFIG_TEXT` 도 3섹션.
- 테스트 헬퍼: `test/test_agent.py` 의 `ScriptedChatModel`, `_tool_call_message`, `_chunk`, `_graph(responses, client, checkpointer)`; `test/conftest.py` 의 `FakeClient`, `write_config`.
- 의존성(`requirements.in`): `langgraph, langchain-core, langchain-ollama, ollama, httpx, pytest, pip-tools`. **`fastapi`, `uvicorn` 없음.**
- RAG 팀 컨벤션(`RAG/src/controller/ask_controller.py:65-82`, `RAG/resources/static/index.html`):
  - SSE 는 `sse-starlette` 없이 `StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})`, 프레임은 `f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"` (data 는 항상 JSON).
  - 스트림 중 오류는 상태코드를 못 바꾸므로 200 + `event: error` 프레임.
  - UI 는 단일 `index.html`(HTML+CSS+JS 인라인, 외부 CDN 없음), `fetch` + `res.body.getReader()` 로 SSE 를 직접 파싱(EventSource 는 GET 전용), 서버 데이터는 **`textContent` 로만** 삽입.
  - 라우트 함수는 블로킹 호출을 스레드풀로 보내기 위해 일부러 `async` 를 빼고, 스트리밍만 `async def`.
  - `/check` 는 `{"status": "ok"|"degraded", ...}`.
  - `test/test_web_ui.py` 가 HTML 을 정규식으로 읽어 서버 계약(상대 경로, 이벤트 이름 집합, `innerHTML` 미사용, CDN 없음)을 고정한다.
- `[미확인]` LangGraph `stream_mode="messages"` 에서 `ChatOllama` 가 토큰 단위 `AIMessageChunk` 를 내는지 실동작은 확인하지 못했다 → 통합 테스트 W7 로 확인한다. 가짜 모델(`FakeMessagesListChatModel`)은 스트리밍 미구현이라 한 덩어리로 올 수 있으므로 단위 테스트는 "token 이벤트 이어붙인 결과 == 최종 답변" 으로만 단언한다.

## 설계

### 전체 구조

```
브라우저 (resources/static/index.html)
   │ GET /                → index.html
   │ GET /check           → {"status","rag","ollama"}
   │ POST /v1/chat/stream {"thread_id","message"}   ← thread_id 는 브라우저가 생성(uuid)
   ▼
web.py  ── uvicorn.run(create_app(graph, settings), host, port)
src/web/app.py  create_app(graph, settings) -> FastAPI
   │  stream_events(graph, config, text) -> AsyncIterator[(event, data)]
   ▼
graph.astream({"messages":[HumanMessage]}, config, stream_mode=["updates","messages"])
   ├ ("updates",  {"agent": {"messages":[AIMessage(tool_calls=[...])]}})  → event "search" × N
   ├ ("messages", (AIMessageChunk, {"langgraph_node":"agent"}))           → event "token"
   └ 끝                                                                     → event "done"
   예외(RUNTIME_ERRORS)                                                     → event "error"
```

- 그래프 인스턴스는 프로세스에 1개, `InMemorySaver` 공유. 대화 분리는 `thread_id` 로만 한다(브라우저 탭/새 대화마다 새 uuid). 프로세스 재시작 시 모든 대화 소실(agent-01 과 동일 정책).
- `main.py`(REPL) 는 그대로 남긴다. 웹은 별도 진입점 `web.py`.

### 파일 변경

```
LangGraph/
├── web.py                               # 신규: FastAPI 진입점 (python web.py --active-profile=local)
├── main.py                              # 수정: RUNTIME_ERRORS·build_repl_graph 를 src/agent.py 에서 import (동작 불변)
├── requirements.in                      # 수정: fastapi, uvicorn 추가 → pip-compile
├── resources/
│   ├── config_local.ini.example         # 수정: [fastapi] 섹션 추가
│   ├── config_local.ini                 # 수정(gitignored 로컬 사본): 동일 섹션 추가
│   └── static/index.html                # 신규: 단일 파일 UI
├── src/
│   ├── agent.py                         # 수정: RUNTIME_ERRORS, build_default_graph(settings) 이동
│   ├── config/settings.py               # 수정: Settings 에 host, port 추가, [fastapi] 읽기
│   └── web/
│       ├── __init__.py
│       ├── dto.py                       # ChatRequest, HealthResponse
│       └── app.py                       # create_app, stream_events, _sse, health 검사
└── test/
    ├── conftest.py                      # 수정: CONFIG_TEXT 에 [fastapi] 추가
    ├── test_settings.py                 # 수정: host/port 단언 추가(기존 단언 유지)
    ├── test_web.py                      # 신규
    ├── test_web_ui.py                   # 신규
    └── test_integration_web.py          # 신규 (-m integration)
```

### 설정

`resources/config_local.ini.example` 에 추가:

```ini
[fastapi]
host=127.0.0.1
port=5020
```

- `reload` 키는 두지 않는다(팩토리 방식 `uvicorn.run(app_obj)` 는 import 문자열이 아니라 reload 를 지원하지 않음. 개발 중 재시작은 수동).
- 팀원 접근은 사용자가 **자기 `config_local.ini`** 에서 `host=0.0.0.0` 으로 바꿔 연다. 템플릿 기본값은 로컬 전용 `127.0.0.1` 로 둔다(인증이 없으므로 — "범위 제외" 참조).
- RAG 서버가 5010 이므로 5020 사용.

`src/config/settings.py`:

```python
@dataclass(frozen=True)
class Settings:
    ...기존 10 필드 순서 유지...
    host: str
    port: int

def load_settings(path) -> Settings
    # fastapi = parser["fastapi"]; host=fastapi["host"], port=int(fastapi["port"])
    # 섹션/키 없으면 KeyError 그대로 전파 (기존 정책)
```

### `src/agent.py` 에 이동하는 것 (main.py 에서)

```python
RUNTIME_ERRORS = (httpx.HTTPError, ollama.ResponseError, ConnectionError, GraphRecursionError)

def build_default_graph(settings: Settings) -> CompiledStateGraph:
    # 기존 main.build_repl_graph 본문 그대로: RagClient → build_tools → build_graph(create_chat_model, tools, InMemorySaver())
```

- `main.py` 는 `from src.agent import RUNTIME_ERRORS, build_default_graph` 로 바꾸고 `build_repl_graph` 정의를 삭제한다(호출부 1곳 이름만 변경). `run_turn`, `warn_if_rag_down`, `main()` 동작은 바꾸지 않는다. `test/test_main.py` 는 수정하지 않는다.
- 이동 이유: 웹 모듈이 `main`(CLI 진입점)을 import 하는 역방향 의존을 피한다.

### `src/web/dto.py`

```python
class ChatRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=64, description="대화 식별자(브라우저 생성 uuid)")
    message: str = Field(min_length=1, max_length=4000, description="사용자 질문")

class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    rag: bool       # RAG GET /check 가 200
    ollama: bool    # Ollama GET /api/tags 200 이고 settings.llm_model 이 목록에 있음
```

### `src/web/app.py`

```python
STATIC_DIR = PROJECT_ROOT / "resources" / "static"      # settings.PROJECT_ROOT 재사용

def _sse(event: str, data) -> str
    # f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"  (RAG 와 동일)

def check_rag(base_url: str, timeout: float) -> bool
    # httpx.get(f"{base_url.rstrip('/')}/check", timeout).status_code == 200 ; httpx.HTTPError → False
def check_ollama(base_url: str, model: str, timeout: float) -> bool
    # httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout) 200 이고 models[].name 에 model 포함 ; 예외 → False
    # (test_integration_agent.live_env 의 판정과 같은 규칙)

async def stream_events(graph, config: dict, text: str) -> AsyncIterator[tuple[str, object]]:
    # graph.astream({"messages": [HumanMessage(text)]}, config=config, stream_mode=["updates", "messages"])
    # ("updates", {"agent": {"messages": [msg]}}) 이고 msg.tool_calls 가 있으면
    #     tool_call 마다 ("search", {"tool": call["name"], "query": call["args"].get("query", "")})
    # ("messages", (chunk, meta)) 이고 meta.get("langgraph_node") == "agent" 이고 isinstance(chunk, AIMessage)
    #     (AIMessageChunk 는 AIMessage 의 서브클래스. 스트리밍 미구현 가짜 모델은 완성 AIMessage 를 흘리므로 AIMessage 로 판정 — 리드 판단 기록 (4))
    #     이고 chunk.content 가 비어 있지 않으면 ("token", chunk.content)
    #     (tool_calls 만 있는 chunk 는 content 가 비어 있어 걸러진다. 문자열이 아닌 content(list) 는 str 로 합치지 않고 무시하지 않는다 — Builder 는 ChatOllama 가 str 을 준다는 전제로 구현하고 통합 테스트 W7 로 확인)
    # 정상 종료 시 ("done", "")
    # RUNTIME_ERRORS 는 여기서 잡지 않는다 — 호출자(라우트)가 잡아 error 프레임으로 변환

def create_app(graph, settings: Settings) -> FastAPI:
    app = FastAPI(title="KUDOS RAG Agent", version="1.0")

    @app.get("/", include_in_schema=False)          -> FileResponse(STATIC_DIR / "index.html", media_type="text/html")
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/check", response_model=HealthResponse, tags=["health check"])
    def check():                                     # 동기 — 스레드풀
        rag = check_rag(settings.rag_base_url, settings.rag_timeout)
        ollama_ok = check_ollama(settings.ollama_base_url, settings.llm_model, settings.rag_timeout)
        return HealthResponse(status="ok" if rag and ollama_ok else "degraded", rag=rag, ollama=ollama_ok)

    @app.post("/v1/chat/stream", tags=["대화"])
    async def chat_stream(request: ChatRequest):
        config = {"configurable": {"thread_id": request.thread_id}, "recursion_limit": settings.recursion_limit}
        async def generate():
            try:
                async for event, data in stream_events(graph, config, request.message):
                    yield _sse(event, data)
            except RUNTIME_ERRORS as e:
                yield _sse("error", f"{type(e).__name__}: {e}")
        return StreamingResponse(generate(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    return app
```

- 라우트는 `/check`, `/v1/chat/stream` 2개 + 숨김 `/`. 비스트리밍 `/v1/chat` 은 만들지 않는다(UI 가 쓰지 않음 — "기각한 대안").
- `RUNTIME_ERRORS` 외 예외는 잡지 않는다(버그가 `error` 프레임에 가려지지 않게. RAG 와 같은 정책). 스트림 도중 발생하면 연결이 끊기고 UI 는 `요청 실패:` 로 표시된다.
- 도구 예외(RAG 서버 장애)는 agent-01 의 `ToolNode(handle_tool_errors=True)` 가 `ToolMessage(status="error")` 로 바꾸므로 여기까지 오지 않고 LLM 이 답변으로 처리한다. 그래프 자체 예외만 `error` 가 된다.

### SSE 이벤트 계약

| event | data (JSON) | 시점 |
|---|---|---|
| `search` | `{"tool": "search_openapi", "query": "메시지 등록 API"}` | agent 노드가 tool_call 을 낼 때마다 1건씩 |
| `token` | `"부분 텍스트"` | 최종 답변 토큰. 이어붙이면 최종 `AIMessage.content` 와 같다 |
| `done` | `""` | 정상 종료. 항상 마지막 |
| `error` | `"ExceptionName: 메시지"` | `RUNTIME_ERRORS` 발생. 이후 프레임 없음 |

### `web.py`

```python
"""KUDOS RAG 검색 에이전트 웹 서버.  python web.py --active-profile=local"""
def main() -> None:
    # argparse --active-profile (기본 local) — main.py 와 동일 문구
    # settings = load_settings(config_path_for(profile)); graph = build_default_graph(settings)
    # warn_if_rag_down 은 쓰지 않는다 — /check 가 같은 역할
    # uvicorn.run(create_app(graph, settings), host=settings.host, port=settings.port)
if __name__ == "__main__": main()
```

### `resources/static/index.html` (단일 파일, 외부 CDN 없음)

RAG `index.html` 을 바탕으로 다음만 다르게 한다.

| 항목 | 내용 |
|---|---|
| 제목/헤더 | `KUDOS RAG Agent`. 헤더 우측에 상태 텍스트(`/check` 결과: `상태: ok · RAG 연결됨 · Ollama 연결됨` / 끊김) 와 **`새 대화` 버튼** |
| 소스 select | **없음** (어느 소스를 검색할지는 에이전트가 결정) |
| thread_id | 페이지 로드 시 `crypto.randomUUID()` 로 생성해 JS 변수에 보관. `새 대화` 클릭 → 새 uuid + `#log` 비우기 |
| 요청 | `fetch('/v1/chat/stream', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({thread_id, message})})` — **상대 경로** |
| `search` 이벤트 | 답변 말풍선 안에 `[검색] {tool}({query})` 한 줄을 흐린 글씨(`.search` 클래스)로 추가. 여러 건이면 여러 줄 |
| `token` 이벤트 | 답변 본문 요소의 `textContent` 에 이어붙임 |
| `done` 이벤트 | 본문 텍스트에서 `https?://[^\s)\]]+` 를 찾아 **DOM 으로** `<a href target="_blank" rel="noopener">` 로 바꿈 (텍스트 노드 분할 + `createElement`. `innerHTML` 금지) |
| `error` 이벤트 | 본문에 `.err` 클래스 + data 문자열 |
| HTTP 오류(422 등) | RAG 와 동일 `formatDetail` (422 는 `detail[].msg` 조인) |
| 전송 중 | 입력·버튼 비활성화, `finally` 에서 복구 (RAG 와 동일) |
| Enter 전송 | `<form>` submit (RAG 와 동일) |

## 작업 목록

1. **설정 확장** — `Settings` 에 `host`, `port`; `load_settings` 가 `[fastapi]` 를 읽음; `.example` 과 로컬 `config_local.ini` 에 섹션 추가; `test/conftest.py` `CONFIG_TEXT` 갱신; `test_settings.py` 에 host/port 단언 추가. 검증: `pytest test/test_settings.py`.
2. **공용 심볼 이동** — `RUNTIME_ERRORS`, `build_default_graph` 를 `src/agent.py` 로; `main.py` 가 import. 검증: `pytest test/test_main.py test/test_agent.py` (기존 테스트 무수정 통과).
3. **의존성** — `requirements.in` 에 `fastapi`, `uvicorn` 추가 → `pip-compile --output-file=requirements.txt requirements.in` → `pip install -r requirements.txt`. 이 2개 외 패키지가 필요하면 리드에게 보고(`sse-starlette`, `jinja2`, `python-multipart` 금지).
4. **웹 계층** — `src/web/dto.py`, `src/web/app.py`(`create_app`, `stream_events`, `_sse`, `check_rag`, `check_ollama`), `web.py`. 검증: `test/test_web.py` (아래 W1~W5).
5. **UI** — `resources/static/index.html`. 검증: `test/test_web_ui.py` (W6) + 브라우저 육안 체크리스트.
6. **통합 테스트 + README** — `test/test_integration_web.py` (W7), README 에 "웹 서버" 절(기동 명령, 포트, `host=0.0.0.0` 안내, 육안 체크리스트).

## 검증 전략

테스트는 `test/test_agent.py` 의 `_graph`/`ScriptedChatModel`/`_tool_call_message` 와 `conftest.FakeClient` 를 재사용한다. `TestClient(create_app(graph, settings))` 로 앱을 만들고 `settings` 는 `load_settings(write_config())` 로 얻는다. SSE 는 RAG 방식으로 읽는다: `with client.stream("POST", ...) as res: text = "".join(res.iter_text())` → `"\n\n"` 분리 → `event: `/`data: ` 파싱 + `json.loads`.

| 항목 | 확인 방법 |
|---|---|
| W1. 정적/헬스 | `GET /` 200, `text/html`, 본문에 `<title>KUDOS RAG Agent`. `GET /check`: `httpx.get` 을 monkeypatch 해 (RAG 200 + tags 에 모델 있음) → `{"status":"ok","rag":true,"ollama":true}`; RAG ConnectError → `degraded, rag=false`; tags 에 모델 없음 → `ollama=false` |
| W2. 스트림 정상 | 가짜 모델 `[tool_call(search_openapi, "메시지 등록"), AIMessage("POST /v1/messages 입니다.\n출처: ...")]` → 이벤트 순서가 `search`(data `{"tool":"search_openapi","query":"메시지 등록"}`) → `token`×N → `done`; `token` data 이어붙인 결과 == 최종 답변 문자열; `done` 이 마지막; 응답 헤더 `content-type` 이 `text/event-stream` 로 시작, `cache-control: no-cache`, `x-accel-buffering: no` |
| W3. 스트림 변형 | (a) tool_call 없는 답변 → `search` 없음, `token`…`done`. (b) 병렬 tool_call 2건 → `search` 2건 순서 유지. (c) 도구 예외(`FakeClient(error=httpx.ConnectError(...))`) → 그래프는 계속되어 `done` 으로 끝남(`error` 아님). (d) 모델이 `ollama.ResponseError` 를 던지도록 `ScriptedChatModel._generate` 를 오버라이드 → 200 + 마지막 프레임 `error`, data 가 `ResponseError:` 로 시작, 이후 프레임 없음 |
| W4. 멀티턴 | 같은 `thread_id` 로 2회 호출 → 두 번째 호출에서 모델이 받은 메시지(`model.received[-1]`)에 첫 턴의 Human/AI 메시지가 포함. 다른 `thread_id` 로 호출 → 포함되지 않음 |
| W5. 검증 실패 | `message` 빈 문자열 / `thread_id` 누락 / `message` 4001자 → 422. OpenAPI 경로 목록이 정확히 `["/check", "/v1/chat/stream"]` (`/` 는 `include_in_schema=False`) |
| W6. UI 계약 (`test_web_ui.py`, 파일을 읽어 정규식) | `http://`/`https://` 문자열이 `<script src`/`<link href` 에 없음(CDN 없음); `fetch(` 인자가 `'/check'`, `'/v1/chat/stream'` 상대 경로; JS 에서 비교하는 이벤트 이름이 `{search, token, done, error}` 부분집합; `innerHTML` 미사용; 요청 body 키가 `thread_id`, `message`(ChatRequest 필드와 일치); `crypto.randomUUID` 사용 |
| W7. 통합 (`-m integration`) | `test_integration_agent.live_env` 와 같은 skip 조건. `TestClient(create_app(build_default_graph(settings), settings))` 로 "메시지 등록 API 호출 방법 알려줘" 스트림 → `search` ≥1건(`tool == "search_openapi"` 포함), `token` ≥1건, `done` 마지막, `token` 이어붙인 문자열에 `출처:` 포함. **`token` 이어붙인 문자열 == `graph.get_state(config).values["messages"][-1].content`** (chunk 뒤에 완성 메시지가 한 번 더 흘러 토큰이 중복 전송되지 않음을 확인). **token 이벤트 개수를 검증 기록에 적는다**(1개면 ChatOllama 스트리밍이 langgraph messages 모드로 전달되지 않는 것 → ESCALATE) |
| W8. 육안 (Validator 기록) | `python web.py --active-profile=local` → `http://127.0.0.1:5020/`: 헤더 상태 표시, 질문 → `[검색] …` 줄 → 답변이 **토큰 단위로 이어서** 표시, 답변 속 url 이 새 탭 링크, 2턴째 질문이 문맥 유지, `새 대화` 후 문맥 초기화, RAG 서버 내린 뒤 질문 → 에이전트가 오류를 답변으로 처리하고 `done`(빨간 오류 아님), 서버(5020) 내린 뒤 질문 → 빨간 `요청 실패:` |
| W9. 회귀 | `pytest -q` 전체 green(agent-01 55건 포함). `test/test_main.py`, `test_agent.py`, `test_tools.py`, `test_rag_client.py` 는 **수정하지 않음**(`git diff` 로 확인) |
| W10. 의존성 | `requirements.txt` 가 `pip-compile` 결과(재실행 diff 0); 새 최상위 import 는 `fastapi`, `uvicorn`(+ 기존 목록) 뿐; `pip check` 정상 |

## 완료 기준

- [ ] W1~W7, W9, W10 통과. W7 은 실환경 PASS 1회 이상(token ≥2 이어야 스트리밍 성립; 1이면 ESCALATE)
- [ ] W8 육안 항목 전부 Validator 가 검증 기록에 결과 기재
- [ ] `main.py` REPL 동작 불변 (`test_main.py` 무수정 통과, `python main.py` 로 1턴 수동 확인)
- [ ] `requirements.in` 추가는 `fastapi`, `uvicorn` 2개만
- [ ] `resources/config_local.ini` 는 여전히 커밋 대상 아님. `.example` 에만 `[fastapi]` 추가가 커밋됨
- [ ] 커밋하지 않음(사용자 작업)
- [ ] 설계 문서와 구현 일치 (경로, 시그니처, 이벤트 이름·data 형식, UI 계약)

## 범위 제외

- 인증/인가, HTTPS, 사용자 식별 — 사내망 + `host=127.0.0.1` 기본값 전제. `0.0.0.0` 으로 열 때의 접근 통제는 별도 티켓
- 영구 체크포인터(재시작 시 대화 소실 허용), 대화 목록/이력 조회 API, 서버 측 thread 관리·만료
- 같은 `thread_id` 동시 요청 직렬화(UI 가 전송 중 입력을 막는 것으로 충분)
- 비스트리밍 `POST /v1/chat`, `/v1/search` 프록시
- 마크다운 렌더링(코드 블록·표) — 텍스트 + url 링크만
- uvicorn `reload`, 로깅 프레임워크(RAG `GlobalLogger`) 도입, 접근 로그 파일
- 답변 품질·프롬프트 변경, `src/tools.py`·`src/rag_client.py` 변경

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| `main.py` 에 `--serve` 플래그 추가 | REPL 과 서버는 실행 모델(동기 input 루프 vs uvicorn 이벤트 루프)이 달라 한 진입점에 두면 분기만 늘어남. `web.py` 분리가 더 작다 |
| RAG 처럼 모듈 전역 `app = FastAPI()` + `Profile` 싱글턴 | agent-01 에서 `Profile` 을 이미 기각. 팩토리 `create_app(graph, settings)` 는 테스트에서 monkeypatch 없이 가짜 그래프 주입이 가능 |
| `sse-starlette` | RAG 도 안 쓰고, 프레임 헬퍼 3줄로 충분. UI 파서가 `event:`/`data:` 2줄 + JSON data 를 전제로 하므로 포맷 통제가 직접 쓰는 쪽이 쉬움 |
| WebSocket | 양방향 필요 없음. SSE 가 RAG UI 와 파서를 공유 |
| 비스트리밍 응답만 제공 | 한 턴이 15~40초(agent-01 통합 테스트 실측) — 빈 화면을 오래 보게 됨. RAG UI 도 스트리밍 |
| 소스 선택 select 유지 | 소스 선택은 에이전트의 역할(agent-01 설계 목표). UI 에 두면 도구 선택 규칙과 충돌 |
| `run_turn` 을 웹에서 재사용 | 동기 `graph.stream` + `out=print` 콜백 구조라 async 제너레이터로 못 씀. 웹은 `astream` 기반 `stream_events` 별도 구현, REPL 은 그대로 둠 |
| 서버가 thread_id 발급(`POST /v1/threads`) | 엔드포인트·상태가 늘어남. 브라우저 uuid 로 충분 |
| 마크다운 렌더러(marked 등 CDN) | 외부 CDN 금지(RAG 테스트로 강제하는 컨벤션) + XSS 표면. url 링크만 DOM 으로 처리 |

## 리드 판단 기록

- 2026-09-22 (1): 사용자가 "팀원에게 브라우저로 열어주기" 선택. 스트리밍은 리드가 포함으로 결정 — 근거: agent-01 통합 테스트 실측 15~40초/턴, RAG UI 가 이미 스트리밍이라 팀원 기대치가 그쪽. `[미확인]` `ChatOllama` → langgraph `messages` 모드 토큰 전달은 W7 로 검증하며, token 이벤트가 1개뿐이면 ESCALATE(대안: `astream_events` 로 전환 또는 `ChatOllama(streaming=True)` 옵션 확인).
- 2026-09-22 (2): 새 의존성 `fastapi`, `uvicorn` — 사용자가 "FastAPI + 웹 UI" 를 선택했으므로 §7 허가로 간주. 이 2개 외는 Builder 가 리드에 보고.
- 2026-09-22 (3): `RUNTIME_ERRORS`·`build_repl_graph` 를 `src/agent.py` 로 이동(`build_default_graph` 로 개명). 웹 모듈이 CLI 진입점 `main` 을 import 하는 역방향 의존을 피하기 위함. `test_main.py` 는 두 심볼을 참조하지 않아 무수정.
- 2026-09-22 (4): Builder 보고 — `[검증]` 가짜 모델(`FakeMessagesListChatModel`)은 스트리밍 미구현이라 langgraph `messages` 모드가 완성 `AIMessage` 를 흘리고, 실제 `ChatOllama` 는 `AIMessageChunk` 를 여러 건(짧은 인사 6건) 흘림. 명세의 `isinstance(chunk, AIMessageChunk)` 필터로는 단위 테스트 W2~W4 에서 token 이 0건. **결정: 옵션 (A) — 필터를 `isinstance(chunk, AIMessage)` 로 완화.** `AIMessageChunk` 는 `AIMessage` 서브클래스라 실환경 동작 동일, tool_call 전용 chunk 는 `content` 비어 있어 기존대로 걸러짐. 옵션 (B)(스트리밍 가짜 모델 도입)는 헬퍼 재사용 방침과 어긋나 기각. 아울러 실환경에서 chunk 뒤에 완성 메시지가 한 번 더 흘러 토큰이 중복될 가능성을 배제하기 위해 W7 에 "`token` 이어붙인 결과 == 최종 상태의 `AIMessage.content`" 단언을 추가함.
- 2026-09-22 (5): Builder 완료 보고 접수 — 작업 1~6 완료. Builder 자체 실행: 단위 95 passed, 3 deselected / 통합 3 passed(agent-01 1 + agent-02 2). **W7 실측: token 295건, search 1건(search_openapi), token 합 == 체크포인터 최종 메시지** → 스트리밍 성립, ESCALATE 조건 아님. 명세 차이는 (4) 승인 건 1건. Builder 가 통합 테스트용으로 RAG 서버(5010)를 백그라운드 기동해 둔 상태(Validator W8 에 사용 가능). Builder 는 "LangGraph 디렉터리가 git 저장소가 아님" 이라 보고했으나 `[검증]` 리드 확인 결과 `.git/HEAD` 존재(agent-01 종결 후 사용자가 `git init`·push 완료) — Validator 는 W9 를 `git diff` 로 확인할 것. 리드가 Validator 스폰.
- 2026-09-22 (6): Validator 판정 READY FOR REVIEW 접수(검증 회차 1). 리드 확인 — (a) 완료 기준 전부 PASS. (b) 테스트 green: 단위 102 passed / 통합 3 passed ×2 / `pip check` 정상 / `pip-compile` 재현 diff 0. (c) 설계-구현 일치: 리드가 `src/web/app.py`·`web.py` 직접 확인, 유일한 차이는 (4) 승인 건. W7 token 295건으로 스트리밍 성립. W9 는 `git diff --stat origin/main` 으로 agent-01 테스트 무수정 확인. **결론: READY FOR REVIEW 선언.** W8 중 순수 렌더링 3항목(토큰 점진 표시, url 새 탭 링크, 오류 빨간색)은 브라우저 자동화 불가로 사용자 육안 확인으로 넘김(README 육안 체크리스트). 비차단 의견 판단: ① `pydantic` 직접 import 의 `requirements.in` 미선언 — RAG 는 `pydantic` 을 명시하고 있으므로 팀 원칙상 명시가 맞지만 이번 티켓 완료 기준(추가 2개만)과 충돌하므로 후속 정리 티켓 후보로 기록(`langgraph-prebuilt`/`langgraph-checkpoint` 건과 함께). ② `/` 핸들러 `async def` — 블로킹 없음, 동작 차이 없음, 명세 의사코드는 구속 아님. ③ `--no-index` 헤더는 pip-tools 표기이며 RAG README 도 같은 주의를 적고 있음.
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

### 2026-09-22 Validator 검증 (검증 회차: 1) — 판정: READY FOR REVIEW

실행 환경: `/Users/mjkim/workspace/LangGraph`, `.venv` (Python 3.12). RAG 서버 5010 기동 중(`/check` 200), Ollama 11434 기동 중, `qwen3:14b` 설치됨.

실행 명령과 결과:

| 명령 | 결과 |
|---|---|
| `.venv/bin/python -m pytest -q` (Validator 보강 테스트 추가 전) | **95 passed, 3 deselected** |
| `.venv/bin/python -m pytest -q` (보강 7건 추가 후) | **102 passed, 3 deselected** |
| `.venv/bin/python -m pytest -m integration -q -s` | **3 passed, 102 deselected** (2회 실행 모두 동일) |
| `.venv/bin/python -m compileall src web.py main.py test` | OK (프로젝트에 ruff/flake8/mypy 등 린터 설정 없음 — `[검증]` 설정 파일·README·`requirements.in` 모두에 없음) |
| `.venv/bin/pip check` | No broken requirements found |
| `pip-compile --output-file=<tmp> requirements.in` 재실행 diff | 헤더의 `--output-file` 경로 1줄 외 **diff 0** |
| `printf '...\nexit\n' \| python main.py --active-profile=local` | 1턴 정상 답변(출처 포함) 후 종료 |

#### W1~W10

| 항목 | 결과 | 증거 |
|---|---|---|
| W1. 정적/헬스 | PASS | `test/test_web.py:43-93` 5건 통과. 실서버 `GET /` → `200 text/html; charset=utf-8`, `GET /check` → `{"status":"ok","rag":true,"ollama":true}` |
| W2. 스트림 정상 | PASS | `test/test_web.py:98-128`. `search` → `token`×N → `done`, token 이어붙인 값 == 최종 답변, 헤더 `text/event-stream`/`no-cache`/`no` 확인 |
| W3. 스트림 변형 (a)~(d) | PASS | `test/test_web.py:133-196`. (c) 도구 예외 → `error` 없이 `done`, (d) `ollama.ResponseError` → 200 + 단일 `error` 프레임(`ResponseError:` 로 시작) |
| W4. 멀티턴 | PASS | `test/test_web.py:201-224`. 같은 thread → `["첫 질문","첫 답변","두 번째 질문"]`, 다른 thread → `["두 번째 질문"]` |
| W5. 검증 실패/라우트 | PASS | `test/test_web.py:229-252`. 6가지 잘못된 body 전부 422, 경계값(64자/4000자) 200, OpenAPI 경로 == `["/check","/v1/chat/stream"]` |
| W6. UI 계약 | PASS | `test/test_web_ui.py` 15건 통과(CDN 없음, 상대 경로 `/check`·`/v1/chat/stream`, 이벤트 이름 부분집합, `innerHTML`/`outerHTML`/`document.write` 미사용, body 키 `thread_id`·`message`, `crypto.randomUUID`) |
| W7. 통합 | PASS | `test/test_integration_web.py` 2건 통과. **`[W7] token 이벤트 295건, search 이벤트 1건(search_openapi), 답변 869자`** (2회 실행 동일). token 이어붙인 값 == 체크포인터 최종 `AIMessage.content`. `done` 이 마지막. → 토큰 스트리밍 성립, ESCALATE 조건 아님 |
| W8. 육안 | 아래 표 참조 (curl 검증 6/8, 순수 렌더링 2항목 `[미확인]`) | |
| W9. 회귀 | PASS | 전체 스위트 green. `git diff --stat origin/main -- test/` == `test/conftest.py(+4)`, `test/test_settings.py(+10)` 뿐 — `test_main.py`, `test_agent.py`, `test_tools.py`, `test_rag_client.py` **무수정** |
| W10. 의존성 | PASS | `requirements.in` 추가는 `fastapi`, `uvicorn` 2개. `pip-compile` 재실행 diff 0. `pip check` 정상. 애플리케이션 코드의 최상위 import: `fastapi`, `uvicorn`, `pydantic`(fastapi 경유 기존 설치) 외 신규 없음 |

#### W8 상세 (5020 기동: `.venv/bin/python web.py --active-profile=local`)

| 항목 | 결과 | 증거 |
|---|---|---|
| 헤더 상태 표시 | PASS(데이터) | `GET /check` → `{"status":"ok","rag":true,"ollama":true}`. UI 는 이 값을 `상태: ok · RAG 연결됨 · Ollama 연결됨` 으로 표시(`index.html:52-62`) |
| 질문 → `[검색] …` 줄이 먼저 | PASS | 실스트림 이벤트 순서 `search`(1) → `token`(295) → `done`(1), search data `{"tool": "search_openapi", "query": "메시지 등록 API"}` |
| 답변이 토큰 단위로 이어서 표시 | PASS(전송) / `[미확인]`(렌더링) | token 295 프레임이 순차 전송됨. 브라우저 화면에서 점진적으로 보이는지는 육안 확인 필요 |
| 답변 속 url 이 새 탭 링크 | `[미확인]` | 실답변에 `https://message-api.qa.hunet.io/docs` 2건 포함 확인. `<a target="_blank" rel="noopener">` 생성 코드는 `index.html:91-112` + `test_web_ui.py:91-98` 로 고정. 실제 클릭 동작은 육안 |
| 2턴째 문맥 유지 | PASS | 같은 `thread_id` 로 "방금 말한 그 API 의 필수 필드만…" → 재검색 없이(`search` 0건) 앞 턴의 `service_key`·`page_key`… 를 그대로 나열 |
| `새 대화` 후 문맥 초기화 | PASS | 새 `thread_id` 로 같은 후속 질문 → 앞 턴과 무관한 답변(문맥 없음). `새 대화` 버튼이 `threadId = crypto.randomUUID()` + `log.textContent=''` 하는 것은 `index.html:169-173`, `test_web_ui.py:69-74` |
| RAG 서버 내린 뒤 질문 → 오류를 답변으로, `done` | PASS | RAG 서버를 내리는 대신 **연결 거부 포트(5999)를 가리키는 임시 설정**으로 같은 앱을 띄워 검증(RAG 서버 무중단). 결과: `/check` → `degraded, rag=false`, 스트림 `search 1 / token 30 / done 1`, **`error` 프레임 0건**, 답변 "메시지 등록 API에 대한 정보를 찾을 수 없습니다…" |
| 웹 서버 내린 뒤 질문 → 빨간 `요청 실패:` | PASS(네트워크) / `[미확인]`(빨간 렌더링) | 5020 종료 후 요청 → connection refused(curl exit 7) → 브라우저 `fetch` reject. catch 에서 `'요청 실패: '` + `.err` 클래스 표시는 `index.html:165` + `test_web_ui.py:131-136` |

#### 완료 기준

| 기준 | 결과 | 증거 |
|---|---|---|
| W1~W7, W9, W10 통과 / W7 실환경 1회 이상, token ≥2 | PASS | 위 표. token 295건 |
| W8 육안 항목 전부 기재 | PASS | 위 W8 표 8항목 기재(2항목은 `[미확인]` 으로 사용자 육안 이관) |
| `main.py` REPL 동작 불변 | PASS | `test_main.py` 무수정 통과, `python main.py --active-profile=local` 1턴 수동 확인 |
| `requirements.in` 추가는 2개만 | PASS | `git diff origin/main -- requirements.in` == `+fastapi`, `+uvicorn` |
| `config_local.ini` 커밋 대상 아님 / `.example` 에만 `[fastapi]` | PASS | `.gitignore:7` 에 `resources/config_local.ini`, `git ls-files resources/` == `resources/config_local.ini.example` 뿐 |
| 커밋하지 않음 | PASS | `git log --oneline -1` == `1a0f1ff`(agent-01), 변경분 전부 unstaged/untracked |
| 설계 문서와 구현 일치 | PASS | 경로·시그니처·이벤트 이름/data 형식·UI 계약 일치. 유일한 차이는 리드 판단 기록 (4) 로 승인된 `isinstance(chunk, AIMessage)` |

RAG 저장소(`/Users/mjkim/workspace/RAG`): agent-02 로 인한 변경 없음. `[검증]` 수정된 파일 5개(`README.md`, `docs/plans/*`, `requirements.*`)의 mtime 이 13:16~13:24 로 agent-02 작업 시각(17:10~17:16)보다 앞서며 내용도 agent-01 범위(`langchain-text-splitters` 등).

#### Validator 가 추가한 테스트 (`test/test_web.py` 말미에 append, 7건)

- `test_check_functions_strip_trailing_slash` — `base_url` 끝 `/` 가 `//check` 를 만들지 않음
- `test_check_ollama_false_when_tags_status_is_not_200` / `..._when_body_is_not_json` / `..._when_connection_fails`
- `test_search_query_defaults_to_empty_string_when_arg_missing` — `args` 에 `query` 가 없어도 `""` 로 전송되고 스트림이 끊기지 않음
- `test_tool_node_output_is_not_streamed_as_token` — 도구 결과(ToolMessage)가 token 으로 새지 않음
- `test_static_mount_serves_index_html` — `/static` 마운트 동작

#### 비차단 의견 (판정 미반영)

- `src/web/dto.py` 가 `pydantic` 을 직접 import 하지만 `requirements.in` 에는 없다(`fastapi` 의 필수 의존성이라 항상 설치됨, 설계 문서도 `BaseModel` 사용을 명시). 명시적 선언 여부는 리드 판단 사항.
- `@app.get("/")` 가 설계 의사코드와 달리 `async def` 다. `FileResponse` 반환만 하므로 블로킹 없음 — 동작 차이 없음.

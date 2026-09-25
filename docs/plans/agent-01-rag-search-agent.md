# agent-01: KUDOS RAG 검색 도구를 가진 LangGraph 에이전트

- 작성일: 2026-09-22
- 작성자: 리드
- 상태: 종결 — RAG·LangGraph 모두 사용자 커밋 완료 (2026-09-22, LangGraph 는 `git status` 로 `origin/main` 동기화 확인, RAG 는 사용자 보고)
- 관련 저장소: `/Users/mjkim/workspace/LangGraph`(신규, 에이전트) · `/Users/mjkim/workspace/RAG`(기존, 검색 엔드포인트 1개 추가)

## 목표

기존 KUDOS RAG(Confluence + OpenAPI 하이브리드 검색)를 LangGraph ReAct 에이전트의 **도구**로 연결해, 에이전트가 질문에 따라 어느 소스를 몇 번 검색할지 스스로 결정하고 출처를 인용해 답하는 멀티턴 CLI 에이전트를 만든다.

## 사용자 확정 사항 (2026-09-22)

| 항목 | 결정 |
|---|---|
| 코드 위치 | `/Users/mjkim/workspace/LangGraph` 신규 프로젝트 |
| RAG 연동 경계 | HTTP. RAG 에 청크만 반환하는 `POST /v1/search` 추가 (RAG 저장소 수정 허용) |
| 1차 범위 | 검색 도구 2개 + 멀티턴 대화 + 출처 인용. 사내 API 실호출은 제외 |
| 실행 인터페이스 | Python 모듈 + CLI REPL(`main.py`) + pytest |
| LLM | 로컬 Ollama `qwen3:14b` (RAG 와 동일 정책: 사내 문서를 외부 API 로 보내지 않음) |

## 확인된 현재 상태 (`[검증]`)

- RAG 검색 진입점: `RetrieverService.search(query, source="all"|"confluence"|"openapi", top_k) -> list[Document]` (`RAG/src/service/retriever_service.py:61`). `reload()` 로 BM25 인메모리 인덱스를 먼저 구축해야 하며, 이 라이프사이클은 `ask_controller.get_context()` 가 담당.
- RAG 서버 엔드포인트: `POST /v1/ask`, `POST /v1/ask/stream`, `POST /v1/reload`, `GET /check`. 원본 청크만 반환하는 엔드포인트 없음.
- 청크 metadata: 공통 `source, doc_id, title, url, chunk_id, chunk_index, section`; Confluence `breadcrumb, version, last_modified`; OpenAPI `service, method, path, tags`.
- RAG 에러 처리: `llm_factory.LLM_ERRORS` 만 잡아 타임아웃 504 / 그 외 Ollama 장애 503 (`ask_controller._translate_llm_error`).
- `RAG/test/test_ask_controller.py:221 test_openapi_lists_four_paths_and_hides_root` 가 라우트 개수를 4개로 고정.
- LangGraph venv(`/Users/mjkim/workspace/LangGraph/.venv`, Python 3.12.7): `langgraph 1.2.11`, `langgraph_prebuilt 1.1.0`, `langgraph_checkpoint 4.2.0`, `langchain_core 1.6.3`, `httpx`, `pydantic` 설치됨. `langchain-ollama`, `pytest` 없음.
- RAG 에이전트/Tool/LangGraph 코드 0건.

## 설계

### 전체 구조

```
[LangGraph 프로젝트]                                   [RAG 서버 (기존, 127.0.0.1:5010)]

main.py (CLI REPL, thread_id 1개)
   │ graph.stream(HumanMessage, thread_id)
   ▼
StateGraph(MessagesState)
   agent ──(tool_calls 있음)──► tools(ToolNode) ──► agent ──(없음)──► END
     │ ChatOllama(qwen3:14b).bind_tools([...])      │
     │                                              ├─ search_confluence(query) ─┐
     │                                              └─ search_openapi(query) ────┤
     │                                                    RagClient.search()     │ HTTP
     │                                                                           ▼
     │                                                            POST /v1/search (신규)
     │                                                              └─ RetrieverService.search()
   InMemorySaver(checkpointer) ─ 멀티턴 기억
```

- 답변 생성은 **에이전트 LLM 이 담당**한다. RAG 의 `/v1/ask`(RAG 쪽 LLM 답변 포함)는 사용하지 않는다.
- RAG 서버는 별도 프로세스로 기동되어 있어야 한다(`python main.py --active-profile=local` in RAG). 에이전트는 RAG 의 Chroma·BM25 상태를 알지 못하며 HTTP 만 사용한다.

### Part A — RAG 저장소: `POST /v1/search` 추가

**영향 파일**: `RAG/src/dto/ask_dto.py`, `RAG/src/controller/ask_controller.py`, `RAG/test/test_ask_controller.py`

**DTO** (`ask_dto.py` 에 추가, 기존 DTO 변경 없음):

```python
class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000, description="검색어")
    source: SourceFilter = Field(default="all", description="검색 대상 소스")
    top_k: Optional[int] = Field(default=None, ge=1, le=20, description="검색 청크 수(미지정 시 설정값)")

class SearchChunk(BaseModel):
    chunk_id: str
    title: str
    url: str
    source: str
    content: str          # Document.page_content 원문 (청킹 접두어 포함)
    metadata: dict        # Document.metadata 전체 (Chroma 제약상 값은 str|int|float|bool)

class SearchResponse(BaseModel):
    chunks: list[SearchChunk]
```

**엔드포인트** (`ask_controller.py`):

```python
@router.post(f"/{__api_root}/search", response_model=SearchResponse, tags=["질의"])
def search(request: SearchRequest):   # 동기 — 임베딩 HTTP 1회를 스레드풀에서 실행
```

동작 규칙:

| 상황 | 결과 |
|---|---|
| 정상 | `get_context().retriever.search(query, source, top_k)` 결과를 `SearchChunk` 로 변환, 순서 유지, 200 |
| 결과 0건 | `{"chunks": []}` 200 (LLM 호출 없음 — 원래 search 는 LLM 을 쓰지 않음) |
| 임베딩(Ollama) 장애 (`llm_factory.LLM_ERRORS`) | 기존 `_translate_llm_error` 재사용 → 504 / 503 |
| 검증 실패 (빈 query, top_k 범위 밖, 잘못된 source) | 422 (pydantic 기본) |

`SearchChunk` 변환은 `chunk_id/title/url/source` 를 `metadata.get(key, "")` 로 채우고, `content=doc.page_content`, `metadata=doc.metadata` 로 한다. 별도 서비스 클래스는 만들지 않는다(컨트롤러 내부 변환 함수 1개까지 허용).

**기존 테스트 갱신 허용**: `test_openapi_lists_four_paths_and_hides_root` 는 라우트 개수 4→5 로 갱신하고 이름을 `test_openapi_lists_five_paths_and_hides_root` 로 바꾼다. 그 외 기존 테스트는 수정하지 않는다.

### Part B — LangGraph 프로젝트

**디렉터리 구조** (RAG/팀 컨벤션을 축소 적용: `main.py`, `--active-profile`, `resources/config_{profile}.ini`, `src/`, `test/`, `requirements.in`+pip-compile):

```
LangGraph/
├── main.py                              # CLI REPL 진입점
├── requirements.in / requirements.txt   # pip-compile (사용자 허가 후 설치)
├── pytest.ini                           # addopts = -m "not integration", markers 등록
├── .gitignore                           # .venv, resources/config_local.ini, .idea, __pycache__, logs
├── resources/
│   ├── config_local.ini.example         # 커밋 대상 템플릿
│   └── config_local.ini                 # .gitignore 대상 (example 복사)
├── src/
│   ├── __init__.py
│   ├── config/
│   │   ├── __init__.py
│   │   └── settings.py                  # Settings dataclass + load_settings()
│   ├── llm_factory.py                   # create_chat_model(settings) — LLM 생성 단일 지점
│   ├── rag_client.py                    # RagClient — /v1/search HTTP 클라이언트
│   ├── tools.py                         # build_tools(client, top_k) — LangChain tool 2개
│   └── agent.py                         # build_graph(chat_model, tools, checkpointer)
└── test/
    ├── __init__.py
    ├── conftest.py
    ├── test_settings.py
    ├── test_rag_client.py
    ├── test_tools.py
    ├── test_agent.py
    └── test_integration_agent.py        # -m integration (RAG 서버 + Ollama 필요, 없으면 skip)
```

import 는 RAG 와 동일하게 프로젝트 루트 기준 `from src.rag_client import RagClient` 형태.

**설정** (`resources/config_local.ini.example`):

```ini
[rag]
base-url=http://127.0.0.1:5010
search-path=/v1/search
timeout=30
top-k=6

[ollama]
base-url=http://127.0.0.1:11434
llm-model=qwen3:14b
num-ctx=16384
temperature=0
llm-timeout=120

[agent]
recursion-limit=12
```

**`src/config/settings.py`** — 표준 `configparser` 만 사용 (RAG 의 `advanced-python-singleton`/`Profile` 은 도입하지 않음 — 아래 "기각한 대안" 참조):

```python
@dataclass(frozen=True)
class Settings:
    rag_base_url: str
    rag_search_path: str
    rag_timeout: float
    rag_top_k: int
    ollama_base_url: str
    llm_model: str
    num_ctx: int
    temperature: float
    llm_timeout: float
    recursion_limit: int

def load_settings(path: str | Path) -> Settings
    # 파일이 없으면 FileNotFoundError, 키가 없으면 KeyError 그대로 전파 (숨기지 않음)

def config_path_for(profile: str) -> Path
    # -> <프로젝트 루트>/resources/config_{profile}.ini
```

**`src/llm_factory.py`**:

```python
def create_chat_model(settings: Settings) -> ChatOllama
    # ChatOllama(model, base_url, temperature, num_ctx, reasoning=False,
    #            client_kwargs={"timeout": llm_timeout})  — RAG llm_factory 와 동일 옵션
```

**`src/rag_client.py`**:

```python
class RagClient:
    def __init__(self, base_url: str, search_path: str, timeout: float,
                 transport: httpx.BaseTransport | None = None)   # transport 는 테스트 주입용
    def search(self, query: str, source: str, top_k: int) -> list[dict]
        # POST {base_url}{search_path} json={"query","source","top_k"}
        # 200 → response["chunks"] (list[dict], SearchChunk 형태)
        # 4xx/5xx → httpx.HTTPStatusError (raise_for_status), 연결/타임아웃 → httpx.HTTPError 그대로 전파
```

**`src/tools.py`**:

```python
NO_RESULT_TEXT = "검색 결과가 없습니다."

def format_chunks(chunks: list[dict]) -> str
    # 청크마다:  "### {title}\n- source: {source} | url: {url}\n{content}"  블록을 빈 줄 2개로 연결
    # 구분자는 "\n\n" (개행 2개 = 블록 사이 빈 줄 1개). 아래 리드 판단 기록 2026-09-22 (2) 참조
    # chunks 가 빈 리스트면 NO_RESULT_TEXT

def build_tools(client: RagClient, top_k: int) -> list[BaseTool]
    # 반환 순서 고정: [search_confluence, search_openapi]
```

도구 계약 (이름·인자·docstring 은 LLM 에 노출되는 인터페이스이므로 변경 금지):

| 도구 이름 | 인자 | source | docstring (설명) |
|---|---|---|---|
| `search_confluence` | `query: str` | `"confluence"` | 팀 Confluence(KUDOS) 문서를 검색한다. 정책, 설정값 정의, 운영 절차, 용어, 배경 설명 질문에 사용한다. |
| `search_openapi` | `query: str` | `"openapi"` | 사내 API 의 OpenAPI 스펙(엔드포인트, HTTP 메서드, 경로, 파라미터, 요청/응답 스키마)을 검색한다. API 호출 방법 질문에 사용한다. |

- 두 도구는 `client.search(query, source, top_k)` → `format_chunks()` 문자열을 반환한다.
- `RagClient` 예외는 도구 안에서 잡지 않는다. 그래프 쪽에서 `ToolNode(tools, handle_tool_errors=True)` 로 **명시**해 예외를 `ToolMessage(status="error")` 로 변환하므로 그래프는 중단되지 않고 LLM 이 오류를 인지한다(테스트로 확인). 기본값에 의존하지 않는 이유는 "리드 판단 기록 (3)" 참조.

**`src/agent.py`**:

```python
SYSTEM_PROMPT: str   # 아래 규칙을 담은 한국어 프롬프트 상수

def build_graph(chat_model: BaseChatModel, tools: list[BaseTool],
                checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph
```

그래프 정의(명시적 StateGraph — `create_react_agent` 프리빌트는 쓰지 않음, "기각한 대안" 참조):

- 상태: `langgraph.graph.MessagesState`
- 노드 `agent`: `[SystemMessage(SYSTEM_PROMPT)] + state["messages"]` 를 `chat_model.bind_tools(tools).invoke()` 에 넘기고 `{"messages": [response]}` 반환. SystemMessage 는 상태에 저장하지 않고 호출 때마다 앞에 붙인다.
- 노드 `tools`: `langgraph.prebuilt.ToolNode(tools, handle_tool_errors=True)` (명시 필수)
- 엣지: `START → agent`, `agent → tools_condition` (`langgraph.prebuilt.tools_condition`: tool_calls 있으면 `tools`, 없으면 `END`), `tools → agent`
- `compile(checkpointer=checkpointer)`

`SYSTEM_PROMPT` 규칙(문구는 builder 재량, 항목은 유지):
1. 팀 내부 문서(Confluence)와 API 명세(OpenAPI)를 근거로 답하는 어시스턴트. 답하기 전에 반드시 도구로 검색한다(일반 상식 질문·인사 등은 예외).
2. 질문 성격에 따라 도구를 고른다. API 호출 방법 → `search_openapi`, 정책·설정·절차 → `search_confluence`, 둘 다 필요하면 둘 다 호출한다. 첫 검색이 부족하면 검색어를 바꿔 다시 검색한다.
3. 검색 결과에 없는 내용은 추측하지 않는다. 찾지 못하면 "관련 내용을 문서에서 찾지 못했습니다." 라고 말한다.
4. API 관련 답에는 HTTP 메서드, 경로, 필수 파라미터/필드를 명시한다.
5. 답변 끝에 `출처:` 목록으로 사용한 문서의 제목과 url 을 적는다.
6. 한국어로 간결하게 답한다.

**`main.py`**:

```
python main.py --active-profile=local
```

- `argparse` 로 `--active-profile`(기본 `local`) 파싱 → `load_settings(config_path_for(profile))`.
- `RagClient`, `build_tools`, `create_chat_model`, `InMemorySaver()`, `build_graph` 조립. `thread_id` 는 프로세스당 1개(`uuid4`).
- REPL: 프롬프트 `> ` 로 입력. `exit` / `quit` / EOF(Ctrl-D) 종료. 빈 입력 무시.
- 각 턴: `graph.stream({"messages": [HumanMessage(text)]}, config={"configurable": {"thread_id": ...}, "recursion_limit": settings.recursion_limit}, stream_mode="values")` 를 순회하며
  - 마지막 메시지가 `AIMessage` 이고 `tool_calls` 가 있으면 `[검색] {name}({query})` 한 줄 출력
  - 마지막 메시지가 최종 `AIMessage`(tool_calls 없음)이면 `content` 출력
- 예외 처리: `httpx.HTTPError`, `ollama.ResponseError`, `ConnectionError`, `langgraph.errors.GraphRecursionError` 를 잡아 한 줄 오류 메시지 출력 후 REPL 계속. 그 외 예외는 전파(숨기지 않음).
- `ToolNode` 가 도구 예외를 흡수하므로 RAG 서버 장애는 보통 LLM 의 답변으로 드러난다. 그래도 REPL 진입 시 `GET {rag_base_url}/check` 1회를 호출해 실패하면 경고 한 줄을 출력한다(종료하지는 않음).

### 새 의존성 (LangGraph 프로젝트, 사용자 허가 필요 — §7)

`requirements.in`:

```
langgraph
langchain-core
langchain-ollama
ollama
httpx
pytest
pip-tools
```

- `langgraph`, `langchain-core`, `httpx` 는 이미 venv 에 있지만 직접 import 하므로 명시 고정(RAG README "의존성 메모" 방식과 동일).
- `langchain-ollama`(ChatOllama), `ollama`(`ResponseError` 예외 타입), `pytest`, `pip-tools` 는 신규 설치 필요.
- **Builder 는 `pip install` 을 실행하기 전에 사용자 허가를 받는다.** 명세 승인 시점(2026-09-22)에 사용자가 "추천대로 진행" 을 확정했으므로 위 목록에 한해 설치를 허용한 것으로 본다. 목록 외 패키지가 필요하면 리드에게 보고하고 대기한다.
- RAG 저장소는 새 의존성 없음.

### 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| RAG 코드를 직접 import (in-process) | RAG 는 패키지가 아니고(`pyproject` 없음) `src.` 최상위 네임스페이스라 두 프로젝트가 충돌. BM25 `reload()` 라이프사이클도 에이전트가 떠안게 됨 |
| 기존 `/v1/ask` 를 도구로 사용 | RAG LLM + 에이전트 LLM 이중 호출로 느리고, 에이전트가 원본 청크를 보지 못해 복합 질문 조합이 불리 |
| `langgraph.prebuilt.create_react_agent` | 1.x 에서 `langchain.agents.create_agent` 로 이동 중인 API(`[미확인]` 정확한 deprecation 상태). 명시적 StateGraph 가 40줄 내이고 프로젝트 목적(LangGraph 학습·확장)에 부합 |
| `Profile` 싱글톤(`advanced-python-singleton`) 도입 | 설정 섹션 3개에 싱글톤·전역 상태는 과함. `configparser` + dataclass 로 충분하며 테스트 주입이 쉬움 |
| 도구를 소스별 2개가 아니라 `search(query, source)` 1개 | 도구 설명이 소스 선택 힌트를 담을 수 있어 소형 로컬 모델의 라우팅 정확도에 유리. 도구 2개는 비용이 거의 없음 |
| 사내 API 실호출 도구 | 인증·부작용(POST/DELETE) 정책 결정이 필요. 별도 티켓 |
| `langgraph dev` / FastAPI 노출 | 1차 목표는 도구 연결 검증. `langgraph-cli` 추가 의존성도 불필요 |

## 작업 목록

각 작업은 독립적으로 검증 가능해야 한다. 순서: 1 → 2 → 3 → 4 → 5 → 6 → 7.

1. **[RAG] `POST /v1/search` 추가** — `ask_dto.py` DTO 3개, `ask_controller.py` 핸들러, `test_ask_controller.py` 에 테스트 추가(정상/빈 결과/422/Ollama 장애 503·504) 및 라우트 개수 테스트 갱신. RAG venv 에서 `pytest --active-profile=local` green.
2. **[Agent] 프로젝트 골격 + 설정** — 디렉터리, `.gitignore`, `pytest.ini`, `requirements.in`(설치는 허가 범위 내), `config_local.ini.example`, `src/config/settings.py`, `test/test_settings.py`(tmp_path ini 로드, 타입 변환, 파일 없음 → `FileNotFoundError`).
3. **[Agent] `RagClient`** — `src/rag_client.py`, `test/test_rag_client.py`(`httpx.MockTransport` 로 요청 body 검증, 응답 파싱, 500 → `HTTPStatusError`, 연결 실패 → `httpx.HTTPError` 계열).
4. **[Agent] 도구** — `src/tools.py`, `test/test_tools.py`(가짜 client 로 도구 이름·인자 스키마·source 매핑·`format_chunks` 포맷·빈 결과 → `NO_RESULT_TEXT`).
5. **[Agent] 그래프** — `src/agent.py`, `src/llm_factory.py`, `test/test_agent.py`. 테스트는 `bind_tools` 를 오버라이드해 자기 자신을 반환하는 테스트 전용 가짜 모델(`langchain_core.language_models.fake_chat_models.FakeMessagesListChatModel` 상속, 응답 스크립트 주입)로 (a) tool_call → 도구 실행 → 최종 답변까지 메시지 순서, (b) tool_calls 없는 첫 응답은 바로 END, (c) 도구가 예외를 던지면 `ToolMessage(status="error")` 가 기록되고 그래프가 계속됨, (d) `InMemorySaver` + 같은 `thread_id` 로 2턴 호출 시 이전 메시지가 유지됨을 확인.
6. **[Agent] CLI `main.py`** — REPL. 테스트는 `test_agent.py` 또는 별도 파일에서 `main.run_turn(graph, config, text) -> None`(출력 함수 주입 가능) 수준으로 분리해 `[검색]` 줄과 최종 답변 출력 형식만 확인. 입력 루프 자체는 수동 확인.
7. **[Agent] 통합 테스트 + README** — `test/test_integration_agent.py`(`-m integration`): RAG `GET /check` 200 + Ollama `/api/tags` 에 `qwen3:14b` 있을 때만 실행, 아니면 `pytest.skip`. 질문 "메시지 등록 API 호출 방법 알려줘" 로 `search_openapi` tool_call 이 1회 이상 발생하고 최종 답변에 `출처:` 가 포함됨을 확인. README 에 사전 준비(RAG 서버 기동, Ollama), 실행, 테스트 방법 기록.

## 검증 전략

| 완료 기준 | 검증 방법 |
|---|---|
| A1. `/v1/search` 정상 응답 | RAG `test_ask_controller.py`: 가짜 retriever 주입 → 200, `chunks[i]` 필드 6개, 순서 유지 |
| A2. 빈 결과 200 | 가짜 retriever 가 `[]` → `{"chunks": []}` |
| A3. 검증 실패 422 | 빈 query, `top_k=0`, `top_k=21`, `source="x"` |
| A4. Ollama 장애 503/504 | retriever.search 가 `httpx.ConnectError` / `httpx.ReadTimeout` 을 던지도록 → 503 / 504 |
| A5. 기존 테스트 회귀 없음 | RAG venv: `pytest --active-profile=local` 전체 green (라우트 개수 테스트만 명세대로 갱신) |
| B1. 설정 로드 | `test_settings.py` |
| B2. HTTP 클라이언트 | `test_rag_client.py` (`MockTransport`, 네트워크 없음) |
| B3. 도구 계약 | `test_tools.py`: `tool.name`, `tool.args` 에 `query` 만 존재, docstring 비어 있지 않음, source 매핑 |
| B4. 그래프 동작 | `test_agent.py` (a)~(d) |
| B5. CLI 출력 | `run_turn` 단위 테스트 |
| B6. 단위 스위트 네트워크 비의존 | `pytest`(기본, integration 제외) 가 RAG 서버·Ollama 없이 green |
| B7. 실제 도구 호출 (qwen3 tool calling `[미확인]` 해소) | `pytest -m integration` PASS 또는 환경 미충족 시 skip 사유 출력. **PASS 1회 기록이 READY FOR REVIEW 조건** |
| B8. 멀티턴 수동 확인 | `python main.py` 에서 "메시지 등록 API 알려줘" → "그 API 필수 필드는?" 두 번째 질문이 문맥을 유지해 답함(Validator 가 결과를 명세 하단 검증 기록에 적음) |

## 완료 기준

- [ ] A1~A5 통과 (RAG 저장소)
- [ ] B1~B7 통과, B8 기록 (LangGraph 저장소)
- [ ] `requirements.txt` 가 `pip-compile` 결과이며 목록 외 패키지 직접 import 없음
- [ ] `resources/config_local.ini` 는 커밋 대상 아님(`.gitignore`), `.example` 만 존재
- [ ] 두 저장소 모두 커밋하지 않음 (사용자가 직접 커밋)
- [ ] 설계 문서와 구현 일치: 모듈 경로·함수 시그니처·도구 이름/설명·설정 키

## 범위 제외

- 사내 API 실호출 도구 (별도 티켓)
- FastAPI/`langgraph dev` 노출, 스트리밍 토큰 출력, 웹 UI
- 영구 체크포인터(SQLite 등) — 프로세스 종료 시 대화 소실은 허용
- RAG 검색 품질·청킹·가중치 튜닝, `/v1/ask` 변경
- 에이전트 답변 품질 평가 세트
- 인용 번호 `[n]` 방식(RAG `/v1/ask` 의 형식) — 에이전트는 제목+url 목록 방식

## 리드 판단 기록

- 2026-09-22 (1): 사용자 "추천대로 진행" 확정. 위 4개 결정 사항 반영.
- 2026-09-22 (2): Builder 질의 — `format_chunks` "블록을 빈 줄 2개로 연결" 문구 해석. **결정: 구분자 `"\n\n"`** (개행 2개, 블록 사이 빈 줄 1개 — 일반 마크다운 블록 구분). 원문 "빈 줄 2개"는 "개행 2개"의 오기였으므로 명세 문구를 정정함. Builder 잠정 구현(해석 A)과 테스트 그대로 유효.
- 2026-09-22 (3): Builder 보고 — `[검증]` `langgraph-prebuilt 1.1.0` 의 `ToolNode` 기본 `handle_tool_errors` 는 `_default_handle_tool_errors` 로, `ToolInvocationError`(인자 검증 실패)만 변환하고 그 외 예외는 재던짐. 명세의 "기본값이 True" 기술은 오류였음. **결정: `ToolNode(tools, handle_tool_errors=True)` 명시** (Builder 조치 승인). 도구 내부에서 예외를 잡아 문자열로 반환하는 대안은 기각 — 도구 계약을 단순하게 유지하고 오류 처리 책임을 그래프 한 곳에 둠. 기존 결과 `ToolMessage(status="error")` 로 테스트 (c) 통과. 아울러 Builder 가 명세 범위 밖으로 추가했다가 삭제한 recursion_limit 루프 테스트는 요구 사항이 아니므로 삭제 상태 그대로 둔다.
- 2026-09-22 (4): Builder 완료 보고 접수 — 작업 1~7 완료. Builder 자체 실행 결과: RAG 324 passed / 에이전트 단위 44 passed, 1 deselected / 통합 1 passed(B7 PASS 1회, Builder 실행). 명세와의 차이는 (2)(3) 승인 건 2건뿐. 부수 사항: `pip-compile` 결과 `langchain-core` 1.6.4 · `langgraph` 1.2.12 로 패치 상향(허용 목록 내 전이 결과, 별도 결정 불필요). B8 은 Builder 미수행(Validator 항목). Builder 가 "validator" 로 보낸 검증 요청은 이 티켓과 무관한 피어 세션으로 갔을 가능성이 있어 무시하고, 리드가 이 티켓 전용 Validator 를 별도 스폰해 A1~A5·B1~B8·완료 기준 검증을 지시함. READY FOR REVIEW 판정은 Validator 보고 후.
- 2026-09-22 (5): 검증 담당 중복 발생 — Builder 의 검증 요청이 피어 세션 `validator [19ffd4]` 에 도달해 그 세션도 검증을 시작했고, 리드가 스폰한 이 티켓 전용 Validator 와 같은 테스트 파일을 동시에 편집하는 충돌이 확인됨. **결정: 이 티켓의 검증 담당은 리드가 스폰한 Validator 1명.** 피어 세션 `validator [19ffd4]` 는 파일 편집을 중단하고 지금까지 직접 실행한 결과(RAG 324 passed, 단위 55 passed, B7 통합 1 passed, B8 멀티턴 2턴 원문)만 리드에게 보고한다. 피어 세션이 이미 append 한 테스트 2건(`test_agent.py`: `test_without_checkpointer_history_is_not_kept`, `test_graph_nodes_and_edges_match_design`)은 green 이므로 삭제하지 않고 유지하며, 담당 Validator 가 자기 검증 기록에 포함해 최종 판정한다. 피어 세션의 B7/B8 실행 결과는 담당 Validator 의 독립 재실행을 대체하지 않는다(가능하면 담당 Validator 가 재실행, 환경 부재 시 피어 결과를 출처 명시하여 인용).
- 2026-09-22 (6): 피어 세션 `validator [19ffd4]` 실행 결과 접수(출처: 피어 세션, 담당 Validator 판정과 별개로 참고 기록). 편집 중단 확인, 명세 미편집. 실행 환경: RAG :5010 `/check` 200, Ollama `qwen3:14b` 확인.
  - A5: RAG `pytest --active-profile=local -q` → 324 passed, 5 deselected. `git diff --cached` 빈 출력(스테이징 0건), HEAD `cb4c9e7` 유지.
  - B1~B5: 단위 55 passed, 1 deselected(Builder 44 + 담당 Validator 9 + 피어 2).
  - B6: 스크래치패드 pytest 플러그인으로 `socket.connect`/`connect_ex` 를 OSError 로 차단한 상태에서 55 passed → 단위 스위트가 실제 소켓을 열지 않음을 실증.
  - B7: `pytest -m integration -q` → 1 passed(15.71s). `search_openapi` tool_call 발생, 최종 답변 `출처:` 포함.
  - B8: `printf '메시지 등록 API 알려줘\n그 API 필수 필드는?\nexit\n' | python main.py --active-profile=local` 종료코드 0. 1턴 `[검색] search_openapi(메시지 등록 API)` 후 엔드포인트 6건 답변 + `출처:`. 2턴은 도구 재호출 없이 1턴 목록의 필수 필드를 나열 → 체크포인터 멀티턴 기억 동작 확인. 로그 원문은 피어 세션 스크래치패드(`.../42a22f4c-.../scratchpad/b8.log`, 세션 종료 시 소멸 가능).
  - 읽기 확인: 서드파티 최상위 import 는 `httpx, langchain_core, langchain_ollama, langgraph, ollama, pytest` 뿐(허용 목록 내). `.gitignore` 에 `resources/config_local.ini` 포함. **LangGraph 디렉터리는 아직 git 저장소가 아님** → ignore 동작 실증 불가(사용자 `git init` 후 확인 필요).
  - 별건 `[미확인]`: 피어가 2026-09-15 에 검증했던 LANGGRAPH-30 산출물(`pyproject.toml`, `src/reviewer/`, `tests/`, `docs/plans/LANGGRAPH-30.md`)이 현재 디렉터리에 없음. 이번 작업 범위와 무관하며 Builder 보고에도 삭제 언급 없음. 사용자 확인 사항으로 남김.
- 2026-09-22 (7): 담당 Validator 판정 READY FOR REVIEW 접수(검증 회차 1, 아래 "검증 기록" 참조). 리드 확인 — (a) 완료 기준 6개 전부 PASS, 설계 문서 미기재 이탈 없음. (b) 테스트 green: RAG 333 passed / 에이전트 단위 55 passed / 통합 1 passed ×2 / 소켓 차단 상태 55 passed. (c) 설계-구현 일치: 리드가 `src/agent.py`·`src/tools.py` 를 직접 읽어 `SYSTEM_PROMPT` 6규칙, `build_graph` 시그니처·엣지·`ToolNode(tools, handle_tool_errors=True)`, `format_chunks` 블록 형식·`"\n\n"` 구분자, 도구 이름·순서·docstring 이 명세와 일치함을 확인. **결론: READY FOR REVIEW 선언.** 비차단 의견 3건은 설계 변경 사유가 아니라고 판단: ① `langgraph.prebuilt`/`langgraph.checkpoint` 직접 import 는 `langgraph` 의 필수 의존이라 명세의 허용 목록을 유지(직접 고정은 후속 과제로만 남김). ② B8 `출처:` 에 제목 누락은 모델 출력 편차로 범위 제외. ③ RAG 작업 트리의 무관한 미커밋 변경은 사용자가 커밋 시 분리해야 할 사항. 남은 사용자 확인 사항: LangGraph 디렉터리 `git init` 후 `resources/config_local.ini` 가 `git status` 에서 제외되는지 확인, LANGGRAPH-30 산출물 부재 원인((6) 참조).
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

### 2026-09-22 Validator 검증 (검증 회차: 1)

실행 환경 `[검증]`: RAG 서버 `GET /check` → `{"status":"ok","ollama":true,"chunk_count":13812}`, Ollama `qwen3:14b`(capabilities 에 `tools` 포함) 설치됨 → B7·B8 실행 가능.

**스위트 결과**

| 명령 | 결과 |
|---|---|
| RAG `.venv/bin/python -m pytest --active-profile=local -q` | **333 passed, 5 deselected** (Validator 추가 테스트 9건 포함. Builder 시점 324) |
| LangGraph `.venv/bin/python -m pytest -q` | **55 passed, 1 deselected** (Builder 44 + Validator 추가 9 + 피어 세션 추가 2) |
| LangGraph `pytest -q -p block_socket` (소켓 차단 플러그인, 프로젝트 외부 주입) | **55 passed, 1 deselected** |
| LangGraph `.venv/bin/python -m pytest -m integration -q` | **1 passed, 55 deselected** (38.1초). 최초 실행 1 passed(23.8초) 포함 **2회 연속 PASS** |

**검증 전략 항목**

| 항목 | 결과 | 증거 |
|---|---|---|
| A1 정상 응답 | PASS | `test/test_ask_controller.py:365` 200·필드 6개·순서 유지·retriever 인자 전달. 실서버 `POST /v1/search` 직접 호출도 정상 응답 확인 |
| A2 빈 결과 200 | PASS | `test/test_ask_controller.py:404` `{"chunks": []}` |
| A3 검증 실패 422 | PASS | `test/test_ask_controller.py:411` (빈 query·필드 없음·source="x"·top_k 0·21) + 추가 `test_search_query_length_boundary`(2000/2001), `test_search_top_k_boundary_accepted`(1·20) |
| A4 Ollama 장애 503/504 | PASS | `test_search_ollama_down_returns_503`, `test_search_embedding_timeout_returns_504` + 추가 `test_search_model_not_found_returns_503`(`ollama.ResponseError`) |
| A5 기존 테스트 회귀 없음 | PASS | 333 passed. 기존 테스트 변경은 `test_openapi_lists_four_paths_and_hides_root` → `..._five_paths_...`(명세 99행 허용) 1건뿐 (`git diff` 확인) |
| B1 설정 로드 | PASS | `test/test_settings.py` 8건 — 전 키 로드·타입 변환·frozen·파일/키/섹션 누락 예외·`config_path_for`·example 템플릿 로드 |
| B2 HTTP 클라이언트 | PASS | `test/test_rag_client.py` — `MockTransport` 로 method/url/body, 404·422·500·503 → `HTTPStatusError`, ConnectError·ReadTimeout 전파. 소켓 차단 상태에서도 green |
| B3 도구 계약 | PASS | `test/test_tools.py` — 이름·순서 `[search_confluence, search_openapi]`, `tool.args` 는 `query`(string) 하나, description 이 명세 표 문구와 완전 일치, source·top_k 매핑, 빈 결과 → `NO_RESULT_TEXT`, 예외 비흡수 |
| B4 그래프 동작 (a)~(d) | PASS | `test/test_agent.py` — (a) `["HumanMessage","AIMessage","ToolMessage","AIMessage"]` (b) tool_calls 없으면 바로 END·모델 1회 호출 (c) `ToolMessage.status == "error"` 후 그래프 계속 (d) 같은 `thread_id` 2턴에서 이전 메시지 유지 + 다른 thread_id 는 격리 |
| B5 CLI 출력 | PASS | `test/test_main.py` — `[검색] search_openapi(메시지 등록)` 줄과 최종 답변, 병렬 tool_call 2줄, `warn_if_rag_down` 정상/연결실패/에러상태 3케이스 |
| B6 단위 스위트 네트워크 비의존 | PASS | `socket.socket.connect`·`socket.create_connection` 을 차단한 플러그인으로 실행 → 55 passed |
| B7 실제 도구 호출 | **PASS** | `pytest -m integration` 1 passed, 2회 연속(23.8초·38.1초). 실제 qwen3:14b 가 `search_openapi` tool_call 발생, 최종 답변에 `출처:` 포함 |
| B8 멀티턴 수동 확인 | **PASS**(2회 재현) | `python main.py --active-profile=local` 에 "메시지 등록 API 알려줘" → "그 API 필수 필드는?" 입력. 1턴: `[검색] search_openapi(메시지 등록 API)` 후 `POST /v1/messages/message` 등 6개 엔드포인트·필수 필드·`출처:` 출력. 2턴: **재검색 없이** 1턴에서 찾은 동일 6개 API 의 필수 필드를 답함 → 문맥 유지 확인. `exit` 로 정상 종료 |

**완료 기준**

| 기준 | 결과 | 증거 |
|---|---|---|
| A1~A5 통과 | PASS | 위 표 |
| B1~B7 통과, B8 기록 | PASS | 위 표 (B7 은 담당 Validator 직접 실행 2회 연속 PASS — 조건 충족) |
| `requirements.txt` 가 `pip-compile` 결과, 목록 외 직접 import 없음 | PASS | 헤더 `pip-compile --no-index --output-file=requirements.txt requirements.in`. 격리 사본에서 재컴파일 → **diff 0**. `pip check` 정상, 핀 49개 전부 설치 버전과 일치(불일치 0). 직접 import 는 `httpx·langchain_core·langchain_ollama·langgraph·ollama·pytest` + 표준 라이브러리뿐 |
| `config_local.ini` 커밋 대상 아님 | PASS(문서상) | `.gitignore:7` 에 `resources/config_local.ini` 존재, `.example` 템플릿 존재(두 파일 내용 동일). `[미확인]` LangGraph 디렉터리가 아직 git 저장소가 아니라 ignore **동작** 자체는 실증 불가 — 사용자 `git init` 후 `git status` 로 확인 필요 |
| 두 저장소 모두 커밋하지 않음 | PASS | RAG `HEAD=cb4c9e7`(사용자 커밋) 그대로, 스테이징·stash 없음, 작업 트리만 수정. LangGraph 는 git 저장소가 아님 |
| 설계 문서와 구현 일치 | PASS | 모듈 경로·`Settings` 필드 10개·`load_settings`/`config_path_for`/`create_chat_model`/`RagClient.search`/`format_chunks`/`build_tools`/`build_graph`/`run_turn` 시그니처·도구 이름·docstring·설정 키 전부 명세와 일치. `ToolNode(tools, handle_tool_errors=True)` 명시(판단 기록 (3)), `format_chunks` 구분자 `"\n\n"`(판단 기록 (2)) |

**피어 세션 테스트 취급**: `test/test_agent.py` 의 `test_without_checkpointer_history_is_not_kept`·`test_graph_nodes_and_edges_match_design` 2건은 다른 피어 세션이 append 한 것으로, 리드 지시에 따라 **유지**했다. 내용을 검토한 결과 기존 단언을 약화시키지 않으며 B4 를 보강한다(체크포인터 미지정 시 이력 미유지, `START→agent→tools→agent`·`agent→END` 엣지 구성). 위 55 passed 는 이 2건을 포함한 수치다.

**Validator 가 추가한 테스트** (테스트 파일에만 append, 프로덕션 코드 무수정)

- `RAG/test/test_ask_controller.py`: query 길이 경계(2000/2001), top_k 경계(1/20), source 3종 전달, LLM 미호출(LLM 이 죽어도 200), `ollama.ResponseError` → 503, metadata 원문 보존
- `LangGraph/test/test_tools.py`: 3청크 순서 보존, 멀티라인 content 원문 보존 + 블록 구분자 `"\n\n"`, 두 도구 모두 top_k 전달
- `LangGraph/test/test_rag_client.py`: 404 → `HTTPStatusError`, 요청 body 키 3개만, 서버 응답 순서 보존
- `LangGraph/test/test_agent.py`: 병렬 tool_call 2건 → `ToolMessage` 2건, 2회 검색 후 답변(agent→tools→agent→tools→agent), `ToolMessage.tool_call_id` 대응

**발견한 버그**: 없음.

**비차단 의견** (판정 미반영)

1. `src/agent.py`·`main.py` 는 `langgraph.prebuilt`(langgraph-prebuilt)·`langgraph.checkpoint`(langgraph-checkpoint) 를 직접 import 하지만 `requirements.in` 에는 `langgraph` 만 있다. 명세 262~279행이 이 목록을 그대로 규정했으므로 기준 위반은 아니다. 다만 RAG 저장소 "후속 5" 에서 정리한 "직접 import 는 `requirements.in` 에 명시" 원칙과는 결이 다르다(둘 다 `langgraph` 의 필수 의존이라 위험은 낮음).
2. B8 실행에서 LLM 이 출력한 `출처:` 목록에 url 만 있고 문서 제목이 없었다. `SYSTEM_PROMPT` 규칙 5 는 "제목과 url" 을 요구하나 이는 모델 출력 편차이며 코드·프롬프트 결함은 아니다. 답변 품질 평가는 명세 "범위 제외".
3. RAG 작업 트리에는 agent-01 과 무관한 미커밋 변경(`README.md`, `requirements.in/txt`, `docs/plans/conventions.md`, `docs/plans/review-04-task12-13-final.md`)이 함께 있다. `[추측]` 종결된 "후속 5" 산출물로 보이며 agent-01 커밋 시 섞이지 않도록 사용자 확인 필요.

**판정: READY FOR REVIEW**

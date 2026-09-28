# agent-09: 영구 체크포인터(SQLite) — 프로세스 재시작 후 대화 유지

- 작성일: 2026-09-28
- 작성자: 리드
- 상태: 종결 — 사용자 커밋·push 완료 (2026-09-28, `bca3b5a` → `origin/main`). 브라우저 화면 렌더링 육안 확인만 남음
- 사용자 확정: "① 영구 체크포인터(SQLite) + thread 만료" 중 **영구 저장까지** 이번 티켓. 만료·정리는 agent-10 후보로 분리(사유: 아래 "범위 제외").
- 저장소: `/Users/mjkim/workspace/LangGraph` 만. RAG 저장소 무변경.

## 목표

프로세스를 내리면 사라지던 대화(`InMemorySaver`)를 SQLite 파일에 저장해, **서버·CLI 를 재시작해도 이어서 대화**할 수 있게 한다. 저장만으로는 사용자에게 아무 효과가 없으므로(진입점이 매번 새 `thread_id` 를 만들기 때문) **대화를 다시 붙잡는 경로까지** 포함한다: CLI `--thread`, 웹 `localStorage` + 이전 대화 복원.

## 확인된 현재 상태 (`[검증]` 2026-09-28 리드가 파일 직접 확인)

- `src/agent.py:91` `build_default_graph(settings)` 가 `InMemorySaver()` 를 하드코딩. `build_graph(chat_model, tools, checkpointer=None)` 는 이미 주입 가능.
- `main.py`: `uuid.uuid4()` 로 매 프로세스 새 `thread_id`. `graph.stream(...)` — **동기**.
- `web.py`: `uvicorn.run(create_app(graph, settings), host, port)`.
- `src/web/app.py`: `create_app(graph, settings)`; 라우트 `/`(숨김), `/check`, `POST /v1/chat/stream`. `stream_events` 는 `graph.astream(...)` — **비동기**.
- `resources/static/index.html`: 페이지 로드 시 `let threadId = crypto.randomUUID()` → 새로고침하면 항상 새 대화.
- 설정 섹션 4개(`[rag] [ollama] [agent] [fastapi]`), `Settings` 12필드, `load_settings` 는 표준 `configparser`(경로 치환 없음).
- `test/test_web_ui.py` 가 `let\s+threadId\s*=\s*crypto\.randomUUID\(\)` 를 정규식으로 고정. `test/test_web.py` 가 OpenAPI 경로 목록을 `["/check", "/v1/chat/stream"]` 로 고정.
- 단위 144 passed / 통합 6 passed (HEAD 기준).

### 핵심 제약 (`[검증]` 2026-09-28 Builder 실측 완료 — 아래 리드 판단 기록 (4))

`langgraph-checkpoint-sqlite` 의 `SqliteSaver` 는 **동기 전용**이며 `aget_tuple`/`aput` 호출 시 `NotImplementedError`("Consider using AsyncSqliteSaver") 를 던지는 것으로 알려져 있다. 따라서:

| 진입점 | 실행 방식 | 사용할 saver |
|---|---|---|
| `main.py` (CLI) | `graph.stream(...)` 동기 | `SqliteSaver` |
| `web.py` (FastAPI) | `graph.astream(...)`·`aget_state(...)` 비동기 | `AsyncSqliteSaver` |

실측으로 확정됨(`langgraph-checkpoint-sqlite==3.1.1`): 동기 saver + `astream`/`aget_state` → `NotImplementedError`, 비동기 saver + 동기 `invoke` → `InvalidStateError`. **양방향 통일이 불가능하므로 위 표대로 분리한다.**

## 설계

### 의존성 (사용자 승인 — §7)

`requirements.in` 에 **`langgraph-checkpoint-sqlite` 1개만** 추가(버전 미지정, 기존 컨벤션). `aiosqlite` 는 이 패키지의 전이 의존이며 코드가 직접 import 하지 않으므로 선언하지 않는다. 이 1개 외 패키지가 필요해지면 리드에 보고.

### 설정

`resources/config_local.ini.example` 에 섹션 추가:

```ini
[checkpoint]
db-path=data/checkpoints.sqlite
```

`src/config/settings.py`:

```python
@dataclass(frozen=True)
class Settings:
    ...기존 12필드 순서 유지...
    checkpoint_db: Path

def load_settings(path) -> Settings
    # checkpoint = parser["checkpoint"]
    # raw = checkpoint["db-path"]
    # checkpoint_db = Path(raw) if Path(raw).is_absolute() else PROJECT_ROOT / raw
    # 섹션/키 없으면 KeyError 그대로 전파(기존 정책)
```

- 상대 경로는 **프로젝트 루트 기준**으로 해석한다(실행 디렉터리에 따라 DB 가 갈리지 않도록).
- `.gitignore` 에 `data/` 추가. 대화 내용이 담긴 DB 는 커밋 대상이 아니다.

### `src/checkpointer.py` (신규)

```python
"""SQLite 체크포인터 생성. 부모 디렉터리를 만든 뒤 컨텍스트 매니저를 그대로 돌려준다."""

def sqlite_saver(path: Path):
    """동기(CLI)용. `with sqlite_saver(p) as saver:` 로 쓴다."""
    # path.parent.mkdir(parents=True, exist_ok=True)
    # return SqliteSaver.from_conn_string(str(path))

def async_sqlite_saver(path: Path):
    """비동기(웹)용. `async with async_sqlite_saver(p) as saver:` 로 쓴다."""
    # path.parent.mkdir(parents=True, exist_ok=True)
    # return AsyncSqliteSaver.from_conn_string(str(path))
```

- 연결 수명은 **진입점의 `with` 블록**이 관리한다(프로세스 종료까지 열려 있음). 별도 종료 처리·풀링 없음.
- 두 진입점이 같은 파일을 동시에 열 수 있다. `[추측]` SQLite 의 파일 잠금으로 직렬화되며 로컬 단일 사용자 규모에서는 문제되지 않는다. WAL 설정은 하지 않는다(범위 제외).

### `src/agent.py`

```python
def build_default_graph(settings: Settings, checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    # checkpointer 가 None 이면 기존대로 InMemorySaver()
```

- **기본값을 유지**해 기존 호출부(`test_integration_agent.py` 등)와 테스트가 그대로 통과하게 한다. `build_graph` 시그니처·그래프 구조·`SYSTEM_PROMPT`·`REWRITE_PROMPT` 는 불변.

### `main.py` (CLI)

- argparse 에 `--thread` 추가(기본 `None` → `uuid4`). 도움말: `이어서 할 대화 ID(생략하면 새 대화)`.
- 기동 시 한 줄 출력: `대화 ID: {thread_id} (이어서 하려면 --thread {thread_id})` — `warn_if_rag_down` 다음, 안내 문구 앞.
- 그래프 생성을 `with sqlite_saver(settings.checkpoint_db) as checkpointer:` 블록 안으로 옮기고 REPL 루프를 그 안에서 돈다.
- `run_turn`·`warn_if_rag_down`·`RUNTIME_ERRORS` 처리·출력 형식은 **불변**(`test_main.py` 무수정).

### `web.py`

```python
def main() -> None:
    # argparse 동일
    settings = load_settings(config_path_for(args.active_profile))

    async def serve() -> None:
        async with async_sqlite_saver(settings.checkpoint_db) as checkpointer:
            app = create_app(build_default_graph(settings, checkpointer), settings)
            await uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port)).serve()

    asyncio.run(serve())
```

- `create_app(graph, settings)` 시그니처 불변.

### 히스토리 API (`src/web/app.py`, `src/web/dto.py`)

저장만 해두고 UI 가 빈 화면을 보여주면 "기억하는데 화면엔 없는" 상태가 된다. 복원용 읽기 엔드포인트 1개를 추가한다.

```python
# dto.py
class ThreadMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str

class ThreadMessagesResponse(BaseModel):
    messages: list[ThreadMessage]

# app.py
@app.get("/v1/threads/{thread_id}/messages", response_model=ThreadMessagesResponse, tags=["대화"])
async def thread_messages(thread_id: str = Path(min_length=1, max_length=64)):
    state = await graph.aget_state({"configurable": {"thread_id": thread_id}})
    return ThreadMessagesResponse(messages=[...])
```

변환 규칙:

| 상태의 메시지 | 결과 |
|---|---|
| `HumanMessage` | `{"role": "user", "content": m.content}` |
| `AIMessage` 이고 `tool_calls` 없고 `content` 비어 있지 않음 | `{"role": "assistant", "content": m.content}` |
| `ToolMessage`, tool_call 전용 `AIMessage`, 빈 content | 제외 |

- 없는 `thread_id` → `{"messages": []}` 200. 65자 이상 → 422. **빈 문자열은 404**(`/v1/threads//messages` 가 라우트에 매칭되지 않음 — 2026-09-28 (7) 로 정정. 거부된다는 성질은 동일하며 422 를 만들려고 별도 라우트를 두지 않는다).
- 도구 호출 기록(`[검색]` 줄)은 복원하지 않는다 — 질문·답변만 되살린다.
- `POST /v1/chat/stream`, SSE 이벤트 계약, `/check` 는 **불변**.

### UI (`resources/static/index.html`)

| 항목 | 내용 |
|---|---|
| thread_id | `let threadId = localStorage.getItem('kudos.threadId') \|\| crypto.randomUUID();` 직후 `localStorage.setItem('kudos.threadId', threadId)` |
| 페이지 로드 | `GET /v1/threads/{threadId}/messages` → `user` 는 기존 질문 말풍선(`addMessage('q', …)`), `assistant` 는 답변 말풍선 + `renderMarkdown` 로 렌더. 실패하면 조용히 넘어간다(빈 화면 = 새 대화와 같음) |
| 새 대화 | 새 uuid 생성 → `localStorage.setItem` → 로그 비움 (기존 동작에 저장 1줄 추가) |
| 그 외 | 스트림 파서·마크다운 렌더러·버튼 잠금·오류 표시 전부 불변 |

### 허용하는 기존 테스트 수정 (설계 변경 직결)

1. `test/test_web_ui.py::test_thread_id_is_generated_in_browser` — `let threadId = crypto.randomUUID()` 정규식이 `localStorage` 조합식과 충돌. **`localStorage.getItem('kudos.threadId')` 폴백 형태를 단언하도록 갱신**하고, `crypto.randomUUID()` 가 최초·새 대화 2곳에 남아 있는 단언은 유지.
2. `test/test_web.py` 의 OpenAPI 경로 목록 — `["/check", "/v1/chat/stream", "/v1/threads/{thread_id}/messages"]` 로 갱신.
3. `test/test_web_ui.py::test_fetch_targets_are_relative_paths` — fetch 대상이 3개가 되므로 집합 동등 비교를 **접두사 + 개수** 방식으로 갱신(2026-09-28 (5), 옵션 B). 단언은 아래 4줄을 모두 유지해 약화하지 않는다:
   ```python
   assert all(url.startswith("/") for url in calls)            # 상대 경로 강제(기존)
   assert {"/check", "/v1/chat/stream"} <= set(calls)          # 기존 2개 필수
   assert any(u.startswith("/v1/threads/") for u in calls)     # 히스토리 복원 경로
   assert len(set(calls)) == 3                                 # 예상 밖 엔드포인트 차단
   ```
   기대 문자열에 `${encodeURIComponent(threadId)}` 같은 보간 표현을 그대로 박아 넣지 않는다(JS 표현 방식에 테스트가 결합되는 것을 피한다).

그 외 기존 테스트는 수정하지 않는다. 수정이 필요해 보이면 ESCALATE.

## 작업 목록

1. **설정·의존성** — `requirements.in` + `pip-compile`(`--upgrade`/`--no-index` 없이) + 설치, `.example`·로컬 `config_local.ini` 에 `[checkpoint]`, `.gitignore` 에 `data/`, `Settings.checkpoint_db` + 경로 해석, `test/conftest.py` `CONFIG_TEXT` 갱신, `test_settings.py` 단언 추가. 검증: S1.
2. **체크포인터 헬퍼** — 위 `[추측]` 제약 실측 보고 후 `src/checkpointer.py`, `build_default_graph(settings, checkpointer=None)`. 검증: S2·S3.
3. **CLI** — `main.py` `--thread`·대화 ID 출력·`with` 배선. 검증: S4.
4. **웹 배선** — `web.py` asyncio + `async_sqlite_saver`. 검증: S5.
5. **히스토리 API** — `dto.py`, `app.py`. 검증: S6.
6. **UI + README** — `index.html`, `test_web_ui.py`, README("대화 유지" 설명: DB 경로, CLI `--thread`, 브라우저 복원, DB 파일 삭제 = 전체 초기화). 검증: S7·S9.

## 검증 전략

**핵심**: 영속성은 가짜 모델(`ScriptedChatModel`)과 임시 SQLite 파일로 **네트워크 없이** 검증할 수 있다. RAG·Ollama 가 필요한 통합 테스트는 늘리지 않는다.

| 항목 | 확인 방법 |
|---|---|
| S1. 설정 | 상대 경로 → `PROJECT_ROOT / 값`, 절대 경로 → 그대로, `[checkpoint]` 없으면 `KeyError`. 기존 12필드 단언 유지 |
| S2. **재시작 후 대화 유지(동기)** | `tmp_path` DB 로 `sqlite_saver` 열고 `build_graph(fake_model, tools, saver)` 로 1턴 실행 → `with` 종료 → **새 saver 인스턴스**로 같은 파일 열고 같은 `thread_id` 로 `graph.get_state(config)` → 이전 Human/AI 메시지가 그대로 있음. 2턴째 실행 시 모델이 받은 입력에 1턴 메시지 포함 |
| S3. 기본값 보존 | `build_default_graph(settings)` (인자 1개)가 여전히 동작하고 체크포인터가 `InMemorySaver`; 인자로 saver 를 주면 그것이 쓰임. 그래프 노드·엣지 불변 |
| S4. CLI | `--thread abc` 파싱 시 `config["configurable"]["thread_id"] == "abc"`, 미지정 시 uuid 형식. 대화 ID 안내 줄 출력. `test_main.py` 의 기존 테스트 무수정 통과 |
| S5. **재시작 후 대화 유지(비동기)** | `TestClient(create_app(graph, settings))` 로 같은 `thread_id` 에 2턴 요청 → 앱·saver 를 **버리고** 같은 DB 파일로 새로 만든 앱에서 3턴째 요청 → 모델이 받은 입력에 1·2턴 메시지 포함. `AsyncSqliteSaver` 가 `astream` 경로에서 동작함을 실행으로 확인 |
| S6. 히스토리 API | 위 상태에서 `GET /v1/threads/{id}/messages` → user/assistant 순서·내용 일치, `ToolMessage`·tool_call 전용 AIMessage 제외. 없는 id → `{"messages": []}` 200. 65자 → 422, 빈 id → 404(2026-09-28 (7) 정정). OpenAPI 경로 3개 |
| S7. UI 계약 | `localStorage.getItem('kudos.threadId')`·`setItem` 존재, 로드 시 `/v1/threads/` 를 상대 경로로 fetch, 응답을 `textContent`/`renderMarkdown` 으로만 삽입(`innerHTML` 계열 0), 새 대화가 uuid 재생성 + `setItem` + 로그 비움. 기존 W6·V1~V3 계약 테스트 전원 통과 |
| S8. 회귀 | `pytest -q` green(기존 144 + 신규). `pytest -m integration -q` **6 passed, failed 0**(RAG 5010 + Ollama 필요 — Validator 가 띄우고 내림). 통합 테스트는 `build_default_graph(settings)` 기본 경로라 `InMemorySaver` 그대로 |
| S9. 수동/육안 (Validator 기록 + 사용자 확인) | **CLI**: `python main.py` 로 1턴 → 종료 → 출력된 ID 로 `python main.py --thread <id>` → 후속 질문이 문맥 유지. **웹**: 서버를 띄워 질문 1회 → **프로세스 종료 후 재기동** → `GET /v1/threads/{id}/messages` 가 이전 질문·답변을 돌려줌. 포트 5020 이 다른 세션 점유 중이면 그 프로세스를 건드리지 말고, `dataclasses.replace(settings, port=5021, checkpoint_db=<scratch>/s9.sqlite)` 로 `web.py` 의 `serve()` 와 같은 구성(`async_sqlite_saver` + `create_app` + `uvicorn.Server`)을 스크래치패드 스크립트로 띄워 동일 절차를 수행한다(프로젝트 파일·`resources/` 무변경). 브라우저 화면 렌더링은 `[미확인]` 로 사용자 육안에 넘긴다 |
| S10. 변경 범위·커밋 | `git diff --stat HEAD` 가 명세 지정 파일 + `docs/plans/agent-09-*.md` 뿐. `data/*.sqlite` 가 `git status` 에 나타나지 않음. 커밋·스테이징 없음 |

## 완료 기준

- [ ] S1~S8, S10 통과. S9 는 Validator 가 CLI·서버 재기동 결과를 기록(브라우저 화면은 `[미확인]` 허용)
- [ ] `requirements.in` 추가는 `langgraph-checkpoint-sqlite` 1개만
- [ ] `build_graph` 시그니처·그래프 구조·SSE 이벤트 계약·프롬프트 불변
- [ ] 기존 테스트 수정은 위 "허용하는 기존 테스트 수정" 3건뿐
- [ ] `data/` 가 `.gitignore` 에 있고 DB 파일이 커밋 대상이 아님
- [ ] 커밋하지 않음(사용자)

## 범위 제외 (근거 포함)

- **thread 만료·정리(agent-10 후보)** — 사용자가 고른 항목에 포함돼 있었으나 분리한다. 근거: ① 저장되는 것은 텍스트뿐이라 디스크 증가가 느림 ② `[미확인]` 설치될 버전에 `delete_thread` 가 있는지 확인되지 않아 별도 실측·검증이 필요하고, 없으면 내부 테이블에 직접 SQL 을 쓰게 되어 스키마 결합 위험이 생김 ③ 이번 티켓이 이미 6개 작업이라 함께 넣으면 검증 단위가 흐려짐. 정리 방법이 필요해지면 "DB 파일 삭제 = 전체 초기화" 를 README 에 적어 임시 대응한다.
- 대화 목록 조회·제목·검색 UI, 여러 대화 전환
- 사용자별 분리·인증 — `thread_id` 를 아는 사람은 그 대화를 읽을 수 있다(로컬/사내망 전제). 접근 통제는 별도 티켓
- Postgres 등 다른 백엔드, WAL·동시성 튜닝, DB 마이그레이션
- 도구 호출(`[검색]` 줄) 복원, `standalone_question` 등 상태 필드의 UI 노출
- 대화 내용 암호화

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| 체크포인터만 SQLite 로 바꾸고 진입점은 그대로 | 진입점이 매번 새 `thread_id` 를 만들어 **사용자에게 효과 0**. 저장은 되지만 아무도 못 꺼냄 |
| 웹도 `localStorage` 만 쓰고 히스토리 API 없음 | 서버는 기억하는데 화면은 비어 있어, 사용자가 문맥을 모른 채 후속 질문하게 됨 |
| 대화 내용을 별도 테이블/JSON 으로 직접 저장 | LangGraph 체크포인터가 이미 하는 일. 이중 저장·정합성 문제 |
| `create_app` 이 내부에서 lifespan 으로 saver 를 열게 | `create_app(graph, settings)` 시그니처·테스트 주입 방식이 깨짐. 진입점 `with` 가 더 단순 |
| `graph.checkpointer` 를 런타임에 교체 | 공개 API 보장 `[미확인]`. 컴파일 시 주입이 정공법 |
| Postgres 체크포인터 | 서버 프로세스 추가 운영 부담. 로컬 PoC 에 과함 |
| CLI 가 마지막 thread 를 파일에 기억해 자동 이어하기 | 의도치 않은 대화 이어붙임 위험. 명시적 `--thread` 가 안전 |

## 리드 판단 기록

- 2026-09-28 (1): 사용자가 ① 선택. 리드 판단으로 **만료·정리를 분리**하고 대신 "대화를 다시 붙잡는 경로"(CLI `--thread`, 웹 localStorage + 히스토리 API)를 포함했다. 근거: 체크포인터 교체만으로는 사용자 체감 효과가 0 이라 티켓 목적을 달성하지 못함.
- 2026-09-28 (2): 동기/비동기 saver 분리는 `[추측]` 단계다. Builder 가 작업 2 착수 전 실측해 보고하고, 결과에 따라 리드가 명세를 갱신한다(통일 가능하면 `web.py` 변경이 줄어든다).
- 2026-09-28 (3): 새 의존성 `langgraph-checkpoint-sqlite` 1개 — 사용자가 "SQLite 영구 체크포인터"를 선택했으므로 §7 허가로 간주. 목록 외는 Builder 가 리드에 보고.
- 2026-09-28 (4): Builder 실측 보고 접수 — `[검증]` `langgraph-checkpoint-sqlite==3.1.1`(전이 `aiosqlite`, `sqlite-vec`)에서 ① 동기 `SqliteSaver`+동기 `stream` 정상 ② 동기 saver+`astream` → `NotImplementedError` ③ 동기 saver+`aget_state` → 동일(히스토리 API 경로도 막힘) ④ `AsyncSqliteSaver`+`astream`·`aget_state` 정상 ⑤ `AsyncSqliteSaver`+동기 `invoke` → `InvalidStateError`. **결정: 명세의 진입점별 saver 분리를 확정(CLI `SqliteSaver`, 웹 `AsyncSqliteSaver`). 설계 변경 없음.** `[추측]` 표기를 `[검증]` 으로 갱신. 부수: `pip-compile` diff 는 신규 3패키지 추가뿐, 기존 핀 변경 0, `pip check` 정상 — 허용 목록(직접 선언 1개) 준수. `sqlite-vec` 은 코드가 직접 import 하지 않으므로 agent-04 원칙상 선언 대상 아님.
- 2026-09-28 (5): Builder ESCALATE — `test_fetch_targets_are_relative_paths` 의 `set(calls) == {"/check", "/v1/chat/stream"}` 가 히스토리 fetch 추가로 반드시 깨짐. **리드 명세 누락 인정**(허용 목록에 있어야 했음, 성격은 ② OpenAPI 경로 목록과 동일). **결정: 옵션 B 채택** — 접두사 확인 + `len(set(calls)) == 3` 으로 갱신(위 3번). 옵션 A(템플릿 리터럴 전문을 기대 문자열에 박기)는 테스트가 JS 보간 표현 방식에 결합돼 이후 표현을 바꾸면 무관한 실패가 나므로 기각. "상대 경로만 사용"·"기존 2개 필수"·"예상 밖 엔드포인트 차단" 세 성질은 모두 유지되므로 단언 약화 아님.
- 2026-09-28 (6): Builder 작업 1~6 완료 보고 — 169 passed(144 +25), 기존 테스트 수정은 허용 3건뿐, `src/agent.py` diff 는 `build_default_graph` 뿐, `src/tools.py`·`test_integration_agent.py` diff 0, `data/` gitignore 확인. 승인 사항 2건: ① `main.py` 를 `build_arg_parser`/`turn_config`/`thread_notice`/`repl` 로 분리 — 테스트 용이성 목적이고 `run_turn`·`warn_if_rag_down`·출력 형식·기존 테스트가 불변이므로 수용. ② 히스토리 테스트에서 `InMemorySaver` 주입 — 체크포인터 없는 그래프는 `aget_state` 가 `ValueError("No checkpointer set")` 를 내고 실제 진입점은 항상 saver 를 가지므로 타당. 부수: 포트 5020·5010 이 다른 세션 점유 중이라 Builder 가 웹 스모크를 생략(S5 단위로 대체) → S9 웹 절차에 5021 우회 경로를 명시(위 표). 리드가 Validator 스폰.
- 2026-09-28 (7): Validator 판정 READY FOR REVIEW 접수(검증 회차 1). S1~S10·완료 기준 전부 PASS — 단위 178 passed / 통합 6 passed / `pip check` 정상. 핵심 확인: ① **민감도 검증** — saver 를 매번 새 `InMemorySaver` 로 치환한 사본에서 S2·S5 가 실제로 실패(3 failed) → 두 테스트가 "새 인스턴스로 같은 파일 열기" 를 검증함이 증명됨. ② S9 실측 — CLI 1턴 후 프로세스 종료, `--thread <id>` 재실행 시 검색어가 앞 턴 엔드포인트로 리라이팅(DB 0B→172KB); 웹은 5020 점유로 5021 우회 경로를 써 SIGTERM → 재기동 후 히스토리 2건 동일, 후속 질문 문맥 유지, 없는 thread → `{"messages": []}`. ③ 기존 테스트 삭제 줄이 정확히 3개이고 전부 허용 목록과 일치. ④ 다른 세션 소유 포트(5010·5020·11434) 무단 조작 없음, 5021 은 Validator 가 띄우고 종료. **결론: READY FOR REVIEW 선언.**
  - **명세 문구 정정 1건**: S6 의 "빈 id → 422" 는 리드 오기. `[검증]` 빈 id 는 라우트 미매칭으로 404 이며, 422 를 만들려면 설계에 없는 별도 라우트가 필요하다. "거부된다" 는 성질은 동일하므로 구현은 그대로 두고 명세 문구만 정정(위 히스토리 API 절·S6 행).
  - 범위 외로 보고된 문서 3건(`agent-01`·`agent-07`·`agent-08` 상태 줄 각 1줄)은 **리드 본인 편집이 맞다**(종결·커밋 완료 기록). 코드 영향 0.
  - 비차단 의견 판단: ① `[추측]` `localStorage.getItem` 이 try 밖이라 접근 차단 환경(`file://`·일부 프라이버시 모드)에서 스크립트가 중단될 수 있음 → **agent-10 후보로 기록**(로컬 서버 접속 전제라 현재 발생 조건 아님, 실측 미수행). ② `restoreHistory()` 미대기로 인한 말풍선 순서 경합 → 창이 매우 짧아 조치 없음. ③ `to_thread_messages` 의 문자열 content 전제 → 현재 Ollama 경로에서 발생하지 않음, 모델 교체 시 재검토. 셋 다 이번 티켓 완료 기준과 무관.
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

### 2026-09-28 Validator 검증 회차 1 — 판정 **READY FOR REVIEW**

실행 환경: `/Users/mjkim/workspace/LangGraph`, `.venv` (Python 3.12.7), `langgraph-checkpoint-sqlite==3.1.1`,
`aiosqlite==0.22.1`, `sqlite-vec==0.1.9`. RAG(5010)·웹(5020)·Ollama(11434) 는 **다른 세션 소유 프로세스**라
기동·종료 없이 확인만 했고(`/check` 200), Validator 는 5021 에만 자기 서버를 띄웠다가 내렸다.

| 항목 | 판정 | 증거 |
|---|---|---|
| S1. 설정 | PASS | `test_settings.py` 8 passed. 상대→`PROJECT_ROOT/data/sub/db.sqlite`, 절대 경로 보존, `[checkpoint]` 삭제 시 `KeyError`. 12필드 단언은 `test_load_settings_reads_all_keys` 전체 비교로 유지 |
| S2. 재시작 후 대화 유지(동기) | PASS | `test_checkpointer.py::test_conversation_survives_new_saver_instance`. **민감도 확인**: `sqlite_saver` 를 매번 새 `InMemorySaver` 로 바꾼 사본은 `KeyError: 'messages'` 로 실패 → 같은 saver 객체 재사용이 아니라 파일 영속성을 실제로 검증함 |
| S3. 기본값 보존 | PASS | `build_default_graph(settings)` → `isinstance(checkpointer, InMemorySaver)`, 주입 시 `is saver`, 노드·엣지 불변. 통합 6건이 1인자 경로를 그대로 통과 |
| S4. CLI | PASS | `--thread abc` → `thread_id == "abc"`, 미지정 → 매번 새 uuid, 안내 줄 문자열 일치. `test_main.py` 기존 테스트 **삭제·수정 0줄**(`git diff HEAD -- test/test_main.py` 에 `-` 라인 없음). `run_turn`·`warn_if_rag_down` 본문 diff 0 |
| S5. 재시작 후 대화 유지(비동기) | PASS | `test_checkpointer.py::test_web_conversation_survives_app_restart` (앱·saver 폐기 후 같은 파일로 재생성). **민감도 확인**: 인메모리 사본은 `IndexError` 로 실패 |
| S6. 히스토리 API | PASS(주 1) | OpenAPI 경로 `['/check', '/v1/chat/stream', '/v1/threads/{thread_id}/messages']`. 없는 id→`{"messages": []}` 200, 64자→200, 65자→**422**(`loc == ["path","thread_id"]`), 빈 id→**404**(주 1). `ToolMessage`·tool_call 전용 AIMessage·빈 content 제외 |
| S7. UI 계약 | PASS | `test_web_ui.py` 전원 통과(기존 W6·V1~V3 포함). `localStorage.getItem/setItem('kudos.threadId')`, `restoreHistory()` 가 `/v1/threads/…/messages` 상대 경로 fetch, 삽입은 `addMessage('q',…)`·`renderMarkdown` 뿐(`innerHTML` 계열 0), 실패 시 조용히 반환 |
| S8. 회귀 | PASS | `pytest -q` → **178 passed**(HEAD 144 + Builder 25 + Validator 9), `pytest -m integration -q` → **6 passed, 0 failed** (143s). `pip check` 정상. 프로젝트에 lint·type check 설정 없음(정적 검증 = pytest) |
| S9. 수동 재기동 | PASS(브라우저 화면은 `[미확인]`) | 아래 "S9 실행 기록" |
| S10. 변경 범위·커밋 | PASS(주 2) | `git check-ignore -v data/checkpoints.sqlite` → `.gitignore:6:data/`. `git status` 에 `data/` 없음. `git diff --cached` 비어 있음(스테이징·커밋 0). 주 2 참조 |

완료 기준: `requirements.in` 추가 1줄(`langgraph-checkpoint-sqlite`) / `build_graph` 시그니처·노드·엣지·프롬프트·SSE 계약 불변
(`src/agent.py` diff 는 `build_default_graph` 뿐) / 기존 테스트 수정은 허용 3건뿐(아래) / `data/` gitignore / 커밋 없음 — 전부 PASS.

기존 테스트에서 삭제된 줄은 정확히 3개이며 모두 허용 목록과 일치한다:
`test_web.py` 의 `sorted(spec["paths"]) == ["/check", "/v1/chat/stream"]`(+함수명 `only_two_paths`→`only_public_paths`),
`test_web_ui.py` 의 `set(calls) == {...}`, `test_web_ui.py` 의 `let threadId = crypto.randomUUID()` 정규식.
`test/conftest.py` 는 `CONFIG_TEXT` 에 `[checkpoint]` 3줄 **추가만**(작업 목록 1 에 명시된 항목, 삭제 0).

#### S9 실행 기록

**CLI** (`data/checkpoints.sqlite` 사용, 파일 삭제 안 함)

1. `printf '메시지 등록 API 알려줘\nexit\n' | .venv/bin/python main.py --active-profile=local` → 종료코드 0.
   첫 줄 `대화 ID: 564621ed-0582-4a09-b2fa-5cc7ff34fe8e (이어서 하려면 --thread 564621ed-…)` 가
   `warn_if_rag_down` 다음·`질문을 입력하세요.` 앞에 출력됨. `[검색] search_openapi(메시지 등록 API)` 후 엔드포인트 6건 + `출처:`.
2. **프로세스 종료 후** `… main.py --active-profile=local --thread 564621ed-…` 로 재실행, 질문 `그 API 의 필수 필드는?`
   → 검색어가 `/v1/messages/message 필수 필드` 로 리라이팅되고 1턴 목록의 필수 필드 5개를 답변.
   **이전 프로세스의 문맥이 SQLite 에서 복원됨을 실행으로 확인.** DB 0B → 172KB.

**웹** (5020 은 다른 세션 점유 → 명세 S9 우회 경로 사용. 스크래치패드 스크립트가 `dataclasses.replace(settings,
port=5021, checkpoint_db=<scratch>/s9.sqlite)` 로 `web.py` 의 `serve()` 와 동일하게 `async_sqlite_saver` +
`create_app` + `uvicorn.Server` 구성. 프로젝트 파일·`resources/` 무변경, 임시 ini 생성 없음)

1. 기동 → `GET /check` 200.
2. `POST /v1/chat/stream {"thread_id":"s9-web-1", …}` → `search` 1건, `token` 491건, `done` 순서 정상.
   `GET /v1/threads/s9-web-1/messages` → user/assistant 2건.
3. **SIGTERM → `/check` 연결 실패(000), 프로세스 소멸 확인.**
4. **재기동 → `GET /v1/threads/s9-web-1/messages` 가 재기동 전 응답과 바이트 단위로 동일**(`diff` 일치).
   없는 thread → `{"messages":[]}` 200.
5. 재기동된 서버에 후속 질문 `그 API 의 필수 필드는?` → 검색어가 `/v1/messages/message 필수 필드` 로 리라이팅,
   히스토리 4건(user/assistant ×2). **재기동 후에도 대화가 이어짐을 실행으로 확인.**
6. 5021 서버 SIGTERM 후 포트 해제 확인. 5010·5020 은 검증 전후 모두 200 으로 무영향.
7. 브라우저 실제 화면 렌더링은 `[미확인]` — 사용자 육안 확인 필요.

#### 주석

- 주 1: S6 의 "빈 id → 422" 는 HTTP 라우팅상 성립 불가하다. `/v1/threads//messages` 는 경로 자체가 매칭되지 않아
  FastAPI 가 **404** 를 준다(422 를 내려면 별도 라우트를 추가해야 하고, 이는 설계에 없다). "빈 id 는 거부된다" 는
  성질은 유지되므로 구현 결함이 아니라 **명세 문구의 사실오차**로 기록한다. 65자→422 는 명세대로 동작하며
  Validator 가 `status_code == 422` + `loc` 까지 확인하는 테스트를 추가했다.
- 주 2: `git diff --stat HEAD` 에 agent-09 대상 외 문서 3건(`agent-01`, `agent-07`, `agent-08` 계획서)의
  상태 줄 수정이 포함돼 있다. 내용은 모두 "종결/커밋 완료" 기록이라 `[추측]` 리드 본인의 정리로 보이며
  코드 영향 0 이다. 리드 확인만 필요하고 판정에는 반영하지 않았다.

#### Validator 가 추가한 테스트 (9건, 모두 append·기존 단언 무변경)

- `test/test_settings.py`: `test_load_settings_missing_db_path_key_raises` (섹션은 있고 키만 없을 때 `KeyError`)
- `test/test_checkpointer.py`: `test_async_saver_creates_parent_directory`(비동기 saver 의 `mkdir` — 기존엔 동기만 검증),
  `test_sqlite_saver_reopens_existing_file`(3회차 재기동까지 대화 유지)
- `test/test_web.py`: `test_thread_messages_excludes_ai_message_that_also_has_tool_calls`(content+tool_calls 동시 보유 — 변환 규칙의 미검증 분기),
  `test_thread_messages_of_empty_state_is_empty_list`, `test_thread_messages_rejects_too_long_thread_id_with_422`(404/422 느슨한 비교 대신 422 확정),
  `test_thread_messages_keeps_question_when_answer_is_missing`
- `test/test_main.py`: `test_active_profile_option_is_unchanged`, `test_main_opens_sqlite_saver_at_configured_path_and_injects_it`
  (진입점 배선 — `settings.checkpoint_db` 로 saver 를 열어 `build_default_graph` 에 주입하고 안내 줄 1개만 출력하는지)

#### 비차단 의견 (판정 미반영)

1. `[추측]` `index.html` 최상단 `localStorage.getItem(...)` 이 try 밖에 있어, `localStorage` 접근이 막힌 환경
   (`file://`·일부 프라이버시 모드)에서는 스크립트 전체가 중단될 수 있다. 실측하지 않음.
2. `[추측]` `restoreHistory()` 는 await 없이 시작되므로, 복원이 끝나기 전에 사용자가 질문을 보내면
   말풍선 순서가 섞일 수 있다(로컬 응답이라 창이 매우 짧음).
3. `[미확인]` `to_thread_messages` 는 `message.content` 가 문자열임을 전제한다. 멀티모달 content(list) 를 주는
   모델로 바꾸면 pydantic 검증에서 깨진다. 현재 Ollama 경로에서는 발생하지 않는다.

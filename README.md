# KUDOS RAG 검색 에이전트 (LangGraph)

기존 KUDOS RAG(Confluence + OpenAPI 하이브리드 검색)를 **도구**로 연결한 LangGraph ReAct 에이전트다.
에이전트가 질문에 따라 어느 소스를 몇 번 검색할지 스스로 결정하고, 출처를 인용해 답한다.

```
main.py (CLI REPL)  |  web.py (FastAPI + 브라우저 UI, SSE)
   │ graph.stream / graph.astream(HumanMessage, thread_id)
   ▼
StateGraph(AgentState)
   rewrite ──► agent ──(tool_calls 있음)──► tools(ToolNode) ──► agent ──(없음)──► END
     │ 후속 질문을 독립 질문으로 다시 씀(첫 턴은 우회)
     │ ChatOllama(qwen3:14b).bind_tools([...])      ├─ search_confluence(query)
     │                                              └─ search_openapi(query)
     │                                                   └─ POST /v1/search (RAG 서버)
   SqliteSaver(CLI) / AsyncSqliteSaver(웹) ─ 멀티턴 기억(data/checkpoints.sqlite, 재시작 후에도 유지)
```

- 답변 생성은 에이전트 LLM 이 한다. RAG 의 `/v1/ask` 는 쓰지 않고 원본 청크만 받아온다.
- 매 턴 시스템 메시지 맨 앞에 **현재 시각**(예: `현재 시각: 2026-09-29 (화) 14:23 KST`)을 넣어 준다.
  날짜·요일 질문에 지어내지 않고 이 값을 그대로 쓴다(서버 로컬 타임존 기준, 대화 기록에는 저장하지 않는다).
- 사내 문서를 외부 API 로 보내지 않는다. LLM 은 로컬 Ollama `qwen3:14b`.

## 빠른 시작

RAG 서버(5010)와 웹 서버(5020)를 한 번에 띄운다. **이미 떠 있는 서비스는 건드리지 않고 건너뛴다.**

```bash
python run.py                  # 기동(중복 실행 방지)
python run.py --status         # 상태만 확인
python run.py --stop           # 이 스크립트가 띄운 것만 종료
```

```
Ollama      : ok (qwen3:14b)
RAG         : 이미 실행 중 http://127.0.0.1:5010 (다른 프로세스가 띄웠을 수 있어 건드리지 않습니다)
LangGraph   : 기동 중... ok (7초)  http://127.0.0.1:5020   로그 logs/web.log

브라우저: http://127.0.0.1:5020
종료: python run.py --stop  (이 스크립트가 띄운 것만 내려갑니다)
```

기동에 실패하면 원인이 함께 나온다.

```
LangGraph   : 기동 실패: 프로세스가 즉시 종료되었습니다 (종료 코드 1). 로그 /…/logs/web.log
  ─ 로그 마지막 15줄 ─
  Traceback (most recent call last):
  ModuleNotFoundError: No module named 'ollama'

종료: python run.py --stop  (이 스크립트가 띄운 것만 내려갑니다)
```

| 인자 | 기본값 | 설명 |
|---|---|---|
| `--active-profile` | `local` | 두 서비스에 같은 값을 넘긴다 |
| `--rag-dir` | `../RAG` | RAG 저장소 경로 |
| `--timeout` | `90` | 기동 후 `/check` 200 을 기다리는 최대 초(RAG 인덱스 로드에 시간이 걸린다) |

- **어느 파이썬으로 실행해도 된다.** `python3 run.py` 로 실행해도 웹 서버는 프로젝트 `.venv/bin/python`(없으면 현재
  인터프리터)으로 띄우고, RAG 는 `<rag-dir>/.venv/bin/python` 으로 띄운다.
- 실행 중 판정은 포트 점유가 아니라 **`/check` 200** 이다. 다른 사람·다른 터미널이 띄운 서버는 그대로 둔다.
- **기동한 프로세스가 곧바로 죽으면 `--timeout` 을 기다리지 않고 즉시 실패**하고, 종료 코드와 함께 이번 실행에서 쌓인
  로그 마지막 15줄을 화면에 보여준다(이전 실행의 오류는 섞이지 않는다). 기동에 실패하면 `브라우저:` 줄을 출력하지 않는다.
- `--stop` 은 **이 스크립트가 만든 PID 파일 + 프로세스 명령줄 확인**을 모두 통과한 프로세스에만 `SIGTERM` 을 보낸다.
  강제 종료(`SIGKILL`)는 하지 않는다. PID 파일이 없으면 "다른 프로세스가 사용 중" 으로 알리고 아무것도 하지 않는다.
- **Ollama 는 확인만 한다.** 없으면 `ollama serve`(또는 `brew services start ollama`), 모델이 없으면
  `ollama pull qwen3:14b` 안내만 출력하고 계속 진행한다. 보통 시스템 데몬으로 떠 있어 중복 기동이 더 위험하다.
- 로그는 `logs/rag.log`, `logs/web.log`, PID 파일은 `logs/*.pid` 에 쌓인다(`logs/` 는 `.gitignore` 대상).
- 기동에 실패해도 이미 성공한 서비스는 내리지 않는다(자동 롤백 없음). 로그 경로를 보고 원인을 확인한다.

사전 준비(설정 파일·의존성 설치·Ollama 모델)는 아래 "사전 준비" 를 먼저 한 번 마쳐야 한다.

## 사전 준비

1. **RAG 서버 기동** (`/Users/mjkim/workspace/RAG`)

   ```bash
   cd ../RAG && python main.py --active-profile=local     # http://127.0.0.1:5010
   ```

   `POST /v1/search` 엔드포인트가 필요하다. `curl http://127.0.0.1:5010/check` 가 200 이어야 한다.

2. **Ollama**

   ```bash
   ollama serve
   ollama pull qwen3:14b
   ```

3. **설정 파일**

   ```bash
   cp resources/config_local.ini.example resources/config_local.ini
   ```

   `config_local.ini` 는 커밋 대상이 아니다(`.gitignore`).

   | 섹션 | 키 | 설명 |
   |---|---|---|
   | `[rag]` | `base-url`, `search-path`, `timeout`, `top-k` | RAG 검색 엔드포인트와 한 번에 받을 청크 수 |
   | `[ollama]` | `base-url`, `llm-model`, `num-ctx`, `temperature`, `llm-timeout` | 에이전트 LLM |
   | `[agent]` | `recursion-limit` | 한 턴에서 허용할 그래프 스텝 수(도구 호출 루프 방지) |
   | `[fastapi]` | `host`, `port` | 웹 서버 바인드 주소·포트. 팀원에게 공개하려면 `host=0.0.0.0` |
   | `[api-services]` | `<서비스 이름>=<QA base URL>` | `call_api` 가 호출할 수 있는 서비스 허용 목록 |
   | `[api]` | `timeout`, `max-response-chars` | 실호출 타임아웃(초), 응답 본문 절단 길이 |

   **`[api-services]`·`[api]` 섹션도 필수다.** 실호출 도구가 추가되면서 생긴 섹션이라,
   그 이전에 복사해 둔 `config_local.ini` 에는 없어 `KeyError: 'api-services'` 로 멈춘다.
   `[checkpoint]` 와 마찬가지로 `.example` 의 해당 섹션을 복사해 넣으면 된다.

   **`[fastapi]` 섹션은 필수다.** 웹 서버가 추가되면서 `host`, `port` 를 읽게 되어, 그 이전에 복사해 둔
   `resources/config_local.ini` 에는 이 섹션이 없다. 그대로 두면 `python main.py` / `python web.py` 가 모두
   `KeyError: 'fastapi'` 로 멈춘다(설정 실수를 숨기지 않는 정책). `.example` 의 `[fastapi]` 섹션을 자기
   `config_local.ini` 에 붙여 넣으면 된다.

4. **의존성 설치**

   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

   의존성은 `requirements.in` 에 적고 `pip-compile requirements.in` 으로 `requirements.txt` 를 갱신한다.

   **의존성 메모**: 아래 3개는 코드가 직접 import 하므로 `requirements.in` 에 명시되어 있다. 원래는
   `langgraph`/`fastapi` 의 전이 의존성으로만 설치되던 것들이라, 상위 패키지 의존성 트리가 바뀌어도 빠지지 않도록
   직접 선언했다: `langgraph-prebuilt`(`src/agent.py` 의 `ToolNode`), `langgraph-checkpoint`(`src/agent.py` 의
   `InMemorySaver`), `pydantic`(`src/web/dto.py`). 새 라이브러리를 직접 import 하게 되면 같은 방식으로
   `requirements.in` 에 추가한 뒤 `pip-compile` 한다.

## 실행 (CLI REPL)

```bash
python main.py --active-profile=local
```

- `> ` 프롬프트에 질문을 입력한다. `exit` / `quit` / Ctrl-D 로 종료한다.
- 검색이 일어나면 `[검색] search_openapi(메시지 등록)` 처럼 한 줄이 먼저 출력되고, 이어서 최종 답변이 나온다.
- 기동 시 `대화 ID: <uuid> (이어서 하려면 --thread <uuid>)` 가 출력된다. 프로세스를 끝낸 뒤
  `python main.py --thread <uuid>` 로 다시 들어가면 그 대화를 이어서 할 수 있다. 생략하면 새 대화다.
- 기동 시 RAG `GET /check` 를 한 번 호출해 실패하면 경고만 출력하고 계속 진행한다.

```
> 메시지 등록 API 호출 방법 알려줘
[검색] search_openapi(메시지 등록 API)
POST /v1/messages ...
출처:
- 메시지 등록 API https://...
> 그 API 필수 필드는?
```

## 웹 서버 (브라우저 UI)

```bash
python web.py --active-profile=local     # http://127.0.0.1:5020
```

- 라우트: `GET /`(채팅 UI), `GET /check`(상태), `POST /v1/chat/stream`(SSE 스트리밍 응답),
  `GET /v1/threads/{thread_id}/messages`(이전 대화 복원용 질문·답변 목록).
- 답변은 토큰 단위로 흘러나오고, 검색이 일어나면 답변 위에 `[검색] search_openapi(메시지 등록)` 줄이 먼저 표시된다.
- 대화는 브라우저가 만든 `thread_id`(uuid) 로 구분하고 `localStorage` 에 보관한다. 새로고침하거나 서버를 재시작해도
  같은 대화를 이어받아, 페이지를 열 때 이전 질문·답변이 화면에 복원된다. `새 대화` 버튼을 누르면 새 uuid 로 바뀌고
  화면이 비워진다(이전 대화는 DB 에 남아 있지만 ID 를 잃으면 다시 찾지 못한다).
- **팀원에게 열어주려면** 자기 `resources/config_local.ini` 의 `[fastapi] host` 를 `0.0.0.0` 으로 바꾸고 다시 기동한다.
  템플릿 기본값은 로컬 전용 `127.0.0.1` 이다. **인증이 없으므로 사내망에서만 열 것.**
- `reload` 옵션은 없다. 코드를 고치면 서버를 수동으로 재시작한다.

SSE 이벤트 계약:

| event | data | 시점 |
|---|---|---|
| `search` | `{"tool": "search_openapi", "query": "메시지 등록 API"}` | 에이전트가 도구를 호출할 때마다 1건 |
| `token` | `"부분 텍스트"` | 최종 답변 토큰. 이어붙이면 최종 답변과 같다 |
| `done` | `""` | 정상 종료. 항상 마지막 |
| `error` | `"ExceptionName: 메시지"` | 그래프 실행 중 Ollama/네트워크 오류. 이후 프레임 없음 |

브라우저 육안 체크리스트:

- [ ] 헤더에 `상태: ok · RAG 연결됨 · Ollama 연결됨` 표시
- [ ] 질문 → `[검색] …` 줄이 먼저, 답변이 토큰 단위로 이어서 표시
- [ ] 마크다운(제목·굵게·목록·코드)이 렌더되어 보이고 `###`/`**` 기호가 노출되지 않음
- [ ] 답변 속 url 이 새 탭 링크로 바뀜
- [ ] 두 번째 질문이 앞 대화 문맥을 유지, `새 대화` 후에는 초기화
- [ ] RAG 서버를 내린 뒤 질문 → 에이전트가 오류를 답변으로 설명하고 정상 종료(빨간 오류 아님)
- [ ] 웹 서버(5020)를 내린 뒤 질문 → 빨간 `요청 실패:` 표시

## API 실호출 (GET 전용)

에이전트가 OpenAPI 스펙을 설명하는 데 그치지 않고, 사용자가 요청하면 **실제로 GET 요청을 보내 응답까지** 보여준다.

```
> 메시지 등록 API 스펙을 실제로 불러와줘
[검색] search_openapi(메시지 등록 API)
[호출] call_api(message-api /openapi.json)
HTTP 200 으로 응답했고 ...
```

- **GET 전용이다.** 도구에 메서드·헤더·바디 인자가 아예 없어서 POST/PUT/PATCH/DELETE 를 보낼 경로가 없다.
- **QA 환경만 호출한다.** 이중 가드다 — ① `[api-services]` 에 등재된 이름만 부를 수 있고,
  ② 그 주소가 `https` + 호스트에 `qa` 라벨 + 경로 없는 순수 origin 이어야 한다. ②는 코드 상수라 설정으로 끌 수 없다.
  운영 주소를 설정에 적으면 **서버 기동 시점에 `ValueError`** 로 실패한다(조용히 넘어가지 않는다).
- 리다이렉트를 따라가지 않는다(`follow_redirects=False`). 30x 는 그대로 결과로 보여준다.
- 4xx·5xx 는 예외가 아니라 상태 코드와 본문을 그대로 돌려줘서 에이전트가 원인을 설명할 수 있다.
- 응답 본문은 `[api] max-response-chars` 로 자르고, 잘리면 원본 길이를 함께 알려준다.
- 에이전트는 **사용자가 요청했을 때만** 호출한다(`SYSTEM_PROMPT` 규칙 4). 일반 질문에는 검색만 한다.
- 인증은 쓰지 않는다(현재 QA API 는 인증 불필요). 운영 호출·쓰기 요청은 이 프로젝트 범위 밖이다.

서비스를 추가하려면 `config_local.ini` 의 `[api-services]` 에 `이름=https://…qa….example.com` 한 줄을 넣고
서버를 다시 띄운다. 항목이 하나도 없으면 실호출 도구 없이 검색 도구 2개만으로 동작한다.

## 대화 유지 (영구 체크포인터)

대화는 SQLite 파일에 저장된다. 경로는 `resources/config_local.ini` 의 `[checkpoint] db-path` 이며,
상대 경로는 **프로젝트 루트 기준**으로 해석한다(실행 디렉터리에 따라 DB 가 갈리지 않도록).

```ini
[checkpoint]
db-path=data/checkpoints.sqlite
```

- CLI 는 동기 `SqliteSaver`, 웹은 비동기 `AsyncSqliteSaver` 를 쓴다. 동기 saver 는 async 경로에서
  `NotImplementedError` 를, 비동기 saver 는 메인 스레드의 동기 호출에서 `InvalidStateError` 를 내므로 섞어 쓸 수 없다.
- 다시 이어가는 방법: CLI 는 `--thread <대화 ID>`, 브라우저는 `localStorage` 에 저장된 ID 로 자동 복원.
- **전체 초기화는 DB 파일 삭제**: `rm data/checkpoints.sqlite`. 오래된 대화만 골라 지우려면 아래 "대화 정리" 참고.
- `data/` 는 `.gitignore` 대상이다. 대화 내용이 들어 있으므로 커밋하지 않는다.
- `thread_id` 를 아는 사람은 그 대화를 읽을 수 있다(인증 없음 — 로컬/사내망 전제).

### 대화 정리

쌓인 대화를 수동으로 정리한다. **기본은 목록만 출력하고 아무것도 지우지 않는다.** `--apply` 를 붙였을 때만 삭제한다.

```bash
python maintenance.py --active-profile=local                        # 14일 이상 미사용 대화 목록만
python maintenance.py --active-profile=local --apply                # 실제 삭제
python maintenance.py --active-profile=local --older-than 30 --apply
python maintenance.py --active-profile=local --apply --vacuum       # 삭제 후 파일 크기 회수
```

```
전체 대화 12개 · 정리 대상 3개 (마지막 활동 14일 경과 기준)
  564621ed-0582-4a09-b2fa-5cc7ff34fe8e  2026-08-11  메시지 8개  메시지 등록 API 알려줘
  s9-web-1                              2026-09-01  메시지 2개  gpts 등록 API 알려줘
--apply 를 붙이면 삭제합니다.
```

| 인자 | 기본값 | 설명 |
|---|---|---|
| `--older-than` | `14` | 마지막 활동 이후 경과 일수. 이 값을 **넘긴** 대화만 대상(정확히 N일은 제외) |
| `--apply` | 없음 | 붙였을 때만 삭제 |
| `--vacuum` | 없음 | `--apply` 와 함께일 때만 `VACUUM` 실행(단독이면 안내 후 무시) |

- **삭제는 되돌릴 수 없다.** 먼저 `--apply` 없이 목록을 확인하고, 필요하면 DB 파일을 복사해 두고 실행한다.
- 서버가 떠 있는 상태에서 대화를 지우면, 그 대화를 보고 있던 브라우저는 새로고침 시 **빈 대화**로 시작한다
  (`thread_id` 는 남아 있지만 기록이 없다). 서버를 내리고 실행할 필요는 없다.
- 새 설정 키는 없다. 보존 기간은 `--older-than` 인자로만 받는다.
- 삭제는 체크포인터의 공개 API(`delete_thread`)로만 하고, DB 테이블을 직접 건드리지 않는다.

## 테스트

```bash
pytest                 # 단위 테스트. 네트워크 없이 동작(pytest.ini 가 integration 을 제외)
pytest -m integration  # RAG 서버 + Ollama 필요. 조건 미충족 시 skip
```

| 파일 | 대상 |
|---|---|
| `test/test_settings.py` | ini 로드, 타입 변환, 파일/키 누락 예외 |
| `test/test_rag_client.py` | `httpx.MockTransport` 로 요청 body·응답 파싱·오류 전파 |
| `test/test_tools.py` | 도구 이름/인자 스키마/설명, source 매핑, `format_chunks` |
| `test/test_agent.py` | 가짜 모델로 그래프 흐름, 도구 오류 처리, 체크포인터 멀티턴 |
| `test/test_main.py` | `run_turn` 출력 형식, RAG 헬스체크 경고 |
| `test/test_integration_agent.py` | 실제 RAG + qwen3:14b 로 도구 호출·출처 인용 확인 |
| `test/test_web.py` | FastAPI 라우트, SSE 이벤트 순서·헤더, 멀티턴, 422 |
| `test/test_checkpointer.py` | SQLite 영속성(재시작 후 대화 유지), `build_default_graph` 기본값 |
| `test/test_maintenance.py` | 대화 열거·만료 선택 경계·삭제·출력 형식(임시 DB) |
| `test/test_api_tool.py` | 운영 차단·GET 전용 계약·입력 방어(요청 미발생)·절단·표시 |
| `test/test_launcher.py` | 중복 실행 방지(spawn 미호출), PID 파일 처리, `--stop` 안전 규칙 |
| `test/test_web_ui.py` | `index.html` 계약(상대 경로, 이벤트 이름, CDN·innerHTML 금지) |
| `test/test_integration_web.py` | 실제 스트리밍으로 `search`/`token`/`done` 확인 |

## 구조

| 경로 | 역할 |
|---|---|
| `main.py` | CLI REPL, 설정 로드 → 조립 → 턴 실행(`run_turn`) |
| `src/config/settings.py` | `Settings` dataclass + `load_settings()` / `config_path_for()` |
| `src/llm_factory.py` | `create_chat_model(settings)` — LLM 생성 단일 지점 |
| `src/rag_client.py` | `RagClient.search()` — `POST /v1/search` 호출 |
| `src/tools.py` | `build_tools(client, top_k)` — `search_confluence`, `search_openapi` |
| `src/api_tool.py` | `validate_base_url`(QA 전용 검사), `build_api_tool` — GET 전용 `call_api` |
| `web.py` | 웹 진입점, `uvicorn.run(create_app(graph, settings))` |
| `src/agent.py` | `SYSTEM_PROMPT`, `build_graph(chat_model, tools, checkpointer)`, `build_default_graph(settings)`, `RUNTIME_ERRORS` |
| `src/agent.py` 의 `rewrite` 노드 | 후속 질문을 앞 대화 없이도 이해되는 독립 질문(`REWRITE_PROMPT`)으로 다시 써 agent 에 힌트로 전달. 첫 턴은 LLM 호출 없이 우회 |
| `src/web/app.py` | `create_app`, `stream_events`(SSE), `check_rag`/`check_ollama` |
| `src/web/dto.py` | `ChatRequest`, `HealthResponse`, `ThreadMessage`, `ThreadMessagesResponse` |
| `src/checkpointer.py` | `sqlite_saver`(CLI·동기), `async_sqlite_saver`(웹·비동기) |
| `maintenance.py` | 대화 정리 진입점(기본 목록만, `--apply` 로 삭제) |
| `run.py` | 기동 스크립트 진입점(`--status`/`--stop`, 중복 실행 방지) |
| `src/launcher.py` | `is_healthy`/`read_pid`/`start`/`stop`/`status` — 기동·종료 판정 |
| `src/maintenance.py` | `collect_threads`, `select_expired`, `purge`, `format_report` |
| `resources/static/index.html` | 단일 파일 채팅 UI(외부 CDN 없음) |

## 범위 밖

쓰기 요청(POST/PUT/PATCH/DELETE)·운영 환경 호출·API 인증, 인증/인가·HTTPS, 자동 정리(기동 시·주기 실행), 웹 UI 에서의 대화 목록·삭제,
대화 내보내기·백업, 사용자별 분리, Ollama 자동 기동·모델 `pull`, 포그라운드 감시 모드·자동 재시작,
Docker/launchd/systemd, `langgraph dev` 노출은 범위에 없다.

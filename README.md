# KUDOS RAG 검색 에이전트 (LangGraph)

기존 KUDOS RAG(Confluence + OpenAPI 하이브리드 검색)를 **도구**로 연결한 LangGraph ReAct 에이전트다.
에이전트가 질문에 따라 어느 소스를 몇 번 검색할지 스스로 결정하고, 출처를 인용해 답한다.

```
main.py (CLI REPL)  |  web.py (FastAPI + 브라우저 UI, SSE)
   │ graph.stream / graph.astream(HumanMessage, thread_id)
   ▼
StateGraph(MessagesState)
   agent ──(tool_calls 있음)──► tools(ToolNode) ──► agent ──(없음)──► END
     │ ChatOllama(qwen3:14b).bind_tools([...])      ├─ search_confluence(query)
     │                                              └─ search_openapi(query)
     │                                                   └─ POST /v1/search (RAG 서버)
   InMemorySaver ─ 멀티턴 기억(프로세스 단위, 종료 시 소실)
```

- 답변 생성은 에이전트 LLM 이 한다. RAG 의 `/v1/ask` 는 쓰지 않고 원본 청크만 받아온다.
- 사내 문서를 외부 API 로 보내지 않는다. LLM 은 로컬 Ollama `qwen3:14b`.

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
- 같은 프로세스 안에서는 대화가 이어진다(`thread_id` 프로세스당 1개).
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

- 라우트: `GET /`(채팅 UI), `GET /check`(상태), `POST /v1/chat/stream`(SSE 스트리밍 응답).
- 답변은 토큰 단위로 흘러나오고, 검색이 일어나면 답변 위에 `[검색] search_openapi(메시지 등록)` 줄이 먼저 표시된다.
- 대화는 브라우저가 만든 `thread_id`(uuid) 로 구분한다. `새 대화` 버튼을 누르면 새 uuid 로 바뀌고 화면이 비워진다.
  서버는 대화를 메모리(`InMemorySaver`)에만 두므로 서버를 재시작하면 모든 대화가 사라진다.
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
| `web.py` | 웹 진입점, `uvicorn.run(create_app(graph, settings))` |
| `src/agent.py` | `SYSTEM_PROMPT`, `build_graph(chat_model, tools, checkpointer)`, `build_default_graph(settings)`, `RUNTIME_ERRORS` |
| `src/web/app.py` | `create_app`, `stream_events`(SSE), `check_rag`/`check_ollama` |
| `src/web/dto.py` | `ChatRequest`, `HealthResponse` |
| `resources/static/index.html` | 단일 파일 채팅 UI(외부 CDN 없음) |

## 범위 밖

사내 API 실호출, 인증/인가·HTTPS, 마크다운 렌더링, 영구 체크포인터(프로세스 종료 시 대화 소실),
`langgraph dev` 노출은 범위에 없다.

# KUDOS RAG 검색 에이전트 (LangGraph)

기존 KUDOS RAG(Confluence + OpenAPI 하이브리드 검색)를 **도구**로 연결한 LangGraph ReAct 에이전트다.
에이전트가 질문에 따라 어느 소스를 몇 번 검색할지 스스로 결정하고, 출처를 인용해 답한다.

```
main.py (CLI REPL)
   │ graph.stream(HumanMessage, thread_id)
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

4. **의존성 설치**

   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```

   의존성은 `requirements.in` 에 적고 `pip-compile requirements.in` 으로 `requirements.txt` 를 갱신한다.

## 실행

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

## 구조

| 경로 | 역할 |
|---|---|
| `main.py` | CLI REPL, 설정 로드 → 조립 → 턴 실행(`run_turn`) |
| `src/config/settings.py` | `Settings` dataclass + `load_settings()` / `config_path_for()` |
| `src/llm_factory.py` | `create_chat_model(settings)` — LLM 생성 단일 지점 |
| `src/rag_client.py` | `RagClient.search()` — `POST /v1/search` 호출 |
| `src/tools.py` | `build_tools(client, top_k)` — `search_confluence`, `search_openapi` |
| `src/agent.py` | `SYSTEM_PROMPT`, `build_graph(chat_model, tools, checkpointer)` |

## 범위 밖

사내 API 실호출, FastAPI/`langgraph dev` 노출, 토큰 스트리밍, 영구 체크포인터(프로세스 종료 시 대화 소실)는 이번 범위에 없다.

# agent-13: 사내 API 실호출 도구 (GET 전용)

- 작성일: 2026-09-29
- 작성자: 리드
- 상태: 종결 — 사용자 커밋·push 완료 (2026-09-29)
- 사용자 확정(2026-09-29): **인증 없음**, **GET 으로 먼저 시작**, **운영 주소 영구 금지**.
- 저장소: `/Users/mjkim/workspace/LangGraph` 만. RAG 저장소는 읽기만 하고 변경하지 않는다.

## 목표

에이전트가 OpenAPI 스펙을 **설명만** 하던 데서, 사용자가 요청하면 **실제로 GET 요청을 보내 응답까지 보여주는** 단계로 넘어간다. 쓰기(POST/PUT/PATCH/DELETE)와 운영 환경 호출은 구조적으로 불가능하게 막는다.

## 확인된 현재 상태 (`[검증]`)

- RAG `resources/openapi_sources.yaml` 에 인덱싱된 대상은 **QA 2개뿐**:
  - `general-chatbot-api` → `https://qa-general-chatbot-api.hunet.ai`
  - `message-api` → `https://message-api.qa.hunet.io`
- `src/tools.py`: `build_tools(client, top_k) -> [search_confluence, search_openapi]`. 도구 이름·`query: str` 인자·docstring 을 `test/test_tools.py` 가 완전 일치로 고정.
- `src/agent.py`: `SYSTEM_PROMPT` 규칙 7개(agent-06 규칙 3 포함), `build_graph(chat_model, tools, checkpointer)`, `build_default_graph(settings, checkpointer=None)` 가 `build_tools(...)` 결과를 그래프에 넘긴다.
- `src/web/app.py` `stream_events`: tool_call 을 `("search", {"tool": name, "query": args.get("query", "")})` 로 내보낸다. → **`query` 인자가 없는 도구는 빈 문자열**이 된다.
- `resources/static/index.html`: `search` 이벤트를 받아 `[검색] {tool}({query})` 로 그린다.
- `main.py` `run_turn`: `[검색] {name}({args.get('query','')})` 출력.
- `Settings` 13필드, `load_settings` 는 `[rag] [ollama] [agent] [fastapi] [checkpoint]` 를 읽고 **키가 없으면 KeyError 를 그대로 전파**한다.
- 웹 서버는 `127.0.0.1` 바인드(로컬 전용). `[검증]` 따라서 이 도구를 쓸 수 있는 사람은 현재 이 맥 사용자뿐이다.

## 설계

### 안전 원칙 (이 티켓의 핵심)

| 위험 | 차단 방법 |
|---|---|
| 데이터 변경·삭제 | **메서드 인자를 아예 두지 않는다.** 도구가 할 수 있는 건 GET 뿐이라 POST 를 부를 경로가 없다 |
| 운영 환경 호출 | 이중 가드 — ① 설정 `[api-services]` 에 등재된 이름만 ② 그 URL 이 **QA 표식과 https 검사**를 통과해야 함. 둘 다 통과해야 호출 |
| 리다이렉트로 운영 이동 | `follow_redirects=False`. 30x 는 그대로 결과로 돌려준다 |
| 임의 호스트 호출(SSRF 유사) | `path` 는 `/` 로 시작하는 경로만. 절대 URL·프로토콜 상대 경로(`//host`) 거부 |
| 에이전트의 무단 호출 | `SYSTEM_PROMPT` 에 "사용자가 요청했을 때만 호출" 규칙 추가 |
| 거대 응답 | `max-response-chars` 로 절단 |

**운영 주소 영구 금지 구현**: 설정에서 읽은 base URL 이 아래를 모두 만족해야 한다. 하나라도 어기면 **도구 생성 시점(=서버 기동 시점)에 `ValueError`** 로 즉시 실패한다(런타임에 조용히 넘어가지 않는다).

1. 스킴이 `https`
2. 호스트 라벨에 QA 표식이 있을 것 — 정규식 `(^|[.-])qa([.-]|$)` 가 호스트에 매치
3. 경로·쿼리·프래그먼트가 없을 것(순수 origin)

`[검증]` 현재 두 호스트 모두 통과한다: `qa-general-chatbot-api.hunet.ai`(`qa-` 접두), `message-api.qa.hunet.io`(`.qa.` 라벨). 운영 도메인을 설정에 적어도 2번에서 걸린다. **이 검사는 설정으로 끌 수 없다**(상수로 코드에 둔다).

### 설정

`resources/config_local.ini.example` 에 두 섹션 추가:

```ini
[api-services]
general-chatbot-api=https://qa-general-chatbot-api.hunet.ai
message-api=https://message-api.qa.hunet.io

[api]
timeout=20
max-response-chars=2000
```

`src/config/settings.py`:

```python
@dataclass(frozen=True)
class Settings:
    ...기존 13필드 순서 유지...
    api_services: dict[str, str]     # 서비스 이름 → base URL
    api_timeout: float
    api_max_chars: int

# load_settings: api_services = dict(parser["api-services"]),
#                api_timeout = float(parser["api"]["timeout"]),
#                api_max_chars = int(parser["api"]["max-response-chars"])
# 섹션/키가 없으면 KeyError 그대로 전파(기존 정책)
```

- `[api-services]` 가 비어 있으면(항목 0개) 도구를 만들지 않는다 → 기존과 동일하게 검색 도구 2개만 있는 에이전트로 동작. 설정 실수로 서버가 못 뜨는 일은 피하되, **섹션 자체가 없으면** KeyError(기존 정책 유지).
- **구 설정 파일 마이그레이션 필요** — agent-05 의 `[fastapi]` 사례와 동일. README 안내 문구를 함께 갱신한다.

### `src/api_tool.py` (신규)

```python
ALLOWED_SCHEME = "https"
QA_HOST_PATTERN = re.compile(r"(^|[.-])qa([.-]|$)")     # 운영 차단 — 설정으로 끌 수 없다

def validate_base_url(name: str, base_url: str) -> str:
    """스킴·QA 표식·순수 origin 검사. 위반 시 ValueError(이유 포함)."""

def build_api_tool(services: dict[str, str], timeout: float, max_chars: int,
                   transport: httpx.BaseTransport | None = None) -> BaseTool:
    """GET 전용 call_api 도구. services 가 비면 None 이 아니라 호출 금지 도구를 만들지 않는다
    (호출부에서 빈 dict 면 아예 부르지 않는다)."""
```

도구 시그니처:

```python
@tool
def call_api(service: str, path: str) -> str:
    """사내 API 를 실제로 호출해 응답을 가져온다. GET 전용이라 데이터가 바뀌지 않는다.
    service 는 다음 중 하나: {서비스 이름 목록}
    path 는 '/' 로 시작하는 경로이며 쿼리스트링을 포함할 수 있다.
    먼저 search_openapi 로 경로와 파라미터를 확인한 뒤 호출한다."""
```

- 서비스 이름 목록은 설정에서 **동적으로 생성**해 docstring 에 넣는다(모델이 유효한 이름을 알아야 한다). agent-07 에서 문제였던 "특정 API 이름 예시"와 달리, 이건 선택지 열거이므로 필요하다.
- 도구 이름 `call_api`, 인자 이름 `service`/`path` 는 고정.

동작 표:

| 상황 | 반환 |
|---|---|
| 정상 | `HTTP {status} GET {url}\n{본문}` (본문은 `max_chars` 로 절단, 잘리면 `… (본문이 잘렸습니다. 전체 {n}자)`) |
| 4xx / 5xx | **예외 아님.** 위와 같은 형식으로 상태코드와 본문을 그대로 반환 → 에이전트가 원인을 설명할 수 있다 |
| 30x | 같은 형식으로 반환(`follow_redirects=False`). 자동 추적하지 않는다 |
| 미등록 `service` | `사용할 수 없는 service 입니다: {입력}. 가능한 값: {목록}` — 문자열 반환(예외 아님). 모델이 고쳐서 재시도 가능 |
| `path` 가 `/` 로 시작하지 않음 / `//` 로 시작 / 절대 URL | `path 는 '/' 로 시작하는 경로여야 합니다: {입력}` — 문자열 반환 |
| 연결 실패·타임아웃 | `httpx` 예외를 **그대로 던진다**. 기존 검색 도구와 동일하게 `ToolNode(handle_tool_errors=True)` 가 `ToolMessage(status="error")` 로 바꾼다 |

### `src/agent.py`

- `build_default_graph` 가 도구 목록을 합친다:
  ```python
  tools = build_tools(client, settings.rag_top_k)
  if settings.api_services:
      tools = tools + [build_api_tool(settings.api_services, settings.api_timeout, settings.api_max_chars)]
  ```
  **`build_tools` 시그니처는 바꾸지 않는다**(agent-01 계약 유지, 기존 테스트 무변경).
- `SYSTEM_PROMPT` 에 규칙 1개 삽입(기존 규칙 3 뒤, 이후 번호 +1 → 총 8개):
  ```
  4. 사용자가 실제 호출·시험·응답 확인을 요청하면 call_api 로 GET 요청을 보냅니다.
     먼저 search_openapi 로 경로와 필수 파라미터를 확인한 뒤 호출합니다.
     요청하지 않았는데 임의로 호출하지 않습니다.
  ```
  기존 규칙 문구는 바꾸지 않고 번호만 민다. **agent-06 규칙 3(후속 질문 독립 검색어)과 agent-08 리라이팅은 건드리지 않는다.**

### 웹·CLI 표시

`query` 인자가 없는 도구가 생겼으므로 표시 문자열 생성 규칙만 손본다. **SSE 이벤트 이름·필드는 그대로**(`search`, `{tool, query}`).

- `src/web/app.py` `stream_events`: `query` 값을 `args.get("query")` 가 없으면 **인자 값들을 공백으로 이어 붙여** 만든다(예: `message-api /v1/messages/message`).
- `resources/static/index.html`: `data.tool === "call_api"` 면 라벨을 `[호출]`, 그 외 `[검색]`.
- `main.py` `run_turn`: 같은 규칙으로 `[호출]` / `[검색]` 분기.

### 허용하는 기존 테스트 수정 (설계 변경 직결)

1. `SYSTEM_PROMPT` 규칙 번호를 단언하는 테스트가 있으면 새 번호로 갱신. **규칙 문구 자체를 바꾸는 수정은 금지.**
2. `stream_events` 의 `query` 생성 규칙을 단언하는 테스트가 있으면 갱신(기존 `query` 인자 도구의 동작은 **불변**이어야 하므로, 기존 단언은 그대로 통과해야 정상).
3. `main.py`·`index.html` 의 `[검색]` 라벨을 고정한 테스트는 `[호출]` 분기 추가에 맞춰 갱신. `[검색]` 동작은 불변.

그 외 기존 테스트 수정이 필요해 보이면 ESCALATE.

## 작업 목록

1. **설정** — `[api-services]`·`[api]` 추가(`.example` + 로컬 ini), `Settings` 3필드, `test/conftest.py` `CONFIG_TEXT`, `test_settings.py`. 검증: C1.
2. **도구** — `src/api_tool.py`(`validate_base_url`, `build_api_tool`) + `test/test_api_tool.py`. 검증: C2~C8.
3. **그래프 결합** — `build_default_graph` 도구 합치기, `SYSTEM_PROMPT` 규칙 삽입. 검증: C9.
4. **표시** — `stream_events` query 생성, `index.html` `[호출]` 라벨, `main.py` 분기. 검증: C10.
5. **통합 + README** — `test/test_integration_api_tool.py`, README "API 실호출" 절(GET 전용·QA 전용·운영 금지·설정 마이그레이션). 검증: C11·C12.

## 검증 전략

단위 테스트는 `httpx.MockTransport` 로 네트워크 없이 수행한다(기존 `RagClient` 테스트와 같은 방식).

| 항목 | 확인 방법 |
|---|---|
| C1. 설정 | `api_services` 가 2개 항목 dict, `api_timeout`/`api_max_chars` 타입 변환. `[api-services]` 섹션 없음 → `KeyError`. 항목 0개 → 빈 dict(예외 아님). 기존 13필드 단언 유지 |
| C2. **운영 차단** | `validate_base_url` 이 거부: `http://...`(스킴), `https://message-api.hunet.io`(QA 표식 없음), `https://prod.hunet.ai`, `https://qa.x.com/sub`(경로 포함). 통과: 실제 두 QA URL. 거부는 **`ValueError`** 이며 메시지에 이유 포함 |
| C3. 기동 시 실패 | 운영 URL 이 든 설정으로 `build_api_tool` 호출 → `ValueError`. **런타임 문자열 반환이 아니라 예외**임을 단언(서버가 뜨지 않아야 한다) |
| C4. 도구 계약 | 이름 `call_api`, `tool.args` 가 정확히 `{service, path}`, description 에 서비스 이름 2개가 모두 포함, `GET` 전용임이 명시. 메서드·헤더·바디 인자가 **없음** |
| C5. 정상 호출 | MockTransport 200 + JSON 본문 → 반환 문자열에 `HTTP 200`, 전체 URL, 본문 포함. 요청이 **GET** 이고 URL 이 `base_url + path` 임을 transport 에서 확인 |
| C6. 오류 응답 | 404·422·500 → 예외 없이 상태코드와 본문이 담긴 문자열. 30x → 그대로 반환되고 **추적하지 않음**(transport 호출 1회) |
| C7. 입력 방어 | 미등록 service → 안내 문자열에 가능한 값 목록 포함, **요청 미발생**(transport 호출 0회). `path` 가 `messages`(슬래시 없음) / `//evil.com/x` / `https://evil.com/x` → 안내 문자열, 요청 미발생 |
| C8. 절단·예외 전파 | 본문이 `max_chars` 초과 → 잘리고 안내 문구 포함, 원본 길이 표시. `httpx.ConnectError`·`ReadTimeout` 은 **그대로 전파**(도구가 삼키지 않음) |
| C9. 그래프·프롬프트 | `build_default_graph(settings)` 의 도구가 3개이고 이름이 `[search_confluence, search_openapi, call_api]`. `api_services` 가 빈 dict 면 2개. `SYSTEM_PROMPT` 에 새 규칙 문구 포함, agent-06 규칙("독립 검색어")과 기존 규칙 문구 전부 보존 |
| C10. 표시 | `stream_events`: `query` 인자 도구는 **기존과 동일**, `call_api` 는 `"message-api /v1/..."` 형태. `index.html` 에 `call_api` → `[호출]` 분기와 기타 `[검색]` 유지(정규식). `main.py` 동일 |
| C11. 통합 (`-m integration`) | **실제 QA API 에 GET 1건.** 부작용 없는 엔드포인트(`/openapi.json` 또는 헬스체크)만 호출한다. skip 조건: 해당 호스트에 연결되지 않으면 skip(사내망·VPN 필요 가능성 `[미확인]`). 반환에 `HTTP 200` 포함 확인. **쓰기 요청은 어떤 테스트에서도 만들지 않는다** |
| C12. 회귀·범위 | `pytest -q` green(HEAD 311 + 신규). 기존 통합 6건 회귀 없음. `git diff --stat HEAD` 가 명세 지정 파일뿐. `src/tools.py`·`src/rag_client.py`·`src/checkpointer.py`·`src/launcher.py`·`maintenance.py`·`run.py` diff 0 |
| C13. 수동 확인 (Validator 기록 + 사용자) | 웹에서 "메시지 등록 API 스펙 좀 실제로 불러와줘" 류 요청 → `[호출]` 줄이 뜨고 응답이 답변에 반영되는지. 요청하지 않은 일반 질문에서는 `call_api` 가 호출되지 않는지(무단 호출 방지) 1건 확인 |

## 완료 기준

- [ ] C1~C12 통과, C13 기록
- [ ] **메서드 인자가 없어 GET 외 요청이 구조적으로 불가능**함이 테스트로 고정(C4)
- [ ] 운영/비-QA URL 은 설정에 적어도 기동 시 `ValueError`(C2·C3), 이 검사는 설정으로 끌 수 없음
- [ ] 미등록 service·잘못된 path 에서 **네트워크 요청이 발생하지 않음**(C7)
- [ ] 기존 검색 도구·SSE 이벤트 계약·`build_tools` 시그니처 불변
- [ ] 기존 테스트 수정은 위 허용 3종뿐
- [ ] 새 의존성 0 (`httpx` 재사용)
- [ ] 커밋하지 않음(사용자)

## 범위 제외

- **POST/PUT/PATCH/DELETE** — 사용자가 GET 먼저로 확정. 쓰기를 붙일 때는 "호출 전 사용자 승인" UI 가 함께 필요(별도 티켓)
- **인증** — 현재 QA API 는 인증 불필요(사용자 확인). 토큰이 필요해지면 환경변수 방식으로 별도 티켓
- **운영 환경 호출** — 영구 금지(사용자 확정). 필요해지면 코드 상수를 고치는 별도 티켓으로만
- 응답 캐시, 재시도, 파일 다운로드·바이너리 처리, 스트리밍 응답
- 호출 이력 저장·감사 로그
- 접근 통제(웹을 `0.0.0.0` 으로 열 때 필요 — 별도 티켓). 이 티켓은 `127.0.0.1` 전제

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| `call_api(method, path, body)` 로 만들고 메서드를 GET 으로 검증 | 검증 한 줄만 뚫리면 쓰기가 나간다. **인자를 아예 안 두는 쪽**이 사고 가능성이 0 이다 |
| 허용 목록만으로 운영 차단 | 설정 파일 한 줄 실수로 운영 주소가 들어갈 수 있다. 코드 상수의 QA 표식 검사를 이중으로 둔다 |
| RAG 의 `openapi_sources.yaml` 을 직접 읽어 목록 공유 | 저장소 간 결합. agent-01 에서 HTTP 경계로 분리한 결정과 어긋난다. 항목 2개라 중복 비용이 작다 |
| 호출 전 사용자 승인 UI | GET 전용·QA 전용이라 되돌릴 게 없다. 쓰기를 허용할 때 함께 도입 |
| 새 SSE 이벤트 `call` 추가 | UI·테스트·계약이 모두 바뀐다. `search` 이벤트에 도구 이름이 이미 실려 있으므로 UI 라벨만 분기하면 된다 |
| 응답 전문을 그대로 반환 | 대용량 응답이 컨텍스트를 날린다. 절단 + 원본 길이 표시로 충분 |

## 리드 판단 기록

- 2026-09-29 (1): 사용자 확정 3건 — 인증 없음 / GET 먼저 / 운영 주소 영구 금지. 세 번째를 **코드 상수의 QA 표식 검사 + 설정 허용 목록 이중 가드**로 구현하고, 설정으로 끌 수 없게 한다.
- 2026-09-29 (2): GET 전용을 "메서드 인자를 두지 않는 것"으로 구현한다. 검증 로직으로 막는 방식보다 사고 가능성이 낮다.
- 2026-09-29 (3): `build_tools` 시그니처를 건드리지 않고 `build_default_graph` 에서 도구를 합친다. agent-01 이 고정한 도구 계약과 기존 테스트를 보존하기 위함.
- 2026-09-29 (4): 새 설정 섹션 2개는 구 `config_local.ini` 에서 `KeyError` 를 일으킨다(agent-02 `[fastapi]` 와 같은 상황). README 마이그레이션 안내를 작업 5 에 포함한다.
- 2026-09-29 (5): Builder 작업 1~5 완료 보고 — 363 passed, **기존 테스트 수정 0건**(허용 3종이 모두 불필요했음 = 기존 단언이 그대로 통과), 런타임 코드 중 무관 파일 diff 0. C2~C8·C10 전부 Builder 실행 확인. **설계 판단 승인**: `tool_call_summary`·`API_TOOL_NAME` 을 `src/web/app.py` 가 아니라 `src/agent.py` 에 둔 것 — `main.py`(CLI)가 웹 모듈을 import 하는 역방향 의존을 피하려는 것으로, agent-02 에서 `RUNTIME_ERRORS`·`build_default_graph` 를 옮긴 것과 같은 근거다. 위치 그대로 유지한다. 리드가 Validator 스폰.
- 2026-09-29 (6): Validator 판정 READY FOR REVIEW 접수(검증 회차 1). C1~C13·완료 기준 전부 PASS. 단위 392 passed / 통합 8 passed. 핵심 확인:
  - **GET 구조적 보장**: HTTP 호출 지점이 `client.get(url)` 한 곳뿐(`.post/.put/.patch/.delete/.request/.stream/.send` grep 0). `method="POST"`·`json`·`headers` 를 invoke 에 끼워 넣어도 pydantic 스키마가 무시해 **실제로 GET·빈 본문으로 나감**을 실행으로 확인.
  - **운영 차단 민감도**: `QA_HOST_PATTERN` 무력화 사본에서 **7건 실패**. 설정으로 끌 수 있는 분기가 코드에 없음을 사용처 전수 확인으로 검증. userinfo 우회(`https://qa.example.com@evil.com`)도 거부.
  - **요청 미발생 민감도**: path 가드 제거 시 6건, service 가드 제거 시 1건 실패. 경로 트릭 7종(`/\/evil.com`, `/%2F%2Fevil.com`, `/@evil.com/x` 등)에서 요청 호스트가 등재 QA 호스트뿐임을 확인.
  - **C11 실호출**: 두 QA 호스트 `/openapi.json` GET → 각각 **HTTP 200**. `[검증]` VPN 없이 접속됨. 쓰기 요청 0건.
  - **C13 수동**: 5021 격리 기동. ① "실제로 호출해줘" → `call_api` 1건 실행, 답변에 `HTTP 200`·`Message Management API` 반영. ② 일반 질문에서는 `call_api` 미호출(무단 호출 방지 확인) + `query` 인자 도구 표시가 기존과 동일.
  - **계약 보존**: `src/tools.py` diff 0, SSE 이벤트·필드 불변, `SYSTEM_PROMPT` 변경이 규칙 4 삽입 + 번호 3개뿐임을 word-diff 로 확인(agent-06 규칙 3·`REWRITE_PROMPT` 무변경). 기존 테스트 삭제 줄 0.
  - 사용자 서버 5010·5020 PID·응답 검증 전후 동일, Validator 가 띄운 5021 은 종료 확인.
  **결론: READY FOR REVIEW 선언.**
  - Validator 보고 사항: 도구 계약 런타임 확인 중 `GET https://message-api.qa.hunet.io/x` 1건이 의도치 않게 실제 전송됨(404). GET·부작용 없음이며 "추가 인자를 줘도 GET 으로 나간다"는 증거가 됐다. 허용 범위 내로 판단하되, 이후 검증에서는 실호출 대상을 사전에 고정할 것.
  - 비차단 의견 판단: ① `Settings` 에 dict 필드가 생겨 `hash(settings)` 불가 — `[검증]` 코드에 `hash(`·`lru_cache` 사용처 없음. 검증된 사본을 클로저에 잡으므로 생성 후 dict 변조도 호출 대상을 바꾸지 못함. 조치 없음. ② `path` 에 비출력 ASCII 가 들어가면 `httpx.InvalidURL` — `ToolNode(handle_tool_errors=True)` 가 잡으므로 영향 없음. ③ `agent-12` 문서 상태 줄은 리드 편집. ④ `test_qa_check_is_a_module_constant_not_config` 의 간접 단언은 보강 테스트가 실질을 덮으므로 조치 없음.
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

### 2026-09-29 Validator 검증 1회차 — READY FOR REVIEW

- 스위트: `.venv/bin/python -m pytest -q` → **392 passed, 8 deselected**(Validator 보강 테스트 29건 추가 전 363 passed).
  HEAD(`d7a29da`) 스냅샷 기준선은 311 selected / 6 deselected.
- 통합: `pytest -q -m integration -s` → **8 passed** (기존 6건 회귀 없음 + 신규 2건), 162초.
- 정적 검증: 프로젝트에 lint/type 설정 없음(`ruff`/`flake8`/`mypy`/CI 설정 파일 부재). `compileall` 만 수행(OK).

| 항목 | 결과 | 증거 |
|---|---|---|
| C1 설정 | PASS | `test/test_settings.py` 16필드 단언·타입 변환·섹션 누락 `KeyError`·항목 0개 빈 dict. 기존 13필드 단언은 삭제 줄 0(`git diff --numstat` 33/0) |
| C2 운영 차단 | PASS | `test_validate_rejects_non_qa_or_unsafe_urls` 7케이스 + Validator 보강(후행 슬래시·프래그먼트·userinfo·스킴 없음) |
| C3 기동 시 실패 | PASS | `test_build_api_tool_raises_on_production_url`, `test_default_graph_fails_fast_on_production_url` — `ValueError` |
| C4 도구 계약 | PASS | `tool.args == {service, path}`, description 에 서비스 2개·GET 명시. 보강: `method/json/headers` 를 끼워 넣어도 나가는 요청은 `GET` 1건·본문 빈값, 모듈에 `.post(`/`.request(`/`.stream(` 등 없음·`client.get(` 1회 |
| C5 정상 호출 | PASS | `test_call_api_sends_get_to_base_url_and_path` |
| C6 오류·30x | PASS | 404/422/500 문자열 반환, 302 미추적(transport 1회) |
| C7 입력 방어 | PASS | 미등록 service·잘못된 path 6종에서 `transport.requests == []`. 보강: 경로 트릭 7종에서도 요청 호스트가 등재 QA 호스트뿐 |
| C8 절단·예외 | PASS | 절단 문구·원본 길이, 경계값(=max/+1), `ConnectError`·`ReadTimeout` 전파. 보강: 그래프에서 `ToolMessage(status="error")` 로 변환 확인 |
| C9 그래프·프롬프트 | PASS | 도구 3개/2개 분기, `SYSTEM_PROMPT` 신규 규칙 4 + 기존 문구 보존. `git diff --word-diff` 로 규칙 5·6·7 번호만 이동(문구 변경 0), agent-06 규칙 3·`REWRITE_PROMPT` 무변경 |
| C10 표시 | PASS | `stream_events` 가 `query` 인자 도구는 기존 그대로, `call_api` 는 `"message-api /openapi.json"`. `main.py` `[호출]`/`[검색]` 분기, `index.html` 정규식 검사 |
| C11 통합 실호출 | PASS | `GET https://qa-general-chatbot-api.hunet.ai/openapi.json` → **HTTP 200**(본문 2024자), `GET https://message-api.qa.hunet.io/openapi.json` → **HTTP 200**(본문 2025자). 쓰기 요청 0건 |
| C12 회귀·범위 | PASS | 전체 green, 무관 모듈(`src/tools.py`·`src/rag_client.py`·`src/checkpointer.py`·`src/launcher.py`·`maintenance.py`·`run.py`·`web.py`·`requirements*`) diff 0, 기존 테스트 삭제 줄 0 |
| C13 수동 확인 | PASS(기록) | 스크래치 서버 5021(`dataclasses.replace(settings, port=5021, checkpoint_db=<scratch>)`). ① "message-api 의 /openapi.json 을 실제로 호출해서…" → `event: search {"tool":"call_api","query":"message-api /openapi.json"}` 1건, 답변 "HTTP 상태코드는 200이며, 제목은 \"Message Management API\"". ② "메시지 등록 API 의 필수 필드가 뭐야?" → `search_openapi` 만, `call_api` 0회(무단 호출 없음). 검증 후 5021 종료, 5010(PID 4576/4617)·5020(PID 4725) PID·`/check` 변화 없음 |

민감도 확인(프로젝트 파일 무변경, 스크래치 사본에서만 변형):

- `QA_HOST_PATTERN` 무력화 → 7건 실패(C2·C3·C9 계열)
- `path` 가드 제거 → 6건 실패
- 미등록 service 가드 제거 → 1건 실패

Validator 보강 테스트: `test/test_api_tool.py` 에 29건 추가(GET 전용의 소스 수준 보장, 추가 인자로 메서드 변조 불가, 경로 트릭의 호스트 이탈 불가, base URL 경계, 절단 경계, 그래프 오류 변환).

비차단 관찰(판정 무관):

- `Settings` 가 `dict` 필드를 가지며 `hash()` 불가로 바뀌었다. 현재 `hash`/`lru_cache` 사용처가 없어 영향 없음.
- `docs/plans/agent-12-launcher-fixes.md` 상태 줄 1행이 함께 변경돼 있다(agent-13 범위 밖 문서 변경).

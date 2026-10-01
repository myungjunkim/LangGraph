# agent-20: 엔드포인트 집계 도구(R1) + 상위 N건 단정 금지(R2)

- 작성일: 2026-09-30
- 작성자: 리드
- 상태: **종결** — Validator 회차 1 PASS(2026-09-30, 실환경 4/4 "총 9개"). 사용자 승인 아래 커밋·푸시 완료(2026-10-01, `032319c`, agent-21 과 한 커밋)
- 계기: agent-19 진단 결과, 사용자가 **R1+R2 우선 진행**을 승인.
- 선행 문서: `docs/plans/agent-19-api-count-diagnosis.md` (원인 분리 근거 전부 여기에 있다. **먼저 읽을 것.**)
- 저장소: `/Users/mjkim/workspace/LangGraph` 만. `/Users/mjkim/workspace/RAG` 는 건드리지 않는다.

## 목표

"이 API 서비스에 엔드포인트가 총 몇 개냐" 는 질문에 **정확한 숫자**로 답한다.
숫자는 **코드가 센다.** LLM 은 센 결과를 전달만 한다.

## 설계 근거 (agent-19 실측)

이 설계는 아래 세 가지 실측에서 나왔다. 임의로 바꾸면 안 된다.

1. `[검증]` 검색은 `top-k=6` 이라 9개짜리 서비스도 셀 수 없다. 서버가 k 를 20으로 캡하고, 20 에서도 7/9 다. **검색으로는 못 푼다.**
2. `[검증]` 근거를 100% 줘도 모델은 합계를 틀린다(전문→8, 운영 청크 포맷→10, 각 3/3 재현). **모델에게 세게 하면 안 된다.**
3. `[검증]` **짧고 정규화된 목록에서는 3/3 정확(9)** 했다. → 출력은 짧고 규칙적이어야 한다.

## 설계

### R1. 새 도구 `list_api_endpoints`

**위치**: `src/api_tool.py` (신규 파일 만들지 말 것). 기존 `validate_base_url` 의 QA 가드와 `base_urls` 조립을 그대로 재사용한다.

**팩토리 시그니처**

```python
def build_endpoint_list_tool(services: dict[str, str], timeout: float, max_chars: int,
                             transport: httpx.BaseTransport | None = None) -> BaseTool
```

`build_api_tool` 과 **같은 인자 모양**을 유지한다(호출부 대칭). 내부에서 `validate_base_url` 을 동일하게 적용한다 — QA 가드는 이 도구에도 그대로 걸린다.

**도구 시그니처**

```python
@tool
def list_api_endpoints(service: str) -> str
```

인자는 `service` 하나뿐이다. **경로 인자를 두지 않는다** — 스펙 경로는 `/openapi.json` 고정이다(두 등재 서비스 모두 이 경로이며, `RAG/resources/openapi_sources.yaml` 의 spec URL 과 일치한다).

**동작**

1. `base_urls` 에 없는 `service` → 네트워크를 쓰지 않고 `f"사용할 수 없는 service 입니다: {service}. 가능한 값: {names}"` (기존 `call_api` 문구 패턴 재사용).
2. `GET {base_url}/openapi.json` — `follow_redirects=False`, `timeout` 은 인자값. `call_api` 와 동일한 httpx 사용 방식.
3. 상태 코드가 200 이 아니면 → `f"HTTP {status} GET {url} — OpenAPI 스펙을 가져오지 못했습니다."`
4. JSON 파싱 실패 → `f"GET {url} 응답이 JSON 이 아닙니다."`
5. `paths` 를 순회해 **(메서드, 경로) 조합**을 센다.
   - **HTTP 메서드 화이트리스트로만 센다**: `get, put, post, delete, options, head, patch, trace`.
     `paths[path]` 아래에는 `parameters`, `summary`, `description`, `servers`, `$ref` 같은 **비(非)메서드 키가 올 수 있다. 이것을 세면 개수가 틀린다.** 이 필터가 이 도구의 정확성 핵심이다.
   - `paths` 가 없거나 비었으면 → `f"{service} 에 엔드포인트가 없습니다."`
6. **정렬**: 경로 사전순 → 같은 경로 안에서는 메서드 사전순. 출력이 매번 같아야 테스트가 고정된다.
7. 아래 형식으로 문자열을 만든다.

**출력 형식 (고정)**

```
general-chatbot-api 엔드포인트 총 9개
출처: https://qa-general-chatbot-api.hunet.ai/openapi.json
- GET /check — Check
- GET /companyinfo — Companyinfo
- GET /v2/dialogs/{service_key}/{page_key}/{chatbot_key}/{user_id} — 대화 목록 조회
...
```

- **1행이 총 개수다. 절대 뒤로 밀지 말 것.** `max_chars` 절단은 뒤에서 일어나므로, 개수를 맨 앞에 두면 **목록이 잘려도 숫자는 살아남는다.** `message-api` 는 엔드포인트가 140개가 넘어 실제로 잘린다 — 이 배치가 그 경우의 정확성을 지킨다.
- **2행에 스펙 URL 을 넣는다.** 모델이 출처로 인용할 URL 을 여기서 제공하지 않으면, 엉뚱한 URL 을 갖다 붙이거나(agent-19 D2) `unverified_source` 경고가 뜬다.
- 각 항목은 `- {METHOD} {path} — {summary}` 한 줄. `summary` 가 없으면 `operationId`, 그것도 없으면 대시와 설명을 생략하고 `- {METHOD} {path}` 로 끝낸다. **줄바꿈을 넣지 않는다**(한 항목 = 한 줄 불변).

**절단**

`max_chars` 초과 시 **줄 경계에서** 자르고, 마지막에 아래를 덧붙인다.

```
… (목록이 잘렸습니다. 위 총 개수는 정확합니다. 표시 N개 / 전체 M개)
```

기존 `TRUNCATED_SUFFIX`(`src/api_tool.py:17`)를 재사용하지 말고 **별도 상수**를 둔다 — 문구가 다르고, "총 개수는 정확하다" 를 모델에게 알려야 하기 때문이다. 줄 중간에서 자르면 엔드포인트가 반쪽으로 남아 모델이 오해한다.

**조립**: `src/agent.py` `build_default_graph` 에서 `api_services` 가 있을 때 `call_api` 와 **함께** 추가한다. 도구는 총 4개가 된다(`search_confluence`, `search_openapi`, `call_api`, `list_api_endpoints`).
이름이 `search_` 로 시작하지 않으므로 `force_search` 대상이 아니다(`src/agent.py:123`) — 의도된 것이다.

**설정 변경 없음**: 새 ini 키를 만들지 않는다. `[api] timeout`·`max-response-chars` 를 그대로 쓴다. (사용자 `config_local.ini` 를 다시 고치게 만들지 않는다.)

### R2. 시스템 프롬프트 안전장치

`src/agent.py` `SYSTEM_PROMPT`(87–101줄)에 **규칙 2개를 반영**한다. 기존 규칙 번호를 유지하고 뒤에 잇거나, 최소 수정으로 삽입한다.

1. 도구 선택 규칙에 추가:
   > 엔드포인트 개수나 전체 목록을 묻는 질문은 `list_api_endpoints` 를 사용합니다.
2. 단정 금지 규칙(기존 규칙 5 근처):
   > 검색 결과는 관련도 상위 일부일 뿐입니다. 검색 결과만으로 전체 개수나 전체 목록을 단정하지 않습니다.

문구는 기존 프롬프트의 어투(평서형 "…합니다")를 따른다. **기존 규칙을 삭제하지 않는다.**

## 작업 목록

1. `src/api_tool.py` 에 `build_endpoint_list_tool` 구현(메서드 화이트리스트, 정렬, 출력 형식, 줄 경계 절단).
2. `src/agent.py` `build_default_graph` 에 도구 등록.
3. `src/agent.py` `SYSTEM_PROMPT` 에 R2 규칙 2건 반영.
4. `test/test_api_tool.py` 에 테스트 추가(`httpx.MockTransport` — 기존 파일 패턴을 따를 것).
5. 기존 테스트 영향 정리: 도구 개수·목록을 단언하는 테스트(`test_agent.py`, `test_checkpointer.py`, `test_settings.py` 등)와 프롬프트 문구를 단언하는 테스트를 찾아 갱신. **단언을 약화시키지 말 것** — 값만 갱신한다.
6. README 갱신: "API 실호출" 절에 이 도구 한 단락 + 구조 표의 `src/api_tool.py` 행.
7. **(회차 2 추가)** 출력 1행에 **고유 경로 수를 병기**한다 — 아래 "회차 2 지시" 참조.

## 회차 2 지시 (2026-09-30, 실측 후 추가)

### T7. 1행에 고유 경로 수 병기

**사유**: 실환경 측정에서 `message-api` 가 **고유 경로 130 / (메서드,경로) 조합 145** 로 **두 기준이 갈렸다.**
`general-chatbot-api` 는 9=9 라 드러나지 않았을 뿐이다. agent-19 에서 "세는 기준이 다르면 반드시 명시한다" 를 원칙으로 세웠고, 지금이 정확히 그 경우다.
`145` 만 보여주면 "경로는 130개인데?" 라는 **이번과 똑같은 종류의 불신**이 재발한다.

**변경**: 출력 1행을 아래로 바꾼다. 조건 분기 없이 **항상 병기**한다(두 값이 같아도 병기 — 결정론적 단순 규칙).

```
message-api 엔드포인트 총 145개 (고유 경로 130개)
general-chatbot-api 엔드포인트 총 9개 (고유 경로 9개)
```

- 개수가 여전히 1행이라는 계약은 불변이다. 절단 내성도 그대로다.
- 2행(출처 URL)·항목 줄 형식·정렬·절단 규칙은 **변경 없다.**
- 관련 테스트 단언 값을 갱신한다(약화 금지).

### 판단 3건 회신 (Builder 보고 §4)

| # | 안건 | 리드 결정 |
|---|---|---|
| 1 | `_get()` 헬퍼 추출 | **승인.** `test_module_has_exactly_one_http_call_site` 의 의도는 "HTTP 호출 지점을 한 곳으로 모아 QA 가드를 우회할 수 없게" 하는 것이다. 헬퍼 추출은 그 의도에 **정면으로 부합**한다. 단언을 고치지 않고 설계를 맞춘 판단이 옳다 |
| 2 | `paths` 는 있으나 메서드 0건 | **승인(현행 유지).** `"… 에 엔드포인트가 없습니다."` 가 `총 0개` 보다 명확하다. 실무에서 거의 없는 경계 케이스에 포맷 일관성을 위해 분기를 늘릴 이유가 없다 |
| 4 | (회차 2) "고유 경로 수" 의 정의를 `len(paths)` 가 아니라 **"엔드포인트를 1개 이상 만든 경로의 수"** 로 구현 | **승인.** Builder 의 근거가 정확하다 — 메서드 화이트리스트와 **같은 기준**이어야 한다. `paths` 에 등재만 되고 메서드가 없는 항목을 경로 1개로 세면 **이 도구가 다시 "세면 안 되는 것을 세는"** 상태가 되고, 그건 이 티켓이 없애려는 결함 그 자체다. 현재 데이터에서는 두 정의가 일치(130=130)하므로 실측 영향도 없다 |
| 3 | R2 를 기존 규칙 2·5 에 삽입 | **승인.** 명세가 허용한 "최소 수정" 이다. 기존 프롬프트 단언 3곳을 **하나도 고치지 않고** 통과한 것이 이 선택이 옳았다는 증거다. 독립 번호 규칙으로 바꾸지 말 것 |

## 검증 전략

| 항목 | 확인 방법 |
|---|---|
| V1. 정확한 개수 | 목 스펙(메서드 여러 개인 경로 포함)에서 **(메서드,경로) 조합 수**가 정확히 나온다 |
| V2. **비메서드 키 제외** | `paths[path]` 에 `parameters`·`summary`·`$ref` 가 있는 목 스펙에서 **그것들이 개수에 포함되지 않는다.** 이 테스트 없이 완료 선언 금지 |
| V3. 개수가 맨 앞 | `max_chars` 를 아주 작게 준 목 케이스에서 **1행의 총 개수가 살아남고** 숫자가 정확하다 |
| V4. 줄 경계 절단 | 절단된 출력의 마지막 항목 줄이 **반쪽으로 잘리지 않는다**. 안내 문구에 표시/전체 개수가 들어간다 |
| V5. 결정론적 출력 | 같은 스펙 입력에 대해 출력이 매번 완전히 동일(정렬 고정) |
| V6. QA 가드 유지 | 운영 주소를 `[api-services]` 에 넣으면 이 도구 생성에서도 `ValueError`. 미등재 service 는 **네트워크 요청 없이** 안내 문자열만 |
| V7. 오류 처리 | 404/500 응답, JSON 아닌 응답, `paths` 없음 각각 예외 없이 설명 문자열 반환 |
| V8. 실환경 정답 ★ | 실제 `general-chatbot-api` 로 도구 직접 호출 → **9**. 그리고 **웹/CLI 에서 "general chatbot api의 총 api 개수가 몇 개야?" 질문 → 답변에 9** (3회, temperature=0 이므로 3/3 동일해야 함) |
| V9. 절단 서비스 실환경 | 실제 `message-api` 로 호출 → **1행 개수가 실제 openapi.json 의 (메서드,경로) 조합 수와 일치**(직접 세어 대조). 목록은 잘려도 된다 |
| V10. 남용 방지 | 일반 질문("메시지 등록 API 호출 방법 알려줘")에서 이 도구를 부르지 않고 기존처럼 검색으로 답한다 |
| V11. 회귀 | `pytest -q` green, `pytest -m integration -q` 전부 PASS. SSE 이벤트 계약(`search`/`token`/`done`/`warning`) 불변 |
| V13. 경로 수 병기 (T7) | 실환경 `message-api` 1행이 `총 145개 (고유 경로 130개)` 형태로 **두 수가 모두 정확**. 목 스펙(한 경로에 GET+POST)에서도 조합 수 ≠ 경로 수가 정확히 나온다 |
| V14. UI 표기 확인 | 브라우저에서 이 도구가 호출될 때 화면에 어떤 줄로 표시되는지 **실제로 확인**하고 원문을 기록한다(Builder `[추측]`: `[검색]` 라벨). 판정은 하지 말고 사실만 보고 — 리드가 결정한다 |
| V12. 범위 | 변경은 `src/api_tool.py`·`src/agent.py`·`test/**`·`README.md`·이 명세뿐. **`resources/config_local.ini*` diff 0**, `resources/static/index.html` diff 0, `src/web/app.py` diff 0 |

**V8 이 이 티켓의 존재 이유다.** 단위 테스트가 다 통과해도 V8 이 9를 못 내면 완료가 아니다.

## 완료 기준

- [ ] V1~V12 통과
- [ ] 실환경에서 "general chatbot api 총 api 개수" → **9** (3/3)
- [ ] 설정 파일 무변경(새 ini 키 없음)
- [ ] 기존 테스트 단언 약화 0건
- [ ] 커밋·스테이징하지 않음(사용자가 직접 한다)

## 범위 제외

- **R3** `rewrite` 노드 방어(agent-19 D1) — 다음 티켓. 이번에 손대지 않는다.
- **R4** 출처 항목 단위 대조(agent-19 D2) — 별도 티켓.
- `top-k` 값 변경, `max-response-chars` 값 변경 — agent-19 에서 기각됨.
- `/Users/mjkim/workspace/RAG` 저장소 변경(전량 열거 API 추가 등).
- GET 이외 메서드, 운영 환경 호출, 인증.
- 스펙 경로를 설정으로 받게 하는 것(현재 두 서비스 모두 `/openapi.json`).
- Confluence 문서 개수 집계.

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| `call_api` 에 "개수 세기" 옵션 인자 추가 | 도구 하나에 성격이 다른 두 동작이 섞인다. 모델이 언제 쓸지 헷갈린다 |
| 도구가 개수만 반환하고 목록은 생략 | 모델이 "어떤 API 들이냐" 후속 질문에 다시 검색해야 한다. 목록은 절단되더라도 있는 편이 낫다 |
| 목록을 앞, 개수를 뒤에 배치 | **절단되면 개수가 날아간다.** `message-api` 에서 실제로 발생한다 |
| LLM 에게 "정확히 세라" 고 프롬프트로 지시 | agent-19 M4-B/C 가 반례(3/3 오답). 지시로 고쳐지는 실패가 아니다 |
| 스펙을 캐싱 | 요청하지 않은 최적화. 호출 빈도가 낮다 |

## 리드 판단 기록

- 2026-09-30 (1): 사용자가 R1+R2 승인. **개수를 출력 1행에 고정**하는 것이 이 설계의 핵심 결정이다 — `message-api` 는 140개가 넘어 `max-response-chars=2000` 에 반드시 걸리는데, 배치만으로 "잘려도 숫자는 정확" 을 보장할 수 있다. 설정값을 올리는 대신 배치로 푼다.
- 2026-09-30 (2): **새 ini 키를 만들지 않는다.** 사용자 `config_local.ini` 가 이미 `[api-services]`·`[api]` 누락으로 두 번 깨진 전례가 README 에 남아 있다. 또 그러게 하지 않는다.
- 2026-09-30 (3): 출력 2행에 스펙 URL 을 넣는 것은 장식이 아니다. agent-19 D2(출처 오귀속)가 **인용할 올바른 URL 이 근거 안에 없을 때** 발생했다. 도구가 직접 제공해 차단한다.
- 2026-09-30 (4): 메서드 화이트리스트(V2)를 명시적 완료 기준으로 올린다. OpenAPI 의 `paths[path].parameters` 는 흔하고, 놓치면 **이 도구가 틀린 숫자를 자신 있게 말하는** 최악의 형태가 된다. 지금 고치는 문제를 그대로 재현하는 셈이다.
- 2026-09-30 (5): **Validator 회차 1 PASS — READY FOR REVIEW 선언.** 단위 570(Builder 559 + Validator 보강 11) / 통합 11, 실패 0.
  - `[검증]` **V8 이 이 티켓의 존재 이유였고 4/4 "총 9개"** 로 통과했다. 웹과 동일 경로(SSE)로 측정했고 `unverified_source` 경고 0건이다.
  - `[검증]` V2 가 실제로 막는다 — `parameters`·`summary`·`description`·`servers`·`$ref`·`x-vendor` 를 전부 넣은 스펙에서 `총 9개 (고유 경로 2개)` 로 독립 계산과 일치. 화이트리스트를 무력화한 사본에서 **10건 실패**(민감도 확인).
  - `[검증]` V3 의 개수 1행 배치가 극단값에서도 성립한다 — `max_chars` 1/5/20/60/100/200 **전부** 1행 개수와 2행 출처가 생존했다. 개수를 뒤로 옮긴 사본에서 8건 실패.
  - `[검증]` V9 는 Builder 값을 복사하지 않고 `message-api/openapi.json`(194,972 bytes)을 직접 파싱해 대조했다 — 조합 145 / 경로 130 일치.
  - `[검증]` 기존 단언 약화 0건. `git diff -U0 -- test/` 삭제 줄이 **단 1줄**(도구 목록 3개→4개)이고 `==` 완전 일치 강도를 유지한다.
  - `[검증]` agent-16/17 계약 유지 — SSE `search(1) → token(268) → done(1)`, **첫 search 이전 token 0건**, token 합 == 최종 답변.
  - 민감도 15종 중 14종 검출. 미검출 1종(절단 조건 off-by-one)은 관측 가능한 계약이 바뀌지 않는 **등가 변이**라는 판단을 수용한다.
- 2026-09-30 (6): **비차단 의견 3건 판단.**
  1. 절단 시 최종 길이가 `max_chars` 를 머리글·안내 문구만큼 초과 — **수용.** 기존 `_format_response` 와 같은 방식이고, "개수는 살아남는다" 는 이 티켓의 핵심 계약이 그 초과를 요구한다.
  2. `HTTP_METHODS` 소문자 전용 — **수용.** OpenAPI 3.x 에서 소문자 고정 필드이고 등재 두 서비스 실 스펙에 대문자 키 0건이다. 일어나지 않는 상황을 위한 방어를 넣지 않는다.
  3. **V14 UI 라벨 — 이번 티켓에서 고치지 않는다.** `[미확정→검증됨]` 실측 결과 화면에 `[검색] list_api_endpoints(general-chatbot-api)` 로 표시된다(`resources/static/index.html:219` 의 `data.tool === 'call_api' ? '[호출]' : '[검색]'` 분기가 유일). **외부로 실제 GET 을 보내는 도구가 "검색" 으로 보이는 것은 사용자에게 부정확하다** — 고쳐야 할 문제로 인정한다. 다만 ① 기능·정확성 결함이 아니고 ② 완료 기준 V12(`index.html` diff 0)를 티켓 막바지에 리드가 스스로 깨면 검증 결과가 무의미해지며 ③ 제대로 고치려면 `call_api` 하드코딩 분기 대신 "실호출 도구 집합" 개념을 서버·UI·CLI 3곳에 도입해야 해 별도 티켓 크기다. **agent-21 후보로 넘긴다.**
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

- 검증자: Validator, 2026-09-30, **검증 회차 1**
- 판정: **PASS (READY FOR REVIEW)** — V1~V13 전부 PASS, V14 는 사실만 기록(리드 판단 대기)
- Builder 보고값은 복사하지 않고 전부 재측정했다.

### 스위트 결과 `[검증]`

| 명령 | 결과 |
|---|---|
| `.venv/bin/python -m pytest -q` | **570 passed**, 11 deselected (2.89s) — Builder 559 + Validator 보강 11 |
| `.venv/bin/python -m pytest -m integration -q` | **11 passed** (231.66s) |
| `python -m compileall -q src test main.py web.py run.py maintenance.py` | OK |
| `python -m pip check` | No broken requirements found |

프로젝트에 lint/type 설정은 없다(기존 상태 유지). 정적 검증은 `compileall` + `pip check`.

### 완료 기준 체크리스트

| 항목 | 판정 | 증거 |
|---|---|---|
| V1 정확한 개수 | PASS | 독립 작성 목 스펙(`/alpha` 에 8메서드, `/zeta` 1, `/onlymeta` 0)에서 출력 1행 `총 9개 (고유 경로 2개)`. 같은 스펙을 raw 파싱한 독립 계산값(조합 9 / 경로 2)과 일치 |
| V2 **비메서드 키 제외** ★ | PASS | 위 스펙의 `/zeta` 에 `parameters`·`summary`·`description`·`servers`·`$ref`·`x-vendor` 를 모두 넣었으나 개수·목록 어디에도 나타나지 않음(`parameters`/`servers`/`$ref`/`경로 요약`/`경로 설명`/`x-vendor` 문자열 부재 확인). 구현 근거 `src/api_tool.py:23`(`HTTP_METHODS`), `src/api_tool.py:100-101`. **민감도 확인**: 화이트리스트 `continue` 를 제거한 사본에서 `test_api_tool.py` 10건 실패 |
| V3 개수가 맨 앞 | PASS | 항목 120개 목 스펙에 `max_chars` = 1 / 5 / 20 / 60 / 100 / 200 — **전부** 1행이 `… 총 120개 (고유 경로 60개)` 로 보존, 2행 출처 URL 보존, 안내 문구의 전체 수도 120 정확. **민감도**: 개수를 맨 뒤로 옮긴 사본에서 8건 실패(`test_truncated_output_keeps_the_exact_count_in_the_first_line` 포함) |
| V4 줄 경계 절단 | PASS | `max_chars=600`, 120항목 → 표시 10개가 전체 목록의 앞 10줄과 **완전히 동일**(반쪽 줄 없음), 마지막 줄 `… (목록이 잘렸습니다. 위 총 개수는 정확합니다. 표시 10개 / 전체 120개)`. **민감도**: 문자 단위 절단 사본에서 4건 실패 |
| V5 결정론적 출력 | PASS | 같은 스펙 3회 호출 및 별도 도구 인스턴스 호출이 모두 동일. `paths` 키 순서를 무작위로 섞은 스펙도 동일 출력. 실환경 `general-chatbot-api`·`message-api` 2회 호출도 동일. **민감도**: `sorted()` 제거 사본(경로/메서드 각각)에서 2건씩 실패 |
| V6 QA 가드 유지 | PASS | `build_endpoint_list_tool({"x": 운영주소})` → `ValueError`(`https://message-api.hunet.io`, `http://qa-x.hunet.ai`, `https://prod.hunet.ai` 3종 확인). 미등재 service → `사용할 수 없는 service 입니다: 없는서비스. 가능한 값: general-chatbot-api, message-api`, **TransportSpy 요청 0건**. 그래프 단위 fail-fast 는 기존 `test_default_graph_fails_fast_on_production_url` 이 4도구 구성에서도 통과 |
| V7 오류 처리 | PASS | 301/302/400/404/500/503 → `HTTP {code} GET {url} — OpenAPI 스펙을 가져오지 못했습니다.`(리다이렉트 미추종, 요청 1건). 비JSON → `… 응답이 JSON 이 아닙니다.` `{}`·`{"paths":{}}`·`{"paths":None}`·`{"paths":[]}`·최상위 list·`{"paths":{"/x":"문자열"}}` → 전부 예외 없이 `… 에 엔드포인트가 없습니다.` |
| V8 **실환경 정답** ★ | PASS | 웹과 동일 경로(`build_default_graph` + `create_app` + `/v1/chat/stream` SSE, 체크포인터만 `InMemorySaver`)로 "general chatbot api의 총 api 개수가 몇 개야?" **4회 질의 → 4/4 "총 9개"**. 매회 `search` 이벤트 1건 = `list_api_endpoints(general-chatbot-api)`, 9개 목록을 정확히 열거, `warning`(unverified_source) **0건**. 도구 직접 호출도 `general-chatbot-api 엔드포인트 총 9개 (고유 경로 9개)` |
| V9 절단 서비스 실환경 | PASS | `GET https://message-api.qa.hunet.io/openapi.json`(194,972 bytes)를 **Validator 가 직접 받아 독립 파싱** → 조합 **145** / 메서드 보유 경로 **130** / `len(paths)` 130 / 비메서드 키 0. 실제 도구 출력 1행 `message-api 엔드포인트 총 145개 (고유 경로 130개)` — **완전 일치**. 출력 1,955자, 표시 24개 / 전체 145개로 정상 절단 |
| V10 남용 방지 | PASS | 실환경 "메시지 등록 API 호출 방법 알려줘" → 호출 도구는 `search_openapi` 하나, `list_api_endpoints` 미호출 |
| V11 회귀 | PASS | 위 스위트 결과. SSE 계약 재확인(실환경 1턴): 이벤트 순서 `search(1) → token(268) → done(1)`, **search 이전 token 0건**(agent-17 임시 답변 미방출 유지), token 합 793자 = 최종 답변, `warning` 없음 |
| V12 범위 | PASS | `git status --porcelain` = `M README.md / M docs/plans/agent-17…md / M src/agent.py / M src/api_tool.py / M test/test_api_tool.py` + 신규 계획 문서. `git diff --stat -- resources/static/index.html src/web/app.py` **출력 없음(diff 0)**. `resources/config_local.ini`·`.example` 는 mtime 2026-09-29 11:12 로 이번 작업 전이며 내용에 새 키 없음(`[api] timeout=20`, `max-response-chars=2000` 그대로) |
| V13 경로 수 병기(T7) | PASS | 실환경 `message-api` 1행 두 수가 독립 파싱값과 일치(V9). 목 스펙 `{"/a":{get,post},"/b":{get}}` → `총 3개 (고유 경로 2개)`. 두 값이 같아도 병기(`총 2개 (고유 경로 2개)`). 메서드 없는 경로는 경로 수에서 제외(판단 #4 준수) — **민감도**: `len(paths)` 로 바꾼 사본에서 `test_paths_without_any_method_are_not_counted_as_unique_paths` 실패 |
| V14 UI 표기 | **사실 기록(판정 보류)** | 아래 절 |
| 설정 파일 무변경 | PASS | V12 참조. 새 ini 키 0건 |
| 기존 테스트 단언 약화 | PASS(0건) | `git diff -U0 -- test/` 의 삭제 줄은 **단 1줄** — `assert names == ["search_confluence","search_openapi","call_api"]` → 4개 목록으로 값만 갱신. 완전 일치 단언(`==`)을 유지해 강도 동일. 그 외 삭제·완화 없음 |
| 커밋·스테이징 | 없음 | Validator 는 `git add`/`commit`/`push` 를 수행하지 않았다 |

### V14. UI 표기 — 사실만 기록 `[검증]`

- 실환경에서 실제로 수신한 SSE 프레임: `event: search` / `data: {"tool": "list_api_endpoints", "query": "general-chatbot-api"}`
- `resources/static/index.html:216-223` 의 `addSearchLine` **원문을 그대로 실행**(node, DOM 최소 스텁)해 위 payload 를 넣은 결과:

  ```
  class=search | 화면 표시 원문: [검색] list_api_endpoints(general-chatbot-api)
  ```

  비교군: `[검색] search_openapi(메시지 등록 API)`, `[호출] call_api(message-api /openapi.json)`
- 라벨 분기는 `data.tool === 'call_api' ? '[호출]' : '[검색]'`(`index.html:219`) 하나뿐이라 `call_api` 외 모든 도구가 `[검색]` 으로 표시된다. CLI 도 같은 규칙(`main.py:36`, `API_TOOL_NAME = "call_api"` = `src/agent.py:26`)이라 `[검색] list_api_endpoints(general-chatbot-api)` 로 출력된다.
- 즉 **Builder 의 `[추측]` 은 사실이었다.** 화면에는 회색 pill 한 줄로 `[검색] list_api_endpoints(general-chatbot-api)` 가 뜬다. 스펙을 실제로 GET 하는 동작이므로 `[검색]` 이 적절한지, `[호출]` 또는 별도 라벨이 나은지는 **리드 판단 사항**이며 Validator 는 수정하지 않았다.
- `[미확인]` 실제 브라우저 렌더링은 확인하지 못했다. 헤드리스 브라우저 도구가 환경에 없고, 새 의존성을 추가하지 않는다는 규칙 때문이다. 대신 실제 SSE payload + UI 원문 실행으로 대체했다.

### Validator 가 추가한 테스트 (11건, `test/test_api_tool.py` 말미 "Validator 보강 (agent-20)")

프로덕션 코드는 수정하지 않았다. 추가 파일 없음.

1. `test_endpoint_list_exactly_at_max_chars_is_not_truncated` — 경계값(길이 == `max_chars`)
2. `test_endpoint_list_one_char_over_max_chars_drops_only_the_last_item` — 경계값(1자 초과 시 마지막 한 줄만 탈락, 1행 개수 불변)
3. `test_spec_path_order_does_not_change_the_output` — 정렬이 입력 키 순서와 무관(기존 결정론 테스트는 같은 dict 를 재사용해 이 축을 덮지 못했다)
4~8. `test_malformed_spec_shapes_are_explained_without_raising`(5 파라미터) — 최상위가 dict 가 아님, `paths` 가 `None`/list, 경로 값이 문자열
9. `test_non_string_summary_falls_back_to_operation_id` — `summary` 가 문자열이 아닐 때 `operationId` 폴백, 둘 다 없으면 설명 생략
10. `test_large_spec_truncated_at_the_real_max_chars_keeps_an_exact_count` — `max_chars=2000` + 146개 스펙(message-api 규모)에서 1행 두 숫자 정확 + 표시 항목 무손상
11. `test_endpoint_list_result_reaches_the_model_as_a_tool_message_in_graph` — 그래프 결합: 도구 출력이 `ToolMessage(status="success")` 로 손상 없이 모델에 도착

### 민감도 검증(변이 테스트) `[검증]`

저장소를 건드리지 않기 위해 스크래치패드에 사본을 만들어 `src/api_tool.py` 를 변이시키고 `test/test_api_tool.py` 를 돌렸다(검증 후 사본 폐기, 원본 diff 0 확인).

| 변이 | 잡혔나 |
|---|---|
| 메서드 화이트리스트 무력화 | ✅ 10건 실패 |
| 개수를 1행에서 맨 뒤로 이동 | ✅ 8건 |
| 문자 단위 절단(줄 경계 무시) | ✅ 4건 |
| 고유 경로 수를 `len(paths)` 로 | ✅ 1건 |
| 1행에서 고유 경로 수 제거 | ✅ 6건 |
| 경로 정렬 제거 / 메서드 정렬 제거 | ✅ 2건 / 2건 |
| `summary` 줄바꿈 정규화 제거 | ✅ 3건 |
| `follow_redirects=True` | ✅ 2건 |
| 미등재 service 에서도 네트워크 호출 | ✅ 1건 |
| 2행 출처 URL 제거 | ✅ 6건 |
| 스펙 경로를 `/docs` 로 | ✅ 8건 |
| `operationId` 폴백 제거 | ✅ 5건 |
| 절단 안내의 전체 수를 표시 수로 | ✅ 4건 |
| `paths` 값 타입 검사 제거 | ✅ 1건 |
| 절단 조건 off-by-one(`>` → `>=`) | ❌ 미검출 — 관측 가능한 계약(개수 정확·줄 경계·표시/전체 수) 어느 것도 바뀌지 않는 **등가 변이**로 판단, 테스트를 추가하지 않았다 |

### 사용자 데이터 무오염 `[검증]`

실환경 측정은 전부 `InMemorySaver` 로 했다. 검증 전후 md5 동일:

```
data/checkpoints.sqlite      34148a1d8e4ccd84dc9882b2572a3eab (전/후 동일)
data/checkpoints.sqlite-wal  49ec1e467e8876f953400ec75ea854e9 (전/후 동일)
data/checkpoints.sqlite-shm  ea8927b5d0a453574e870277b7ad02b8 (전/후 동일)
```

서버는 새로 띄우지 않았다. RAG(5010, pid 2427)·LangGraph(5020, pid 2551)는 검증 시작 전부터 떠 있었고 그대로 두었으며, 실환경 측정은 떠 있는 웹 서버가 아니라 `TestClient` 로 별도 앱 인스턴스를 만들어 수행했다(운영 중 스레드 DB 미접촉).

### 비차단 의견 (판정에 반영하지 않음)

1. `[검증]` 절단 시 최종 출력 길이가 `max_chars` 를 **안내 문구 + 머리글 길이만큼 초과**할 수 있다(`max_chars=600` → 613자, `max_chars=1` → 147자). 기존 `_format_response` 도 같은 방식(본문 `max_chars` + 접미사)이고, "개수는 반드시 살아남는다" 는 이 티켓의 핵심 계약상 의도된 동작으로 보인다. 변경 제안 아님.
2. `[검증]` `HTTP_METHODS` 는 소문자만 센다. 등재된 두 서비스의 실제 스펙에는 대문자 메서드 키가 0건이고 OpenAPI 3.x 도 소문자 고정 필드를 규정하므로 현재 문제 없다. `[미확인]` 향후 비표준 스펙이 등재될 경우의 동작은 확인하지 않았다.
3. `[검증]` V14 의 `[검색]` 라벨은 리드 판단 사항이라 손대지 않았다.

# agent-17: 버려질 임시 답변이 화면에 흐르지 않게

- 작성일: 2026-09-30
- 작성자: 리드
- 상태: READY FOR REVIEW (2026-09-30, 리드 확인, 검증 회차 2) — 커밋 대기(사용자)
- 계기: 사용자 보고 — "안녕" 을 치면 ① 답변이 먼저 나오고 ② 사라지고 ③ `[검색]` 줄이 나오고 ④ 다시 답변이 나온다. **아무리 봐도 이상하다.**
- 저장소: `/Users/mjkim/workspace/LangGraph` 만.

## 목표

agent-16 의 강제 검색이 켜진 턴에서, **버려질 임시 답변이 브라우저에 흘렀다가 지워지는 현상**을 없앤다. 사용자는 최종 답변만 봐야 한다.

## 확인된 현재 상태 (`[검증]`)

- agent-16 이후 강제 검색 턴의 흐름: `agent`(근거 없는 임시 답변 생성) → `force_search` → `agent`(최종 답변).
- `stream_events` 는 `agent` 노드의 **모든 패스**에서 `token` 을 내보낸다. 그래서 **임시 답변이 먼저 스트리밍**되고, 이어서 `search` 이벤트가 오면 UI 가 `raw=''`·`body.replaceChildren()` 으로 지운 뒤 최종 답변을 다시 그린다.
- agent-16 검증에서 Validator 가 이 현상을 **비차단**으로 보고했고 리드가 수용했다. **판단 착오였다** — 사용자에게는 가짜 답변이 떴다 사라지는 것으로 보이고, 그 임시 답변에는 환각 내용·가짜 URL 이 들어 있을 수 있다(agent-16 실측에서 274자 임시 답변에 가짜 URL 포함).
- CLI(`run_turn`)는 agent-16 F2 로 이미 스트림 종료 후 1회만 출력하므로 영향이 없다.

## 설계

**핵심 관찰**: `agent` 패스가 **답변 토큰을 만드는 경우**는 그 패스에 tool_calls 가 없다는 뜻이다. 그리고 **이번 턴에 아직 도구 결과가 없다면** 그 답변은 agent-16 의 강제 검색에 의해 **반드시 버려진다.** 즉 **버려질 토큰은 사전에 식별 가능하다.**

→ `src/web/app.py` `stream_events` 가 **이번 턴에 도구 결과가 생기기 전까지 `token` 이벤트를 내보내지 않는다.**

| 상황 | token 방출 |
|---|---|
| 첫 `agent` 패스, 이번 턴 도구 결과 0건 | **보류**(버려질 임시 답변) |
| 모델이 스스로 도구를 호출한 패스 | 원래 content 가 비어 토큰이 없음 — 영향 없음 |
| 도구 결과가 생긴 뒤의 `agent` 패스 | **정상 방출**(최종 답변) |

- 판정은 스트림 안에서 **ToolMessage 관찰 여부**로 한다(`updates` 에서 도구 결과가 한 번이라도 나왔는가). `system_facts` 도 도구 결과이므로 강제 검색 직후 조건이 참이 된다.
- **안전장치(필수)**: 스트림이 끝날 때까지 `token` 을 하나도 내보내지 않았다면, **최종 답변을 `done` 직전에 한 번에 내보낸다.** 강제 검색이 어떤 이유로 일어나지 않는 구성(예: 검색 도구가 없는 그래프)에서 답변이 영영 안 보이는 사태를 막는다.
- `search`·`warning`·`done`·`error` 이벤트 이름·형식·순서는 **불변**. UI 는 손대지 않는다(현재 `search` 에서 본문을 비우는 로직은 그대로 둬도 무해하다 — 비울 내용이 애초에 없어진다).

### 부수 효과 (의도한 것)

- agent-16 에서 "`token` 합 == 최종 답변" 이 **마지막 `search` 이후** 토큰에만 성립하던 것이, 이제 **전체 합**으로 성립한다. README 의 단서를 되돌린다.
- 강제 검색 턴에서 사용자는 `[검색]` 줄을 먼저 보고 → 최종 답변이 흐른다. 임시 답변은 화면에 전혀 나타나지 않는다.

## 작업 목록

1. `src/web/app.py` `stream_events` 에 보류 로직 + 종료 시 폴백.
2. `test/test_web.py` 테스트 추가.
3. README 의 `token` 행 단서 정정.

## 검증 전략

| 항목 | 확인 방법 |
|---|---|
| S1. 임시 답변 미방출 | 가짜 모델이 "임시 답변 → (강제 검색) → 최종 답변" 을 내는 시나리오에서 **첫 `search` 이전에 `token` 이벤트 0건**, 최종 답변만 토큰으로 나감 |
| S2. token 합 | 강제 검색 턴에서 **전체 `token` 합 == 최종 답변**(마지막 search 이후만이 아니라) |
| S3. 일반 턴 무영향 | 모델이 스스로 도구를 호출하는 턴, 도구 결과가 이미 있는 턴의 이벤트 순서·내용이 기존과 동일 |
| S4. 폴백 | 강제 검색이 일어나지 않고 도구 결과도 없는 구성에서 **답변이 `done` 직전에 한 번에 방출**됨(빈 화면으로 끝나지 않음) |
| S5. 계약 불변 | `search`·`warning`·`done`·`error` 이름·형식·순서 불변, `warning` 은 여전히 `done` 직전. UI 파일 diff 0 |
| S6. 회귀 | `pytest -q` green, `pytest -m integration -q` 전부 PASS(W7 의 `answer == stored` 포함) |
| S7. 실환경 (Validator) | 웹에서 `"안녕"` 3회 → **임시 답변이 보였다 사라지는 현상 0건**, `[검색]` 줄 뒤 최종 답변만. `token` 이벤트 순서 원문 기록 |
| S8. 범위 | 변경은 `src/web/app.py`·`test/test_web.py`·`README.md`·이 명세뿐. `src/agent.py`·`main.py`·`resources/static/index.html` diff 0 |

## 완료 기준

- [ ] S1~S8 통과
- [ ] 그래프·프롬프트·도구·UI 파일 무변경
- [ ] 폴백이 테스트로 고정됨(답변이 사라지는 최악의 경우 방지)
- [ ] 커밋하지 않음(사용자)

## 범위 제외

- 강제 검색 자체의 동작 변경(agent-16 유지)
- 인사말 등 특정 질문을 강제 검색에서 제외하는 것
- UI 렌더링 방식 변경

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| UI 가 첫 토큰을 늦게 그리기(디바운스) | 타이밍에 기대는 미봉책. 느린 답변에서는 그대로 보인다 |
| `agent` 노드가 임시 답변을 만들지 않게 하기 | 모델이 답을 내봐야 tool_calls 유무를 알 수 있다. 구조상 불가 |
| 임시 답변을 회색으로 표시해 "초안" 임을 알리기 | 지어낸 내용·가짜 URL 을 사용자에게 보여줄 이유가 없다 |
| agent-16 의 강제 검색 철회 | 근거 없는 답변·출처 환각이 되살아난다 |

## 리드 판단 기록

- 2026-09-30 (1): 사용자 보고로 착수. **agent-16 검증에서 Validator 가 이 현상을 비차단으로 보고했고 리드가 수용한 것이 착오였다** — "최종 화면 결과가 정상" 이라는 이유였지만, 사용자는 과정을 본다. 임시 답변에 환각·가짜 URL 이 들어 있을 수 있으므로 **잠깐이라도 보여서는 안 된다.**
- 2026-09-30 (2): 서버에서 막는다. "버려질 토큰은 사전에 식별 가능하다"(이번 턴 도구 결과가 없는 agent 패스의 답변 토큰)는 관찰이 근거다. UI 타이밍 조정은 미봉책이라 기각.
- 2026-09-30 (3): **폴백을 필수로 둔다.** 보류만 하고 방출 조건이 안 맞으면 답변이 통째로 사라진다 — 가장 나쁜 실패 모드다.
- 2026-09-30 (4): **Validator 회차 1 PASS.** 단위 531 / 통합 11×2회. `[검증]` S7 실환경 `"안녕"` 3/3 — 이벤트가 `search ×2 → token×11 → done` 이고 **첫 `search` 이전 token 0건**. agent-16 에서 같은 자리에 흘렀던 274자 임시 답변(가짜 URL 포함)이 **완전히 사라졌다.** 전체 token 합 == 저장된 답변(agent-16 의 "마지막 search 이후" 단서 소멸). 보류·폴백 둘 다 민감도 확인됨(제거 시 각각 6건·3건 실패). 기존 테스트 수정은 계약 변경 4건 + 기계적 1건이고 **전부 정확 동등 비교를 유지**해 약화 없음.
  - **minor 1건 정리 지시**: `[검증]` **검색 도구가 없는 그래프**에서는 `answer.clear()` 가 `system_facts` 의 `continue` 때문에 건너뛰어져, 버려진 임시 답변의 가짜 URL 이 `warning` 으로 나간다. `build_default_graph` 는 항상 검색 도구를 붙이므로 **배포 경로에는 도달하지 않지만**, 잠재 함정이라 지금 없앤다(`answer.clear()` 를 `continue` 앞 또는 ToolMessage 관찰 시점으로 이동).
  - Builder 의 폴백 테스트 docstring 이 실제 구성과 다르다는 지적도 함께 정정한다(테스트 자체는 유효하며 Validator 가 명세 경로용 테스트를 별도로 추가했다).
- 2026-09-30 (5): **Validator 회차 2 PASS — READY FOR REVIEW 선언.** 단위 532 / 통합 11 passed.
  - `[검증]` 폴백 재작성은 **강화**다: 이벤트 꼬리 2개만 보던 것 → **전체 목록 정확 동등**, 값도 한 프레임에 합쳐짐까지 고정. 폴백 3줄 제거 시 3건 실패(민감도 유지).
  - `[검증]` `answer.clear()` 이동은 **배포 경로 동작을 바꾸지 않는다**(리드 `[추측]` 확인됨). 실환경 재측정에서 "안녕"·userstore 모두 첫 `search` 이전 token 0건, 전체 합 == 저장된 답변, `warning` 0건. W7 `answer == stored` 통과.
  - `[검증]` 신규 회귀 테스트가 수정 지점을 정확히 겨냥한다 — `answer.clear()` 를 되돌린 사본에서 **그 테스트만** 실패(나머지 63건 통과).
  - **이 티켓에서 드러난 사실 하나를 기록한다**: 회차 1 에서 "배포 경로에 도달하지 않는 minor" 로 분류했던 `answer.clear()` 건은 실제로는 **폴백 경로를 오염시키고 있었다**(폴백이 최종 답변이 아니라 버려진 임시 답변을 내보냈다). 기존 폴백 테스트는 그 결함에 기대어 통과하고 있었다. **"도달 불가" 라는 분류가 틀릴 수 있으며, 고쳐 보고 나서야 드러나는 의존이 있다.**
  - Builder 판단 승인: 리드 지시("단언은 그대로")와 충돌했으나, 수정 후 그 테스트가 잘못된 것을 검증하고 있음이 드러났으므로 **재작성이 옳았다.** 지시를 기계적으로 따르지 않고 근거와 함께 보고한 판단이 정확했다.
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

(Validator 가 기록)

### 2026-09-30 Validator 1회차 — **PASS**

- 환경: `.venv`, RAG 5010(PID 2427·2436)·웹 5020(PID 2551) 검증 전후 동일, `/check` 전후 `{"status":"ok","rag":true,"ollama":true}`. 실측은 전부 `InMemorySaver`(사용자 DB 무오염), 사내 API 는 통합 테스트의 GET 만.
- 스위트: `pytest -q` **531 passed / 0 failed**(Validator 추가 2건 포함), `pytest -m integration -q` **2회 전부 11 passed**.

| 항목 | 판정 | 증거 |
|---|---|---|
| S1 임시 답변 미방출 | PASS | 단위 `test_no_token_is_sent_before_the_first_search` + 실환경 3회 모두 첫 `search` 이전 `token` **0건** |
| S2 token 합 | PASS | 실환경 강제 검색 턴 **전체 token 합 == 저장된 최종 답변**(True). 통합 W7 `answer == stored` 2/2 |
| S3 일반 턴 무영향 | PASS | 실환경 "메시지 등록 API 호출 방법 알려줘" → `search` 먼저, token 385건, 합 == 저장. 통합 11 passed ×2 |
| S4 폴백 | PASS | 도구 결과가 **한 번도 없는** 그래프에서 `['token','done']`, 답변 1건으로 방출. 폴백 제거 사본에서는 3개 테스트가 모두 실패(= 고정됨) |
| S5 계약 불변 | PASS | 이벤트 이름·순서 불변, `warning` 은 폴백 경로에서도 `done` 직전. `resources/static/index.html` diff 0 |
| S6 회귀 | PASS | 단위 531 green, 통합 2회 전부 11 passed(T6·P4·P6·W7·`call_api`) |
| S7 실환경 | **PASS 3/3** | `"안녕"` 3회 이벤트 순서 `search, search, token×11, done` — 임시 답변이 떴다 사라지는 현상 **0건** |
| S8 범위 | PASS | `src/agent.py`·`main.py`·`resources/static/index.html` **diff 0** |

- 기존 테스트 수정 분류(Builder 보고와 대조): **계약 의미 변경 4건**(`test_text_before_tool_call_streams_before_search_event` → `..._is_not_streamed`(agent-05 계약 대체), `test_groundless_answer_emits_forced_search_events` 의 token 단언, `test_rewrite_output_is_not_streamed_as_token` 의 token 단언, `test_tokens_after_the_last_search_...` → `test_all_tokens_reproduce_the_stored_answer_exactly`) + **기계적 1건**(`test_sse_data_is_json_encoded` 스크립트 응답 추가). **단언 약화 없음** — 네 건 모두 정확 동등 비교를 유지했고, agent-05 건은 `== "문서를 먼저 검색해 볼게요."` → `== []` 로 **반대 사실을 같은 강도로** 단언한다.
- 민감도: 보류 로직을 무력화하면 6건, 폴백을 제거하면 3건이 실패한다.
- 비차단(판정 미반영):
  1. Builder 의 `test_fallback_emits_the_answer_when_no_tool_result_ever_appears` 는 docstring 과 달리 **도구 결과가 있는** 구성이다 — `build_graph(model, [], None)` 도 `force_search` 가 `system_facts` ToolMessage 를 넣는다. 이 테스트가 폴백을 태우는 실제 이유는 가짜 모델이 같은 메시지 객체를 재사용해 두 번째 `agent` 패스에서 `messages` 청크가 나오지 않기 때문이다. 명세가 말한 경로를 직접 태우는 테스트를 Validator 가 추가했다.
  2. 검색 도구가 없는 그래프에서는 `answer.clear()` 가 `system_facts` 의 `continue` 때문에 건너뛰어져, 버려진 임시 답변의 가짜 URL 이 여전히 `warning` 에 뜬다(실측). 배포 구성(`build_default_graph`)은 항상 검색 도구 2종을 붙이므로 도달하지 않는다. agent-16 잔여 사항이며 한 줄(클리어 위치) 이동으로 정리 가능.
  3. `docs/plans/agent-16-...md` 의 상태 줄 1줄이 함께 바뀌었다(S8 목록 밖, 문서 정리성 변경).
- Validator 추가 테스트: `test/test_web.py::test_fallback_emits_the_answer_when_the_graph_never_produces_tool_results`, `::test_fallback_keeps_the_warning_right_before_done`.

### 2026-09-30 Validator 2회차 (범위 최소 재확인) — **PASS**

- 서버: 5010(PID 2427·2436)·5020(PID 2551) 전후 동일, `/check` 전후 `{"status":"ok","rag":true,"ollama":true}`. 실측 `InMemorySaver`, 사내 API 는 통합의 GET 만.
- `pytest -q` **532 passed / 0 failed**. `pytest -m integration -q` **11 passed**(W7 `answer == stored` 포함, T6 3/3·P4 2케이스·P6·`call_api`).
- **폴백 재작성은 약화가 아니다** `[검증]`: 이전 단언(`token 합 == "유일한 답변"` + `names[-1]=="done"` + `names[-2]=="token"`)보다 새 단언(`[이벤트 목록] == ["token","done"]` + `events[0][1] == "유일한 답변"`)이 **전체 이벤트 목록을 정확 동등으로 고정**해 더 강하다. 또 이전 테스트는 `build_graph(..., [], None)` 이라 `system_facts` 도구 결과가 실제로 생겼고, 통과 이유가 **폴백이 버려진 임시 답변을 내보내던 결함**이었다(Builder 보고와 일치). 새 테스트는 `_ScriptedGraph` 로 "도구 결과가 영영 없는" 경로를 직접 태운다.
- 민감도: 폴백 3줄 제거 → 폴백 테스트 **3건 모두 실패**(Builder 1 + Validator 2). `answer.clear()` 를 `continue` 뒤로 되돌리면 → `test_no_false_warning_when_the_graph_has_no_search_tools` **실패**(= 신규 테스트가 그 경로를 실제로 태운다).
- `answer.clear()` 이동의 배포 경로 영향 없음 `[검증]`: 실환경 재측정 — `"안녕"`·`userstore` 질문 모두 `search(search_confluence) → search(search_openapi) → token… → done`, **첫 search 이전 token 0건**, 전체 token 합 == 저장된 답변, `warning` 0건. `system_facts` 는 여전히 `search` 이벤트로 나가지 않는다. 통합 11 passed 로도 확인.
- 검색 도구 없는 구성 재측정: 프레임 `[('token','검색 결과 없이 답합니다.'), ('done','')]` — 최종 답변은 그대로 나가고 임시 답변의 가짜 URL 은 경고로도 새지 않는다(회차 1 minor 해소).
- 범위: `src/agent.py`·`main.py`·`resources/static/index.html` **diff 0** 재확인.

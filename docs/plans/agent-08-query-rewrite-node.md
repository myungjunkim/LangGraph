# agent-08: 후속 질문 질의 리라이팅 노드 (단계 ②)

- 작성일: 2026-09-26
- 작성자: 리드
- 상태: READY FOR REVIEW (2026-09-26, 리드 확인, 검증 회차 2) — 커밋 대기(사용자). 동작 변화 1건(P6 재검색) 사용자 인지 필요
- 요청 경로: 사용자 승인(다른 세션 `ailab_general_chatbot` 경유). 선행: agent-06(규칙 3, 커밋 `220b26c`), agent-07(도구 설명 — 효과 0, 롤백·종결).
- 저장소: `/Users/mjkim/workspace/LangGraph` 만. HEAD `220b26c`.

## 목표

후속 질문("방금 알려준 API의 v1이랑 v2 차이는?")에서 검색어가 앞 대화의 대상 없이 생성되는 케이스(메시지 등록 API, 누적 7회 재현)를 **그래프에 `rewrite` 노드를 추가**해 해결한다. 후속 턴에서만 LLM 1회로 "앞 대화 없이도 이해되는 독립 질문"을 만들어 agent 노드에 힌트로 전달한다. 첫 턴은 우회하고, 원래 메시지·체크포인터의 메시지 내용은 보존한다.

## 확인된 현재 상태 (`[검증]`)

- `src/agent.py`: `build_graph(chat_model, tools, checkpointer=None)`; 상태 `MessagesState`; 노드 `agent`(`[SystemMessage(SYSTEM_PROMPT)] + state["messages"]` 를 `bind_tools` 모델에 invoke), `tools`(`ToolNode(handle_tool_errors=True)`); 엣지 `START→agent`, `agent→tools_condition→tools|END`, `tools→agent`. `build_default_graph(settings)`, `RUNTIME_ERRORS`.
- `src/web/app.py` `stream_events`: `updates` 모드에서 `payload.get("agent")` 의 tool_calls → `search`; `messages` 모드에서 `meta["langgraph_node"] == "agent"` 이고 `AIMessage` 이고 content 비어 있지 않으면 `token`.
- `main.py` `run_turn`: `stream_mode="values"` 로 마지막 메시지만 봄(`AIMessage` tool_calls → `[검색]`, 아니면 답변).
- 테스트 헬퍼: `test/test_agent.py` `ScriptedChatModel`(`bind_tools` 가 self 반환, `_generate` 가 받은 메시지 기록, 응답은 순서대로 소비), `_graph(responses, client, checkpointer)`.
- 실측(agent-06/07): 프롬프트 규칙·도구 설명 모두 메시지 등록 케이스에서 무효. 1턴 답변에 경로가 명시돼 있어도 2턴 검색어 `v1과 v2의 차이`.

## 설계

### 그래프

```
START → rewrite → agent ─(tool_calls)→ tools → agent ─(없음)→ END
```

- `tools → agent` 엣지는 그대로(같은 턴의 도구 루프에서 rewrite 재실행 없음).
- 상태: `class AgentState(MessagesState): standalone_question: str` — `MessagesState` 를 확장. 체크포인터에 함께 저장되지만 **`messages` 내용은 불변**(rewrite 는 메시지를 추가하지 않는다).

### `rewrite` 노드

```python
REWRITE_PROMPT = """다음은 사용자와 어시스턴트의 대화입니다. 마지막 사용자 질문을 앞 대화 없이도 이해되도록 한 문장으로 다시 쓰세요.
규칙:
1. 앞 대화에서 다룬 대상(API 이름, 경로, 기능명, 문서 제목)을 질문에 명시합니다.
2. 대화에 나오지 않은 이름은 넣지 않습니다.
3. 질문의 의도는 바꾸지 않습니다.
4. 다시 쓴 질문 한 문장만 출력합니다. 설명·따옴표·접두어 없이."""

def rewrite(state: AgentState) -> dict:
    messages = state["messages"]
    humans = [m for m in messages if isinstance(m, HumanMessage)]
    if len(humans) < 2:
        return {"standalone_question": ""}          # 첫 턴: 우회 (LLM 호출 없음)
    history = [m for m in messages if isinstance(m, HumanMessage)
               or (isinstance(m, AIMessage) and not m.tool_calls and m.content)]
    # ToolMessage(청크 원문)와 tool_call 전용 AIMessage 는 제외 — 컨텍스트 절약. 마지막 원소가 이번 턴의 HumanMessage.
    response = chat_model.invoke([SystemMessage(REWRITE_PROMPT)] + history)
    return {"standalone_question": response.content.strip()}
```

- `chat_model` 은 `bind_tools` 하지 않은 원본(도구 호출이 나오면 안 됨). `build_graph` 인자로 받은 모델을 그대로 쓴다(별도 모델 인스턴스 없음).
- `history` 가 길어져도 자르지 않는다(`num_ctx` 16384, 대화가 수십 턴이 되기 전에는 문제 없음 — `[추측]`; 범위 제외에 기록).
- 응답이 빈 문자열이면 `""` 로 저장(힌트 없음 = 기존 동작).

### `agent` 노드 변경

```python
def agent(state: AgentState) -> dict:
    system = SYSTEM_PROMPT
    if state.get("standalone_question"):
        system += ("\n\n[이번 질문의 독립 표현] " + state["standalone_question"]
                   + "\n검색이 필요할 때 query 는 이 독립 표현을 기준으로 만듭니다. 앞 대화의 답변만으로 충분하면 검색하지 않아도 됩니다.")
        # 2026-09-26 (3) 개정: 초안 "검색 도구의 query 는 이 독립 표현을 기준으로 만듭니다." 가 재검색을 유도해 P6 회귀(E2)
    response = model_with_tools.invoke([SystemMessage(system)] + state["messages"])
    return {"messages": [response]}
```

- SystemMessage 는 여전히 상태에 저장하지 않는다.
- `standalone_question` 은 rewrite 가 **매 턴** 덮어쓰므로(첫 턴 `""`), 이전 턴의 힌트가 남지 않는다.

### `build_graph` 시그니처

변경 없음: `build_graph(chat_model, tools, checkpointer=None) -> CompiledStateGraph`. 내부에서 `StateGraph(AgentState)` 사용. `build_default_graph`, `RUNTIME_ERRORS` 불변.

### 웹/CLI 영향

- `stream_events`: rewrite 노드의 LLM 출력은 `messages` 모드로 흐르지만 `langgraph_node == "rewrite"` 라 `token` 으로 나가지 않는다(기존 필터). `updates` 모드의 `{"rewrite": {...}}` 는 `payload.get("agent")` 에 걸리지 않는다. **코드 변경 없음, 테스트로 고정(R4).**
- `run_turn`: `values` 모드에서 rewrite 후 상태의 마지막 메시지는 여전히 이번 턴 HumanMessage 라 출력 없음. 변경 없음.
- SSE 이벤트 계약·UI 불변. 리라이팅 결과를 사용자에게 보여주는 것은 범위 제외.

### 게이트 미달 시 대안 (사용자 재확인 후에만 전환 — 이번 티켓에서 구현하지 않음)

| 대안 | 내용 | 추가로 바뀌는 동작 |
|---|---|---|
| F1. 모델 입력 치환 | agent 노드가 모델에 넘기는 메시지 목록에서 **마지막 HumanMessage 만** `standalone_question` 으로 바꿔 전달. 체크포인터·상태의 메시지는 원문 유지 | 모델이 보는 질문 문장이 달라져 답변 어투·범위가 리라이팅 품질에 종속 |
| F2. 첫 도구 호출 query 강제 | agent 노드가 이번 턴 첫 응답의 tool_calls 중 `query` 를 `standalone_question` 으로 덮어씀(도구 루프 2회째부터는 모델 자유) | 모델의 검색어 선택권을 첫 호출에 한해 제거. 도구 2개 병렬 호출 시 둘 다 같은 query |

리드 선호 순서: F1 → F2. 어느 쪽이든 그래프 동작이 추가로 바뀌므로 사용자 확인 필수.

## 파일 변경

- `src/agent.py`: `AgentState`, `REWRITE_PROMPT`, `rewrite` 노드, `agent` 노드 힌트, 엣지 `START→rewrite→agent`.
- `test/test_agent.py`: R1~R3 append.
- `test/test_web.py`: R4 append.
- `test/test_integration_agent.py`: R5 (Validator — 기존 P4 테스트의 xfail 마크 제거 + 반복 게이트 확인, R6 지연 측정 기록).
- README: "구조" 표의 `src/agent.py` 설명에 rewrite 노드 1줄, 다이어그램 갱신.

## 작업 목록

1. [Builder] `src/agent.py` 구현 + `test/test_agent.py` R1~R3 + `test/test_web.py` R4 + README. `pytest -q` green. RAG 미기동.
2. [Validator] R1~R7.

## 검증 전략

단위 테스트는 `ScriptedChatModel` 을 쓴다. 응답 큐 순서: 후속 턴에서는 **rewrite 응답이 먼저 소비**되고 그다음 agent 응답. `model.received` 로 각 호출의 입력을 검사한다.

| 항목 | 확인 방법 |
|---|---|
| R1. 첫 턴 우회 | 새 thread 1턴: 모델 호출이 agent 1회뿐(`model.received` 길이 = 1, 첫 메시지가 `SYSTEM_PROMPT` 로 시작), `state["standalone_question"] == ""` |
| R2. 후속 턴 리라이팅 | 같은 thread 2턴(`InMemorySaver`): 첫 호출 입력 = `[SystemMessage(REWRITE_PROMPT), Human1, AI1, Human2]` (ToolMessage·tool_call AIMessage 제외 확인 — 1턴에 tool_call 이 있었던 시나리오로), 응답 "메시지 등록 API의 v1과 v2 차이" → 두 번째(agent) 호출의 SystemMessage 가 `SYSTEM_PROMPT` 로 시작하고 `[이번 질문의 독립 표현] 메시지 등록 API의 v1과 v2 차이` 를 포함. 상태 `messages` 에 SystemMessage·리라이팅 결과가 **추가되지 않음**(종류 순서가 Human/AI/Tool/AI/Human/AI 등 기존과 동일) |
| R3. 힌트 덮어쓰기·빈 응답 | 3턴째 rewrite 응답 `""` → agent SystemMessage 에 `[이번 질문의 독립 표현]` 없음. 2턴 힌트가 3턴에 남지 않음 |
| R4. SSE 격리 | `test_web.py`: 후속 턴 스트림에서 rewrite 응답 문자열이 `token` 이벤트에 **없음**, `search`/`token`/`done` 순서·`token` 합 == 최종 답변은 기존과 동일. `updates` 의 rewrite 갱신이 `search` 로 오인되지 않음 |
| R5. **통합 게이트** (Validator, RAG 띄우고 내림) | gpts·메시지 등록 두 케이스 각 **3회 연속**, 2턴 검색어에 **대상 포함** 3/3 × 2, 다른 케이스 이름 혼입 0/6. "대상 포함" 판정은 대화에 등장한 표기 중 하나라도 있으면 충족(대소문자 무시): gpts 케이스 `{"gpts"}`, 메시지 케이스 `{"메시지 등록", "message"}`(1턴 답변에 `Message Management API`, `/v1/messages/message` 가 등장 — 2026-09-26 (3)). 오염 판정은 다른 케이스 표기(`gpts` ↔ `메시지`/`message`) 미포함. PASS 시 `test_integration_agent.py` 메시지 케이스 `xfail` 마크 제거 후 `pytest -m integration -q` → failed 0, xfailed 0. 회차별 검색어 원문 + **`standalone_question` 원문** 기록 |
| R6. 지연 측정 (Validator) | 후속 턴에서 rewrite 노드 소요 시간을 실측(`graph.astream(stream_mode="updates")` 로 `rewrite` 갱신 도착 시각 − 턴 시작 시각, 또는 probe 에서 `time.perf_counter`). 3회 평균·최대를 검증 기록에 적는다. 판정 기준 없음(정보) |
| R7. 기존 회귀 | `pytest -q` green; `pytest -m integration -q` 기존 3(agent-01 1, W7 2) + P6 PASS. P6("그 API 필수 필드는?")에서 도구 재호출이 생기더라도 검색어가 R5 와 같은 표기 집합(`메시지 등록`/`message`)을 포함하면 PASS. 재호출 여부·답변 길이를 기록해 힌트 개정 전후를 비교한다 |

## 완료 기준

- [ ] R1~R7 통과 (R5 3/3 × 2, 오염 0)
- [ ] `build_graph` 시그니처·SSE 계약·UI·`run_turn` 출력 불변
- [ ] 체크포인터 `messages` 에 rewrite 산출물 미저장
- [ ] R6 지연 수치 기록
- [ ] 커밋하지 않음(사용자)

## 범위 제외

- F1/F2 대안 구현(게이트 미달 시 사용자 확인 후 별도)
- 리라이팅 결과를 CLI/웹에 표시
- `history` 길이 제한·요약
- 리라이팅 전용 소형 모델 사용(같은 `qwen3:14b` 사용)
- `standalone_question` 을 근거로 한 답변 품질 평가

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| 리라이팅을 항상 실행(첫 턴 포함) | 첫 턴은 리라이팅 대상이 없어 호출 낭비 |
| 리라이팅 결과를 HumanMessage 로 상태에 추가 | 체크포인터 대화 기록이 오염되고 UI 의 멀티턴 표시와 어긋남. 힌트는 상태 필드로 |
| 리라이팅 입력에 ToolMessage 포함 | 청크 원문이 길어 컨텍스트 낭비. 답변(AIMessage)에 이미 경로가 요약돼 있음 |
| agent-07 재시도(프롬프트 문구 변형) | 2회 무효 — 리드 판단 기록 agent-07 (3)-(c) |

## 리드 판단 기록

- 2026-09-26 (1): 사용자 승인(다른 세션 경유)으로 착수. 사용자 요청 반영: 힌트 방식 1차, 미달 시 대안 F1/F2 를 명세에 선기재하고 전환 전 재확인; 지연 실측(R6); SSE 격리 테스트(R4).
- 2026-09-26 (2): Builder 작업 1 완료 — 4개 파일, 138 passed, SSE/CLI/도구/통합 테스트 diff 0. **기존 테스트 2건 갱신 승인**: ① `test_graph_nodes_and_edges_match_design` 의 `START→agent` 단언을 `START→rewrite`, `rewrite→agent` 로 갱신 + `START→agent` 부재 단언 추가(토폴로지 변경에 직결, 강화 방향). ② `test_checkpointer_keeps_previous_turn` 은 후속 턴에서 rewrite 가 응답 큐를 먼저 소비하므로 큐에 1건 추가·인덱스 조정(단언 내용 동일). 둘 다 설계 변경의 필연적 결과로 명세 R2 와 정합. 리드가 Validator 스폰.
- 2026-09-26 (3): **Validator 회차 1 ESCALATE E1·E2 접수.** `[검증]` 리라이팅 자체는 동작: 메시지 케이스 `standalone_question` = "방금 알려준 Message Management API의 v1과 v2 버전 차이는 무엇인가요?", 검색어 `Message Management API v1과 v2의 차이`(3/3) — agent-06/07 의 `v1과 v2의 차이`(대상 전무)에서 벗어나 1턴 답변에 등장한 서비스명을 포함. gpts 3/3, 오염 0/6, rewrite 지연 평균 1.21s·최대 1.76s. **E1 결정**: 게이트 리터럴 `메시지 등록` 은 리드가 너무 좁게 잡은 것 — 목표는 "앞 대화의 대상 포함" 이므로 대화에 등장한 표기(`메시지 등록`/`message`)로 재정의(R5 갱신). F1/F2 전환 불필요(사용자 재확인 대상 아님). **E2 결정**: P6("그 API 필수 필드는?")가 재검색(`/v1/messages/message 필수 필드`, 답변 938→291자)으로 바뀐 것은 이번 변경의 회귀. 원인 `[추측]` 힌트 문장 "검색 도구의 query 는 이 독립 표현을 기준으로 만듭니다" 가 검색을 유도. 힌트를 "검색이 필요할 때 … 앞 대화의 답변만으로 충분하면 검색하지 않아도 됩니다" 로 개정(agent 노드 절). P6 기준도 R5 와 같은 표기 집합으로 정렬. Builder 재작업(힌트 1문장) → Validator 회차 2. 회차 2 에서 P6 가 여전히 재검색하더라도 검색어가 대상을 포함하면 PASS 로 두되, 답변 길이 변화를 기록해 사용자 보고에 포함한다. REWRITE_PROMPT 에 언어 유지 규칙은 추가하지 않음(근거 부족, 게이트 재정의로 해소).
- 2026-09-26 (4): Builder 재작업 완료 — 힌트 문장 개정, 완전 일치 단언 1곳 갱신, 144 passed, 구 문구 잔존 0. Validator 회차 2 스폰(R5 재정의 기준·P6 재확인·답변 길이 비교).
- 2026-09-26 (5): Validator 회차 2 READY FOR REVIEW 접수. 단위 144 passed / 통합 6 passed, failed 0, xfailed 0(메시지 케이스 xfail 제거). R5 gpts 3/3·메시지 3/3·오염 0/6, R6 rewrite 지연 평균 1.20s·최대 1.69s, R7 기존 3건 회귀 없음. **결론: READY FOR REVIEW.** 단, `[검증]` 힌트 개정은 P6 동작을 바꾸지 못함 — "그 API 필수 필드는?" 이 baseline(재검색 없음, 938자)과 달리 재검색 1회(`/v1/messages/message 필수 필드`, 291자)로 답함(3/3 재현). 검색어는 대상을 정확히 포함하므로 근거는 유지되며 답변이 짧아진 것이 품질 저하인지는 `[미확인]`(필수 필드만 답한 것일 수 있음). 이 동작 변화를 사용자에게 보고하고 수용 여부를 맡긴다. 수용 불가 시 후보: 힌트를 SystemMessage 가 아닌 rewrite 결과가 "검색이 필요한가" 까지 판단하게 하는 방식 — 별도 티켓. agent-07 실험 문서는 이번 커밋에 함께 포함 권장.
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

(Validator 가 기록)

- 검증 회차: 1 (Validator, 2026-09-26). 환경: `.venv`, Ollama 11434(기존 기동 — `curl /api/tags` 로 `qwen3:14b` 확인만), RAG 5010(Validator 가 기동 후 종료). HEAD `220b26c` + 미커밋 변경.

| 항목 | 결과 | 증거 |
|---|---|---|
| R1. 첫 턴 우회 | PASS | `test_first_turn_skips_rewrite`(`test/test_agent.py:218`): `len(model.received) == 1`, `received[0][0].content.startswith(SYSTEM_PROMPT)`, `state["standalone_question"] == ""`. 통과 |
| R2. 후속 턴 리라이팅 | PASS | `test_followup_turn_rewrites_and_passes_hint_to_agent`(`test/test_agent.py:229`): rewrite 입력이 `[SystemMessage(REWRITE_PROMPT), Human1, AI1, Human2]`(ToolMessage·tool_call AIMessage 제외 확인), agent SystemMessage 가 `SYSTEM_PROMPT` 로 시작 + 힌트 포함, 상태 `messages` 종류 순서 `Human/AI/Tool/AI/Human/AI` 유지. 보강: `test_agent_receives_original_question_text_not_the_rewritten_one`(마지막 HumanMessage 원문 유지·힌트 문장 전문 일치), `test_standalone_question_is_checkpointed_without_touching_messages` |
| R3. 힌트 덮어쓰기·빈 응답 | PASS | `test_empty_rewrite_leaves_no_hint_and_does_not_reuse_previous_turn`(공백만 응답), 보강 `test_exactly_empty_rewrite_response_adds_no_hint`(빈 문자열): 3턴 agent SystemMessage `== SYSTEM_PROMPT`, `standalone_question == ""` |
| R4. SSE 격리 | PASS | `test_rewrite_output_is_not_streamed_as_token`, `test_rewrite_turn_keeps_search_then_token_order`(`test/test_web.py:357,380`): rewrite 문자열이 `token` 에 없음, `token` 합 == 최종 답변, `search` 는 agent tool_call 만, `search → token → done` 순서 유지 |
| R5. **통합 게이트** | **FAIL — 메시지 등록 0/3(리터럴 `메시지 등록` 기준), gpts 3/3, 오염 0/6** | 아래 회차별 표. 게이트 미달로 `xfail` 마크 제거하지 않음(`test/test_integration_agent.py` 무변경). ESCALATE E1 |
| R6. 지연 측정 | 기록 | `astream(stream_mode="updates")` 의 `rewrite` 갱신 도착 시각 − 턴 시작 시각. 6회(케이스별 3회): 평균 **1.21초**, 최대 **1.76초**, 최소 0.81초. gpts 1.50/1.51/0.86, 메시지 1.76/0.81/0.81. 같은 턴 전체 소요는 gpts 평균 13.2초, 메시지 평균 32.8초 |
| R7. 기존 회귀 | **부분 FAIL** | `pytest -q` → **144 passed, 6 deselected**(Builder 138 + Validator 보강 6). `pytest -m integration -q -rxX` → **1 failed, 4 passed, 1 xfailed**(agent-01 1 PASS, W7 2 PASS `[W7] token 295건, search 1건, 답변 869자`, P4-gpts PASS, P4-메시지 xfail, **P6 FAIL**). ESCALATE E2 |

R5 실측(각 회차 새 `thread_id`, 1턴 "<대상> 알려줘" → 2턴 "방금 알려준 API의 v1이랑 v2 차이는 뭐야?", 스크래치패드 probe, `src` 무변경):

| 회차 | 케이스 | `standalone_question` 원문 | 2턴 tool_call query 원문 | 판정 |
|---|---|---|---|---|
| 1 | gpts 등록 API | `방금 알려준 GPTs 등록 API의 v1과 v2 차이는 무엇인가요?` | `gpts 등록 API v1 v2 차이` | PASS (대상 `gpts` 포함, `메시지` 오염 없음) |
| 1 | 메시지 등록 API | `방금 알려준 Message Management API의 v1과 v2 버전 차이는 무엇인가요?` | `Message Management API v1과 v2의 차이` | FAIL (게이트 키워드 `메시지 등록` 미포함, `gpts` 오염 없음) |
| 2 | gpts 등록 API | `방금 알려준 GPTs 등록 API의 v1과 v2 차이는 무엇인가요?` | `gpts 등록 API v1 v2 차이` | PASS |
| 2 | 메시지 등록 API | `방금 알려준 Message Management API의 v1과 v2 버전 차이는 무엇인가요?` | `Message Management API v1과 v2의 차이` | FAIL |
| 3 | gpts 등록 API | `방금 알려준 GPTs 등록 API의 v1과 v2 차이는 무엇인가요?` | `gpts 등록 API v1 v2 차이` | PASS |
| 3 | 메시지 등록 API | `방금 알려준 Message Management API의 v1과 v2 버전 차이는 무엇인가요?` | `Message Management API v1과 v2의 차이` | FAIL |

- 6회 모두 2턴 도구는 `search_openapi` 1회. 다른 케이스 이름 혼입 **0/6**(gpts 검색어에 `메시지` 없음, 메시지 검색어에 `gpts` 없음).
- `[검증]` 메시지 케이스의 검색어는 agent-06/07 의 `v1과 v2의 차이`(대상 전무, 누적 7회)에서 벗어나 **대상을 명시**하게 바뀌었다. `Message Management API` 는 1턴 답변 원문에 있는 서비스명(`- **서비스**: Message Management API (message-api)`)이라 REWRITE_PROMPT 규칙 2(대화에 없는 이름 금지)를 위반하지 않는다. 실패한 것은 테스트의 리터럴 키워드(`메시지 등록`) 일치뿐이다.
- `[검증]` 참고(판정 미반영): RAG `/v1/search`(source=openapi, top_k=6) 직접 호출 시 `Message Management API v1과 v2의 차이` 는 `GET/POST /v1·/v2/messages/message` 4건을 상위에 반환해, `메시지 등록 API v1과 v2의 차이` 와 검색 품질 차이가 관측되지 않았다.

### ESCALATE E1 — R5 게이트가 리터럴 키워드에서만 미달한다 (major, 리드 판단 필요)

- 구현은 명세와 일치한다(R1~R4 PASS, `build_graph` 시그니처·SSE·CLI·체크포인터 불변). 구현 결함 증거는 없다.
- 게이트 문구는 "2턴 검색어에 대상 포함"이고 테스트 단언은 `"메시지 등록" in q.lower()` 다. 실측은 "대상은 포함하되 1턴 답변에 쓰인 영문 서비스명" → 기준 해석에 따라 PASS/FAIL 이 갈린다. Validator 는 기준을 임의로 바꾸지 않고 리터럴 기준으로 FAIL 판정했다.
- 선택지: ① 게이트 키워드를 대화에 등장한 대상 표기(`메시지`/`messages`/`message management` 등)로 재정의 ② F1(모델 입력 치환) ③ F2(첫 tool_call query 강제) — ②③ 은 명세대로 사용자 확인 필요.

### ESCALATE E2 — P6(B8 멀티턴) 회귀: rewrite 도입 후 2턴 도구 재호출이 생기고 검색어가 경로로 바뀐다 (major, 신규 실패)

- 재현: `pytest -m integration -q` → `test_followup_question_reuses_context_without_losing_the_api_name` FAIL. `queries=['/v1/messages/message 필수 필드']`.
- probe 3/3 동일 재현: `standalone_question` = `메시지 등록 API의 필수 필드는 무엇인가요?`(대상 포함, 한국어), 그런데 agent 의 tool_call query 는 `/v1/messages/message 필수 필드`(리터럴 `메시지 등록` 없음), 최종 답변 291자.
- `[검증]` 신규 실패다. 같은 RAG·Ollama 환경에서 커밋 `220b26c` 시점 그래프(rewrite 노드 없음, 스크래치패드에서 동일 부품으로 재구성, `src` 무변경)로 2회 실행 시 2턴 도구 재호출 없음(`queries=[]`, 답변 938자 — agent-06 회차 3 기록과 동일 수치). rewrite 힌트가 "재검색해도 된다"는 신호로 작용한 것으로 보인다(`[추측]`).
- 즉 rewrite 노드는 후속 턴 검색어에서 대상 누락은 없앴지만, 기존에 재검색하지 않던 케이스를 재검색으로 바꾸고 답변 분량을 줄였다. 게이트 조정(E1)과 함께 판단이 필요하다.

Validator 추가 테스트(모두 append, 기존 단언 수정 없음): `test/test_agent.py` 5건(`test_rewrite_uses_the_model_without_tools_bound`, `test_agent_receives_original_question_text_not_the_rewritten_one`, `test_exactly_empty_rewrite_response_adds_no_hint`, `test_build_graph_public_signature_is_unchanged`, `test_standalone_question_is_checkpointed_without_touching_messages`), `test/test_main.py` 1건(`test_run_turn_output_is_unchanged_on_followup_turn`).

Builder 가 갱신한 기존 2건 확인: `test_graph_nodes_and_edges_match_design` 은 `START→agent` 부재 단언이 추가돼 강화됐고, `test_checkpointer_keeps_previous_turn` 은 인덱스만 1→2 로 옮겼을 뿐 단언 내용이 동일하다 — 약화 없음.

**판정: ESCALATE TO LEAD (E1 + E2).**

### 회차 2 (2026-09-26, 힌트 문장 개정 + R5 기준 재정의 후)

- 검증 회차: 2. 환경 동일(`.venv`, Ollama 11434 확인만, RAG 5010 Validator 가 기동 후 종료). `src/agent.py` 는 Builder 의 힌트 2문장 개정 반영본.

| 항목 | 결과 | 증거 |
|---|---|---|
| R1~R4 (단위) | PASS | `pytest -q` → **144 passed, 6 deselected**. 힌트 문구 개정의 영향은 완전 일치 단언 1곳(`test_agent_receives_original_question_text_not_the_rewritten_one`, `test/test_agent.py:323`)뿐이며 Builder 가 신문구로 갱신 — 단언 범위(접두어 + 두 문장 전문 포함, `HINT_PREFIX` 1회) 는 그대로라 약화 없음. R1(첫 턴 우회)·R2(입력 구성·messages 불변)·R3(빈 응답·덮어쓰기)·R4(SSE 격리) 단언은 문구 비의존이라 무변경 |
| R5. 통합 게이트 (재정의 기준) | **PASS — gpts 3/3, 메시지 등록 3/3, 오염 0/6** | 아래 회차 2 표. `xfail` 마크 제거 완료 |
| R6. 지연 측정 | 기록 | rewrite 갱신 도착까지 6회: 평균 **1.20초**, 최대 **1.69초**, 최소 0.82초(gpts 1.51/1.49/0.86, 메시지 1.69/0.82/0.82). 회차 1(평균 1.21·최대 1.76)과 사실상 동일 |
| R7. 기존 회귀 | PASS | `pytest -q` 144 passed / `pytest -m integration -q -rxX` → **6 passed, 144 deselected, failed 0, xfailed 0** (agent-01 1, W7 2 `[W7] token 295건, search 1건, 답변 869자`, P4 2, P6 1) |
| P6 (R7 세부) | PASS(기준 충족) / 동작은 회차 1과 동일 | `[P6] 2턴 검색어 ['/v1/messages/message 필수 필드'], 답변 291자`. probe 3/3 동일 재현(`standalone_question` = `메시지 등록 API의 필수 필드는 무엇인가요?`). **힌트 2문장 개정 후에도 재검색은 사라지지 않았다** — 회차 1(재검색, 291자)과 글자·길이까지 동일. PASS 는 검색어에 `message` 표기가 있어 재정의된 기준을 충족했기 때문 |

R5 실측 회차 2(각 회차 새 `thread_id`, 1턴 "<대상> 알려줘" → 2턴 "방금 알려준 API의 v1이랑 v2 차이는 뭐야?"):

| 회차 | 케이스 | `standalone_question` 원문 | 2턴 tool_call query 원문 | 대상 포함 | 오염 |
|---|---|---|---|---|---|
| 1 | gpts 등록 API | `방금 알려준 GPTs 등록 API의 v1과 v2 차이는 무엇인가요?` | `gpts 등록 API v1 v2 차이` | O (`gpts`) | 없음 |
| 1 | 메시지 등록 API | `방금 알려준 Message Management API의 v1과 v2 버전 차이는 무엇인가요?` | `Message Management API v1과 v2의 차이` | O (`message`) | 없음 |
| 2 | gpts 등록 API | 동일 | `gpts 등록 API v1 v2 차이` | O | 없음 |
| 2 | 메시지 등록 API | 동일 | `Message Management API v1과 v2의 차이` | O | 없음 |
| 3 | gpts 등록 API | 동일 | `gpts 등록 API v1 v2 차이` | O | 없음 |
| 3 | 메시지 등록 API | 동일 | `Message Management API v1과 v2의 차이` | O | 없음 |

- 6회 모두 2턴 도구는 `search_openapi` 1회. 출력은 회차 1과 문자열까지 동일(temperature 0, 힌트 개정의 영향 없음).
- 테스트 변경(Validator): `test/test_integration_agent.py` 의 P4 parametrize 를 `(first, subject, foreign)` 로 갱신 — `GPTS_SUBJECT = ("gpts",)`, `MESSAGE_SUBJECT = ("메시지 등록", "message")`, 오염은 다른 케이스 표기(`("메시지", "message")` ↔ `("gpts",)`) 미포함. 단언 구조는 ① tool_call ≥ 1 ② 오염 없음 ③ 대상 표기 중 하나 포함(모든 query 가 충족) 로 회차 1과 동일하며, ③ 만 리터럴 1개 → 표기 집합으로 재정의됐다. 메시지 케이스 `xfail` 마크 삭제. P6 단언도 같은 집합(`MESSAGE_SUBJECT`)으로 정렬.

`[검증]` 답변 길이 비교(사용자 보고용): P6 2턴 답변은 rewrite 노드 도입 전 938자(재검색 없음, 앞 턴 답변 재사용) → 도입 후 291자(재검색 1회, 검색 결과 기반 요약). 힌트 개정으로는 되돌아가지 않았다. 게이트상 결함은 아니지만 후속 턴의 답변 분량·근거 출처가 바뀌는 동작 변화로 남는다.

**판정: READY FOR REVIEW** (완료 기준 R1~R7 충족, 단위 144 passed, 통합 6 passed / failed 0 / xfailed 0. 커밋하지 않음).

# agent-14: 현재 시각 주입 + 근거 없는 추측 억제

- 작성일: 2026-09-29
- 작성자: 리드
- 상태: 종결 — 사용자 커밋·push 완료 (2026-09-29). 최종 산출물은 "현재 시각 주입" 하나
- 계기: 사용자가 "오늘 몇월 몇일이야?" 라고 묻자 에이전트가 **"2026년 1월 31일"** 이라고 답함(실제 2026-09-29). 모델에 현재 시각이 주어진 적이 없어 지어낸 값이다.
- 저장소: `/Users/mjkim/workspace/LangGraph` 만.

## 목표

1. 에이전트에게 **현재 시각을 사실로 제공**해 날짜·시각 질문에 추측하지 않게 한다.
2. 프롬프트의 "일반 상식 질문은 예외" 라는 우회로를 좁혀, 문서에서 확인되지 않은 사실을 지어내지 않게 한다.

## 확인된 현재 상태 (`[검증]` 리드가 코드 직접 확인)

- `src/agent.py` `SYSTEM_PROMPT` 규칙 8개(agent-13 이 규칙 4 삽입). **규칙 1 에 우회로가 있다**: `"답하기 전에 반드시 검색 도구로 근거를 찾습니다. 인사말이나 일반 상식 질문은 예외입니다."` → 날짜 질문이 "일반 상식" 으로 분류돼 근거 없이 답하는 경로로 빠진다. agent-01 에서 리드가 넣은 문구다.
- agent 노드는 `system = SYSTEM_PROMPT` 로 시작해, `standalone_question` 이 있으면 `HINT_PREFIX` 블록을 **뒤에** 붙인다(agent-08). SystemMessage 는 상태에 저장하지 않고 호출 때마다 만든다.
- `rewrite` 노드는 `REWRITE_PROMPT` 를 따로 쓴다.
- `[검증]` 프롬프트 지시만으로 모델 행동을 바꾸려던 시도가 이 프로젝트에서 **두 번 실패**했다(agent-06 규칙 3 → 일부 케이스 0/3, agent-07 도구 설명 → 0/3). 따라서 이번에도 **프롬프트 규칙은 보조**로 두고, 결정적 해결은 "사실을 맥락에 넣는 것" 으로 한다.

## 설계

### A. 현재 시각 주입 (결정적 해결)

`src/agent.py` 에 추가:

```python
WEEKDAYS = "월화수목금토일"

def current_time_line(now: datetime | None = None) -> str:
    """현재 시각 한 줄. 로컬 타임존 기준.
    예) '현재 시각: 2026-09-29 (화) 14:23 KST'
    """
    moment = now or datetime.now().astimezone()
    # 요일은 로케일에 의존하지 않도록 직접 매핑한다(%a 는 환경에 따라 'Tue' 가 된다)
    # 타임존 이름이 비면 UTC 오프셋(+09:00)으로 대체한다
```

agent 노드에서 SystemMessage 를 만들 때 **맨 앞에** 붙인다:

```
현재 시각: 2026-09-29 (화) 14:23 KST

{SYSTEM_PROMPT}

{HINT_PREFIX}... (agent-08 힌트, 있을 때만)
```

- **매 턴 새로 계산**한다(SystemMessage 를 상태에 저장하지 않는 기존 설계 덕분에 자동).
- 이 값은 지시가 아니라 **맥락에 담긴 사실**이므로, 모델이 규칙을 따르든 말든 날짜 질문의 답이 이미 화면 안에 있다. 프롬프트 의존도가 낮은 것이 이 방식을 고른 이유다.

### B. 프롬프트 규칙 손질 (보조) — **2026-09-29 전면 철회. 아래는 이력 보존용이며 구현하지 않는다(리드 판단 기록 (6)(7))**

정확히 두 곳만 바꾼다.

1. **규칙 1 의 우회로 축소**
   - 현재: `인사말이나 일반 상식 질문은 예외입니다.`
   - 변경: `인사말처럼 정보가 필요 없는 말만 예외입니다.`
2. **규칙 추가** — 기존 규칙 5(`검색 결과에 없는 내용은 추측하지 않습니다…`) 바로 뒤에 새 규칙 6 을 넣고 이후 번호를 +1 한다(총 9개).
   ```
   6. 문서에서 확인되지 않은 사실(날짜·수치·이름·사내 현황)은 지어내지 않습니다.
      위에 주어진 현재 시각처럼 확실히 제공된 정보만 그대로 쓰고, 그 밖에 모르는 것은 모른다고 답합니다.
   ```

   **2026-09-29 개정(리드 판단 기록 (5))** — 위 초안은 규칙 3(앞 대화의 대상 이름을 검색어에 넣어라)과 충돌한다. 아래 문구로 교체한다:
   ```
   6. 문서나 위에 주어진 정보로 확인되지 않은 사실(날짜·수치·현황)은 지어내지 않습니다.
      모르는 것은 모른다고 답합니다. 앞 대화에 이미 나온 이름·경로는 확인된 정보이므로 규칙 3 대로 그대로 사용합니다.
   ```

**그 외 규칙 문구는 한 글자도 바꾸지 않는다.** 특히 agent-06 규칙 3(후속 질문 독립 검색어)과 agent-13 규칙 4(call_api 사용 조건)는 번호만 이동한다. `REWRITE_PROMPT` 는 건드리지 않는다.

### 변경하지 않는 것

`build_graph` 시그니처, 그래프 구조, `rewrite` 노드·`REWRITE_PROMPT`, 도구 계약, SSE 이벤트, CLI 출력, 설정 스키마, 의존성. 상태(`messages`)에 SystemMessage·시각을 저장하지 않는 기존 계약도 유지.

### 허용하는 기존 테스트 수정

- `SYSTEM_PROMPT` 규칙 **번호**를 단언하는 테스트가 있으면 새 번호로 갱신.
- 규칙 1 의 "일반 상식" 문구를 단언하는 테스트가 있으면 새 문구로 갱신.
- agent 노드의 SystemMessage 가 `SYSTEM_PROMPT` 로 **시작**한다고 단언하는 테스트가 있으면 "포함" 으로 완화(앞에 시각 줄이 붙으므로). **힌트가 맨 뒤라는 단언(agent-08)은 유지**해야 한다.

그 외 수정이 필요해 보이면 ESCALATE.

## 작업 목록

1. `current_time_line` 추가 + agent 노드 주입. 검증: T1~T4.
2. 프롬프트 규칙 2곳 수정. 검증: T5.
3. README "동작" 관련 설명에 한 줄(에이전트는 현재 시각을 안다) — 기존 절에 문장 추가 수준. 검증: T8.

## 검증 전략

| 항목 | 확인 방법 |
|---|---|
| T1. 시각 문자열 | `current_time_line(datetime(2026,9,29,14,23, tzinfo=KST))` → `현재 시각: 2026-09-29 (화) 14:23 KST`. 요일 매핑 7종(월~일) 확인. 타임존 이름이 없는 tzinfo → UTC 오프셋 표기로 대체 |
| T2. 주입 위치 | 가짜 모델이 받은 SystemMessage 가 **`현재 시각: ` 로 시작**하고, 그 뒤에 `SYSTEM_PROMPT` 전문이 있고, 힌트가 있으면 **맨 뒤**에 온다(agent-08 순서 보존) |
| T3. 매 턴 갱신 | `current_time_line` 을 monkeypatch 해 1턴과 2턴에 다른 값을 주면 **각 턴의 SystemMessage 에 각각 그 값**이 들어감. 앞 턴 시각이 남지 않음 |
| T4. 상태 미오염 | 2턴 실행 후 `state["messages"]` 종류 순서가 기존과 동일하고, 어떤 메시지 본문에도 `현재 시각:` 이 없음(SystemMessage 미저장 계약 유지) |
| T5. 프롬프트 | **B 철회에 따라 기준 변경** — `SYSTEM_PROMPT` 가 **agent-13 상태 그대로**여야 한다: 규칙 1 이 `인사말이나 일반 상식 질문은 예외입니다.`, 규칙 번호 1~8(9 부재), 새 규칙 6 부재, agent-06 규칙 3(`독립 검색어`)·agent-13 규칙 4(`call_api`)·`REWRITE_PROMPT` 원문 보존. 그리고 `SYSTEM_PROMPT` 상수 자체에는 `현재 시각` 이 들어 있지 않아야 한다(주입은 agent 노드에서만) |
| T6. **통합 — 날짜 (게이트)** | RAG·Ollama 필요. 새 thread 로 `"오늘 몇월 몇일이야?"` **3회**. 각 답변에 **실행 시점의 연·월·일이 포함**되어야 한다(형식은 자유: `2026-09-29`/`9월 29일` 등 → 연/월/일 숫자 존재로 판정). **3/3 이어야 PASS.** 미달 시 ESCALATE(리드가 주입 위치·형식 조정). 답변 원문을 검증 기록에 적는다 |
| T7. 통합 — 모르는 사실 (기록, 게이트 아님) | `"우리 회사 직원 수가 몇 명이야?"` **3회**. "찾지 못했" / "모른" 류 응답 횟수를 기록. `[추측]` 프롬프트 의존이라 확률적 — **판정에 반영하지 않고 수치만 남긴다** |
| T8. 회귀·범위 | `pytest -q` green. 기존 통합 전부 PASS. **agent-06 P4 "메시지 등록" 케이스는 2026-09-29 회귀가 확인됐으므로 3회 연속 대상 유지를 재확인한다**(gpts 케이스·P6·agent-13 `call_api` 포함). `git diff --stat HEAD` 가 `src/agent.py`·`test/test_agent.py`·`test/test_integration_agent.py`·`README.md`·이 명세뿐. 다른 `src/**` diff 0 |

## 완료 기준

- [ ] T1~T5, T8 통과. T6 **3/3**. T7 은 수치 기록
- [ ] 주입은 매 턴 갱신되고 상태에 저장되지 않음
- [ ] agent-06 규칙 3·agent-13 규칙 4·`REWRITE_PROMPT` 문구 불변
- [ ] 기존 테스트 수정은 허용 3종뿐
- [ ] 새 의존성 0, 설정 변경 0
- [ ] 커밋하지 않음(사용자)

## 범위 제외

- `rewrite` 노드에 시각 주입 — 후속 질문의 상대 표현("어제")은 agent 노드가 해석하므로 불필요
- 사용자별 타임존 설정(서버 로컬 타임존 사용)
- 시각 조회 도구(`current_time()`) — 주입이 항상 가능하므로 도구가 필요 없다
- 환각 전반의 정량 평가 — "답변 품질 평가 세트" 티켓에서 다룬다(이번 사례가 그 첫 문항 후보)
- 상대 날짜 계산의 정확성 보장("3주 전 화요일" 등)

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| 프롬프트에 "날짜를 모르면 모른다고 해라" 만 추가 | 모른다고 답하는 게 최선이 아니다. 시각은 **제공할 수 있는 사실**이다. 또 `[검증]` 프롬프트만으로 행동을 바꾸려던 시도가 이 프로젝트에서 두 번 실패했다 |
| `current_time()` 도구 추가 | 모델이 그 도구를 부를지에 또 의존한다. 주입은 호출 없이 항상 유효하고 턴당 비용도 0 |
| HumanMessage 에 시각을 덧붙이기 | 사용자 발화가 오염되고 체크포인터에 남는다. SystemMessage 는 저장되지 않아 깨끗하다 |
| 시각을 상태 필드로 저장 | 저장할 이유가 없다. 매 턴 계산이 더 정확하다(체크포인터에 든 옛 시각이 되살아나지 않음) |
| 규칙 1 의 예외를 통째로 삭제 | "안녕" 에도 검색을 돌리게 된다. 인사말 예외는 남긴다 |

## 리드 판단 기록

- 2026-09-29 (1): 사용자 보고로 착수. **규칙 1 의 "일반 상식 질문은 예외" 는 agent-01 에서 리드가 넣은 문구이며 이번 환각의 통로였다** — 설계 결함으로 기록한다.
- 2026-09-29 (2): 해결을 A(사실 주입)와 B(규칙)로 나누고, **게이트는 A 에만 건다**(T6). 근거: agent-06·agent-07 에서 프롬프트 지시가 특정 케이스에 0/3 으로 무효였던 실측. B 의 효과는 T7 로 수치만 남기고 판정에 넣지 않는다.
- 2026-09-29 (3): 요일은 로케일 비의존으로 직접 매핑한다. `%a` 는 환경에 따라 `Tue` 가 되어 출력이 흔들린다.
- 2026-09-29 (4): Builder 작업 1~3 완료 보고 — 421 passed, 변경은 `src/agent.py`·`test/test_agent.py`·`test/test_api_tool.py`(1줄)·`README.md`. T1~T5·T8 전부 Builder 실행 확인. 기존 테스트 수정은 허용 범위 내 6곳(“`SYSTEM_PROMPT` 로 시작” → “포함” 완화 5곳 + 규칙 번호 1곳)이며, 첫 번째에는 `startswith("현재 시각: ")` 단언을 새로 넣어 **오히려 강화**했고 agent-08 의 "힌트가 맨 뒤" 단언은 전부 유지 — 승인. 리드가 Validator 스폰(T6 게이트·T7 기록).
- 2026-09-29 (5): **Validator ESCALATE 접수 — T6 는 3/3 PASS 이나 agent-06 P4(메시지 등록)가 회귀.** `[검증]` Validator 귀속 실험: 시각 주입만 → 2/2 통과, 프롬프트 변경만 → 2/2 통과, **둘 다 적용 → 0/5 실패**(검색어가 `v1과 v2의 차이` 로 대상 누락). `standalone_question` 은 대상을 유지하므로 rewrite 노드가 아니라 **agent 노드 시스템 프롬프트 쪽 영향**.
  - **리드 분석**: 새 규칙 6 의 금지 목록에 **"이름"** 이 들어 있어 규칙 3(앞 대화의 **대상 이름**을 검색어에 넣어라)과 정면으로 충돌한다. 모델이 "확인되지 않은 이름은 쓰지 마라" 를 넓게 적용해 앞 턴에서 얻은 서비스명을 검색어에서 뺀 것으로 보인다(`[추측]`, 다만 두 규칙의 문구 충돌은 `[검증]` 된 사실). 시각 주입만으로는 안 터지고 조합에서만 터진 것은 프롬프트가 길어지며 충돌 규칙의 영향이 커진 결과로 본다(`[추측]`).
  - **결정 (a)**: 규칙 6 을 개정해 **충돌을 제거**한다 — 금지 목록에서 "이름" 을 빼고, "앞 대화에 이미 나온 이름·경로는 확인된 정보이므로 규칙 3 대로 그대로 사용한다" 는 예외를 명시(위 설계 절). 시각 주입·주입 위치·규칙 1 변경은 **그대로 둔다**(각각 단독으로는 회귀가 없었고, T6·T7 성과를 유지해야 한다).
  - **결정 (b)**: 게이트를 확장한다 — T6 3/3 **그리고** agent-06 P4 두 케이스 3/3 **그리고** P6·agent-13 통합 PASS 를 모두 만족해야 READY FOR REVIEW.
  - **결정 (c)**: (a) 로도 P4 가 복구되지 않으면 다음 후보는 ① 시각 줄 위치를 `SYSTEM_PROMPT` 뒤·힌트 앞으로 이동 ② agent-08 이 선기재한 F1(모델 입력의 마지막 HumanMessage 를 독립 질문으로 치환). **②는 그래프 동작이 바뀌므로 사용자 재확인 후에만** 진행한다(agent-08 명세 규정).
  - Validator 가 테스트 전용으로 복구한 `test_exactly_empty_rewrite_response_adds_no_hint` 의 `HINT_PREFIX not in` 단언은 **승인**한다. Builder 의 완화로 "힌트 없음" 보장이 사라진 것을 정확히 짚었다.
  - `[검증]` 비차단: naive datetime 을 넘기면 `UTC` 로 표기되는 건 기본 경로(`datetime.now().astimezone()`)에서 발생하지 않으므로 조치하지 않는다.
- 2026-09-29 (6): **규칙 6 개정으로도 회귀 미해소** — Builder 스모크: gpts 3/3 PASS, 메시지 등록 **0/3 FAIL**(`v1과 v2의 차이`). (5)-(a) 의 "규칙 충돌" 가설은 반증됐다.
  - **결정: B(프롬프트 변경)를 전부 철회하고 A(시각 주입)만 남긴다.** 근거:
    1. 이 명세가 처음부터 **A 를 결정적 해결, B 를 보조**로 규정했다. 보조가 회귀를 유발하면 남길 이유가 없다(최소 설계 원칙).
    2. `[검증]` Validator 귀속 실험에서 **"시각 주입만" 은 P4 2/2 통과**했다. 즉 A 단독은 알려진 정상 구성이다.
    3. `[검증]` 새 규칙 6 은 기존 규칙 5(`검색 결과에 없는 내용은 추측하지 않습니다. 근거를 찾지 못하면 "관련 내용을 문서에서 찾지 못했습니다."`)와 내용이 크게 겹친다. 없어도 거절 경로가 이미 있다.
    4. 프롬프트 문구 조정은 이 프로젝트에서 **네 번째 실패**(agent-06, agent-07, 그리고 이번 티켓에서 두 번). 같은 수단을 더 시도하지 않는다.
  - **실행 순서(Builder)**: ① 규칙 6 삭제 + 규칙 1 원복(= `SYSTEM_PROMPT` 를 agent-13 상태로) → 번호 8개 복귀. 시각 주입·주입 위치는 유지. ② P4 두 케이스 각 3회, T6 3회, T7 3회를 재측정해 보고.
  - **T7 판단 기준**: A 만으로도 "찾지 못했" 류가 3/3 이면 B 는 불필요로 확정. 3/3 미만이면 **규칙 1 축소만** 되살려(규칙 6 은 계속 제외) P4·T7 을 재측정한다. 그 조합도 P4 를 깨면 B 전면 철회로 확정하고 T7 결과를 기록만 남긴다.
  - 이 결정으로 이 티켓의 산출물은 **"현재 시각 주입" 하나**가 된다. 티켓 목표 중 2번(추측 억제)은 규칙 5 가 이미 담당하며, 추가 개선은 프롬프트가 아니라 **답변 품질 평가 세트**로 측정 기반을 만든 뒤 다루는 것이 맞다고 판단한다.
- 2026-09-29 (7): **B 철회 후 재측정 — 세 항목 모두 3/3.** `[검증]` Builder 실측: P4 gpts 3/3(`POST /v1/gpts와 POST /v2/gpts의 차이`), P4 메시지 등록 **3/3**(`/v1/messages/message vs /v2/messages/message 차이` — 직전의 `v1과 v2의 차이` 에서 복구, **회귀 해소**), T6 3/3(`오늘은 2026년 9월 29일입니다.`), T7 거절 3/3(`관련 내용을 문서에서 찾지 못했습니다.`). 425 passed.
  - **T7 이 B 없이도 3/3 이므로 (6) 의 분기(규칙 1 축소 되살리기)는 불필요.** 답변 문구가 기존 규칙 5 의 문장과 정확히 일치해, 거절 경로를 규칙 5 가 담당한다는 판단이 실측으로 확인됐다.
  - **이 티켓의 최종 산출물은 "현재 시각 주입" 하나**로 확정. 목표 2번(추측 억제)은 별도 변경 없이 기존 규칙 5 로 충족됨을 기록하고 닫는다. 추가 개선은 프롬프트가 아니라 **답변 품질 평가 세트**로 측정 기반을 만든 뒤 다룬다.
  - 회고: 프롬프트 문구 조정이 이 프로젝트에서 4회 연속 무효/유해였다. 앞으로 환각·행동 교정은 **맥락에 사실을 넣는 방식**을 우선하고, 프롬프트 수정은 측정 세트가 생긴 뒤에만 시도한다.
- 2026-09-29 (8): Validator 회차 2 판정 READY FOR REVIEW 접수. 단위 427 passed / 통합 11 passed. 핵심 확인:
  - **B 철회 독립 확인**: `SYSTEM_PROMPT` 가 agent-13 원문과 일치(규칙 1~8, 규칙 6·`9.` 부재). agent-14 4조각을 되돌린 사본을 `git show HEAD:src/agent.py` 와 diff 하니 **차이가 agent-13 변경분과 정확히 일치** → agent-14 가 프롬프트를 건드리지 않았음이 양방향으로 증명됨.
  - **회귀 해소**: P4 메시지 등록 **3/3**(`/v1/messages/message vs /v2/messages/message 차이`), gpts 3/3, 오염 0. 회차 1 의 실패 형태(`v1과 v2의 차이`) **0회 재현**(0/5 → 6/6). P6 도 대상 유지.
  - T6 3/3, T7 거절 3/3(문구가 규칙 5 문장과 일치 — (7) 판단 독립 재확인).
  - **민감도 양방향**: 주입 1줄 제거 사본 → 6건 실패, **B 재적용 사본 → 9건 실패**. 후자는 Validator 가 추가한 `test_withdrawn_prompt_rule_b_stays_out_of_the_system_prompt` 가 **철회한 B 의 재유입을 막는 가드**로 동작함을 뜻한다 — 이번 티켓에서 가장 값진 산출물 중 하나로 기록한다.
  - 테스트 무결성: 삭제된 테스트 0, 회차 1 복구 단언(`HINT_PREFIX not in`) 잔존 확인. 범위: agent-14 귀속 변경은 `src/agent.py`·`test/test_agent.py`·`test/test_integration_agent.py`·`README.md`·이 명세뿐이며, 작업 트리의 나머지는 agent-12/13 귀속임을 문자열 검사로 분리 확인.
  - 사용자 자산 보호: 5010·5020 PID·응답 불변, 사내 API 는 GET 1건, `data/checkpoints.sqlite*` mtime 불변.
  **결론: READY FOR REVIEW 선언.**
  - 비차단 의견 판단: `[검증]` P4 는 pytest 1회 실행당 케이스별 1회만 돌아 3/3 은 실행 3회로 얻은 값이다. 이 회귀가 두 번 재발한 이력을 보면 `parametrize` 로 3연속 자동화하는 편이 안전하나 통합 시간이 약 +160초 늘어난다 — **후속 후보로 기록**하고 이번엔 넣지 않는다. lint/type 도구 부재는 기존 상태로 조치 없음.
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

- 검증 회차: 1 (Validator, 2026-09-29)
- 스위트: `pytest -q` **427 passed** (11 deselected) / `pytest -m integration` **10 passed, 1 failed**
- 5010(PID 4576·4617)·5020(PID 4725) 검증 전후 PID·`/check` 동일. 종료하지 않음. 사내 API 는 GET 만 발생(C11 1건)

| 항목 | 결과 | 증거 |
|---|---|---|
| T1 시각 문자열 | PASS | `test_current_time_line_format`, 요일 7종 파라미터, 이름 없는 tz → `UTC+09:00`. Validator 보강: 음수·30분 오프셋, 이름 있는 tz 보존, 시/분 0 패딩 |
| T2 주입 위치 | PASS | `test_system_message_starts_with_time_then_prompt`, `test_hint_stays_at_the_end_after_time_injection` (`시각 < SYSTEM_PROMPT < HINT_PREFIX`). 보강: 빈 줄 1개 구분, 턴당 시각 줄 1개, rewrite 노드 미주입 |
| T3 매 턴 갱신 | PASS | `test_time_is_recomputed_every_turn` (앞 턴 시각 잔존 없음) |
| T4 상태 미오염 | PASS | `test_time_is_not_stored_in_conversation_state` |
| T5 프롬프트 | PASS | `git diff --word-diff -- src/agent.py`: 규칙 1 문구 교체 + 규칙 6 삽입 + 번호 이동만. agent-06 규칙 3·agent-13 규칙 4·`REWRITE_PROMPT` 무변경. agent-14 변경을 되돌린 사본의 `SYSTEM_PROMPT` 가 `HEAD + agent-13 규칙 4` 와 정확히 일치 |
| T6 날짜 (게이트) | **PASS 3/3** | 3회 모두 `"오늘은 2026년 9월 29일입니다."` (실행 시각 2026-09-29 11:48 KST). `test_today_question_answers_with_the_real_current_date` 로 자동화 |
| T7 모르는 사실 (기록) | 3/3 "찾지 못했" | 3회 모두 `"관련 내용을 문서에서 찾지 못했습니다."` 지어낸 수치 0건. 판정 미반영 |
| T8 회귀·범위 | **FAIL** | `pytest -q` green, agent-14 diff 범위는 `src/agent.py`·`test/test_agent.py`·`test/test_api_tool.py`(1줄)·`README.md`·이 명세뿐(다른 `src/**` 에 `current_time_line`·`현재 시각` 0건). 그러나 **agent-06 P4(메시지 등록) 통합이 회귀**(아래) |

### ESCALATE — agent-06 P4 회귀 (메시지 등록 케이스)

`test_followup_question_query_keeps_the_subject_of_the_previous_turn[메시지 등록]` 이 실패한다.
2턴 검색어가 `['v1과 v2의 차이']` 로, 앞 대화의 대상 표기(`메시지 등록`/`message`)가 사라진다.

`src/agent.py` 만 바꿔 가며 같은 시나리오(temperature=0)를 돌린 귀속 실험:

| 변형 | 대상 유지 |
|---|---|
| agent-13 상태(주입·규칙 변경 모두 되돌림) | **3/3 성공** — `['/v1/messages/message vs /v2/messages/message 차이']` |
| 시각 주입만 (프롬프트는 agent-13) | 2/2 성공 |
| 프롬프트 변경만 (주입 없음) | 2/2 성공 |
| **agent-14 현재 상태(둘 다)** | **0/5 실패** — 5회 모두 `['v1과 v2의 차이']` |

두 변경을 **함께** 적용했을 때만 재현된다. 1턴 답변이 대상을 `Message Management API` 로 부르게 바뀌고,
그 결과 2턴 agent 가 검색어에서 대상 이름을 통째로 뺀다(`standalone_question` 에는 남아 있다).

설계가 정한 "정확히 두 곳" 을 Builder 가 그대로 구현한 결과이므로 구현 결함이 아니다.
해소하려면 주입 위치·형식이나 규칙 문구 등 **설계 판단**이 필요해 리드에 에스컬레이션한다.

### 완료 기준

| 기준 | 결과 |
|---|---|
| T1~T5, T8 통과 / T6 3/3 / T7 수치 기록 | **T8 FAIL**, 나머지 PASS |
| 주입 매 턴 갱신 + 상태 미저장 | PASS (T3·T4) |
| agent-06 규칙 3·agent-13 규칙 4·`REWRITE_PROMPT` 문구 불변 | PASS (word-diff) |
| 기존 테스트 수정은 허용 3종뿐 | PASS — 6곳(완화 5 + 규칙 번호 1), 삭제된 테스트 0 |
| 새 의존성 0, 설정 변경 0 | PASS — `requirements*` diff 0, `pip install` 미실행 |
| 커밋하지 않음 | PASS |

### Validator 가 추가한 테스트

- `test/test_agent.py`: 오프셋 표기 3종, 빈 줄 구분, 턴당 시각 줄 1개, rewrite 노드 미주입,
  그리고 완화로 빠졌던 "힌트 없음" 단언 복구(`test_exactly_empty_rewrite_response_adds_no_hint`)
- `test/test_integration_agent.py`: T6 게이트 자동화(3회 파라미터화)

### 회차 2 (Validator, 2026-09-29) — B 철회·회귀 해소 독립 확인

- 스위트: `pytest -q` **427 passed** (11 deselected) / `pytest -m integration -q` **11 passed** (P4 2건·P6·agent-13 C11·W7·T6 3건 포함)
- 5010(PID 4576·4617)·5020(PID 4725) 검증 전후 PID 동일, `/check` 둘 다 200. 종료하지 않음
- 사내 API 는 GET 만 발생(C11 1건, `GET https://qa-general-chatbot-api.hunet.ai/openapi.json` → 200)
- 사용자 체크포인트 DB(`data/checkpoints.sqlite`) 무변경 — 통합·수동 실행 모두 `build_default_graph(settings)` 기본 `InMemorySaver`. `thread_id LIKE '%agent-14%'` 0건, 파일 mtime 불변

| 항목 | 결과 | 증거 |
|---|---|---|
| 1. B 철회 | **PASS** | `SYSTEM_PROMPT` 규칙 1 이 `인사말이나 일반 상식 질문은 예외입니다.` (HEAD 원문), 규칙 1~8·9 부재·새 규칙 6 부재, agent-06 규칙 3·agent-13 규칙 4·`REWRITE_PROMPT` 원문. agent-14 4조각(`from datetime import datetime`·`WEEKDAYS`+`current_time_line`·agent 노드 주석 1줄·주입 1줄)을 되돌린 사본과 `git show HEAD:src/agent.py` 의 diff 가 **agent-13 변경분(= `build_api_tool` import·`API_TOOL_NAME`·`tool_call_summary`·규칙 4·`build_default_graph` 배선)과 정확히 일치** |
| 2. A 유지 (T1~T4) | **PASS** | 관련 유닛 테스트 전부 green. 민감도: 주입 1줄만 제거한 사본에서 6건 실패(`test_system_prompt_is_prepended_but_not_stored`, `..starts_with_time_then_prompt`, `..hint_stays_at_the_end..`, `..recomputed_every_turn`, `..separated_from_the_prompt_by_a_blank_line`, `..only_one_time_line..`). B 를 되살린 사본에서는 T5 계열 9건 실패 — 두 방향 모두 테스트가 실제로 잡는다 |
| 3. T6 게이트 | **PASS 3/3** | 3회 모두 `'오늘은 2026년 9월 29일입니다.'` (실행 2026-09-29 12:0x KST) |
| 4. agent-06 P4 회귀 해소 | **PASS 3/3 + 3/3** | gpts 3회 모두 `['POST /v1/gpts와 POST /v2/gpts의 차이']`, 메시지 등록 3회 모두 `['/v1/messages/message vs /v2/messages/message 차이']`. 대상 표기 유지, 다른 케이스 이름 오염 0. 회차 1 의 `['v1과 v2의 차이']` 재현 0회 |
| 5. T7 (기록만) | 거절 3/3 | 3회 모두 `'관련 내용을 문서에서 찾지 못했습니다.'` — 규칙 5 문장과 일치. 지어낸 수치 0건. **판정 미반영** |
| 6. 기존 통합 전체 | **PASS** | `pytest -m integration -q` 11 passed |
| 7. 테스트 무결성 | **PASS** | `git diff HEAD -- test/` 에 삭제된 테스트 0(HEAD 의 테스트 함수 전부 잔존). 회차 1 에서 복구한 `test_exactly_empty_rewrite_response_adds_no_hint` 의 `HINT_PREFIX not in model.received[2][0].content` 단언 **잔존 확인**. 완화는 설계 허용 3종(`== SYSTEM_PROMPT` → `in` 5곳)뿐이며 첫 곳에는 `startswith("현재 시각: ")` 가 추가로 붙어 있다 |
| 8. 범위 | **PASS** | agent-14 로 인한 변경은 `src/agent.py`·`test/test_agent.py`·`test/test_integration_agent.py`(T6 게이트, 회차 1 Validator)·`README.md`(2줄)·이 명세뿐. 나머지 작업 트리 변경(`main.py`·`src/config/settings.py`·`src/web/app.py`·`resources/**`·`test/conftest.py`·`test/test_settings.py`·`test/test_api_tool.py`·`src/api_tool.py`·agent-12 문서 상태줄)에 `현재 시각`·`current_time_line`·`WEEKDAYS`·`agent-14` 문자열 0건 = agent-13 귀속 |

#### Validator 가 추가한 테스트 (회차 2)

- `test/test_agent.py::test_withdrawn_prompt_rule_b_stays_out_of_the_system_prompt` — 철회한 규칙 1 축소 문구·규칙 6 문구가 되살아나면 실패(회귀 재발 방지 가드)
- `test/test_agent.py::test_time_line_is_reinjected_once_on_the_tool_loop_agent_call` — 도구 호출 뒤 agent 재진입 경로에서도 시각 1개·힌트 맨 뒤 유지(회차 1 미커버 경로)

#### 완료 기준

| 기준 | 결과 |
|---|---|
| T1~T5, T8 통과 / T6 3/3 / T7 수치 기록 | **전부 PASS** (T8 = 회차 1 FAIL 사유였던 P4 회귀 해소) |
| 주입 매 턴 갱신 + 상태 미저장 | PASS (T3·T4) |
| agent-06 규칙 3·agent-13 규칙 4·`REWRITE_PROMPT` 문구 불변 | PASS |
| 기존 테스트 수정은 허용 3종뿐 | PASS |
| 새 의존성 0, 설정 변경 0 | PASS — `requirements*` diff 0, `pip install` 미실행 |
| 커밋하지 않음 | PASS |

**판정: READY FOR REVIEW.**

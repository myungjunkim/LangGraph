# agent-07: 후속 질문 검색어 — 도구 설명 보강 (단계 ①)

- 작성일: 2026-09-26
- 작성자: 리드
- 상태: 종결 — 단계 ① 효과 없음(0/3), docstring 변경 롤백. 단계 ② 는 사용자 결정 대기 (2026-09-26)
- 요청 경로: 사용자 승인(다른 세션 `ailab_general_chatbot` 경유). 선행: agent-06(커밋 `220b26c`) 리드 판단 기록 (5)-(c).
- 저장소: `/Users/mjkim/workspace/LangGraph` 만.

## 목표

후속 질문("방금 알려준 API의 v1이랑 v2 차이는?")에서 검색 도구의 `query` 가 앞 대화의 대상 없이 만들어지는 잔여 케이스(메시지 등록 API, agent-06 에서 4회 재현·xfail)를 **도구 설명(docstring) 보강**으로 해소한다. 단계 ② (질의 리라이팅 노드)는 이 티켓 범위 밖이며, ① 로 부족하면 사용자 확인 후 별도 티켓.

## 확인된 현재 상태 (`[검증]`)

- `src/tools.py` 도구 docstring:
  - `search_confluence`: `팀 Confluence(KUDOS) 문서를 검색한다. 정책, 설정값 정의, 운영 절차, 용어, 배경 설명 질문에 사용한다.`
  - `search_openapi`: `사내 API 의 OpenAPI 스펙(엔드포인트, HTTP 메서드, 경로, 파라미터, 요청/응답 스키마)을 검색한다. API 호출 방법 질문에 사용한다.`
- `test/test_tools.py` 가 두 description 을 **완전 일치**로 단언(agent-01 B3). agent-01 명세는 "도구 이름·`query` 인자·docstring 변경 금지" 였으나 그 티켓의 범위 제약이며, 이번 티켓에서 docstring 만 변경을 허용한다(이름·인자 시그니처는 여전히 불변).
- `src/agent.py` `SYSTEM_PROMPT` 규칙 3(agent-06): 후속 질문이면 앞 대화의 대상 이름으로 시작하는 독립 검색어, 앞 대화에 없던 이름 금지.
- `test/test_integration_agent.py` P4: gpts 케이스 PASS, 메시지 등록 케이스 `xfail(strict=False, reason="후속 질문 검색어에 대상 누락 — agent-07")`.
- `[추측]` 도구 인자 생성 시 모델은 시스템 규칙보다 도구 스키마의 description 을 더 직접적으로 참조한다. 이 가정을 P4 반복 실행으로 검증한다.

## 설계

### docstring 변경 (`src/tools.py`)

두 도구 docstring 끝에 같은 문장을 붙인다. **특정 API 이름 예시는 넣지 않는다**(agent-06 E1 오염 사례).

```
search_confluence:
"""팀 Confluence(KUDOS) 문서를 검색한다. 정책, 설정값 정의, 운영 절차, 용어, 배경 설명 질문에 사용한다.
query 는 대화 맥락 없이도 이해되는 완전한 검색어여야 한다. 후속 질문이면 앞서 다룬 문서 제목이나 기능명을 query 에 포함한다."""

search_openapi:
"""사내 API 의 OpenAPI 스펙(엔드포인트, HTTP 메서드, 경로, 파라미터, 요청/응답 스키마)을 검색한다. API 호출 방법 질문에 사용한다.
query 는 대화 맥락 없이도 이해되는 완전한 검색어여야 한다. 후속 질문이면 앞서 다룬 API 이름이나 경로를 query 에 포함한다."""
```

- `@tool` 기본 동작에서 docstring 전체가 `description` 이 된다. 첫 줄/둘째 줄 구분은 의미 없음(LangChain 이 그대로 전달).
- 도구 이름, `query: str` 단일 인자, 반환 형식, `format_chunks`, `build_tools` 시그니처는 불변.
- `SYSTEM_PROMPT` 는 변경하지 않는다(규칙 3 유지). 두 지시가 중복되지만 위치가 다르며(시스템 vs 도구 스키마) 이번 실험의 변수는 도구 설명 하나로 한정한다.

### 테스트 변경

- `test/test_tools.py`: description 완전 일치 단언을 새 문구로 갱신(기존 문구 앞부분은 그대로 포함되므로 "기존 문장 유지 + 새 문장 포함" 두 단언으로 써도 됨. Builder 재량, 단 완전 일치를 유지하는 쪽을 권장).
- `test/test_integration_agent.py` P4 메시지 등록 케이스: **Builder 는 xfail 마크를 건드리지 않는다.** Validator 가 게이트 통과를 확인한 뒤 마크를 제거한다(제거 후 재실행으로 green 확인).

## 작업 목록

1. [Builder] `src/tools.py` docstring 2곳 변경, `test/test_tools.py` 갱신. `pytest -q` green. 다른 파일 변경 금지. RAG 미기동.
2. [Validator] 아래 Q1~Q5.

## 검증 전략

| 항목 | 확인 방법 |
|---|---|
| Q1. 단위 회귀 | `pytest -q` green. `git diff --stat HEAD` 변경 파일이 `src/tools.py`, `test/test_tools.py`(+ Validator 의 `test/test_integration_agent.py`, `docs/plans/agent-07-*.md`) 뿐 |
| Q2. 도구 스키마 | `build_tools(FakeClient(), 6)` 의 각 `tool.description` 에 "대화 맥락 없이도" 포함, 특정 API 이름(`gpts`, `메시지`, `faq`) 미포함. `tool.args` 는 여전히 `query` 하나 |
| Q3. **P4 반복 재현 (게이트)** — RAG 5010 을 Validator 가 띄우고 끝나면 내림 | `test_integration_agent.py` 의 P4 두 케이스를 **각 3회 연속** 실행(`pytest -m integration -k <P4 테스트> --count` 가 없으므로 `-p no:cacheprovider` 로 3번 반복 호출 또는 probe 스크립트). 기록: 회차별 2턴 검색어 원문. **PASS 조건: 메시지 등록 케이스 3/3 대상 포함, gpts 케이스 3/3 대상 포함, 두 케이스 모두 3/3 오염 없음.** 2/3 이면 "부분 개선" 으로 ESCALATE(리드가 사용자에게 ② 진행 여부 확인), 오염이 1회라도 생기면 즉시 ESCALATE |
| Q4. xfail 해소 | Q3 PASS 시 Validator 가 메시지 케이스의 `xfail` 마크를 제거하고 `pytest -m integration -q` 1회 재실행 → failed 0, xfailed 0 |
| Q5. 기존 통합 회귀 | `pytest -m integration -q` 전체: agent-01 1 + agent-02 2(W7) + P6 PASS. W7 의 search/token 수치 기록 |

## 완료 기준

- [ ] Q1~Q5 통과 (Q3 은 3/3 × 2케이스)
- [ ] 도구 이름·인자·시그니처 불변, `SYSTEM_PROMPT` 불변
- [ ] docstring 에 특정 API 이름 없음
- [ ] 커밋하지 않음(사용자)

## 범위 제외

- 질의 리라이팅 노드(단계 ②) — 사용자 확인 후 별도 티켓
- `SYSTEM_PROMPT` 재조정
- Confluence 후속 질문 케이스의 통합 테스트(코퍼스에 Confluence 문서가 있는지 `[미확인]`)

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| `query` 인자에 `Annotated[str, "..."]` 로 인자 설명만 추가 | LangChain `@tool` 의 인자 설명은 `parse_docstring=True` 또는 `args_schema` 가 필요해 코드 형태가 바뀜. 도구 description 한 문장이 더 작다 |
| docstring 에 구체 예시 추가 | agent-06 E1 재발 위험 |
| 단계 ② 로 바로 진행 | 그래프 구조·지연 변경은 사용자 결정 사항. ① 이 더 작다 |

## 리드 판단 기록

- 2026-09-26 (1): 사용자 승인(다른 세션 경유)으로 착수. agent-01 의 "docstring 변경 금지" 제약은 이번 티켓에서 docstring 에 한해 해제(이름·인자 불변). 게이트는 반복 3/3 으로 재현성을 요구.
- 2026-09-26 (2): Builder 작업 1 완료 — `src/tools.py` docstring 2곳, `test/test_tools.py` 갱신+1건, 134 passed, `src/agent.py`·`test_integration_agent.py` diff 0. 구현 메모: `@tool` 은 docstring 을 dedent 하지 않으므로 둘째 줄을 왼쪽 끝에 붙여 description 에 들여쓰기 공백이 들어가지 않게 함(명세 예시 형태와 일치). 리드가 Validator 스폰.
- 2026-09-26 (3): **Validator ESCALATE 접수 — 단계 ① 실패.** `[검증]` gpts 3/3 유지(이미 되던 것), 메시지 등록 **0/3** — 검색어 `v1과 v2의 차이` 가 baseline·agent-06·agent-07 전부에서 글자까지 동일(누적 7회). 오염 0/6, 회귀 0. 명세의 `[추측]`("도구 description 이 더 직접 작용")은 이 케이스에서 반증. agent-06 (5)-(c) 의 원인 가설("1턴 답변에 경로가 뚜렷하지 않을 때")도 반증 — 1턴 답변에 `POST /v1/messages/message`, `POST /v2/messages/message` 가 명시돼 있어도 2턴 검색어가 이를 버림. **결정:**
  - (a) **docstring 변경 롤백** — 효과가 0 인 변경을 남기지 않는다(최소 변경 원칙). Builder 가 `src/tools.py`, `test/test_tools.py` 를 HEAD(`220b26c`) 상태로 되돌린다(스테이징 미접촉). 롤백 후 `pytest -q` 133 passed 로 확인.
  - (b) 단계 ② (질의 리라이팅 노드) 는 사용자 확인 후 별도 티켓(agent-08 후보). 설계 초안: `agent` 노드 앞에 `rewrite` 노드 — 상태에 Human 메시지가 2개 이상일 때만 LLM 1회 호출로 "앞 대화 없이 이해되는 독립 질문" 을 생성해 **도구 호출용 힌트로 SystemMessage 에 덧붙임**(원래 Human 메시지는 보존, 체크포인터 저장 내용 불변). 비용: 후속 턴에서만 LLM 호출 +1(짧은 출력, `[추측]` 2~5초). 첫 턴은 우회. 검증 게이트는 이번과 동일한 3/3.
  - (c) 프롬프트/설명 추가 조정은 더 하지 않는다 — 2회 시도(시스템 규칙, 도구 설명) 모두 이 케이스에 무효.
- 2026-09-26 (4): Builder 롤백 완료 — `git diff HEAD -- src/tools.py test/test_tools.py` 비어 있음, `git status` 는 미추적 이 문서 1건뿐, 스테이징 없음, 133 passed. 워킹트리 = HEAD `220b26c` + 이 명세. **agent-07 종결.** 단계 ② 는 사용자 결정 대기(agent-08 후보, 초안은 (3)-(b)).
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

(Validator 가 기록)

- 검증 회차: 1 (Validator, 2026-09-26). 환경: `.venv`, Ollama 11434(기존 기동 — `curl /api/tags` 로 `qwen3:14b` 확인만), RAG 5010(Validator 가 기동 후 종료). HEAD `220b26c` + 미커밋 변경.

| 항목 | 결과 | 증거 |
|---|---|---|
| Q1. 단위 회귀 | PASS | `.venv/bin/python -m pytest -q` → `134 passed, 6 deselected`(agent-06 회차 3 의 133 + Builder 신규 1). `git diff --stat HEAD` = `src/tools.py`(+4/-2), `test/test_tools.py`(+15/-2) 뿐 + 미추적 `docs/plans/agent-07-*.md`. `src/agent.py`·`test/test_integration_agent.py`·`web.py`·`main.py`·`resources/` diff 0 → `SYSTEM_PROMPT` 불변 |
| Q2. 도구 스키마 | PASS | `build_tools(FakeClient(), 6)` 실측: 두 도구 모두 `tool.args == {'query': {...'type':'string'}}`, 이름 `search_confluence`/`search_openapi` 불변. `description` 에 `대화 맥락 없이도` 포함, `gpts`·`메시지`·`faq`·`설문` 미포함. `tool_call_schema` 의 `description` 도 docstring 전문과 동일(들여쓰기 공백 없음) |
| Q3. **P4 반복 재현 (게이트)** | **FAIL — 메시지 등록 0/3** | 아래 회차별 표. gpts 3/3 대상 유지, 메시지 등록 0/3 대상 누락. 오염(다른 케이스 이름 혼입)은 6회 모두 없음 |
| Q4. xfail 해소 | 미수행 | Q3 미통과 → 설계상 xfail 마크 제거 조건 불충족. `test/test_integration_agent.py` 무변경 |
| Q5. 기존 통합 회귀 | PASS | `pytest -m integration -q -rxX` → `5 passed, 134 deselected, 1 xfailed`(failed 0). agent-01 1건, agent-02 2건 PASS, `[W7] token 이벤트 409건, search 이벤트 1건, 답변 1130자`. `[P6] 2턴 검색어 [], 답변 658자` → 재호출 없음 조건 충족 |

Q3 실측(각 회차 새 `thread_id`, 1턴 "<대상> 알려줘" → 2턴 "방금 알려준 API의 v1이랑 v2 차이는 뭐야?", 스크래치패드 probe 스크립트, `src` 무변경):

| 회차 | 1턴 대상 | 2턴 검색어 원문 | 대상 포함 | 오염 |
|---|---|---|---|---|
| 1 | gpts 등록 API | `/v1/gpts와 /v2/gpts 차이` | O(`gpts`) | 없음(`메시지` 미포함) |
| 1 | 메시지 등록 API | `v1과 v2의 차이` | X(`메시지 등록` 누락) | 없음(`gpts` 미포함) |
| 2 | gpts 등록 API | `/v1/gpts와 /v2/gpts 차이` | O | 없음 |
| 2 | 메시지 등록 API | `v1과 v2의 차이` | X | 없음 |
| 3 | gpts 등록 API | `/v1/gpts와 /v2/gpts 차이` | O | 없음 |
| 3 | 메시지 등록 API | `v1과 v2의 차이` | X | 없음 |

- 2턴 도구는 6회 모두 `search_openapi` 1회 호출(도구 선택은 정상). 검색어만 문제.
- 메시지 등록 케이스의 2턴 검색어 `v1과 v2의 차이` 는 agent-06 회차 2·3 및 커밋 `06e01fa` baseline(규칙 3 없음)과 **글자까지 동일**하다. docstring 보강 전후로 이 케이스의 출력이 변하지 않았다(누적 7회 동일 재현).
- `[검증]` agent-06 (5)-(c) 의 가설("1턴 답변에 경로가 뚜렷하지 않아 규칙이 무시된다")은 성립하지 않는다. 1턴 답변 원문에 `POST /v1/messages/message`, `POST /v2/messages/message` 가 명시되어 있는데도 2턴 검색어는 경로·이름을 모두 버렸다(6회 중 메시지 3회 모두 동일 형태의 1턴 답변).
- `[검증]` 명세 "확인된 현재 상태" 의 `[추측]`("도구 인자 생성 시 모델이 시스템 규칙보다 도구 description 을 더 직접 참조한다")은 이 케이스에서 지지되지 않는다. description 에 같은 취지의 지시를 넣어도 gpts 케이스(이미 되던 것)만 유지되고 메시지 케이스는 0/3.

### ESCALATE E1 — 단계 ①(도구 docstring)로는 잔여 케이스가 해소되지 않는다 (major, 설계 판단 필요)

- Builder 구현은 명세와 일치한다(docstring 2곳·테스트 갱신, 이름·인자·`SYSTEM_PROMPT` 불변). 구현 결함이 아니라 설계 가설의 반증이다.
- 게이트 기준은 "메시지 등록 3/3" 인데 실측 0/3 이며, "부분 개선(2/3)" 도 아니라 **개선 0** 이다. 오염은 발생하지 않았으므로 악화도 없다.
- 남은 선택지는 명세 "범위 제외" 의 단계 ②(질의 리라이팅 노드, 그래프 구조 변경) 또는 다른 접근이며, 모두 사용자 결정·별도 티켓 사항이다.
- 현재 변경(docstring 보강)의 존치 여부도 리드 판단 사항이다. `[검증]` 존치 시 부작용은 관측되지 않았다(단위 134 green, 통합 회귀 없음, 오염 0/6). `[미확인]` Confluence 후속 질문 케이스에 대한 효과는 코퍼스·테스트가 없어 확인하지 못했다.

**판정: ESCALATE TO LEAD.**

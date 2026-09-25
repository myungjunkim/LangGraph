# agent-05: 웹 UI 스트림 처리 수정 2건 접수 + 설정 마이그레이션 안내

- 작성일: 2026-09-25
- 작성자: 리드
- 상태: READY FOR REVIEW (2026-09-25, 리드 확인) — agent-02/03 판정 유지 확인. 커밋 대기(사용자)
- 관련 티켓: agent-02(웹 UI), agent-03(마크다운 렌더링) — 두 티켓의 READY FOR REVIEW 이후, 미커밋 상태에서 다른 세션(`ailab_general_chatbot`)이 `index.html`·테스트를 직접 수정함. 이 문서는 그 수정을 설계에 편입하고 재검증하는 기록이다.

## 목표

1. 다른 세션이 적용한 UI 스트림 처리 수정 2건을 설계 문서에 반영하고(agent-02/03 명세와 구현이 다시 일치하도록), Validator 가 회귀·계약을 재검증한다.
2. agent-02 에서 `[fastapi]` 섹션이 필수가 되어 **agent-01 시점에 복사한 구 `config_local.ini` 로는 CLI(`main.py`)까지 `KeyError` 로 멈추는** 문제를 README 에 안내한다.

## 접수한 외부 수정 (`[검증]` 2026-09-25 리드가 파일 직접 확인)

파일: `resources/static/index.html` `ask()`, `test/test_web_ui.py`, `test/test_web.py`. 서버 코드·SSE 이벤트 계약 무변경.

| # | 문제 | 수정 | 리드 판단 |
|---|---|---|---|
| 1 | 모델이 tool_call 과 함께 낸 content("문서를 먼저 검색해 볼게요.")가 `token` 으로 흘러 최종 답변 앞에 이어붙음. agent-02 명세는 "tool_call 전용 chunk 는 content 가 비어 걸러진다" 고 가정했으나 content 가 있는 경우를 다루지 않음 | `search` 이벤트 수신 시 `raw = ''; body.replaceChildren();` 로 그때까지의 본문을 비운 뒤 `addSearchLine` | **수용.** 서버에서 거르려면 tool_call 여부를 알 때까지 버퍼링해야 해 스트리밍이 사라짐. 도구 호출 직전 멘트는 버려도 정보 손실이 없음. 대안(멘트를 흐린 글씨로 남기기)은 화면만 늘리므로 기각 |
| 2 | `done`/`error` 없이 연결이 끊기면 부분 답변만 남고 표시가 없음 | `let ended = false`, `done`/`error` 에서 `true`. 루프 종료 후 `!ended` 면 `.err` + `"\n응답이 중단되었습니다."` | **수용.** 기존 오류 표시 방식과 동일 |

테스트 변경: `test_web_ui.py` `test_done_event_has_no_extra_handler` → `test_done_event_only_marks_stream_ended` 로 교체(agent-03 의 "done 전용 분기 없음" 은 "done 은 `ended` 표시만, 재렌더 없음" 으로 완화), `test_search_event_discards_text_streamed_before_tool_call`, `test_stream_end_without_done_or_error_shows_interrupted` 추가. `test_web.py` `test_text_before_tool_call_streams_before_search_event` 추가(멘트 token 이 `search` 앞에, 그 뒤 token 합이 최종 답변 — UI 가 이 순서에 의존).

→ agent-02 명세 "SSE 이벤트 계약" 의 `token` 설명은 "최종 답변 토큰" 에서 "**agent 노드의 텍스트 토큰. `search` 앞에 온 token 은 도구 호출 직전 멘트이며 UI 가 버린다**" 로 읽는다. agent-03 명세 "렌더링 흐름" 의 `done` 행은 "`ended = true` 만 표시, 재렌더 없음" 으로 읽는다. 두 명세 본문은 수정하지 않고 이 문서를 참조한다(상태줄에 링크).

## 설계 (README 안내)

`README.md` "사전 준비 › 설정 파일" 절에 1단락 추가:

> agent-02 부터 `[fastapi]` 섹션(`host`, `port`)이 필수다. 그 이전에 복사한 `resources/config_local.ini` 에는 이 섹션이 없어 `python main.py` / `python web.py` 모두 `KeyError: 'fastapi'` 로 멈춘다. `.example` 의 `[fastapi]` 섹션을 자기 `config_local.ini` 에 붙여 넣으면 된다.

같은 절의 "섹션 | 키 | 설명" 표에 `[fastapi]` 행(`host`, `port` — 웹 서버 바인드 주소·포트, 팀원 공개 시 `host=0.0.0.0`)을 추가한다(agent-02 README 작업의 누락 — 2026-09-25 (2)).

코드로 기본값을 주는 방식(`[fastapi]` 없으면 127.0.0.1:5020)은 채택하지 않는다 — `load_settings` 의 "키 없으면 KeyError 그대로 전파(설정 실수를 숨기지 않음)" 정책(agent-01)과 충돌.

## 작업 목록

1. [Builder] README 안내 단락 추가. 다른 파일은 변경하지 않는다.
2. [Validator] 외부 수정분 포함 재검증 (아래 R1~R5).

## 검증 전략

| 항목 | 확인 방법 |
|---|---|
| R1. 회귀 | `pytest -q` green(다른 세션 보고 기준 124 passed). `pytest -m integration -q` 는 RAG 5010 이 내려가 있으면 skip 3건으로 기록 — 띄우지 말 것 |
| R2. 계약 유지 | agent-02 W6·agent-03 V1~V3 의 기존 테스트가 무삭제·통과. `test_web_ui.py` 에서 삭제된 테스트는 `test_done_event_has_no_extra_handler` 1건뿐이고 대체 테스트가 존재 |
| R3. UI 동작 | node 최소 DOM 셰임(스크래치패드)으로 `ask()` 를 가짜 SSE 로 실행: (a) 멘트 token → search → 답변 token → done: 본문 == 답변만, `[검색]` 줄 1개, `.err` 없음 (b) token 도중 스트림 종료(done 없음): 본문에 `응답이 중단되었습니다.` + `.err` (c) error 프레임: `.err` + data, 중단 안내 없음 (d) search 없는 정상: 본문 == 답변 |
| R4. 서버 무변경 | `src/`, `web.py`, `main.py` 의 mtime 이 09-22 그대로(agent-04 검증과 동일 방식) |
| R5. README | 안내 단락 존재, `[fastapi]`·`KeyError` 언급. 설정 표에 `[fastapi]` 행(`host`, `port`) 존재 |

## 완료 기준

- [ ] R1~R5 통과
- [ ] 서버 코드·SSE 계약 무변경
- [ ] 커밋하지 않음(사용자)

## 범위 제외

- `InMemorySaver` 무한 누적·thread 만료, thread_id 기반 접근 통제 — 다른 세션 리뷰 지적. 후속 "접근 통제" 티켓과 함께 검토
- `[fastapi]` 기본값 코드 처리

## 리드 판단 기록

- 2026-09-25 (1): 다른 세션의 직접 수정을 사후 접수. 리드/Builder/Validator 절차 밖의 변경이지만 내용이 타당하고 테스트가 동반되어 되돌리지 않고 편입한다. 다만 READY FOR REVIEW 선언 이후의 변경이므로 Validator 재검증 없이는 agent-02/03 판정을 유지할 수 없어 R1~R5 를 수행한다. 향후 같은 상황이 오면 수정 전에 리드에 알려 주도록 해당 세션에 요청(회신 불필요 메시지였으므로 별도 전송하지 않음, 다음 접점 때 전달).
- 2026-09-25 (2): Builder 작업 1 완료 보고(README 안내 단락, 124 passed). Builder 지적: README 설정 표에 `[fastapi]` 행이 없음 — agent-02 의 README 작업 누락으로 판단, agent-05 범위에 표 행 추가를 편입(R5 에 포함). "agent-02 부터" 를 "웹 서버가 추가되면서" 로 바꾼 문구 변경은 독자 기준으로 타당, 승인.
- 2026-09-25 (3): Validator 판정 READY FOR REVIEW 접수(검증 회차 1). R1~R5·완료 기준 전부 PASS: 124→129 passed(Validator 가 `ask()` 실행 테스트 5건 추가, 무력화 시 실패하는 민감도 확인), 통합 3 skipped(5010 미기동, 의도), agent-02 W6·agent-03 V1~V3 테스트 전부 현존·통과, 삭제는 대체된 1건뿐, 서버 코드 mtime 09-22 그대로, README 안내·표 행 확인. **결론: READY FOR REVIEW — agent-02/03 의 판정도 이 재검증으로 유지된다.** 비차단 `[추측]`: 한 턴 안에서 답변 토큰이 흐른 뒤 모델이 도구를 또 부르면 `search` 가 본문을 비워 그때까지의 답변이 사라질 수 있음 — 현 프롬프트 구조상 `search` 앞 텍스트는 멘트뿐이라 재현 사례 없음. 재현되면 "search 앞 텍스트를 흐린 글씨로 보존" 으로 전환할 것을 후속 후보로 기록.
- 2026-09-25 (4): 다른 세션 **수정 3** 접수(`[검증]` 리드가 파일 확인: `index.html:295,298`, `test_web_ui.py:131`). 문제: 전송 중 `input`/`sendBtn` 만 잠기고 `새 대화` 는 열려 있어, 답변 도중 누르면 로그는 비고 입력은 스트림 종료까지 잠긴 빈 화면이 남음. 수정: submit 시작 시 `resetBtn.disabled = true`, `finally` 에서 `false`. 테스트 `test_reset_button_is_locked_while_answering` 추가. **수용** — agent-02 명세 "전송 중 입력·버튼 비활성화" 의 자연스러운 범위이며 서버 무변경. 다른 세션 자체 점검(422 경계값, 재귀 한도 error 프레임, 멀티바이트 절단, 도구 2회 연속, 네트워크 끊김) 이상 없음 보고, 130 passed. 리드는 회귀·계약 재확인만 Validator 에 위임(검증 회차 2). 참고: 공백만 있는 `message` 는 서버가 200 으로 받음(UI 가 trim 으로 차단) — 서버측 `str_strip_whitespace`/검증 추가는 범위 밖, 기록만.
- 2026-09-25 (5): Validator 회차 2 PASS 접수 — 130 passed / 0 failed / 0 skipped, `index.html` 변경은 submit 핸들러 2줄뿐(`ask()`·reset 핸들러 무변경), 서버 코드 mtime 09-22 유지, 회차 1 테스트 5건 및 W6/V1~V3 계약 전부 현존·통과, 신규 테스트 민감도 확인. **READY FOR REVIEW 유지 확정(수정 3 포함).** 커밋은 사용자.
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

### 2026-09-25 Validator 검증 (검증 회차: 1) — 판정: READY FOR REVIEW

#### 실행한 명령

| 명령 | 결과 |
|---|---|
| `.venv/bin/python -m pytest -q` (Builder 산출물 + 외부 수정분 그대로) | **124 passed, 3 deselected** |
| `.venv/bin/python -m pytest -q` (Validator 테스트 5건 추가 후) | **129 passed, 3 deselected** |
| `.venv/bin/python -m pytest -m integration -q` | **3 skipped**, 129 deselected (RAG 5010 미기동 — 명세대로 띄우지 않음) |
| `node run.mjs` (스크래치패드 DOM 셰임 + 가짜 SSE reader 로 `ask()` 실행) | R3 (a)~(d) 전부 기대대로 |
| `.venv/bin/python -m compileall test/test_web_ui.py` | OK (프로젝트에 린터 설정 없음 — agent-02/03 검증 기록과 동일) |
| `find src web.py main.py -type f -exec stat …` | 전부 2026-09-22 |

#### 검증 전략

| 항목 | 판정 | 증거 |
|---|---|---|
| R1. 회귀 | PASS | `pytest -q` 124 passed / 0 failed / 0 skipped (다른 세션 보고 124건과 일치). 통합은 3 skipped(RAG 미기동). Validator 테스트 추가 후 129 passed |
| R2. 계약 유지 | PASS | agent-02 W6 6항목 전부 현존·통과(`test_no_external_resources`, `test_fetch_targets_are_relative_paths`, `test_sse_event_names_match_server_contract`, `test_server_data_is_not_inserted_via_innerhtml`, `test_request_body_keys_match_chat_request`, `test_thread_id_is_generated_in_browser`). agent-03 V1~V3 테스트 11건 전부 현존·통과(`test_web_ui.py:168-253`). agent-03 Validator 8건 중 7건 그대로, 삭제는 `test_done_event_has_no_extra_handler` **1건뿐**이며 `test_done_event_only_marks_stream_ended`(`:279`)로 대체 — 대체 테스트가 `ended = true` 존재 + `renderMarkdown`/`replaceChildren` 부재를 단언해 "done 은 표시만, 재렌더 없음" 을 고정. 테스트 함수 총계 17(agent-02)+11(agent-03 Builder)+8(agent-03 Validator, 1건 개명)+2(외부 추가)=38 로 파일 실측과 일치. 서버측 순서 계약은 `test_web.py:158 test_text_before_tool_call_streams_before_search_event` 가 새로 고정(멘트 token 이 `search` 앞, 그 뒤 token 합 == 최종 답변). W2 의 `token` 이어붙임 == 최종 답변 단언(`test_web.py:112`)도 무변경 |
| R3. UI 동작 | PASS | `index.html` 의 `addMessage`~`ask` 원본을 그대로 떼어 최소 DOM 셰임 + 가짜 SSE(7바이트씩 쪼갠 reader)로 node v26.8.2 실행. (a) 멘트 2 token → `search` → 답변 2 token → `done`: 본문 `POST /v1/messages 입니다.`(멘트 0자), `[검색] search_openapi(메시지 등록)` 1줄, `.err` 없음 (b) `done` 없이 종료: 본문 `POST /v1/messages 입니\n응답이 중단되었습니다.` + `.err` (c) `error` 프레임: `.err` + `ResponseError: …`, 중단 안내 없음(`ended` 가 `error` 에서도 켜짐) (d) `search` 없는 정상: 본문 == 답변 전문. 민감도 확인: 스크래치패드 사본에서 `raw = ''; body.replaceChildren();` 와 `if (!ended)` 를 무력화하면 (a) 가 멘트를 이어붙이고 (b) 의 안내가 사라짐 — 테스트가 실제로 이 두 수정을 잡는다(프로젝트 파일 무수정, 사본 변형) |
| R4. 서버 무변경 | PASS | `src/**`(10개), `web.py`, `main.py` mtime 이 전부 2026-09-22 16:12~17:13. `src/web/app.py` 17:13 으로 agent-03(17:35)·agent-05(09-25) 작업보다 앞섬. SSE 이벤트 이름·프레임 포맷(`app.py:18-19,39-53`) 무변경 |
| R5. README | PASS | `README.md:53-56` 안내 단락(`[fastapi]` 섹션 필수, `python main.py`/`python web.py` 둘 다 `KeyError: 'fastapi'`, `.example` 붙여넣기 안내). 설정 표 `README.md:51` 에 `[fastapi] | host, port | 웹 서버 바인드 주소·포트. 팀원에게 공개하려면 host=0.0.0.0` 행 존재. 문구 근거도 확인 — `src/config/settings.py:43` `parser["fastapi"]` 는 무조건 읽고, `main.py:11` 이 같은 `load_settings` 를 호출한다. 회귀는 기존 `test_settings.py:63 test_load_settings_missing_fastapi_section_raises` 가 고정 |

#### 완료 기준

| 기준 | 판정 | 증거 |
|---|---|---|
| R1~R5 통과 | PASS | 위 표 |
| 서버 코드·SSE 계약 무변경 | PASS | R4. `src/`·`web.py`·`main.py` mtime 09-22, 이벤트 이름 `search/token/done/error` 와 `event:`/`data:` JSON 포맷 그대로 |
| 커밋하지 않음(사용자) | PASS | `git log --oneline -1` == `1a0f1ff`(agent-01). `git status --porcelain` 의 스테이징 열 전부 공백(untracked/unstaged). Validator 는 `git add/commit/push/stash` 미실행 |

#### Validator 가 추가한 테스트 (`test/test_web_ui.py` 말미에 append, 5건)

R3 두 수정은 지금까지 문자열 grep 으로만 고정돼 있었다(분기 안에 `raw = ''` 가 "있는지"만 확인 — 화면에 무엇이 남는지는 확인 불가). `ask()` 원본을 실제로 실행하는 테스트로 보강했다.

- `test_ask_drops_preamble_and_shows_only_the_final_answer` — (a) 멘트가 최종 답변에 섞이지 않고 `[검색]` 줄은 1개
- `test_ask_marks_stream_interrupted_when_done_is_missing` — (b) 중단 안내 + `.err`
- `test_ask_error_frame_shows_detail_without_interruption_notice` — (c) `error` 는 정상 종결로 보고 중단 안내를 덧붙이지 않음
- `test_ask_without_search_keeps_the_whole_answer` — (d) 비우기 로직이 도구 없는 턴의 답변을 먹지 않음
- `test_ask_renders_markdown_answer_after_search` — `search` 로 비운 뒤에도 마크다운 렌더 계약(agent-03) 유지

`[검증]` agent-03 이 만든 `_DOM_SHIM` 에 `insertBefore`/`replaceChildren`/`scrollIntoView`/`classList` 4개를 **추가만** 했다(기존 단언·렌더러 경로 무변경, 렌더 테스트 8건 그대로 통과). `node` 가 없으면 `pytest.skip` 이라 새 필수 의존성은 아니다.

#### 비차단 의견 (판정에 반영하지 않음)

- `search` 는 볼 때마다 본문을 비우므로, 한 턴에서 **답변 토큰이 흐른 뒤 모델이 도구를 한 번 더 부르는** 경우 그때까지의 답변이 사라진다. 현재 구조에서 `search` 앞 텍스트는 전부 "도구 호출 직전 멘트" 이므로 리드 판단(수용)과 모순되지 않고, 실행으로 재현한 사례도 없다(`[미확인]`). 기록만 남긴다.
- `[미확인]` 브라우저 실제 화면(중단 안내의 빨간색 표시, 멘트가 지워질 때의 깜빡임)은 자동화로 확인할 수 없어 사용자 육안 영역이다.

### 2026-09-25 Validator 재확인 (검증 회차: 2) — 판정: PASS (READY FOR REVIEW 유지)

대상: 리드 판단 기록 (4) 의 **수정 3**(전송 중 `resetBtn` 잠금) 만. 회귀·계약 재확인 범위.

| 명령 | 결과 |
|---|---|
| `.venv/bin/python -m pytest -q` | **130 passed, 0 failed, 0 skipped**, 3 deselected(`addopts = -m "not integration"`), 1 warning(starlette 기존 DeprecationWarning) |
| `.venv/bin/python -m pytest test/test_web_ui.py -q -k "ask_ or reset_button"` | **6 passed** (회차 1 의 `ask()` 실행 테스트 5건 + 신규 `test_reset_button_is_locked_while_answering`) |
| `find src web.py main.py -type f -exec stat …` | 12개 전부 2026-09-22 16:12~17:13 |
| 민감도(스크래치 사본, 프로젝트 파일 무수정) | `resetBtn.disabled = true` / `= false` 중 하나라도 지운 사본은 신규 테스트 로직이 FAIL |

| 확인 항목 | 판정 | 증거 |
|---|---|---|
| 회귀 | PASS | 130 passed(회차 1 의 129 + 외부 추가 1). skip 0, fail 0 |
| `index.html` 변경 국소성 | PASS | `ask()` 본문(`:240-284`)이 회차 1 기록과 동일 — `search` 에서 `raw = ''; body.replaceChildren();`(`:277-279`), `let ended = false`(`:255`)가 `error`/`done` 에서 `true`(`:282-283`), 루프 종료 후 `if (!ended)` 중단 안내(`:285`). submit 핸들러(`:287-299`)의 차이는 `resetBtn.disabled = true`(`:295`)·`finally` 의 `resetBtn.disabled = false`(`:298`) 2줄뿐. 파일이 untracked 라 git diff 불가라 본문 대조로 확인(`[검증]` 동작 기술 일치, `[미확인]` 바이트 단위 diff) |
| 서버 무변경 | PASS | `src/**`·`web.py`·`main.py` mtime 09-22 그대로. `resources/static/index.html` 과 `test/test_web_ui.py` 만 09-25 20:32 |
| 계약 유지 | PASS | agent-02 W6 6건 + agent-03 `test_done_event_only_marks_stream_ended`·`test_search_event_discards_text_streamed_before_tool_call`·`test_stream_end_without_done_or_error_shows_interrupted` 전부 현존·통과. `test_web_ui.py` 테스트 함수 44개 = 회차 1 의 43 + 외부 추가 1 → 삭제·약화 없음. `test_web.py` 25건 무변경(mtime 09-25 19:22, 회차 1 이전) |

`[검증]` 신규 테스트는 submit 핸들러 구간만 잘라 잠금/해제를 단언하므로 기존 단언을 건드리지 않는다. Validator 는 이번 회차에 프로젝트 파일을 수정하지 않았고 `git add/commit/push/stash` 도 실행하지 않았다.

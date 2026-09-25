# agent-03: 웹 UI 답변 마크다운 렌더링

- 작성일: 2026-09-22
- 작성자: 리드
- 상태: READY FOR REVIEW (2026-09-22, 리드 확인) — 커밋 대기(사용자). 브라우저 화면 모양은 사용자 육안 확인. **2026-09-25 추가**: `done` 분기가 `ended = true` 표시용으로 생김(재렌더 없음) → `agent-05-stream-ui-fixes.md` 참조
- 선행 티켓: agent-02 (READY FOR REVIEW). 저장소: `/Users/mjkim/workspace/LangGraph` 만. 변경 파일은 `resources/static/index.html`, `test/test_web_ui.py`, `README.md` 3개뿐. 서버(`src/`, `web.py`)·프롬프트·RAG 저장소는 변경하지 않는다.

## 목표

브라우저 UI 에서 LLM 답변의 마크다운(`###`, `**`, 목록, 코드)이 원문 기호 그대로 보이는 문제를 고친다. 외부 라이브러리·CDN·`innerHTML` 없이 DOM API 로 만든 소형 렌더러로 처리한다.

## 배경 (`[검증]`)

- 사용자 보고: `### API 호출 방법`, `**HTTP 메서드:**`, `- `목록이 기호 그대로 표시됨. agent-02 명세가 "마크다운 렌더링" 을 범위 제외로 뒀고 UI 가 `body.textContent += data` 로만 그린 결과. `qwen3:14b` 는 형식 지시 없이도 마크다운으로 답한다.
- 현재 `index.html`: `addAnswer()` 가 `.msg` 안에 `body` div 를 만들고, `token` 이벤트마다 `body.textContent += data`, `done` 에서 `linkifyUrls(body)`(텍스트 노드를 쪼개 `<a>` 생성), `error` 에서 `.err` + `textContent += data`. `.msg` 는 `white-space: pre-wrap`.
- `test/test_web_ui.py` 가 고정하는 계약: 외부 리소스 없음, `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`document.write` 미사용, `textContent` 사용, `linkifyUrls` 가 `createElement('a')`·`createTextNode`·`target='_blank'`·`rel='noopener'` 사용, `pre-wrap` 존재.

## 설계

### 렌더링 흐름

```
token 이벤트 → raw += data → body.replaceChildren(renderMarkdown(raw))   (매 토큰 재렌더)
done  이벤트 → (변경 없음: 마지막 재렌더 결과 유지)
error 이벤트 → body.classList.add('err'); body.append(document.createTextNode(data))
```

- 답변 원문은 JS 변수 `raw`(문자열)에 누적하고, 화면은 항상 `renderMarkdown(raw)` 결과로 교체한다. 2KB 이내 문자열 렌더링은 토큰당 1ms 미만이므로 스로틀 없이 매 토큰 재렌더한다(`[추측]` 성능; 육안 확인 V5 에서 끊김이 보이면 `requestAnimationFrame` 1회 스로틀로 조정 — Builder 재량, 명세 갱신 불필요).
- 닫히지 않은 `**` 나 진행 중인 코드 블록은 그 시점 규칙대로(원문 텍스트로) 보이다가 닫히면 렌더된다. 이는 허용한다.
- `linkifyUrls` 는 렌더러 안에서 텍스트 노드를 만들 때 호출하는 형태로 흡수한다(기존 함수 시그니처 `linkifyUrls(container)` 와 내부 구현은 유지 — 테스트가 고정). 코드 블록/인라인 코드 안의 url 은 링크로 만들지 않는다.

### `renderMarkdown(text) -> DocumentFragment` 지원 문법

지원 범위는 현재 모델 답변에서 실제로 나오는 것으로 한정한다. 아래 표 밖의 문법(표, 인용 `>`, 이미지, 수평선, 중첩 목록 2단 이상, HTML 태그)은 **원문 텍스트 그대로** 보이면 된다.

| 문법 | 판정 규칙 (줄 단위) | 생성 DOM |
|---|---|---|
| 코드 블록 | ```` ``` ```` 로 시작하는 줄부터 다음 ```` ``` ```` 줄까지. 언어 표기는 무시 | `<pre><code>` 원문 그대로(인라인 서식·링크 미적용) |
| 제목 | `^(#{1,4})\s+(.+)$` | `<h1>`~`<h4>` (CSS 로 크기만 조정, 답변 안에서 과하지 않게: h1 18px, h2 16px, h3 15px, h4 14px, 굵게) |
| 순서 없는 목록 | 연속된 `^\s*[-*]\s+(.+)$` 줄 묶음. 앞 공백 2칸 이상이면 1단 중첩 `<ul>` 까지만 지원 | `<ul><li>` |
| 순서 있는 목록 | 연속된 `^\s*\d+[.)]\s+(.+)$` 줄 묶음 | `<ol><li>` (번호는 브라우저 기본) |
| 문단 | 그 외 줄. 빈 줄로 문단 구분 | `<p>` — 문단 안 줄바꿈은 `<br>` |
| 인라인 굵게 | `**텍스트**` (줄 안에서 최단 매칭) | `<strong>` |
| 인라인 코드 | `` `텍스트` `` | `<code>` (안의 `**` 등은 해석하지 않음) |
| url | `https?://[^\s)\]]+` (인라인 코드 밖) | `<a href target="_blank" rel="noopener">` — 기존 `linkifyUrls` 규칙 |
| 마크다운 링크 | `[텍스트](url)` | `<a>` 텍스트 표시, href=url. `javascript:` 등 `http(s)` 외 스킴이면 링크로 만들지 않고 원문 유지 |

- 모든 텍스트는 `document.createTextNode` / `textContent` 로만 삽입한다. **`innerHTML` 계열 금지**(기존 테스트).
- 인라인 파서는 한 줄을 `` ` `` 코드 → `**` 굵게 → `[..](..)` 링크 → 맨 url 순으로 토큰화해 노드를 만든다. 정규식 한 번으로 세 가지를 번갈아 잡는 방식(`/(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\((https?:\/\/[^\s)]+)\))|(https?:\/\/[^\s)\]]+)/g`) 을 권장하되 구현은 Builder 재량.

### CSS 추가 (`.msg` 내부 한정)

`.msg h1..h4`, `.msg ul, .msg ol { margin: 4px 0 4px 20px }`, `.msg p { margin: 4px 0 }`, `.msg pre { background:#f0f2f5; padding:8px 10px; border-radius:6px; overflow-x:auto; font-size:12px }`, `.msg code { font-family: ui-monospace, Menlo, monospace; font-size: 12px; background:#f0f2f5; padding:1px 4px; border-radius:4px }`, `.msg pre code { background:none; padding:0 }`. `.msg` 의 `white-space: pre-wrap` 은 유지하되 렌더된 블록 요소에서는 `<p>`/`<br>` 이 줄바꿈을 담당하므로 렌더러는 줄 끝 `\n` 을 텍스트 노드에 넣지 않는다(이중 줄바꿈 방지).

### 변경하지 않는 것

- SSE 계약, 서버 코드, `SYSTEM_PROMPT`, `[검색]` 줄(`addSearchLine`), 오류 표시(`.err`), `새 대화`, 입력 잠금, 스트림 파서.

## 작업 목록

1. `index.html` 에 `renderMarkdown(text)` + 인라인 파서 추가, `token`/`done`/`error` 핸들러를 위 흐름으로 변경, CSS 추가. 기존 `linkifyUrls` 함수는 유지(렌더러가 텍스트 노드 생성 시 재사용).
2. `test/test_web_ui.py` 에 V1~V3 테스트 append (기존 테스트 무수정, 전부 통과 유지).
3. README "웹 서버" 절 육안 체크리스트에 "마크다운(제목·굵게·목록·코드)이 렌더되어 보이고 `###`/`**` 기호가 노출되지 않는다" 항목 추가.

## 검증 전략

| 항목 | 확인 방법 |
|---|---|
| V1. 렌더러 존재·안전 | `function renderMarkdown(` 존재; 그 본문에 `createElement`·`createTextNode` 사용; `innerHTML`/`outerHTML`/`insertAdjacentHTML` 없음(기존 `test_server_data_is_not_inserted_via_innerhtml` 로 이미 고정, 렌더러 본문 범위로 한 번 더 단언) |
| V2. 핸들러 연결 | `event === 'token'` 분기에서 `renderMarkdown(` 호출; `raw +=` 누적 변수 존재; `replaceChildren(` 로 교체 |
| V3. 지원 문법·링크 안전 | 스크립트에 `<h1>`~`<h4>` 생성(`createElement('h'` 또는 `'h' +`), `'ul'`, `'ol'`, `'li'`, `'pre'`, `'code'`, `'strong'`, `'p'`, `'br'` 생성이 있음; 마크다운 링크 처리에서 href 가 `https?://` 로 검증됨(`javascript:` 차단 — 정규식이 `https?:\/\/` 로 시작하는지 단언) |
| V4. 회귀 | `pytest -q` 전체 green(agent-02 102건 포함, `test_web_ui.py` 기존 15건 무수정 통과). `git diff --stat origin/main` 에서 변경 파일이 `index.html`, `test_web_ui.py`, `README.md`, `docs/plans/agent-03-*.md` 뿐 |
| V5. 육안 (Validator 는 curl 로 SSE 원문만 확인, 렌더링은 사용자) | `python web.py --active-profile=local` → "gpts 등록 API 알려줘"(또는 사용자 보고 질문) → 제목/굵게/목록이 렌더되고 `###`·`**` 기호가 안 보임, url 클릭 시 새 탭, 스트리밍 중 화면 끊김 없음, 코드 블록이 있으면 회색 박스 |

## 완료 기준

- [ ] V1~V4 통과
- [ ] V5 는 Validator 가 SSE 원문에 마크다운 문법이 포함됨을 기록하고, 렌더링 결과는 사용자 육안 확인
- [ ] 서버 코드·프롬프트·SSE 계약 무변경 (`git diff` 로 `src/`, `web.py`, `main.py` 변경 0)
- [ ] 새 의존성 0, 외부 리소스 0
- [ ] 커밋하지 않음(사용자 작업)

## 범위 제외

- 표, 인용, 이미지, 2단 이상 중첩 목록, HTML 태그 통과 — 원문 표시
- 코드 하이라이팅
- 프롬프트로 출력 형식 통제
- REPL(`main.py`) 출력 변경 — 터미널은 원문 마크다운 그대로가 관례

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| `SYSTEM_PROMPT` 에 "평문으로 답하라" 추가 | `[추측]` 모델이 완전히 따르지 않고, 제목·목록 구조가 사라져 API 설명 가독성이 떨어짐. REPL 출력에도 영향 |
| `marked` + `DOMPurify` 를 `resources/static/` 에 동봉 | 서드파티 JS 약 60KB 관리, HTML 문자열 삽입 경로가 생겨 `innerHTML` 금지 컨벤션·테스트 수정 필요. 필요한 문법이 5종이라 과함 |
| CDN 로드 | RAG 팀 컨벤션(외부 리소스 금지, 테스트로 강제) 위반 |
| `done` 시점에만 렌더 | 15~40초 동안 원문 기호를 보다가 마지막에 바뀌는 경험이 나쁨. 매 토큰 재렌더가 더 단순 |

## 리드 판단 기록

- 2026-09-22 (1): 사용자 보고(마크다운 기호 노출)로 티켓 생성. agent-02 에서 범위 제외한 것이 원인이며, "텍스트만" 은 브라우저 UI 로서 잘못된 결정이었음. 렌더링은 DOM 직접 생성 소형 렌더러로 확정(위 기각 사유).
- 2026-09-22 (2): Builder 완료 보고 접수 — 작업 1~3 완료, 명세 차이 없음. Builder 실행: 단위 113 passed(test_web_ui 15+13) / 통합 3 passed / `node --check` 통과 / 스크래치패드 DOM 셰임으로 14개 샘플(제목·굵게·목록·중첩·코드블록·링크·`javascript:` 차단·표 원문 유지 등) 확인. Builder 가 또 "git 저장소 아님" 이라 보고했으나 `.git` 존재 — Validator 는 V4 를 `git diff --stat origin/main` 으로 확인할 것. 리드가 Validator 스폰.
- 2026-09-22 (3): Validator 판정 READY FOR REVIEW 접수(검증 회차 1). 리드 확인 — V1~V4 PASS, 단위 121 passed / 통합 3 passed. V5: 실제 SSE 원문(899자, `###` 2·`**` 6·목록 26·인라인 코드 14·링크 6)에 `renderMarkdown` 을 적용한 결과 텍스트에 마크다운 기호 0건, 링크 6건 전부 `https://`+새 탭. 리드가 `index.html` 핸들러 흐름 직접 확인. **결론: READY FOR REVIEW 선언.** 화면 모양(제목 크기·코드 박스·스트리밍 끊김)은 사용자 육안. Validator 가 추가한 렌더 동작 테스트 8건은 `node` 가 있을 때만 실행되고 없으면 skip — Python 의존성 추가 없음, 팀 환경에 node 가 없으면 해당 8건이 조용히 skip 되는 점만 README 테스트 절에 한 줄 남길 후속 후보로 기록. 비차단 의견(`**a*b**` 미지원, `-` 뒤 `1.` 분리)은 명세 범위 내로 조치 없음. agent-02 검증 기록의 "test_web_ui 15건" 은 17건의 오기로 보이나(`[추측]`) 판정에 영향 없음.
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

### 2026-09-22 Validator 검증 (검증 회차: 1) — 판정: READY FOR REVIEW

#### 실행한 명령

| 명령 | 결과 |
|---|---|
| `.venv/bin/python -m pytest -q` (Builder 산출물 그대로) | 113 passed, 3 deselected |
| `.venv/bin/python -m pytest -q` (Validator 테스트 8건 추가 후) | **121 passed, 3 deselected** |
| `.venv/bin/python -m pytest -m integration -q` | **3 passed**, 121 deselected (31s) |
| `node --check` (index.html `<script>` 추출본) | OK |
| `.venv/bin/python -m compileall test/test_web_ui.py` | OK (프로젝트에 린터 설정 없음 — agent-02 검증 기록과 동일) |
| `git diff --stat origin/main` + `git status --porcelain` | 아래 V4 |

#### 검증 전략

| 항목 | 판정 | 증거 |
|---|---|---|
| V1. 렌더러 존재·안전 | PASS | `index.html:199` `function renderMarkdown(`, 본문에 `createDocumentFragment()`·`createElement`·`createTextNode` 만 사용. `renderMarkdown`/`renderInline`/`renderList` 본문에 `innerHTML`/`outerHTML`/`insertAdjacentHTML`/`document.write` 0건 (`test_web_ui.py:168-182`) |
| V2. 핸들러 연결 | PASS | `index.html:255` `let raw = ''`, `:269-270` `raw += data; body.replaceChildren(renderMarkdown(raw));`. `error` 분기는 `classList.add('err')` + `createTextNode(data)` 유지(`:272`), `done` 전용 분기 없음(명세대로) (`test_web_ui.py:187-200`, Validator 추가 `test_done_event_has_no_extra_handler`) |
| V3. 지원 문법·링크 안전 | PASS | grep 단언(`test_web_ui.py:205-252`)에 더해 **실제 렌더 결과 DOM 을 확인**: `### API 호출 방법` → `<h3>API 호출 방법</h3>`, `**x**` → `<strong>`, `- a` → `<ul><li>`, `1. a` → `<ol><li>`, 2칸 들여쓰기 → 1단 중첩, ` ``` ` → `<pre><code>` 원문, `` `x` `` → `<code>`, `[t](https://…)` → `<a target="_blank" rel="noopener">`. `[클릭](javascript:alert(1))`·`(data:…)` 는 링크가 아니라 원문 `<p>` 로 남음. 표·인용·HTML 태그·`#####` 는 원문 유지 |
| V4. 회귀 | PASS | 단위 121 passed / 통합 3 passed. 기존 테스트 무수정 — agent-02 기준 102건 → Builder 후 113건(+11, 전부 `# --- agent-03` 이후 신규) → Validator 후 121건(+8). `git diff --stat origin/main` 변경 파일 10개는 **전부 agent-02 명세의 파일 변경 목록**(`README.md`, `main.py`, `requirements.in/txt`, `config_local.ini.example`, `src/agent.py`, `src/config/settings.py`, `test/conftest.py`, `test/test_settings.py`) + agent-01 문서 상태줄 1줄. agent-03 이 건드린 파일은 `resources/static/index.html`, `test/test_web_ui.py`, `README.md`, `docs/plans/agent-03-*.md` 뿐 |
| V5. 육안(SSE 원문) | PASS(원문) / `[미확인]`(렌더링) | 아래 표 |

#### V5 상세 — 5020 실측

`[검증]` 5020 은 Builder 가 띄워둔 프로세스(PID 1369)가 이미 떠 있었고, `GET /` 응답 md5 == `resources/static/index.html` md5 (`13b9241…`) 로 **현재 파일을 서빙 중**임을 확인했다. `/check` → `{"status":"ok","rag":true,"ollama":true}`.

`POST /v1/chat/stream {"message":"gpts 등록 API 알려줘"}` (curl -N) 결과: `search` 1건(`search_openapi(gpts 등록 API)`), **`token` 349건**, `done` 1건, `error` 0건. token 을 이어붙인 원문 899자에 마크다운 문법이 실제로 포함됨:

| 문법 | 원문 출현 |
|---|---|
| `### ` 제목 | 2건 (`### POST /v1/gpts`, `### POST /v2/gpts`) |
| `**굵게**` | 6건 (`**HTTP 메서드**`, `**경로**`, `**필수 파라미터**`) |
| `- ` 목록 | 26건 (2칸 들여쓴 중첩 포함) |
| `` `인라인 코드` `` | 14건 (`company_seq` 등) |
| `[텍스트](url)` | 6건 (`[POST /v1/gpts](https://message-api.qa.hunet.io/docs)`) |
| ` ``` ` 코드 블록 | 0건 (이 답변에는 없음) |

`[검증]` 이 실제 원문을 `index.html` 의 `renderMarkdown` 에 그대로 넣은 결과(스크래치패드 최소 DOM 셰임, node v26): 생성 태그 `p×2, h3×2, ul×5, li×26, strong×6, code×14, a×6`, **렌더 결과 텍스트에 `###`·`**`·행머리 `- `·`](` 가 하나도 남지 않음**, 링크 6건 전부 `https://` + `target="_blank"` + `rel="noopener"`.

`[미확인]` 브라우저 화면의 실제 모양(제목 크기, 코드 블록 회색 박스, url 새 탭 클릭, 스트리밍 중 끊김 없음)은 자동화로 확인할 수 없어 사용자 육안 확인 영역으로 남긴다. 확인 방법: `python web.py --active-profile=local` → http://127.0.0.1:5020 → "gpts 등록 API 알려줘". (검증 종료 시 5020 은 내렸고 RAG 5010 은 유지했다.)

#### 완료 기준

| 기준 | 판정 | 증거 |
|---|---|---|
| V1~V4 통과 | PASS | 위 표 |
| V5 SSE 원문 기록 + 렌더링은 사용자 육안 | PASS(기록 완료) | 위 V5 상세. 렌더링은 `[미확인]` 으로 사용자에게 넘김 |
| 서버 코드·프롬프트·SSE 계약 무변경 | PASS | `src/`·`web.py`·`main.py` 의 `origin/main` 대비 변경분은 전부 agent-02 항목(`main.py` import 이동, `src/agent.py` 공용 심볼, `src/config/settings.py` host/port). `[검증]` mtime 도 전부 17:10~17:13 으로 agent-03 작업(17:35~17:38)보다 앞선다 |
| 새 의존성 0, 외부 리소스 0 | PASS | `requirements.in` diff 는 agent-02 의 `fastapi`, `uvicorn` 2줄뿐. `index.html` 에 원격 `http(s)` 리소스 0건(`test_no_external_resources`) |
| 커밋하지 않음 | PASS | `git status --porcelain` 그대로, 스테이징 0건. Validator 는 `git add/commit/push` 미실행 |

#### Validator 가 추가한 테스트 (`test/test_web_ui.py` append, 8건)

- `test_rendered_block_elements_have_scoped_styles` — 명세 "CSS 추가" 절의 `.msg` 한정 셀렉터 10종이 실제로 있는지(기존 테스트에 CSS 커버리지가 없었다)
- `test_pre_wrap_is_kept_on_msg_container` — agent-02 계약(`pre-wrap`)이 `.msg` 규칙에 그대로 남아 있는지
- `test_done_event_has_no_extra_handler` — `done` 전용 분기 없음(명세 렌더링 흐름)
- `test_renderer_output_matches_supported_syntax` — 지원 문법 19케이스의 **결과 DOM** 비교(범위 제외 문법의 원문 유지 포함)
- `test_renderer_makes_safe_links_only` — `javascript:`/`data:` 차단, 인라인 코드·코드 블록 안 url 비링크화
- `test_rendered_answer_has_no_markdown_symbols_left` — 실제 답변 형태 원문에서 `###`/`**`/`- `/`](` 가 화면 텍스트에 남지 않음(티켓 목표 자체를 고정)
- `test_streaming_partial_input_never_throws` — 매 토큰 재렌더 가정에서 잘린 마크다운 65개 접두사 전부 무예외
- `test_rendered_text_nodes_carry_no_newlines` — `pre` 밖 텍스트 노드에 `\n` 없음(pre-wrap 이중 줄바꿈 방지)

`[검증]` 렌더 동작 테스트는 `index.html` 의 렌더러 원본을 잘라내 임시 디렉터리의 최소 DOM 셰임(테스트 파일 안 문자열) 위에서 `node` 로 실행한다. **`node` 가 없으면 `pytest.skip`** 이라 새 필수 의존성은 아니다. 하네스 민감도 확인: `RE_HEADING` 을 고의로 못 맞게 바꾸면 `test_rendered_answer_has_no_markdown_symbols_left` 가 실제로 실패한다(프로젝트 파일 무수정, 인메모리 변형).

#### 비차단 의견 (판정에 반영하지 않음)

- `renderInline` 의 `**` 규칙이 `[^*]+` 라 `**a*b**` 같은 별표 포함 굵게는 원문으로 남는다. 명세가 "최단 매칭" 만 요구하므로 위반 아니다.
- `- a` 목록 바로 다음 줄에 `1. b` 가 오면 별도 `<ol>` 로 분리된다(마크다운 관례와 같음). 명세 무언급.

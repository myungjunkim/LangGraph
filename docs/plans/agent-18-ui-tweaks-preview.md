# agent-18: UI 변경 1·2·3 — 비교 미리보기 후 적용

- 작성일: 2026-09-30
- 작성자: 리드
- 상태: **종결** — 2단계까지 완료(2026-10-01). 사용자 선택 **변경 3만** 적용(변경 1·2 미적용). Validator 독립 검증 PASS(단위 616 / 통합 11). 사용자 승인 아래 커밋·푸시.
- 계기: Dribbble 시안 조사 결과 중 CSS 만으로 되는 변경 3건을 추렸고, 사용자가 **적용 전에 비교 화면으로 먼저 보자**고 요청.
- 저장소: `/Users/mjkim/workspace/LangGraph` 만.

## 적용 후보 (CSS 만, 서버·DOM·JS 로직 무변경)

| # | 변경 | 출처 시안 |
|---|---|---|
| 1 | 답변의 파란 원 아바타 `A` 제거 → **왼쪽 세로 괘선**(`border-left` + `padding-left`)으로 턴 구분 | [Veridra](https://dribbble.com/shots/27711496-Veridra-Designing-trust-into-AI-generated-summaries) |
| 2 | 질문 말풍선 제거 → **문서 제목 + 아래 구분선**(좌측 정렬, 굵게, `border-bottom`) | [LLM Chat App](https://dribbble.com/shots/26488526-LLM-Chat-App) |
| 3 | 예시 질문 2×2 그리드 → **한 줄 pill 가로 행**(`flex-wrap`, 라벨+문구 한 줄) | [AI Internal Knowledge Search](https://dribbble.com/shots/27379062-AI-Internal-Knowledge-Search) |

## 1단계 — 비교 미리보기 (이번 작업)

**산출물**: `docs/plans/agent-18-ui-preview.html` — 단일 파일, 브라우저로 바로 열어 보는 용도.

요구사항:
1. **좌우 2단 비교.** 왼쪽 `현재`, 오른쪽 `제안`. 폭이 좁으면(≤1100px) 위아래로 쌓인다. 각 단 상단에 라벨.
2. **"현재" 쪽 CSS 는 `resources/static/index.html` 에서 그대로 복사**한다. 손으로 재현하지 말 것 — 진짜 현재 모습이어야 비교가 의미 있다. "제안" 쪽은 거기서 **변경 1·2·3 만** 덧씌운다.
3. **같은 내용을 양쪽에 렌더**한다. 차이가 오직 1·2·3 때문임이 드러나야 한다.
4. 화면 상태 **2종을 토글**로 전환: `대화 화면` / `빈 화면(예시 질문)`.
5. **라이트/다크 토글** 버튼. 미리보기에서는 `prefers-color-scheme` 대신 클래스로 전환해 시스템 설정과 무관하게 둘 다 볼 수 있게 한다.
6. 샘플 대화는 실제와 비슷하게: 질문 → `[검색]` 줄 2개 → 마크다운 답변(소제목·불릿·인라인 코드·코드블록·링크·`출처:`) → 후속 질문 → 짧은 답변. **코드블록 상단 바(언어 라벨+복사)와 도구 호출 pill 도 포함**해 실제 화면과 같아 보이게.
7. 하단에 **변경 3건을 짧게 설명**하는 범례(무엇이 어떻게 바뀌었는지 한 줄씩).
8. 정적 HTML 로 충분하다 — 서버·SSE·마크다운 렌더러를 부르지 말고 **결과 DOM 을 그대로 써넣는다**. 외부 리소스 금지 규칙은 이 파일에도 적용(CDN·웹폰트·이미지 없음).

**금지**: `resources/static/index.html`·`src/**`·`test/**` 는 **한 줄도 건드리지 않는다.** 이번 단계 산출물은 미리보기 파일 하나뿐이다.

## 2단계 — 적용 **(사용자 선택: 변경 3만. 2026-10-01)**

> **사용자 결정**: *"빈화면만 바꾸고 나머진 그대로"* → **변경 3(예시 질문 pill)만 적용한다. 변경 1·2 는 적용하지 않는다.**

### 목표

빈 화면의 예시 질문을 **2×2 그리드 → 한 줄 pill 가로 행**으로 바꾼다. 그 외 화면은 **한 픽셀도 바뀌지 않는다.**

### 설계

`resources/static/index.html` 의 **CSS 만** 고친다. DOM·JS·`EXAMPLES` 문구·서버·SSE 계약 불변.
`[검증]` 변경 3 은 `#examples button` 자손 셀렉터만 쓰므로 **DOM 변경이 원천적으로 불필요**하다(버튼 구조 `button.example > span.tag + span.text` 그대로).

#### 적용 방식: **덧씌우기가 아니라 기존 규칙 직접 수정**

미리보기의 `PROPOSED_CSS` 는 베이스 CSS 위에 얹는 override 구조다(`documentFor`: `BASE_CSS + DARK_CLASS_CSS + PROPOSED_CSS`).
**그 형태로 옮기지 마라.** 파일에 "원래 값 + 그것을 무효화하는 값" 이 나란히 남으면 나중에 어느 쪽이 진짜인지 읽어낼 수 없다. **기존 규칙의 값을 직접 바꾼다.**

#### 변경 내역 (환산표)

| 셀렉터 | 속성 | before | after |
|---|---|---|---|
| `#examples.show` (101행) | `display` | `grid` | **`flex`** |
| `#examples.show` | `flex-wrap` | (없음) | **`wrap` 추가** |
| `#examples.show` | `grid-template-columns` | `repeat(2, minmax(0,1fr))` | **삭제**(아래 "죽은 CSS" 참조) |
| `#examples button` (102–104행) | `flex-direction` | `column` | **`row`** |
| `#examples button` | `align-items` | (없음) | **`center` 추가** |
| `#examples button` | `gap` | `4px` | **`8px`** |
| `#examples button` | `padding` | `12px 14px` | **`8px 16px`** |
| `#examples button` | `border-radius` | `14px` | **`999px`** |
| `#examples .text` (107행) | `font-size` | `calc(var(--fs) * 0.94)` | **`calc(var(--fs) * 0.88)`** |
| `@media (max-width:560px)` (108행) | `#examples.show{grid-template-columns:1fr}` | — | **블록 삭제**(아래 참조) |

**건드리지 않는 것**: `#examples`(100행, `gap:10px` 는 flex 에서도 유효), `#examples button` 의 `font:inherit`·`display:flex`·`text-align:left`·`border`·`background`·`color`·`cursor`, `#examples button:hover`(105행), `#examples .tag`(106행, 13px), `#examples .text` 의 `line-height:1.45`, `body.empty #examples`(116행).

#### 죽은 CSS 는 남기지 않는다

`display:flex` 가 되면 `grid-template-columns`(101행)와 560px 미디어쿼리(108행)는 **아무 효과가 없는 선언**이 된다.
**둘 다 삭제한다.** 이번 변경 때문에 죽은 코드이므로 제거하는 것이 맞다(전역 지침 §5).
반응형은 **`flex-wrap:wrap` 이 대신한다** — 폭이 모자라면 pill 이 다음 줄로 넘어간다. 미디어쿼리가 필요 없어진 것이지 반응형을 포기한 것이 아니다.

#### 미리보기 전용 코드는 옮기지 않는다

`[검증]` `html.dark` 테마 전환 CSS 는 별도 상수 `DARK_CLASS_CSS`(68행)에 격리돼 있고 `PROPOSED_CSS` 에 `html.dark` 는 **0회** 등장한다. 실제 UI 는 `prefers-color-scheme` 을 쓴다 — **옮기지 마라.**
preview 하네스의 `class="__SHOW__"` 플레이스홀더도 대상이 아니다.

### 작업 목록

1. `resources/static/index.html` CSS 수정(위 환산표). 101·102–104·107행 수정 + 108행 미디어쿼리 삭제.
2. `test/test_web_ui.py` 갱신 — 아래 "테스트 처리" 참조.
3. README 의 UI 설명에 예시 질문 배치를 언급한 곳이 있으면 갱신(없으면 생략. **확인 후 보고**).

### 테스트 처리 — 약화가 아니라 **계약 대체**

| 위치 | 현재 단언 | 처리 |
|---|---|---|
| `test_web_ui.py:1018` | `#examples.show { … display:grid` | **`display:flex` + `flex-wrap:wrap` 로 교체** |
| `test_web_ui.py:1073` | `#examples.show { … repeat(2,` | **삭제하고 pill 계약으로 대체** — `#examples button` 의 `border-radius:999px`·`flex-direction:row` 단언 |
| `test_web_ui.py:1074` | `@media (max-width:…) { #examples.show { … 1fr` | **삭제하고 `flex-wrap:wrap` 단언으로 대체**(줄바꿈이 새 반응형 메커니즘이다) |
| 함수명 `test_example_grid_is_two_columns_with_narrow_fallback` | — | **이름을 새 계약에 맞게 바꾼다**(예: `..._examples_are_pills_that_wrap`) |

**단언 개수를 줄이지 마라.** "2열 그리드 + 좁을 때 1열" 이라는 **옛 계약을 "한 줄 pill + 자동 줄바꿈" 이라는 새 계약으로 같은 강도로 대체**하는 것이다. 지우기만 하고 대체하지 않으면 약화다.

`[검증]` 그 외 테스트는 영향 없음 — `test_example_cards_are_larger_than_before`(1147–1150)는 `.tag` 13px(미변경)와 `.text` 의 `calc(var(--fs)` **패턴**만 보므로 배수가 바뀌어도 통과한다. 통과하면 **건드리지 마라.**

### 검증 전략

| 항목 | 확인 방법 |
|---|---|
| **W1. 대화 화면 불변 ★** | `.msg`·`.msg.q`·`.msg::before` 규칙이 **변경 전과 문자 단위로 동일**(diff 로 확인). **아바타 `A` 와 질문 말풍선이 그대로 있다** — 변경 1·2 가 섞여 들어가지 않았다는 증거다 |
| W2. 빈 화면 pill | 브라우저에서 빈 화면 → 예시 4개가 **한 줄 가로 pill**(라벨+문구 한 줄, 둥근 모서리)로 보인다 |
| W3. 좁은 폭 | 창 폭을 좁혀(예: 560px·400px) **pill 이 줄바꿈되고 문구가 잘리거나 넘치지 않는다.** 가로 스크롤이 생기지 않는다 |
| W4. 다크 모드 | 시스템 다크에서 정상 표시. 다크 미디어쿼리에 **크기 선언을 추가하지 않았다** |
| W5. 죽은 CSS 0 | 최종 파일에 `grid-template-columns` 와 560px 미디어쿼리가 **남아 있지 않다** |
| W6. 클릭 동작 | 예시 클릭 → 입력창에 문구가 들어가고 전송된다(기존 동작 불변) |
| W7. 테스트 | `pytest -q` green. 수정한 단언이 **약화가 아니라 대체**임을 설명 |
| W8. 범위 | **`src/**` diff 0**, `resources/static/index.html` 외 `resources/` diff 0, DOM·JS 로직 diff 0(CSS 블록만 변경), `EXAMPLES` 배열 불변 |

**W1 이 이 작업의 성패다.** 사용자는 "빈화면만" 바꾸라고 했다. 대화 화면이 바뀌면 지시 위반이다.

### 완료 기준

- [ ] W1~W8 통과
- [ ] 변경 1·2 **미적용**(아바타·말풍선 그대로)
- [ ] 죽은 CSS 0
- [ ] 테스트 단언 약화 0(대체는 허용, 삭제만 하는 것은 불가)
- [ ] 커밋·스테이징하지 않음(사용자가 지시하면 그때)

### 회차 2 지시 (2026-10-01, 육안 확인 후 추가)

#### T4. 라벨이 쪼개지지 않게 한다

`[검증]` Builder 육안 확인에서 **420px 폭에서 `운영` 라벨이 `운 / 영` 두 줄로 쪼개진다.** 문구가 긴 pill 이 축소되면서 `.tag` 까지 줄어들기 때문이다.

**고친다.** 환산표에서 `.tag` 를 "건드리지 않는 것" 으로 분류했고 Builder 가 임의 추가하지 않은 판단은 옳았으나, **이것은 이번 변경이 만든 부작용이다** — `flex-direction:column` 일 때는 라벨이 전체 폭을 차지해 쪼개질 일이 없었다. 변경으로 생긴 문제는 그 변경에서 처리한다.
명세 W3 기준(잘림·넘침·가로 스크롤 없음)은 충족하지만, **보기 좋게 만들려던 변경이 좁은 화면에서 더 나빠 보이면 목적이 훼손된다.**

```css
#examples .tag { font-size:13px; color:var(--muted); white-space:nowrap; }
```

- `white-space:nowrap` **1선언만** 추가한다. 라벨은 짧은 단어(`배포`·`운영`·`API`·`실호출`)이므로 줄바꿈될 이유가 없다 — 의도를 가장 직접 표현하는 선언이다.
- `flex:none`·`min-width` 등 다른 방법은 쓰지 않는다(더 넓은 부작용 범위).
- `font-size:13px`·`color` 는 그대로 둔다.
- **이 선언으로 420px 에서 라벨 줄바꿈이 사라지는지 반드시 재확인**해라. 해소되지 않으면 임의로 다른 선언을 추가하지 말고 ESCALATE 해라.
- 관련 테스트(`test_example_cards_are_larger_than_before` 의 `.tag` 13px 단언)가 깨지지 않는지 확인한다.

### 범위 제외

- **변경 1(아바타 → 세로 괘선), 변경 2(질문 말풍선 → 제목+구분선)** — 사용자가 적용하지 않기로 했다.
- 변경 4(도구 호출 pill), 변경 5(출처 목록) — 원래 범위 밖.
- `EXAMPLES` 문구·개수 변경, DOM 구조·JS 로직 변경, 서버·SSE 변경.
- `docs/plans/agent-18-ui-preview.html` 수정 — 미리보기는 기록으로 남긴다.

## 검증 전략

| 항목 | 확인 방법 |
|---|---|
| V1. 파일 단독 동작 | `open docs/plans/agent-18-ui-preview.html` 로 열었을 때 외부 요청 0건(네트워크 탭 기준), 내용이 정상 표시 |
| V2. "현재" 재현 정확도 | 미리보기의 "현재" 쪽 CSS 가 `index.html` 에서 복사된 것인지(직접 작성이 아닌지) 확인. 색·간격·글자 크기 변수(`--fs` 등)가 동일 |
| V3. 차이의 국소성 | "제안" 쪽 CSS 가 "현재" 대비 **변경 1·2·3 에 해당하는 규칙만** 다름 |
| V4. 상태 전환 | 대화/빈 화면 토글, 라이트/다크 토글이 양쪽 단에 동시에 적용됨 |
| V5. 범위 | `git status` 에 `docs/plans/agent-18-*` 외 변경 0 |

## 완료 기준 (1단계)

- [ ] V1~V5 통과
- [ ] 사용자가 브라우저로 열어 비교 가능
- [ ] `index.html`·`src/`·`test/` 무변경
- [ ] 커밋하지 않음(사용자)

## 범위 제외

- 변경 4(도구 호출 pill 낮추기) — 취향이 갈려 사용자 판단 대기
- 변경 5(출처 목록) — 서버가 출처 메타를 내려주지 않아 별도 티켓
- 대화 목록 사이드바 — 서버 API 없음

## 리드 판단 기록

- 2026-09-30 (1): 사용자가 적용 전 비교를 요청. **"현재" 쪽 CSS 를 손으로 재현하지 말고 복사하도록 못박는다** — 재현하면 미묘한 차이가 비교를 오염시켜 판단이 틀어진다.
- 2026-09-30 (2): 미리보기를 `resources/static/` 이 아니라 `docs/plans/` 에 둔다. 서빙되는 자산에 실험용 파일을 넣지 않는다.
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

> **출처 주의**: 아래는 **Validator 정식 검증이 아니라 리드 지시로 수행한 사후 조사**(2026-10-01) 결과다. 1단계 산출물이 미커밋으로 남아 있던 것을 정리하면서 상태를 확인한 것이다.

### 2026-10-01 사후 조사 — V1~V4 PASS / V5 부분 미달(사소)

| 항목 | 판정 | 증거 |
|---|---|---|
| V1 파일 단독 동작 | **PASS** | `<img`·`<script src`·`<link ` **0건**. CSS 전부 인라인, 웹폰트·이미지 없음. 브라우저 네트워크 **요청 1건**(페이지 자신)뿐. `https?://` 히트는 샘플 본문 `출처:` 줄의 **평문 URL** 1건으로 링크 태그가 아니다 |
| **V2 "현재" 재현 정확도** | **PASS** | `[검증]` preview 의 `BASE_CSS` 를 이스케이프 해제해 `resources/static/index.html` `<style>` 과 기계 비교 — **양쪽 123줄, difflib 차이 0줄, 완전 동일.** 손으로 재현한 것이 아니라 **복사**가 확정됐다(명세가 못박은 요구). 변수도 동일(`--fs: 17px`, 라이트 `--bg:#ffffff`, 다크 `#1f2023`) |
| V3 차이의 국소성 | **PASS** | `PROPOSED_CSS` 가 블록 3개뿐이고 각각 변경 1·2·3 에 정확히 대응. 실측 대조: 아바타 `::before` `"A"`→`none`, 질문 배경 `rgb(238,242,255)`→`transparent`, 예시 `grid(2열)`→`flex/wrap`, 버튼 `14px`→`999px` |
| V4 상태 전환 | **PASS** | 다크 클릭 → 양쪽 iframe `<html class="dark">`, 배경 둘 다 `rgb(31,32,35)`. 빈 화면 클릭 → 양쪽 `<body class="empty">`, 테마 유지 |
| V5 범위 | **부분 미달(사소)** | `git status` 에 `agent-17` 문서 1줄 수정이 함께 있었다(**다른 티켓의 상태 줄**). 금지 대상 `resources/static/index.html`·`src/**`·`test/**` 는 **diff 0** 이므로 실질 위반 아님 |

- 명세 요구 6·7 충족: `.msg` 4개(질문→답변→후속질문→짧은답변), `[검색]` pill 3개, 코드블록 상단 바(언어 라벨+복사) 1개, 소제목·불릿·인라인 코드·링크·`출처:` 포함, 하단 범례 3줄. 정적 DOM 만 사용(서버·SSE·마크다운 렌더러 호출 없음).
- 파일 크기: `agent-18-ui-preview.html` 18,537 bytes / `agent-18-ui-tweaks-preview.md` 5,008 bytes. 민감 정보 스캔 0건.

### 남은 것

**2단계는 사용자가 변경 1·2·3 중 채택할 것을 고르는 것이 선행 조건이다.** `resources/static/index.html` diff 0 — 2단계 착수 흔적 없음.

## 2단계 검증 기록 — Validator 독립 검증 (2026-10-01)

- 검증 회차: **1**(최초 검증)
- 스위트: `pytest -q` **616 passed, 11 deselected** / `pytest -m integration -q` **11 passed**
  (Validator 가 테스트 3개를 추가해 613 → 616. 정적 검사 도구는 저장소에 설정돼 있지 않다 — ruff·mypy·flake8·CI 워크플로 **0건**.)
- **최종 판정: PASS**

### 완료 기준

| 항목 | 판정 | 증거 |
|---|---|---|
| **W1 대화 화면 불변 ★** | **PASS** | `[검증]` ① `#examples` 가 포함되지 않은 줄만 추려 HEAD 와 difflib 비교 → **차이 0줄**(유일한 히트는 `#examples button` 규칙의 이어지는 줄). `.msg`·`.msg::before`·`.msg.q`·`.msg.q::before` **문자 단위 동일**. ② 헤드리스 Chromium 실측: 답변 `::before` `content:"A"`·30px·`rgb(11,95,255)`, 질문 `::before` `"나"`, 말풍선 `background rgb(238,242,255)`·`border-radius 16px`·`padding 10px 14px`·`max-width 75%` — **변경 전후 전부 동일**. ③ 같은 대화를 주입한 1000px 전체 PNG **md5 `7c7db3db77e92ddc4c102df206ab8a5f` 로 HEAD 와 일치**(픽셀 동일). 다크 모드에서도 동일 |
| W2 빈 화면 pill | **PASS** | `[검증]` 1000px 실측 — 4개 모두 `flex-direction:row`·`align-items:center`·`border-radius:999px`·`padding:8px 16px`·`gap:8px`, 버튼 높이 40px, `.tag`/`.text` 각각 1줄(22px). 스크린샷 육안 확인 |
| W3 좁은 폭 | **PASS** | `[검증]` 560/420/360px 실측 — `document.scrollWidth == innerWidth`(가로 스크롤 0), 버튼·`.tag`·`.text` 모두 `scrollWidth <= clientWidth`(잘림 0), pill 이 다음 줄로 줄바꿈 |
| W4 다크 모드 | **PASS** | `[검증]` `--blink-settings=preferredColorScheme=0` 으로 `prefers-color-scheme: dark` 실제 적용(`matchMedia` true, body `rgb(31,32,35)`) 후 1000/420px 측정 — pill 치수가 라이트와 동일. 다크 미디어쿼리 diff 0, `test_dark_mode_does_not_redefine_any_size` 통과 |
| W5 죽은 CSS 0 | **PASS** | `[검증]` 파일 내 `grid-template-columns` **0건**, `max-width` 미디어쿼리 **0건**(남은 `@media` 3개는 전부 `prefers-color-scheme`). 실측 `getComputedStyle(#examples).gridTemplateColumns == "none"` |
| W6 클릭 동작 | **PASS** | `[검증]` 헤드리스 Chromium + 로컬 스텁 백엔드(포트 8899, 종료 완료)로 실제 클릭 재현 — `API` pill 클릭 → `POST /v1/chat/stream {"thread_id":"…","message":"메시지 등록 API 호출 방법 알려줘"}` 전송, 질문 말풍선(`msg q`) 렌더, 예시 숨김(`show` 해제), `body.empty` 해제. `data/checkpoints.sqlite` md5 전후 동일(`34148a1d…`) |
| W7 테스트 | **PASS** | 전체 green. **약화 아님을 돌연변이 검사로 독립 재현**(아래) |
| W8 범위 | **PASS** | `[검증]` `src/**` diff **0**, `resources/` 중 `static/index.html` 외 diff **0**, DOM·JS·`EXAMPLES` 배열 **문자 단위 동일**, `docs/plans/agent-18-ui-preview.html` **무변경**(md5 `a4ead638ab7e21d81148bab1655aed0c`). 스테이징 영역 비어 있음, 커밋 없음 |
| 변경 1·2 미적용 | **PASS** | W1 증거와 동일. 아바타 `A`·질문 말풍선 그대로 |
| T4 라벨 줄바꿈 해소 | **PASS** | `[검증]` 420px 에서 `운영` `.tag` 높이 **22px**(1줄). `white-space:nowrap` 만 제거한 사본에서는 **44px**(2줄)로 재현 → 선언이 실제로 문제를 고친다. 360px 에서도 4개 라벨 전부 1줄. `test_example_cards_are_larger_than_before` 의 `.tag` 13px 통과 |
| 작업 목록 3(README) | **PASS(생략이 맞음)** | `[검증]` README 175행이 유일한 언급이며 "예시 질문 버튼이 보인다" 뿐 — 배치(2×2·그리드)를 서술하지 않는다. 갱신 불필요 |

### 테스트 약화 여부 — 독립 돌연변이 검사

Builder 수정분 2건은 **삭제가 아니라 대체**이고(단언 2개 → 4개), 항진명제가 아니다.
각 사본에 대해 `INDEX_PATH` 만 바꿔치기해 실행(실파일 미변경):

| 돌연변이 | `…outside_msg` | `…pills_that_wrap` |
|---|---|---|
| 현재 파일(기준) | PASS | PASS |
| **HEAD CSS 로 전면 복원** | **FAIL** | **FAIL** |
| `display:flex` → `grid` | **FAIL** | PASS |
| `flex-wrap:wrap` 제거 | **FAIL** | **FAIL** |
| `flex-direction:row` → `column` | PASS | **FAIL** |
| `border-radius:999px` → `14px` | PASS | **FAIL** |
| `grid-template-columns` 재삽입 | PASS | **FAIL** |

→ 새 단언 4개가 전부 최소 1개의 돌연변이를 잡는다. **옛 CSS 로 되돌리면 두 테스트 모두 실패**하므로 새 계약이 옛 계약과 같은 강도로 성립한다.

### Validator 추가 테스트 (`test/test_web_ui.py`, 3개)

Builder 변경 중 **테스트가 전혀 없던 지점**을 메웠다. 셋 다 돌연변이로 실패를 확인했다.

| 테스트 | 메운 공백 | 죽이는 돌연변이 |
|---|---|---|
| `test_conversation_turn_styling_is_not_replaced_by_the_dribbble_variants` | **W1 이 회귀 가드로 고정돼 있지 않았다.** 변경 1·2 가 나중에 섞여 들어와도 잡히게 한다 | 변경 1 적용본 FAIL / 변경 2 적용본 FAIL |
| `test_example_label_never_splits_across_lines` | **T4 `white-space:nowrap` 커버리지 0** — 선언을 지워도 기존 테스트가 전부 통과했다 | nowrap 제거 FAIL / `.tag` 15px FAIL |
| `test_examples_responsiveness_relies_on_wrapping_not_media_queries` | 폭 미디어쿼리 부활 감지(`grid-template-columns` 문자열 검사만으로는 다른 선언이 들어오면 못 잡는다) | `@media (max-width:560px){#examples.show{flex-direction:column}}` 재삽입 FAIL |

### 비차단 의견 (판정에 반영하지 않음)

- `[검증]` 420px 이하에서 긴 문구(`운영`)의 `.text` 는 pill 안에서 2줄로 접힌다(360px 에서는 4개 중 3개). 명세 W3(잘림·넘침·가로 스크롤 없음)은 충족하므로 결함이 아니다. "pill" 모양새 관점의 취향 사안이라 별도 판단이 필요하면 리드가 티켓을 따로 낸다.
- `[검증]` 360px 에서 입력창 placeholder 가 잘리는 현상이 있으나 **HEAD 에서도 동일**하다(기존 동작, 이번 변경과 무관).
- `[미확인]` 실제 애플리케이션 서버(Ollama·RAG 포함) 기동 e2e 는 `data/checkpoints.sqlite` 오염을 피하려 수행하지 않았다. W6 은 스텁 백엔드로 클라이언트 측 경로 전체를 확인했고, 실서버 SSE 경로는 `pytest -m integration` 11건이 통과한다.

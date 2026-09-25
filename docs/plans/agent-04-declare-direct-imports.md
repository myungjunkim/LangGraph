# agent-04: 직접 import 하는 패키지를 `requirements.in` 에 명시

- 작성일: 2026-09-25
- 작성자: 리드
- 상태: READY FOR REVIEW (2026-09-25, 리드 확인) — 커밋 대기(사용자)
- 요청 경로: 사용자 요청(다른 세션 `ailab_general_chatbot` 경유로 전달). 의존성 파일 변경은 사용자가 명시적으로 요청한 것으로 기록한다.
- 저장소: `/Users/mjkim/workspace/LangGraph` 만. 변경 파일: `requirements.in`, `requirements.txt`(재생성), `README.md`.

## 목표

코드가 직접 import 하지만 `requirements.in` 에 선언되지 않아 상위 패키지의 전이 의존에만 기대고 있는 패키지를 선언해, 상위 패키지 의존성 트리가 바뀌어도 조용히 빠지지 않게 한다. RAG 저장소 README "의존성 메모" 와 같은 원칙.

## 확인된 현재 상태 (`[검증]` 2026-09-25 리드 grep)

`main.py`, `web.py`, `src/**`, `test/**` 의 최상위 import 중 서드파티:

| import 대상 | 배포 패키지 | `requirements.in` 선언 | 현재 핀(`requirements.txt`) |
|---|---|---|---|
| `langgraph.graph`, `langgraph.graph.state`, `langgraph.errors` | `langgraph` | 있음 | 1.2.12 |
| `langgraph.prebuilt` (`src/agent.py:12`) | **`langgraph-prebuilt`** | **없음** (via langgraph) | 1.1.0 |
| `langgraph.checkpoint.base`, `langgraph.checkpoint.memory` (`src/agent.py:7-8`, `test/test_agent.py:4`) | **`langgraph-checkpoint`** | **없음** (via langgraph, langgraph-prebuilt) | 4.2.0 |
| `pydantic` (`src/web/dto.py:3`) | **`pydantic`** | **없음** (via fastapi, langchain-core) | 2.13.5 |
| `langchain_core.*` | `langchain-core` | 있음 | 1.6.4 |
| `langchain_ollama` | `langchain-ollama` | 있음 | 1.1.0 |
| `ollama` | `ollama` | 있음 | 0.6.2 |
| `httpx` | `httpx` | 있음 | 0.28.1 |
| `fastapi`, `fastapi.responses`, `fastapi.staticfiles`, `fastapi.testclient` | `fastapi` | 있음 | 0.141.1 |
| `uvicorn` | `uvicorn` | 있음 | 0.53.0 |
| `pytest` | `pytest` | 있음 | 9.1.1 |

→ 추가 대상은 **`langgraph-prebuilt`, `langgraph-checkpoint`, `pydantic` 3개**. 그 외 누락 없음. `starlette` 는 코드가 직접 import 하지 않으므로 대상 아님.

## 설계

### `requirements.in` 변경

기존 컨벤션(버전 미지정, 한 줄에 패키지 1개)을 따라 **버전 없이** 3줄을 끝에 추가한다:

```
langgraph-prebuilt
langgraph-checkpoint
pydantic
```

- 버전 핀은 `requirements.txt` 가 담당한다(pip-tools 가 기존 핀을 존중하므로 재생성해도 버전이 바뀌지 않는다 — RAG README "개발 환경" 절과 동일).
- 순서: 기존 9줄 뒤에 그대로 붙인다(정렬하지 않음 — 기존 파일도 미정렬).

### `requirements.txt` 재생성 — **한다**

```bash
cd /Users/mjkim/workspace/LangGraph && source .venv/bin/activate
pip-compile --output-file=requirements.txt requirements.in
```

- `--no-index`, `--upgrade` 를 붙이지 않는다(`--upgrade` 는 전체 핀을 올려 버림, `--no-index` 는 해석 실패 — agent-02 검증 기록 참조).
- 기대 diff: 세 패키지의 `# via` 주석에 `-r requirements.in` 이 추가되는 것 **뿐**. 버전 변경 0, 패키지 추가/삭제 0. 그 외 변경이 나오면 리드에게 보고하고 되돌린다.

### 패키지 설치 — **하지 않는다**

세 패키지는 이미 venv 에 설치되어 있다(전이 의존으로). `pip install`/`pip install -r` 을 실행하지 않는다. 다른 세션의 Validator 가 같은 venv 로 agent-02/03 테스트를 재실행 중일 수 있어 venv 를 건드리지 않기 위함이기도 하다(사용자 지시).

### `README.md`

"사전 준비 › 의존성 설치" 절 아래에 짧은 메모 1단락을 추가한다(RAG README "의존성 메모" 형식 축약):

> 아래 3개는 코드가 직접 import 하므로 `requirements.in` 에 명시되어 있다. 원래는 `langgraph`/`fastapi` 의 전이 의존성으로만 설치되던 것들이라, 상위 패키지 의존성 트리가 바뀌어도 빠지지 않도록 직접 선언했다: `langgraph-prebuilt`(`src/agent.py` ToolNode), `langgraph-checkpoint`(`src/agent.py` InMemorySaver), `pydantic`(`src/web/dto.py`). 새 라이브러리를 직접 import 하게 되면 같은 방식으로 `requirements.in` 에 추가한 뒤 `pip-compile` 한다.

## 작업 목록

1. `requirements.in` 3줄 추가.
2. `pip-compile` 로 `requirements.txt` 재생성, `git diff requirements.txt` 가 `# via` 주석 변경뿐인지 확인.
3. README 메모 추가.

## 검증 전략

| 항목 | 확인 방법 |
|---|---|
| D1. 선언 완전성 | `grep -rhoE "^(import|from) [a-zA-Z_]+" main.py web.py src test \| sort -u` 로 최상위 모듈 목록을 뽑아 표준 라이브러리·`src`·`test` 를 제외한 나머지가 전부 `requirements.in` 의 배포 패키지에 대응됨(`langgraph.prebuilt`→`langgraph-prebuilt`, `langgraph.checkpoint`→`langgraph-checkpoint` 서브패키지 매핑 포함) |
| D2. 핀 불변 | `git diff requirements.txt` 에서 `==` 가 포함된 줄의 추가/삭제 0건. 변경은 `# via` 주석 줄과 `-r requirements.in` 줄뿐 |
| D3. 재현성 | `cp requirements.txt <scratch>/req.txt` 로 **기존 핀을 복사한 뒤** `pip-compile --output-file=<scratch>/req.txt requirements.in` 실행 → 프로젝트 `requirements.txt` 와 헤더 경로 외 diff 0. (빈 경로에 새로 생성하면 pip-tools 가 기존 핀 없이 최신 버전을 해석하므로 비교 불가 — 리드 판단 기록 (2)). 같은 파일에 재실행해 diff 0 을 확인하는 방식도 동등하게 인정 |
| D4. 환경 무변경 | `pip freeze` 가 작업 전후 동일(작업 전 `pip freeze > <scratch>/before.txt` 로 저장 후 비교). `pip check` 정상 |
| D5. 회귀 | `pytest -q` green(121 passed 기준, node 없으면 일부 skip 허용). 설치를 안 했으므로 통합 테스트는 불필요 |
| D6. 변경 범위 | `git status` 변경 파일이 `requirements.in`, `requirements.txt`, `README.md`, `docs/plans/agent-04-*.md` 뿐(agent-02/03 미커밋 변경분은 별개) |

## 완료 기준

- [ ] D1~D6 통과
- [ ] `requirements.in` 추가는 정확히 `langgraph-prebuilt`, `langgraph-checkpoint`, `pydantic` 3줄, 버전 미지정
- [ ] `pip install` 계열 명령 미실행
- [ ] 커밋하지 않음(사용자 작업)

## 범위 제외

- 버전 상한/하한 지정, `--upgrade` 로 핀 갱신
- RAG 저장소 `requirements.in` 변경
- `requirements.in` 정렬·정리
- 테스트 전용 의존성 분리(`requirements-dev.in`)

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| 버전 핀을 `requirements.in` 에도 적기 | 기존 9줄이 전부 미지정. 핀은 `requirements.txt` 한 곳에서 관리하는 팀 컨벤션 |
| `requirements.txt` 재생성 생략 | `# via` 주석이 실제 선언과 어긋나 D3 재현성이 깨짐. 재생성해도 핀은 불변이므로 비용 없음 |
| `pip install -r requirements.txt` 로 동기화 | 이미 설치된 패키지라 no-op 이며, 다른 세션 Validator 가 venv 를 쓰는 중이라 건드리지 않음(사용자 지시) |

## 리드 판단 기록

- 2026-09-25 (1): 사용자 요청(다른 세션 경유)으로 티켓 생성. 리드가 직접 grep 으로 대상 3개 확정. 설치 금지·재생성 필수를 명세에 고정.
- 2026-09-25 (2): Builder 보고 — `[검증]` D3 의 "새 경로에 pip-compile 후 비교" 는 기존 핀이 없는 경로라 pip-tools 가 최신 버전을 새로 해석해 diff 0 이 될 수 없음(실측: langchain-core 1.6.4→1.6.5, starlette 1.6.0→1.7.0, httpcore2/httpx2 2.13.0→2.13.1 — 9/22 이후 릴리스). 명세 검증식의 오류. **결정: D3 을 "기존 `requirements.txt` 를 스크래치에 복사한 뒤 그 파일을 출력 대상으로 재컴파일 → diff 0" 으로 정정**하고, 같은 파일 재실행 diff 0 도 동등하게 인정. 산출물(작업 1·2)은 명세대로이며 변경 없음. Builder 가 이미 같은 파일 재실행 diff 0 을 확인했으므로 D3 충족.
- 2026-09-25 (3): Builder 완료 보고 접수 — 작업 1~3 완료, 차이는 (2) 승인 건뿐. Builder 실행: D1 대응 완전, D2 `==` 줄 변경 0, D3 같은 파일 재실행 diff 0, D4 `pip freeze` 전후 동일·`pip check` 정상, D5 121 passed, D6 변경 3파일. 부수: 사용자 요청(다른 세션 경유)으로 Builder 가 agent-02 때 띄운 RAG 서버(PID 87197·87201) SIGTERM 종료, `curl :5010/check` → 000(connection refused), Ollama 무영향. Validator 는 D6 를 `git status` 로 확인할 것(LangGraph 는 git 저장소). 리드가 Validator 스폰.
- 2026-09-25 (4): Validator 판정 READY FOR REVIEW 접수(검증 회차 1). D1~D6·완료 기준 전부 PASS: 서드파티 최상위 모듈 9개 전부 대응(서브패키지 소유는 `packages_distributions`·dist-info RECORD 로 확정), `==` 줄 완전 동일, 스크래치 복사본 재컴파일 diff 헤더 1줄, site-packages 최신 mtime 09-22(오늘 설치 0건)·`pip check` 정상, 121 passed, 변경 4파일. 리드가 `requirements.in` 내용을 직접 확인. **결론: READY FOR REVIEW 선언.** 비차단: 워킹트리에 agent-02/03/04 미커밋분이 섞여 검증 비용이 오르므로 사용자 커밋 권장.
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

- 검증 회차: 1 (최초 검증) — 2026-09-25 Validator
- 판정: **READY FOR REVIEW** (D1~D6 전부 PASS, 완료 기준 4항목 전부 PASS)
- 프로젝트 파일 무변경 원칙 준수: 이 "검증 기록" 절 외에는 아무것도 수정하지 않음. `pip install`/`uninstall` 미실행, `git add`/`commit`/`push`/`stash` 미실행.

| 항목 | 결과 | 증거 |
|---|---|---|
| D1. 선언 완전성 | PASS | 명세 grep 재실행 결과 최상위 모듈 20개 중 서드파티는 `fastapi`, `langchain_core`, `langchain_ollama`, `langgraph`, `pydantic`, `httpx`, `ollama`, `pytest`, `uvicorn` 9개이며 전부 `requirements.in` 에 대응. 나머지는 표준 라이브러리(`argparse`, `configparser`, `dataclasses`, `json`, `pathlib`, `re`, `typing`, `uuid`)와 로컬(`main`, `src`, `test`). 함수 내부 import 까지 추가 확인했으나 신규 서드파티 없음(`atexit`/`json`/`shutil`/`subprocess`/`tempfile`/`pathlib`/`pytest`/`httpx`/`ollama`). `importlib.metadata.packages_distributions()['langgraph'] == ['langgraph-prebuilt','langgraph-checkpoint','langgraph']`, `langgraph_prebuilt-1.1.0.dist-info/RECORD` 가 `langgraph/prebuilt/**` 15개, `langgraph_checkpoint-4.2.0.dist-info/RECORD` 가 `langgraph/checkpoint/**` 23개를 소유 → 서브패키지 매핑 확정. `starlette` 직접 import 0건(명세대로 대상 아님) |
| D2. 핀 불변 | PASS | agent-02/03 미커밋 변경분이 섞여 `git diff origin/main` 만으로는 분리 불가하므로, agent-04 이전 상태(`requirements.in` 앞 9줄)를 현재 `requirements.txt` 복사본에 재컴파일해 재구성한 뒤 비교. `==` 포함 줄만 뽑아 `diff` → **exit 0(완전 동일)**. 실제 차이는 `langgraph-checkpoint`/`langgraph-prebuilt`/`pydantic` 의 `# via` 에 `-r requirements.in` 이 붙은 3곳뿐(나머지 diff 줄은 스크래치 경로가 `-r <path>` 로 찍힌 표기 차이). 패키지 추가/삭제 0 |
| D3. 재현성 | PASS | 리드 판단 기록 (2) 의 정정된 방식으로 실행: `cp requirements.txt <scratch>/req_d3.txt` 후 `pip-compile --output-file=<scratch>/req_d3.txt requirements.in` → 프로젝트 `requirements.txt` 와의 diff 가 **헤더 `--output-file` 경로 1줄뿐**. 핀·`# via`·패키지 목록 전부 동일 |
| D4. 환경 무변경 | PASS | `pip check` → `No broken requirements found.` (exit 0). 설치 여부는 mtime 으로 검증: `.venv/lib/python3.12/site-packages` 최신 갱신이 **2026-09-22 17:11**(agent-02 의 fastapi/uvicorn/starlette/annotated-doc 설치 시점)이고 오늘(09-25) 자 갱신 0건 → agent-04 작업 중 설치·제거 없음. 3개 패키지 설치 버전이 `requirements.txt` 핀과 일치: `langgraph-checkpoint==4.2.0`, `langgraph-prebuilt==1.1.0`, `pydantic==2.13.5`. (작업 전 `pip freeze` 스냅샷은 Builder 세션 산출물이라 Validator 가 직접 대조하지 못함 — mtime 으로 대체 검증) |
| D5. 회귀 | PASS | `pytest -q` → **121 passed, 3 deselected, 1 warning in 0.51s**. 경고 1건은 starlette `testclient.py:53` 의 anyio DeprecationWarning 으로 이번 변경과 무관 |
| D6. 변경 범위 | PASS | `git status --porcelain` 에 agent-01~03 변경분이 함께 보이므로 mtime 으로 분리. **오늘(2026-09-25) 자 파일은 `requirements.in`(05:03), `requirements.txt`(05:04), `README.md`(05:04), `docs/plans/agent-04-declare-direct-imports.md`(05:06) 4개뿐**이고 나머지(`main.py`, `src/agent.py`, `src/config/settings.py`, `resources/*`, `test/*`, `web.py`, agent-01~03 문서)는 전부 09-22 자 |

완료 기준:

- [x] D1~D6 통과 — 위 표
- [x] `requirements.in` 추가는 정확히 3줄, 버전 미지정 — 파일 끝 3줄이 `langgraph-prebuilt` / `langgraph-checkpoint` / `pydantic`, 비교 연산자 없음. (`git diff origin/main` 에는 `fastapi`, `uvicorn` 도 보이나 이는 agent-02 미커밋분이며 명세 현재상태 표에 "있음" 으로 기록된 대로 agent-04 이전부터 존재)
- [x] `pip install` 계열 미실행 — D4 의 site-packages mtime 증거
- [x] 커밋하지 않음 — `HEAD == 1a0f1ff`(agent-01), `git diff --cached` 비어 있음, `git stash list` 비어 있음

부수 확인(완료 기준 외): README "사전 준비 › 의존성 설치" 절 아래 **의존성 메모** 단락이 명세 문구대로 추가됨(`README.md` 61-65행, 3개 패키지와 사용처 `src/agent.py` ToolNode / `src/agent.py` InMemorySaver / `src/web/dto.py` 명시).

비차단 의견(판정 무관):

- `requirements.in` 의 `-r requirements.in` 주석 검증은 agent-02/03 미커밋분과 섞이면 재구성이 필요하다. agent-02/03 이 커밋되면 `git diff` 만으로 D2 를 바로 확인할 수 있다.
- `pip-compile` 헤더에 `--no-index` 가 남아 있으나(초기 생성 시 옵션), 현재 재컴파일에서는 붙이지 않아도 동일 결과가 나온다. 정리는 범위 밖.

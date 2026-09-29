# agent-12: 기동 스크립트 실패 처리 개선

- 작성일: 2026-09-29
- 작성자: 리드
- 상태: 종결 — 사용자 커밋·push 완료 (2026-09-29)
- 계기: 사용자가 `python3 run.py`(시스템 파이썬)로 실행 → LangGraph 가 `ModuleNotFoundError: No module named 'ollama'` 로 즉시 죽었는데 **90초를 기다린 뒤 "응답이 없습니다" 만 출력**. 원인 파악에 로그를 직접 열어야 했다.
- 선행: agent-11(커밋 `52c7943`). 저장소: `/Users/mjkim/workspace/LangGraph` 만.

## 목표

기동 스크립트가 (1) 어느 파이썬으로 실행해도 동작하고, (2) 자식이 죽으면 기다리지 않고 즉시 실패하며, (3) 실패 원인을 화면에서 바로 보여주게 한다. 중복 실행 방지·종료 안전 규칙(agent-11)은 **그대로 유지**한다.

## 확인된 현재 상태 (`[검증]` 리드가 코드·로그 직접 확인)

- `run.py:39` RAG 는 `<rag-dir>/.venv/bin/python` 을 명시적으로 쓴다.
- `run.py:49` LangGraph 는 `sys.executable` — **run.py 를 띄운 인터프리터를 그대로 쓴다.** 이 비대칭이 이번 사고의 직접 원인.
- `run.py:114-117` 은 RAG venv 파이썬 존재만 미리 확인하고, LangGraph 쪽에는 같은 확인이 없다.
- `src/launcher.py:81-87` `spawn_process` 는 `Popen` 을 만들고 **`pid`(int)만 반환**한다. 이후 `_wait_healthy`(`:90-96`)는 `/check` 만 폴링하므로 자식이 죽어도 알 수 없다.
- `src/launcher.py:118` 실패 메시지는 `기동 실패: {timeout}초 안에 응답이 없습니다. 로그 {path}` 뿐.
- 로그는 append 모드(`:84`)라 이전 실행의 오류가 그대로 쌓인다. 실제로 `logs/web.log` 에 같은 traceback 이 3개 있어 어느 것이 이번 실행인지 구분이 안 됐다.
- `run.py:121-123` 은 **기동 실패 시에도** `브라우저: http://127.0.0.1:5020` 을 출력한다(오해 유발).
- `[검증]` 자식이 죽은 뒤 `os.kill(pid, 0)` 은 좀비 프로세스에 대해 **성공**한다(부모가 reap 하기 전까지). 따라서 `is_alive` 폴링으로는 조기 종료를 감지할 수 없고 `Popen.poll()` 이 필요하다.

## 설계

### F1. LangGraph 도 프로젝트 venv 파이썬을 쓴다

`run.py`:

```python
def project_python() -> str:
    """프로젝트 venv 를 우선한다. 없으면 현재 인터프리터로 폴백."""
    candidate = PROJECT_ROOT / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable
```

`build_services` 의 web `command[0]` 을 `project_python()` 으로 바꾼다. RAG 쪽은 변경하지 않는다.

- 이제 `python3 run.py`, `.venv/bin/python run.py`, `source .venv/bin/activate && python run.py` 모두 동작한다.
- venv 가 없으면 폴백하고, 그때 의존성이 없으면 F2·F3 가 즉시·명확하게 알려준다.

### F2. 자식이 죽으면 즉시 실패

`src/launcher.py`:

- `spawn_process(service)` 의 반환을 **`subprocess.Popen`(`.pid`, `.poll()` 을 가진 객체)** 으로 바꾼다.
- `_wait_healthy` 를 `"ok" | "died" | "timeout"` 을 돌려주게 바꾸고, 폴링마다 `child_exited()` 를 확인한다.

```python
def _wait_healthy(service, timeout, healthy, sleep, child_exited=lambda: False) -> str:
    # 매 반복: healthy → "ok";  child_exited() → "died";  기한 초과 → 마지막 healthy 확인 후 "ok"/"timeout"
```

- `start()` 의 신규 기동 경로:

```python
process = spawn(service)
pid = process.pid
service.pid_path.write_text(str(pid))
outcome = _wait_healthy(..., child_exited=lambda: process.poll() is not None)
```

| outcome | 결과 |
|---|---|
| `ok` | 기존과 동일 `started` |
| `died` | `failed` — `기동 실패: 프로세스가 즉시 종료되었습니다 (종료 코드 N). 로그 {path}` + 로그 꼬리. **pid 파일 삭제** |
| `timeout` | `failed` — 기존 문구 + 로그 꼬리. **pid 파일 삭제** |

- pid 파일을 지우는 이유: 남겨두면 다음 실행이 "기동 중이던 프로세스" 분기로 잘못 들어간다.
- 기존 PID 파일이 있는 대기 경로(`:107-110`)에는 `Popen` 이 없으므로 `child_exited` 기본값(`False`)을 그대로 쓴다. 동작 변화 없음.

### F3. 실패 원인을 화면에 보여준다

`src/launcher.py` 에 추가:

```python
LOG_TAIL_LINES = 15

def tail_log(path: Path, offset: int = 0, lines: int = LOG_TAIL_LINES) -> str:
    """offset 바이트 이후에 쌓인 내용의 마지막 N줄. 파일이 없거나 새 내용이 없으면 빈 문자열."""
```

- `start()` 는 **spawn 직전에 로그 파일 크기를 기록**하고, 실패 시 그 오프셋 이후만 꼬리로 붙인다 → 이전 실행의 오류가 섞이지 않는다.
- 실패 메시지 형식:

```
기동 실패: 프로세스가 즉시 종료되었습니다 (종료 코드 1). 로그 /…/logs/web.log
  ─ 로그 마지막 15줄 ─
  Traceback (most recent call last):
  ...
  ModuleNotFoundError: No module named 'ollama'
```

(꼬리 각 줄은 공백 2칸 들여쓰기. 새 내용이 없으면 꼬리 블록 자체를 붙이지 않는다.)

- `run.py`: **LangGraph 기동에 실패했으면 `브라우저:` 줄을 출력하지 않는다.** 종료 안내(`--stop`)는 그대로 출력한다.

### 변경하지 않는 것

중복 실행 방지(`/check` 200 이면 spawn 0회), `--stop` 의 PID 파일 + 명령줄 이중 가드, `SIGKILL` 부재, Ollama 확인 전용, 기동 순서(RAG→LangGraph), 종료 역순, `--status`/`--stop` 배타, 새 의존성·설정 키 0. 런타임 코드(`src/agent.py`, `src/web/**`, `main.py`, `web.py`, `maintenance.py`, `resources/**`)도 전부 불변.

### 허용하는 기존 테스트 수정 (설계 변경 직결)

`test/test_launcher.py` 에서 **가짜 `spawn` 이 int 를 반환하던 부분**을 `.pid`/`.poll()` 을 가진 가짜 객체로 바꾼다. `spawn` 호출 횟수·`state` 값 등 **기존 단언 내용은 유지**한다. 그 외 기존 테스트 수정이 필요해 보이면 ESCALATE.

## 작업 목록

1. F1 (`run.py` `project_python`) + 테스트.
2. F2 (`spawn_process` 반환형, `_wait_healthy` 3-상태, `start()` 분기, pid 파일 정리) + 기존 가짜 spawn 갱신 + 테스트.
3. F3 (`tail_log` 오프셋 기반, 실패 메시지, `브라우저:` 줄 조건부) + 테스트.
4. README "빠른 시작" 갱신 — 어느 파이썬으로 실행해도 된다는 점, 실패 시 화면에 로그 꼬리가 나온다는 점.

## 검증 전략

| 항목 | 확인 방법 |
|---|---|
| V1. 인터프리터 | `PROJECT_ROOT/.venv/bin/python` 이 있으면 web `command[0]` 이 그 경로(문자열 비교), 없으면 `sys.executable`(monkeypatch 로 `exists` 를 False 로). RAG `command[0]` 은 기존대로 `<rag-dir>/.venv/bin/python` |
| V2. 조기 종료 즉시 실패 | 가짜 process(`poll()` 이 `1`) + `healthy` 항상 False + `timeout=90` → `state="failed"`, 메시지에 `종료 코드 1`, **`sleep` 호출 1회 이하**(90초를 기다리지 않음을 호출 횟수로 단언) |
| V3. 실패 후 pid 파일 정리 | `died`·`timeout` 두 경우 모두 `pid_path` 가 존재하지 않음. 이어서 같은 서비스로 `start()` 를 다시 부르면 "기동 중" 분기가 아니라 새 spawn 경로로 감 |
| V4. 로그 꼬리 | `tail_log`: 파일 없음/빈 파일/새 내용 없음 → `""`. 20줄 중 마지막 15줄만. **오프셋 이전 내용은 제외**(이전 실행 traceback 이 섞이지 않음을 파일에 옛 내용 + 새 내용을 넣어 확인). 실패 Result 메시지에 꼬리가 들여쓰기되어 포함 |
| V5. 성공 경로 회귀 | `poll()` 이 `None` 인 가짜 process → 기존과 동일하게 `state="started"`, spawn 1회, pid 파일 기록 |
| V6. 실패 시 출력 | LangGraph 실패 시 출력에 `브라우저:` 없음, `종료:` 는 있음. 성공 시 둘 다 있음 |
| V7. agent-11 규칙 회귀 | 기존 L1~L6 테스트 전원 통과(`healthy=True` → spawn 0회, `--stop` 의 `not_ours` 에서 시그널 0회, `SIGKILL` 부재 grep). `pytest -q` green |
| V8. 범위 | `git diff --stat HEAD` 가 `run.py`, `src/launcher.py`, `test/test_launcher.py`, `README.md`, 이 명세뿐. 런타임 코드·`requirements*`·`resources/**` diff 0 |
| V9. 실환경 (Validator) | ① **시스템 파이썬으로 기동**: 포트가 비어 있으면 `/usr/bin/python3 run.py` 로 두 서비스가 뜨는지 확인 후 `run.py --stop` 으로 정리. 점유 중이면 `[미확인]`. ② **즉시 실패 경로 실측**: 스크래치 스크립트에서 `Service(command=[sys.executable, "-c", "import sys; sys.stderr.write('BOOM\\n'); sys.exit(3)"], health_url=<응답 없는 주소>)` 로 `start(timeout=90)` 호출 → **90초가 아니라 수 초 내** `failed` 반환, 메시지에 `종료 코드 3` 과 `BOOM`. 실제 서버·포트를 쓰지 않는다 |

## 완료 기준

- [ ] V1~V9 통과(V9 ① 은 포트 점유 시 `[미확인]` 허용)
- [ ] 자식 즉시 종료 시 `--timeout` 을 기다리지 않음이 테스트로 고정
- [ ] 실패 메시지에 로그 꼬리가 포함되고, 이전 실행 내용이 섞이지 않음
- [ ] agent-11 의 중복 방지·종료 안전 규칙 전부 유지(V7)
- [ ] 새 의존성 0, 새 설정 키 0, 런타임 코드 변경 0
- [ ] 기존 테스트 수정은 "가짜 spawn 반환형" 1종뿐
- [ ] 커밋하지 않음(사용자)

## 범위 제외

- 의존성 자동 설치·venv 자동 생성
- 포그라운드 모드, 자동 재시작, 헬스 감시 루프
- 로그 로테이션, 실행별 로그 파일 분리(오프셋 방식으로 충분)
- RAG 쪽 기동 실패의 세부 진단(같은 꼬리 출력은 공통 적용되지만 RAG 전용 안내는 없음)
- Windows 지원(`ps`·POSIX 시그널 전제는 그대로)

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| `is_alive(pid)` 폴링으로 조기 종료 감지 | `[검증]` 좀비 프로세스에 대해 `os.kill(pid,0)` 이 성공한다. `Popen.poll()` 이라야 정확하고, `poll()` 은 reap 까지 해준다 |
| `sys.executable` 유지 + "venv 로 실행하세요" 안내만 추가 | 사용자가 이미 `python3 run.py` 로 실행했다. 안내문은 실행 전에 읽히지 않는다. 동작하게 만드는 편이 낫다 |
| 기동 전에 `import ollama` 등 의존성을 미리 검사 | 검사 목록을 따로 관리해야 하고 실제 실패 원인(문법 오류·설정 오류 등)은 못 잡는다. 실제로 띄워 보고 죽으면 로그를 보여주는 쪽이 일반적 |
| 실행마다 로그 파일을 새로 만들기(`web-20260929.log`) | 파일이 쌓이고 정리 대상이 하나 더 는다. 오프셋 기반 꼬리로 같은 목적 달성 |
| 실패 시 이미 뜬 RAG 를 자동으로 내리기 | agent-11 에서 "자동 롤백 없음" 으로 정한 규칙. 다시 실행하면 RAG 는 건너뛰므로 손해가 없다 |

## 리드 판단 기록

- 2026-09-29 (1): 사용자 실행 실패 보고로 착수. **원인은 agent-11 설계의 비대칭**(RAG 는 자기 venv, LangGraph 는 `sys.executable`) — 리드 설계 결함으로 기록한다. 세 가지를 함께 고친다: 인터프리터 선택(F1), 조기 종료 감지(F2), 실패 원인 노출(F3). 셋 다 같은 사고에서 드러난 문제라 한 티켓으로 묶는다.
- 2026-09-29 (2): `spawn` 의 반환형을 int → `Popen` 으로 바꾸는 것은 테스트에 보이는 인터페이스 변경이므로 "허용하는 기존 테스트 수정" 에 명시한다. 좀비 프로세스 때문에 PID 기반 감지가 불가능하다는 것이 근거.
- 2026-09-29 (3): 로그 꼬리는 **spawn 직전 파일 크기 오프셋** 기준으로 자른다. 근거: 이번 사고에서 `logs/web.log` 에 이전 실행의 traceback 이 3개 쌓여 있어 원인 판별이 늦어졌다.
- 2026-09-29 (4): Builder 작업 1~4 완료 보고 — 296 passed(HEAD 278 + 18), 변경 4파일, 런타임 코드·`resources/**`·`requirements*` diff 0. F1~F3 모두 명세대로. 기존 테스트 수정은 허용 1종뿐이며 삭제 라인이 정확히 3줄(`SpawnSpy` 반환형 관련)이고 단언 내용은 전부 유지 — 승인. V2 의 "90초를 기다리지 않음" 은 `sleep` 호출 횟수로 단언됨. agent-11 규칙(L1~L6) 58건 전원 통과. 리드가 Validator 스폰.
- 2026-09-29 (5): Validator 판정 READY FOR REVIEW 접수(검증 회차 1). V1~V9·완료 기준 전부 PASS, 311 passed / 0 failed. 핵심 실측: ① **V9 ② 즉시 실패 = 벽시계 0.549초**(timeout 90), 메시지에 `종료 코드 3` + 로그 꼬리 `BOOM`, pid 파일 정리됨. 2회차에는 파일에 `BOOM` 이 2줄인데 꼬리에는 1줄만 → 오프셋 동작 실증. ② **V9 ① F1 직접 증거** — 사고 당시와 같은 프레임워크 파이썬(`ollama` 없음)으로 격리 사본에서 `run.py` 실행 → LangGraph 기동 성공, `/check` 가 `"ollama":true`, `lsof` 로 자식이 프로젝트 venv site-packages 를 적재함을 확인. ③ **돌연변이 5종 전부 테스트가 잡음**. 특히 조기 종료 감지 제거 시 스위트 실행 시간이 0.87초 → **276.68초**로 늘어 90초 대기가 되살아남을 실증. ④ V7 agent-11 규칙 유지(중복 방지 spawn 0회, `--stop` 이중 가드, 금지 원시연산 grep 0건) — 실환경에서도 재확인. ⑤ 기존 테스트 삭제 줄 정확히 3줄(전량 인용), 전부 허용된 `SpawnSpy` 반환형 변경. **결론: READY FOR REVIEW 선언.**
  - Validator 가 메운 커버리지 공백 1건: 기존 `test_failed_start_removes_pid_file` 이 `timeout=0` 이라 두 파라미터 모두 timeout 분기로 빠져 **`died` 분기의 pid 파일 정리가 미검증**이었다. 구현은 두 분기 공통이라 올바르며 테스트만 보강됨.
  - 비차단 의견 판단: ① `run.py` 가 `code == 0` 기준이라 **RAG 만 실패하고 LangGraph 는 성공한 경우에도 `브라우저:` 줄이 숨는다** — 명세 미규정 구간. RAG 없이도 웹 UI 자체는 뜨므로 안내를 보여주는 편이 나을 수 있으나, 그 상태의 UI 는 검색이 안 되는 반쪽이라 숨기는 것도 합리적이다. **후속 후보로만 기록**하고 이번에는 조치하지 않는다(Validator 가 미정의 동작을 테스트로 고정하지 않은 판단도 타당). ② `tail_log` 가 15줄을 자른 뒤 공백 줄을 버려 실제 출력이 더 적을 수 있음 — 진단 목적에 영향 없음. ③ `test_project_python_prefers_venv` 의 환경 의존은 Validator 추가분이 환경 독립으로 덮음. ④ README 의 `InMemorySaver` → `SqliteSaver/AsyncSqliteSaver` 정정은 작업 4 범위 밖이나 HEAD 의 낡은 서술을 고친 것이라 **유지 승인**. ⑤ `agent-10`·`agent-11` 문서 상태 줄은 리드 편집.
  - **사용자에게 알릴 사항**: `logs/rag.pid` 가 사용자의 현재 RAG 서버 PID(25111)를 담고 있다. 사용자가 `python3 run.py` 로 띄운 것이므로 `run.py --stop` 이 그 서버를 내리는 것은 **설계상 정상 동작**이지만, 지금 쓰는 중이라면 의도치 않은 종료가 될 수 있으니 안내한다. `logs/web.pid`(32820)는 stale 이며 다음 실행 시 자동 정리된다.
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

- Validator, 2026-09-29, **검증 회차 1**. 판정 **READY FOR REVIEW**.
- 스위트: `.venv/bin/python -m pytest -q` → **311 passed, 6 deselected** (HEAD 278 + Builder 18 + Validator 15). 커밋하지 않음.
- 프로세스 안전: 검증 전/후 5010 = `200`(pid 25111·25143, 사용자 소유) 동일, 5020 = `000` 동일. 사용자 `logs/` 원본 무변경(`web.log` 1622바이트·traceback 3개 그대로). 실환경 기동은 스크래치 사본에서만 수행 — 실제 `logs/rag.pid` 가 `25111`(사용자 RAG)이라 저장소에서 `run.py --stop` 을 돌리면 사용자 서버에 `SIGTERM` 이 가므로 격리 사본을 썼다.

| 항목 | 판정 | 증거 |
|---|---|---|
| V1 인터프리터 | PASS | `run.py:58` `project_python()`, `run.py:48` RAG 는 그대로. `test_launcher.py:655,665,673` + Validator 추가 `test_project_python_falls_back_without_venv`/`..._uses_venv_when_present`. 돌연변이(`project_python()`→`sys.executable`) → `test_web_uses_project_venv_python` 실패 |
| V2 조기 종료 즉시 실패 | PASS | `src/launcher.py:130-131`. `test_dead_child_fails_without_waiting_for_timeout`(sleep 호출 0회, `종료 코드 1`). **민감도**: `child_exited()` 확인 블록을 지운 사본에서 해당 2건이 실패하고 실행 시간이 0.87초 → **276.68초**로 늘어남 |
| V3 실패 후 pid 정리 | PASS | `src/launcher.py:161`. 기존 `test_failed_start_removes_pid_file[1|None]` 은 `timeout=0` 이라 둘 다 timeout 분기였음 → Validator 가 `test_died_branch_removes_pid_file_and_reports_exit_code`(died 분기 실측)와 `test_timeout_branch_keeps_timeout_wording_and_removes_pid_file` 추가. `test_retry_after_failure_spawns_again` 이 재실행 경로 확인. 돌연변이(`unlink` 삭제) → 5건 실패 |
| V4 로그 꼬리 | PASS | `src/launcher.py:94-120`. 없음/빈 파일/새 내용 없음/20줄 중 15줄/오프셋 제외 전부 테스트. **실환경**: 사용자 `logs/web.log`(traceback 3개) 사본을 스크래치 기동에 물려 오프셋 1622 이후 6줄만 새로 쌓임을 확인. V9-② 2회차에서 `x.log` 에 `BOOM` 2줄이 있어도 메시지에는 1줄만. 돌연변이(`offset = 0`) → 3건 실패 |
| V5 성공 경로 회귀 | PASS | `test_live_child_still_starts_normally` + Validator `test_spawn_process_returns_object_with_pid_and_poll`(실제 `Popen` 반환형·`poll()` 회수 확인) |
| V6 실패 시 출력 | PASS | `run.py:131`. `test_browser_line_is_hidden_when_start_failed` / `..._shown_on_success`. 돌연변이(조건 제거) → 1건 실패 |
| V7 agent-11 회귀 | PASS | L1·L5 포함 28건 선별 통과, 전체 311 green. `grep -rnE "SIGKILL|kill -9|\.terminate\(|\.kill\(|pkill|killpg"` → `src/launcher.py:48` `os.kill(pid, 0)`(생존 확인, 시그널 0)과 `:183` `signal.SIGTERM` 뿐. 실환경 `--stop` 에서 RAG = `not_ours`, 시그널 0회, 5010 계속 200 |
| V8 범위 | PASS | `git diff --stat HEAD -- src main.py web.py maintenance.py resources requirements.in requirements.txt` → `src/launcher.py` 1파일뿐. `requirements*`·`resources/**` diff 0줄, 신규 import 0. 기존 테스트 삭제 라인 정확히 3줄(`SpawnSpy.__init__` 시그니처, `self._pid = pid`, `return self._pid`) — 허용된 "가짜 spawn 반환형" 1종뿐 |
| V9-① 시스템 파이썬 기동 | PASS | `/usr/bin/python3`(3.9.6)은 `httpx` 자체가 없어 `run.py` 를 못 띄운다(HEAD 부터의 제약, 이번 변경과 무관). 사용자가 실제로 쓴 `python3` = 프레임워크 3.12(= `ollama` 미설치)로 격리 사본에서 실행 → `LangGraph : 기동 중... ok (0초)`, `/check` 200. 자식 pid 57858 이 `.venv/lib/python3.12/site-packages/**` 를 적재함을 `lsof` 로 확인(F1 실증). `run.py --stop` 으로 정리, RAG 는 `not_ours` |
| V9-② 즉시 실패 실측 | PASS | `timeout=90` 인데 **벽시계 0.549초**. `state='failed'`, 메시지: `기동 실패: 프로세스가 즉시 종료되었습니다 (종료 코드 3). 로그 …/x.log` + `  ─ 로그 마지막 15줄 ─` + `  BOOM`. pid 파일 없음. 같은 시나리오를 `test_real_child_failure_returns_in_seconds_not_at_timeout` 으로 스위트에 고정(`elapsed < 15`) |

**완료 기준**: V1~V9 통과 / 자식 즉시 종료 시 `--timeout` 미대기 테스트 고정(sleep 호출 수 + 벽시계) / 로그 꼬리 포함·이전 실행 미혼입 / agent-11 규칙 유지 / 새 의존성·설정 키·런타임 코드 변경 0 / 기존 테스트 수정 1종 / 미커밋 — **전 항목 충족**.

**Validator 추가 테스트** (`test/test_launcher.py` 말미 15건): died 분기 pid 정리, timeout 분기 문구·pid 정리, 기존 PID 대기 경로 불변(pid 파일 유지·spawn 0회·꼬리 없음), `_wait_healthy` 3-상태 직접 검증 3건, `tail_log` 경계 4건(오프셋 초과·공백만·`lines` 인자·디렉터리), 이전 50줄 미혼입, `spawn_process` 반환형, 실제 자식 즉시 실패 벽시계, `project_python` 폴백 2건.

**비차단 의견**(판정 미반영):
1. `run.py:131` 은 `code == 0` 기준이라 **RAG 만 실패하고 LangGraph 는 떴을 때도** `브라우저:` 줄이 숨는다(실측 확인). 명세 F3 문구는 "LangGraph 기동에 실패했으면" 이고 V6 기준은 충족하나, RAG 없이도 웹 UI 는 뜨므로 향후 티켓에서 정할 여지가 있다.
2. `tail_log` 는 마지막 15줄을 **자른 뒤** 공백 줄을 버려서 실제 출력이 15줄보다 적을 수 있다. 진단 목적에는 영향 없음.
3. `test_project_python_prefers_venv` 는 저장소에 실제 `.venv` 가 있어야 통과한다(환경 의존). Validator 가 추가한 `tmp_path` 기반 2건이 같은 로직을 환경 독립적으로 덮는다.
4. `README.md` 의 `InMemorySaver` → `SqliteSaver/AsyncSqliteSaver` 줄 수정은 agent-12 작업 목록 4(빠른 시작 갱신) 범위 밖이다. HEAD 의 낡은 서술을 고친 것이라 내용은 맞다.

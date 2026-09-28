# agent-11: 기동 스크립트 (중복 실행 방지)

- 작성일: 2026-09-28
- 작성자: 리드
- 상태: READY FOR REVIEW (2026-09-28, 리드 확인, 검증 회차 1) — 커밋 대기(사용자)
- 사용자 확정: 기동 스크립트 1개로 RAG + LangGraph 를 띄우되 **이미 떠 있으면 다시 띄우지 않는다**.
- 저장소: `/Users/mjkim/workspace/LangGraph` 만. **RAG 저장소는 읽기만 하고 수정하지 않는다.**

## 목표

터미널 두 개로 나눠 띄우던 RAG 서버와 LangGraph 웹 서버를 명령 하나로 기동한다. 이미 실행 중인 서비스는 **건드리지 않고 건너뛴다**. 내가 띄운 것만 내릴 수 있는 `--stop`, 현재 상태만 보는 `--status` 를 함께 제공한다.

## 확인된 현재 상태 (`[검증]`)

- 필요한 프로세스 3개: Ollama(11434), RAG(5010), LangGraph 웹(5020).
- `Settings` 에 이미 다 있다 — `rag_base_url`(`http://127.0.0.1:5010`), `ollama_base_url`, `llm_model`, `host`, `port`. **RAG 의 ini 를 따로 읽을 필요가 없다.**
- 두 서버 모두 `GET /check` 를 제공한다. RAG → `{"status","ollama","chunk_count"}`, LangGraph → `{"status","rag","ollama"}`.
- README 가 이미 RAG 를 형제 디렉터리로 가정한다(`cd ../RAG && python main.py --active-profile=local`).
- `.gitignore` 에 `logs/` 있음 → 로그·PID 파일을 두기 적합.
- 진입점 컨벤션: 프로젝트 루트에 `main.py`, `web.py`, `maintenance.py` + `--active-profile`.
- `httpx` 는 이미 직접 선언된 의존성.
- `[검증]` 다중 세션 환경에서 **다른 작업이 띄워 둔 서버가 이미 5010·5020 을 점유하고 있는 경우가 실제로 반복 발생**했다. 남의 프로세스를 죽이지 않는 것이 이 티켓의 안전 요건이다.

## 설계

### 명령

```bash
python run.py                      # 기동(이미 떠 있으면 건너뜀)
python run.py --status             # 상태만 출력
python run.py --stop               # 내가 띄운 것만 종료
python run.py --rag-dir ../RAG --active-profile local --timeout 90
```

| 인자 | 기본값 | 설명 |
|---|---|---|
| `--active-profile` | `local` | LangGraph·RAG 양쪽에 같은 값 전달 |
| `--rag-dir` | `PROJECT_ROOT/../RAG` | RAG 저장소 경로 |
| `--timeout` | `90` | 기동 후 `/check` 200 을 기다리는 최대 초(RAG 인덱스 로드에 시간이 걸림) |
| `--status` / `--stop` | 없음 | 상호 배타. 둘 다 주면 오류 종료(2) |

### 각 서비스에 대한 동작 (기동)

판정 순서를 명확히 고정한다.

| 상태 | 판정 | 동작 |
|---|---|---|
| `/check` 200 | **이미 실행 중** | 건너뜀. `이미 실행 중 (다른 프로세스가 띄웠을 수 있어 건드리지 않습니다)` |
| `/check` 응답 없음 + PID 파일의 프로세스가 살아 있음 | **기동 중** | `--timeout` 까지 `/check` 200 을 기다림 |
| `/check` 응답 없음 + PID 파일 없음/죽은 PID | **미실행** | 새로 기동(백그라운드), PID 파일 기록, `/check` 200 까지 대기 |
| 대기 시간 초과 | **실패** | 로그 경로를 알려주고 종료 코드 1. 이미 성공한 다른 서비스는 그대로 둔다(자동 롤백 없음) |

- **Ollama 는 절대 기동·종료하지 않는다.** `{ollama_base_url}/api/tags` 로 확인만 하고, 없거나 모델이 없으면 경고 한 줄 출력 후 계속 진행한다(`brew services start ollama` / `ollama pull` 안내). 이유: 보통 시스템 데몬으로 떠 있고, 중복 기동하면 기존 서비스와 충돌한다.
- 기동 순서는 **RAG → LangGraph**. LangGraph 가 뜨자마자 `/check` 로 RAG 를 보기 때문.
- 기동 방식: `subprocess.Popen(..., cwd=<서비스 디렉터리>, stdout=로그파일, stderr=STDOUT, start_new_session=True)`. 부모(run.py)가 끝나도 서버는 계속 돈다.
- 실행 파이썬: RAG 는 `<rag-dir>/.venv/bin/python`(없으면 안내 후 종료 1), LangGraph 는 `sys.executable`(run.py 를 띄운 인터프리터를 그대로 사용).

### `--stop` 안전 규칙 (핵심)

- **PID 파일에 적힌 프로세스만** `SIGTERM`. `SIGKILL` 은 쓰지 않는다.
- 종료 전 `ps -p <pid> -o command=` 로 명령줄을 확인해 **기대하는 진입점(`main.py`/`web.py`)이 들어 있을 때만** 종료한다. 아니면 PID 재사용으로 보고 파일만 지운다.
- `/check` 는 200인데 PID 파일이 없으면 → `다른 프로세스가 사용 중입니다. 건드리지 않습니다.` 출력하고 **아무것도 하지 않는다.**
- SIGTERM 후 최대 10초 동안 종료를 확인하고, 안 내려가면 PID 와 함께 안내만 한다(강제 종료 없음).

### 출력 예

```
$ python run.py
Ollama       : ok (qwen3:14b)
RAG          : 이미 실행 중 http://127.0.0.1:5010 (다른 프로세스가 띄웠을 수 있어 건드리지 않습니다)
LangGraph    : 기동 중... ok (7초)  http://127.0.0.1:5020   로그 logs/web.log

브라우저: http://127.0.0.1:5020
종료: python run.py --stop  (이 스크립트가 띄운 것만 내려갑니다)
```

### 파일

```
run.py                  # 신규: 진입점(argparse, start/status/stop 분기, 출력)
src/launcher.py         # 신규: 판정 로직(순수 함수 + 주입 가능한 의존)
test/test_launcher.py   # 신규
logs/{rag,web}.log      # 런타임 산출물(gitignore)
logs/{rag,web}.pid      # 런타임 산출물(gitignore)
README.md               # 수정: "빠른 시작" 절
```

**런타임 코드(`src/agent.py`, `src/web/**`, `src/tools.py`, `main.py`, `web.py`, `maintenance.py`, `resources/**`)와 설정 스키마, 의존성은 전부 불변.**

### `src/launcher.py` 인터페이스

```python
@dataclass(frozen=True)
class Service:
    name: str                 # "RAG" | "LangGraph"
    health_url: str           # ".../check"
    url: str                  # 사람에게 보여줄 주소
    cwd: Path
    command: list[str]
    log_path: Path
    pid_path: Path

@dataclass(frozen=True)
class Result:
    state: Literal["already", "started", "waiting", "failed", "stopped", "not_ours", "not_running"]
    message: str

def is_healthy(url: str, timeout: float = 2.0) -> bool
    # httpx.get 200 이면 True, httpx.HTTPError 는 False (예외를 밖으로 내지 않는다)

def read_pid(pid_path: Path) -> int | None
    # 파일 없음/숫자 아님/죽은 프로세스 → None. 죽은 PID 면 파일을 지운다(stale 정리)

def is_alive(pid: int) -> bool                    # os.kill(pid, 0)
def matches_command(pid: int, marker: str) -> bool  # ps -p 결과에 marker 포함 여부

def start(service: Service, timeout: float, *, healthy=is_healthy, spawn=...) -> Result
def stop(service: Service, marker: str, *, healthy=is_healthy) -> Result
def status(service: Service, *, healthy=is_healthy) -> Result
```

- `healthy`/`spawn` 을 인자로 주입할 수 있게 해 **네트워크·프로세스 없이 단위 테스트**한다.
- 시간 대기는 `time.sleep(0.5)` 폴링. 테스트에서는 주입한 가짜 `healthy` 가 즉시 True 를 돌려주게 한다.

## 작업 목록

1. `src/launcher.py` + `test/test_launcher.py`(L1~L5). 네트워크·프로세스 기동 없이 전부 검증 가능해야 한다.
2. `run.py` 진입점(start/status/stop, 출력 형식) + L6.
3. README "빠른 시작" 절 + L7 회귀.

## 검증 전략

| 항목 | 확인 방법 |
|---|---|
| L1. 이미 실행 중이면 기동 안 함 | 가짜 `healthy` 가 True → `start()` 가 `state="already"` 이고 **`spawn` 이 한 번도 호출되지 않음**(호출 기록으로 단언). 중복 실행 방지의 핵심이므로 반드시 "호출 안 됨"을 단언할 것 |
| L2. 미실행이면 기동 | `healthy` 가 처음 False → 이후 True. `spawn` 1회 호출, PID 파일 기록, `state="started"` |
| L3. PID 파일 처리 | 죽은 PID → `read_pid` 가 `None` 이고 파일이 삭제됨. 숫자 아님·빈 파일·파일 없음 모두 `None`. 살아 있는 PID → 그 값 반환 |
| L4. 기동 실패 | `healthy` 가 계속 False → `--timeout` 후 `state="failed"`, 메시지에 로그 경로 포함. `spawn` 은 1회만(재시도 없음) |
| L5. `--stop` 안전 | ① PID 파일 없고 healthy True → `state="not_ours"`, **시그널 미발송**(가짜 killer 호출 0회) ② PID 파일 있고 `matches_command` False → 시그널 없이 파일만 삭제 ③ 정상 → SIGTERM 1회, `state="stopped"` ④ `SIGKILL` 을 보내는 경로가 코드에 없음(grep) |
| L6. 진입점 | `--status`/`--stop` 동시 지정 → 종료 코드 2. `--rag-dir` 가 없는 경로 → 안내 후 1. RAG venv 파이썬 없음 → 안내 후 1. 정상 경로 출력에 두 서비스 줄 + 브라우저 주소 + 종료 안내 포함 |
| L7. 회귀·범위 | `pytest -q` green(HEAD 220 + 신규). `git diff --stat HEAD` 가 `README.md` + 신규 3파일 + 이 명세뿐. `src/**`(launcher 제외)·`main.py`·`web.py`·`maintenance.py`·`resources/**`·`requirements*` diff 0 |
| L8. 실환경 (Validator) | **주의: 5010·5020 이 다른 세션 소유일 수 있다.** ① 현재 상태에서 `python run.py --status` 실행 → 실제 상태가 사람이 읽을 수 있게 나오는지 ② 점유 중이라면 `python run.py` 를 실행해 **두 서비스 모두 "이미 실행 중" 으로 건너뛰고 새 프로세스가 생기지 않는지**(실행 전후 `pgrep -f "main.py --active-profile"` 개수 비교) ③ `python run.py --stop` 이 **남의 프로세스를 죽이지 않는지**(PID 파일이 없으므로 `not_ours` 가 떠야 함, 실행 후 서버 여전히 200) ④ 포트가 비어 있으면 실제 기동→`--stop` 왕복까지 확인하고, 점유 중이라 확인 못 한 항목은 `[미확인]` 으로 기록 |

## 완료 기준

- [ ] L1~L8 통과(L8 중 포트 점유로 못 한 항목은 `[미확인]` 기록 허용)
- [ ] **이미 실행 중인 서비스에 대해 `spawn` 이 호출되지 않음**이 테스트로 고정
- [ ] `--stop` 이 PID 파일 없는 서비스를 종료하지 않음이 테스트로 고정, `SIGKILL` 경로 없음
- [ ] Ollama 를 기동·종료하지 않음
- [ ] 새 의존성 0, 새 설정 키 0, 런타임 코드 변경 0, RAG 저장소 변경 0
- [ ] 커밋하지 않음(사용자)

## 범위 제외

- 포그라운드 감시 모드(Ctrl-C 로 둘 다 내리기), 자동 재시작·헬스 감시 루프
- Ollama 기동·모델 자동 `pull`
- Docker / launchd / systemd
- 로그 로테이션·병합 뷰(`tail -f` 는 사용자가 직접)
- RAG 인제스트(`ingest.py`) 자동 실행
- 포트 충돌 시 대체 포트 자동 선택
- 원격 호스트 기동

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| bash 스크립트(`run.sh`) | 두 서비스의 주소를 알려면 ini 를 읽어야 하는데, LangGraph `Settings` 에 이미 `rag_base_url`·`host`·`port` 가 있다. 파이썬이면 그걸 그대로 쓰고 단위 테스트도 된다 |
| `pgrep` 으로 중복 판정 | 다른 프로젝트의 `main.py` 까지 잡히고, 기동 중·좀비 상태를 구분하지 못한다. `/check` 200 이 "실제로 서비스 가능한가" 를 직접 말해준다. PID 는 **내가 띄운 것 식별용**으로만 쓴다 |
| 포트 점유(`lsof`)로 판정 | 우리 서비스가 아닌 프로세스가 점유해도 "실행 중"으로 오인한다. `/check` 는 우리 서비스임을 함께 확인해 준다 |
| 이미 떠 있으면 죽이고 다시 띄우기 | 다른 세션·다른 사람의 작업을 끊는다. 사용자 요구("중복 실행하지 말 것")와도 반대 |
| `--stop` 이 포트 기준으로 종료 | 남의 프로세스를 죽일 수 있다. PID 파일 + 명령줄 확인의 이중 조건이 필요 |
| LangGraph 프로세스가 RAG 를 자식으로 띄우기 | 한쪽이 죽을 때 상태가 꼬이고 로그가 섞인다. 기동만 돕는 별도 스크립트가 더 단순 |
| 헬스 체크를 `src/web/app.py` 의 `check_rag` 재사용 | 런처가 웹 모듈(FastAPI)을 import 하게 되어 의존 방향이 어색하고 기동이 느려진다. `httpx.get` 3줄 직접 사용 |

## 리드 판단 기록

- 2026-09-28 (1): 사용자 확정 — 기동 스크립트, 중복 실행 금지. 중복 판정은 **`/check` 200 기준**(프로세스 검색이 아니라 실제 서비스 가능 여부), 종료는 **PID 파일 + 명령줄 확인 이중 조건**으로 남의 프로세스를 건드리지 않게 한다. 다중 세션 환경에서 서버 점유 충돌이 반복된 이력을 반영한 결정.
- 2026-09-28 (2): Ollama 는 확인만 하고 기동하지 않는다. 시스템 데몬으로 떠 있는 경우가 많아 중복 기동이 더 위험하다.
- 2026-09-28 (3): 새 설정 키를 만들지 않는다(agent-10 과 동일 근거). RAG 경로는 `--rag-dir` 인자, 기본값은 README 가 이미 가정하는 형제 디렉터리 `../RAG`.
- 2026-09-28 (4): Builder 작업 1~3 완료 보고 — 253 passed(HEAD 220 + 33), 신규 3파일 + README 만 변경, 런타임 코드·설정·의존성·RAG 저장소 diff 0, 명세와의 차이 없음. 핵심 요건 고정 확인: healthy=True 시 `spawn` 호출 0회(PID 파일 잔존 케이스 포함), `--stop` 의 `not_ours` 경로에서 killer 호출 0회, `SIGKILL`/`kill -9`/`terminate()` 부재를 grep 테스트로 고정, Ollama 기동·종료 경로 없음. 구현 메모 승인: `stop()` 의 10초 대기를 `wait` 인자로 주입 가능하게 함(단위 테스트가 실제로 10초를 기다리지 않도록). 기본값은 명세대로 10초이므로 동작 변경 없음. 리드가 Validator 스폰.
- 2026-09-28 (5): Validator 판정 READY FOR REVIEW 접수(검증 회차 1). L1~L8·완료 기준 전부 PASS, 278 passed / 0 failed. 핵심 확인: ① **L1 민감도** — 스크래치 사본에서 `if healthy(...): return already` 가드만 삭제하니 해당 테스트 2건이 실제로 실패(`'started' == 'already'`), `matches_command` 가드 삭제 시 1건 실패 → 두 가드가 테스트로 고정돼 있음이 실증됨. ② **`--stop` 안전** — 시그널 발송 지점은 `src/launcher.py` 1곳뿐이고 앞에 PID 생존 + 명령줄 일치 이중 가드. `SIGKILL`/`kill -9`/`terminate()`/`killpg`/`pkill`/`os.system`/`pgrep`/`lsof` 전부 0건(파라미터화 테스트로 고정). ③ **L8 실환경 4경로 전부 실측** — 검증 시작 시 5010·5020 이 비어 있어 Validator 가 직접 띄우고 정리함. 2회차 `run.py` 실행 후 PID 동일·새 프로세스 0(중복 기동 없음), PID 파일을 치운 상태의 `--stop` 은 `다른 프로세스가 사용 중입니다. 건드리지 않습니다.` 출력 후 서버 2대 모두 200 유지(시그널 미발송), PID 파일 복구 후에만 정상 종료. 무관한 타 세션 프로세스(71790)는 전 구간 생존. ④ 런타임 코드·설정·의존성 diff 0, 기존 테스트 삭제 0줄. **결론: READY FOR REVIEW 선언.**
  - 비차단 의견 판단: ① Builder 테스트 `test_launcher.py:405` 의 슬라이싱이 `spawn_process` 가 아니라 다음 함수 본문을 검사함 — 통과하지만 의미가 약함. Validator 가 정확한 버전을 추가해 커버리지 공백이 없으므로 **수정 지시하지 않음**(잘못된 단언이 아니라 대상이 빗나간 약한 단언). 다만 추후 그 파일을 손댈 때 정리할 후보로 기록. ② Builder 테스트의 `Path("src/launcher.py")` cwd 의존 — 프로젝트 루트에서 실행하는 것이 팀 관례(RAG README 도 명시)라 조치 없음. ③ `pid 0` 은 `ps -p 0` 빈 출력으로 `matches_command` 가 False → 안전, 테스트로 고정됨. 코드 변경 불필요.
  - `[추측]` RAG 저장소 `docs/plans/conventions.md` 1건 수정은 agent-11 과 무관한 RAG 쪽 다른 작업 산출물. 이 티켓은 RAG 소스·설정 변경 0.
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

- 2026-09-28 Validator 검증 회차 1 — **READY FOR REVIEW**. 스위트 `278 passed / 6 deselected`
  (`.venv/bin/python -m pytest -q`, HEAD `bca3b5a` + agent-10·agent-11 미커밋분 + Validator 추가 25건).
  `test/test_launcher.py` 는 33 → 58 (순수 append, 기존 줄 삭제·수정 0).

| 항목 | 결과 | 증거 |
|---|---|---|
| L1 이미 실행 중이면 기동 안 함 | PASS | `test_launcher.py:56,68` — `state="already"`, `spawn.calls == []`. 민감도: 스크래치패드 사본에서 `src/launcher.py:102-103` 가드를 지우자 두 테스트 모두 실패(`assert 'started' == 'already'`) |
| L2 미실행이면 기동 | PASS | `test_launcher.py:83` — `spawn.calls == ["RAG"]`, pid 파일 `1234`, `state="started"` |
| L3 PID 파일 처리 | PASS | `test_launcher.py:110-133` + 추가 `matches_command(0, ...) is False` |
| L4 기동 실패 | PASS | `test_launcher.py:138,149` — `state="failed"`, 로그 경로 포함, `spawn` 1회(재시도 없음) |
| L5 `--stop` 안전 | PASS | ① `test_launcher.py:172` killer 0회 ② `:196` 시그널 없이 pid 파일만 삭제 ③ `:214` SIGTERM 1회 ④ `SIGKILL`/`kill -9`/`terminate()`/`.kill()`/`killpg`/`pkill`/`killall`/`os.system`/`lsof`/`pgrep`/`fuser` 모두 `run.py`·`src/launcher.py` 에 없음(grep + 추가 파라미터화 테스트) |
| L6 진입점 | PASS | 실제 실행: `--status --stop` → 2, `--rag-dir /nope/nope` → 1, venv 파이썬 없음 → 1(단위), 정상 출력에 두 서비스 줄 + `브라우저:` + `종료:` 포함 |
| L7 회귀·범위 | PASS | `git diff HEAD --stat -- src main.py web.py maintenance.py resources requirements.in requirements.txt` 출력 0줄. 신규 추적외 파일은 `run.py`·`src/launcher.py`(본 티켓) + agent-10 산출물. 새 의존성 0, 새 설정 키 0 |
| L8 실환경 | PASS | 검증 시점에 5010·5020 이 **비어 있었다**(baseline `lsof` 리스너 0, `/check` 000) → ①~④ 전부 실측. 아래 표 참조 |

L8 실측(내가 띄운 프로세스로 왕복 확인, 타 세션 프로세스 `pid 71790 main.py --active-profile local` 은 전 구간 무사):

| 단계 | 명령 | 결과 |
|---|---|---|
| ① | `run.py --status` (빈 상태) | `실행 중이 아닙니다` 2줄, 종료 0 |
| ② | `run.py` 1회차 | RAG pid 5073(+워커 5085), Web pid 5180 기동, `/check` 둘 다 200 |
| ② | `run.py` 2회차 | 두 서비스 모두 `이미 실행 중 ... (건드리지 않습니다)`, `pgrep -f active-profile` 전후 동일(`5073 5180 71790`) — 새 프로세스 0 |
| ③ | pid 파일을 치운 뒤 `run.py --stop` | 두 서비스 모두 `다른 프로세스가 사용 중입니다. 건드리지 않습니다.` / 실행 후에도 pid 5073·5180 생존, `/check` 5010=200 5020=200 — **시그널 미발송 확인** |
| ④ | pid 파일 복구 후 `run.py --stop` | `종료했습니다 (pid 5180)` / `(pid 5073)`, 워커 5085 도 함께 종료, `/check` 000, pid 파일 삭제, 타 세션 71790 생존. 검증 후 환경은 baseline(포트 비어 있음)으로 복귀 |

- 완료 기준: L1~L8 전부 PASS([미확인] 없음) / `spawn` 미호출 테스트 고정 / `--stop` 의 pid 파일 부재 경로 시그널 0회 고정 + `SIGKILL` 경로 없음 / Ollama 는 `GET /api/tags` 확인만(`spawn_process` 본문·`build_services` 명령에 ollama 없음, `check_ollama` 중 `subprocess.Popen`·`run` 호출 시 실패하는 테스트 추가) / 새 의존성·설정 키·런타임 코드 변경 0 / HEAD 는 `bca3b5a` 그대로(미커밋).
- Validator 가 추가한 테스트(`test/test_launcher.py` 끝에 append, 25건): `is_healthy` 200/비200/예외, `is_alive`, 실제 `ps` 기반 `matches_command`(pid 0·없는 pid 포함), 실제 `matches_command` 로 남의 pid 거부, 강제 종료·프로세스 탐색 문자열 부재(파라미터화), `spawn_process` 본문·`build_services` 명령의 ollama 부재, `check_ollama` 성공 경로 및 프로세스 미기동, `timeout=0` 경계, `status` 의 stale pid 파일 정리, 진입점 `--stop` 전체 경로 시그널 0회.
- `[검증]` RAG 저장소 `git status`: `docs/plans/conventions.md` 1건 수정 상태. 내용은 RAG 자체 "후속 6" 기준선 커밋 해시 표기(런처와 무관, 문서 전용). `[추측]` 이 티켓이 아닌 RAG 쪽 다른 작업의 산출물. 소스·설정 변경은 0.
- `[미확인]` `--rag-dir` 의 RAG venv 파이썬 부재 경로는 단위 테스트로만 확인했고 실제 환경에서는 venv 가 존재해 실측하지 않았다.

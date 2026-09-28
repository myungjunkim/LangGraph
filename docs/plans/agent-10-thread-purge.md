# agent-10: 오래된 대화 정리 명령 (수동)

- 작성일: 2026-09-28
- 작성자: 리드
- 상태: READY FOR REVIEW (2026-09-28, 리드 확인, 검증 회차 1) — 커밋 대기(사용자)
- 사용자 확정: **수동 명령**(자동 삭제 없음), 기본 보존 **14일**, README 에 사용법 추가.
- 선행: agent-09(영구 체크포인터, 커밋 `bca3b5a`). 저장소: `/Users/mjkim/workspace/LangGraph` 만.

## 목표

`data/checkpoints.sqlite` 에 계속 쌓이는 대화를 사용자가 원할 때 정리할 수 있는 명령을 만든다. 기본은 **목록만 보여주고 아무것도 지우지 않으며**, `--apply` 를 붙였을 때만 삭제한다. 에이전트·웹·CLI 런타임 동작은 일절 바꾸지 않는다.

## 확인된 현재 상태 (`[검증]`)

- `src/checkpointer.py`: `sqlite_saver(path)`(동기, 컨텍스트 매니저), `async_sqlite_saver(path)`. 부모 디렉터리 생성 포함.
- `src/config/settings.py`: `Settings.checkpoint_db: Path`(상대 경로는 `PROJECT_ROOT` 기준), `config_path_for(profile)`, `load_settings(path)`.
- `langgraph-checkpoint-sqlite==3.1.1`, `langgraph-checkpoint` 설치됨. CLI 는 동기 `SqliteSaver` 경로에서 정상 동작(agent-09 실측).
- 현재 어떤 경로로도 체크포인트를 삭제하지 않는다. DB 는 단조 증가한다.
- 진입점 컨벤션: 프로젝트 루트에 `main.py`, `web.py` + `--active-profile`.

### 선행 확인 게이트 (`[검증]` 2026-09-28 Builder 실측 — G1~G4 전부 PASS, 리드 판단 기록 (4))

| 확인할 것 | 확인 방법 | 실패 시 |
|---|---|---|
| G1. 전체 대화 열거 | `SqliteSaver.list(None)` 이 모든 thread 의 체크포인트를 돌려주는지, 각 항목에서 `config["configurable"]["thread_id"]` 를 얻을 수 있는지 | ESCALATE |
| G2. 마지막 활동 시각 | `CheckpointTuple.checkpoint["ts"]` 로 시각을 얻을 수 있는지, 형식(ISO 문자열 여부·타임존) | ESCALATE |
| G3. 대화 삭제 | `saver.delete_thread(thread_id)` 가 존재하고 해당 thread 만 지우는지 | **ESCALATE**(내부 테이블에 직접 `DELETE` 를 쓰지 말 것 — 패키지 스키마에 결합되면 업그레이드 때 깨진다) |
| G4. 미리보기 정보 | 최신 체크포인트의 `channel_values["messages"]` 로 메시지 개수와 첫 질문을 얻을 수 있는지 | **ESCALATE 아님** — 얻을 수 없으면 해당 2개 열을 빼고 `thread_id` + 마지막 활동일만 출력하고 리드에 보고 |

실측은 임시 SQLite 파일 + 가짜 모델로 하고(네트워크 불필요), 결과를 리드에 보고한 뒤 진행한다.

## 설계

### CLI

```bash
python maintenance.py --active-profile=local                        # 14일 이상 미사용 대화 목록만(삭제 없음)
python maintenance.py --active-profile=local --apply                # 실제 삭제
python maintenance.py --active-profile=local --older-than 30 --apply
python maintenance.py --active-profile=local --apply --vacuum       # 삭제 후 파일 크기 회수
```

| 인자 | 기본값 | 설명 |
|---|---|---|
| `--active-profile` | `local` | `resources/config_{profile}.ini` |
| `--older-than` | **14** | 마지막 활동 이후 경과 일수. 이 값을 **넘긴** 대화만 대상(정확히 N일은 대상 아님) |
| `--apply` | 없음(False) | 붙였을 때만 삭제. 없으면 목록만 |
| `--vacuum` | 없음(False) | `--apply` 와 함께일 때만 `VACUUM` 실행. 단독이면 무시하고 안내 |

- **새 설정 키를 만들지 않는다.** 보존 기간은 인자로만 받는다(근거: 아래 "기각한 대안").
- 종료 코드: 정상 0. DB 파일이 없거나 대화가 0개여도 안내 후 **0**(정리할 게 없는 것은 오류가 아니다). 삭제 중 예외는 메시지 출력 후 1.

### 출력 형식

```
전체 대화 12개 · 정리 대상 3개 (마지막 활동 14일 경과 기준)
  564621ed-0582-4a09-b2fa-5cc7ff34fe8e  2026-08-11  메시지 8개  메시지 등록 API 알려줘
  s9-web-1                              2026-09-01  메시지 2개  gpts 등록 API 알려줘
  ...
--apply 를 붙이면 삭제합니다.
```

`--apply` 일 때 마지막 줄만 교체: `삭제 완료: 3개 대화 (남은 대화 9개)`. `--vacuum` 이면 그 뒤에 `VACUUM 완료: 172,032B → 61,440B`.

- 정렬: 마지막 활동 **오래된 순**.
- 첫 질문은 40자까지, 줄바꿈은 공백으로 치환.

### `src/maintenance.py`

```python
@dataclass(frozen=True)
class ThreadSummary:
    thread_id: str
    last_active: datetime        # 마지막 체크포인트 시각
    message_count: int           # G4 실패 시 0
    first_question: str          # G4 실패 시 ""

def collect_threads(saver) -> list[ThreadSummary]
    # saver.list(None) 로 전체를 훑어 thread_id 별 집계. 마지막 활동 오름차순 정렬.

def select_expired(threads: list[ThreadSummary], older_than_days: int, now: datetime) -> list[ThreadSummary]
    # now - last_active > timedelta(days=older_than_days) 인 것만 (엄격히 초과)

def purge(saver, threads: list[ThreadSummary]) -> int
    # 각 thread 에 delete_thread 호출, 삭제 개수 반환

def format_report(all_threads, expired, older_than_days: int) -> str
    # 위 출력 형식의 목록 부분(마지막 안내 줄 제외)
```

- 시각 비교는 `datetime.now(timezone.utc)` 기준. `ts` 에 타임존이 없으면 UTC 로 간주하고, 이 가정을 코드 주석에 남긴다.
- `VACUUM` 은 saver 내부 커넥션에 손대지 말고 **`with` 블록을 빠져나온 뒤** `sqlite3.connect(path)` 로 별도 실행한다. 다른 프로세스가 DB 를 잠그고 있어 실패하면 경고만 출력하고 종료 코드는 0(삭제 자체는 성공했으므로).

### `maintenance.py` (진입점)

argparse → `load_settings(config_path_for(profile))` → DB 파일 존재 확인 → `with sqlite_saver(settings.checkpoint_db) as saver:` 안에서 `collect_threads` / `select_expired` / (`--apply` 면) `purge` → 리포트 출력 → (`--apply --vacuum` 이면) VACUUM.

### 변경하지 않는 것

`src/agent.py`, `src/web/**`, `src/tools.py`, `src/rag_client.py`, `src/llm_factory.py`, `src/config/settings.py`, `src/checkpointer.py`, `main.py`, `web.py`, `resources/**`, `requirements.in/txt`. **런타임 동작·의존성·설정 스키마 전부 불변.**

## 작업 목록

1. 선행 게이트 G1~G4 실측 후 리드에 보고(코드 작성 전).
2. `src/maintenance.py` 구현 + `test/test_maintenance.py`(M1·M2·M6).
3. `maintenance.py` 진입점 + M3·M4·M5 테스트.
4. README "대화 정리" 절 추가 + M7 회귀.

## 검증 전략

모든 테스트는 **임시 SQLite 파일**(`tmp_path`)과 가짜 모델을 쓴다. RAG·Ollama 불필요, 통합 테스트 추가 없음.

| 항목 | 확인 방법 |
|---|---|
| M1. 열거 | 서로 다른 시각의 대화 3개를 만든 뒤 `collect_threads` 가 3개를 돌려주고, `thread_id`·`last_active` 가 맞고, 오래된 순으로 정렬됨. `message_count`·`first_question` 이 실제 대화 내용과 일치(G4 성공 시) |
| M2. 선택 경계 | `now` 를 고정하고 마지막 활동이 13일/정확히 14일/15일 전인 대화 → **15일짜리만** 대상. 대화 0개·전부 대상인 경우도 확인 |
| M3. 기본 모드 무삭제 | `--apply` 없이 진입점 실행 → 종료 코드 0, DB 의 대화 3개가 그대로 남고 목록이 출력됨. **실행 후 각 대화의 메시지를 다시 읽어 손상되지 않았는지 확인** |
| M4. 삭제 | `--apply` 실행 → 대상만 사라지고 나머지 대화는 여전히 메시지가 읽힘(체크포인터로 상태 조회). 삭제 개수 출력이 실제와 일치 |
| M5. 빈 상태 | DB 파일 없음 → 안내 출력·종료 코드 0·파일 생성하지 않음(또는 빈 DB 생성은 허용하되 삭제·오류 없음). 대화 0개 DB → 안내 출력·종료 코드 0 |
| M6. 출력 형식 | 목록 줄에 `thread_id`·날짜·메시지 개수·첫 질문(40자 절단, 줄바꿈 없음)이 포함. `--apply` 시 `삭제 완료:` 줄. `--vacuum` 단독이면 무시 안내 |
| M7. 회귀·범위 | `pytest -q` green(HEAD 178 + 신규). `git diff --stat HEAD` 가 `maintenance.py`, `src/maintenance.py`, `test/test_maintenance.py`, `README.md`, `docs/plans/agent-10-*.md` 뿐. `requirements.in` diff 0, `resources/` diff 0 |
| M8. 수동 확인 (Validator) | 실제 `data/checkpoints.sqlite` 에 대해 `python maintenance.py --active-profile=local --older-than 0` 를 **`--apply` 없이** 실행해 현재 대화 목록이 사람이 읽을 수 있게 나오는지 확인. **실제 DB 에는 절대 `--apply` 를 쓰지 말 것**(사용자 대화). 삭제 실행 확인은 임시 DB 사본으로 한다 |

## 완료 기준

- [ ] M1~M8 통과
- [ ] 새 의존성 0, 새 설정 키 0, 런타임 코드(`src/agent.py`·`src/web/**`·`main.py`·`web.py`) 변경 0
- [ ] `--apply` 없이는 어떤 경로로도 삭제되지 않음
- [ ] 삭제 후 남은 대화가 정상적으로 읽힘(M4)
- [ ] README 에 사용법·주의(되돌릴 수 없음, 서버 실행 중 삭제 시 해당 브라우저는 빈 대화로 시작) 기재
- [ ] 커밋하지 않음(사용자)

## 범위 제외

- **자동 정리**(기동 시·주기 실행) — 사용자가 수동으로 확정. 되돌릴 수 없는 동작이라 기본값으로 두지 않는다
- 보존 기간 설정 키(`[checkpoint] retention-days` 등)
- 대화 내보내기·백업·복구
- 대화 하나 안에서 중간 체크포인트만 정리(압축) — 체크포인트 사슬이 끊겨 복원이 깨질 수 있다
- 웹 UI 에서 대화 삭제·목록 보기
- 사용자별 소유권·권한(현재 `thread_id` 를 아는 사람이 곧 소유자)

## 기각한 대안

| 대안 | 기각 이유 |
|---|---|
| 서버·CLI 기동 시 자동 삭제 | 사용자가 수동 선택. 되돌릴 수 없는 데이터 삭제를 기본 동작으로 두지 않는다 |
| 보존 기간을 `config_local.ini` 에 추가 | agent-02 에서 `[fastapi]` 를 필수 키로 만들어 기존 설정 파일이 `KeyError` 로 죽은 전례가 있다. 인자 1개로 충분하므로 설정 스키마를 건드리지 않는다 |
| `sqlite3` 로 `checkpoints`/`writes` 테이블에 직접 `DELETE` | 패키지 내부 스키마에 결합. 업그레이드 시 조용히 깨진다. 공개 API(`delete_thread`)가 없으면 ESCALATE 후 리드가 판단 |
| 대화마다 만료 시각을 따로 저장 | 체크포인트에 이미 시각이 있다. 이중 관리 |
| `--apply` 없이 바로 삭제하고 `--dry-run` 을 옵션으로 | 기본값이 파괴적이면 오타 한 번에 대화가 사라진다. 안전한 쪽을 기본으로 |

## 리드 판단 기록

- 2026-09-28 (1): 사용자 확정 — 수동 명령, 기본 14일, README 추가. 자동 실행은 명시적으로 기각(범위 제외에 기록).
- 2026-09-28 (2): 설정 키를 만들지 않기로 결정. 근거는 agent-02 `[fastapi]` 필수 키 도입 때 기존 `config_local.ini` 가 `KeyError` 로 멈춘 전례(agent-05 에서 README 안내로 대응). 같은 실수를 반복하지 않는다.
- 2026-09-28 (3): `delete_thread` 공개 API 가 없을 경우 내부 테이블 직접 조작을 **금지**하고 ESCALATE 하도록 게이트 G3 에 명시. 스키마 결합은 패키지 업그레이드 시 조용한 파손으로 이어진다.
- 2026-09-28 (4): Builder 선행 게이트 실측 보고 — **G1~G4 전부 PASS, ESCALATE 없음.** `[검증]` G3 `SqliteSaver.delete_thread(thread_id) -> None` 존재하며 해당 thread 만 삭제(A 삭제 후 B 메시지 정상 조회) → 내부 테이블 직접 DELETE 불필요. G1 `list(None)` 이 전체 thread 체크포인트 반환, `config["configurable"]["thread_id"]` 로 ID 획득. G2 `checkpoint["ts"]` 는 **타임존 포함 ISO 8601 문자열**(예 `2026-09-28T06:20:37.784670+00:00`) → `datetime.fromisoformat` 로 tz-aware 파싱. 명세의 "타임존 없으면 UTC 간주" 방어는 그대로 두되(버전 변화 대비) 현재는 항상 tz 가 붙는다. G4 최신 체크포인트의 `channel_values["messages"]` 로 메시지 개수·첫 질문 획득 가능 → **미리보기 2개 열 축소 불필요, 명세 출력 형식 그대로 유지.** 설계 변경 없음.
  - Builder 관찰 승인: `list(None)` 의 thread **간** 순서는 보장으로 삼지 않고, `collect_threads` 가 thread 별 `max(ts)` 로 마지막 활동 시각을, 그 최신 체크포인트로 미리보기를 계산한다. 타당하므로 그대로 구현.
- 2026-09-28 (5): Builder 작업 1~4 완료 보고 — 206 passed(HEAD 178 + 28), 신규 3파일 + README 만 변경, `src/**`·`main.py`·`web.py`·`resources/**`·`requirements*` diff 0, 명세와의 차이 없음. 경계(15일만 대상)·무삭제 기본·삭제 후 잔존 대화 정상 조회·빈 상태 처리 모두 Builder 실행 확인. 실제 `data/checkpoints.sqlite` 미접촉(`--help` 만 실행), M8 은 Validator 몫으로 남김. 리드가 Validator 스폰.
- 2026-09-28 (6): Validator 판정 READY FOR REVIEW 접수(검증 회차 1). M1~M8·완료 기준 전부 PASS, 220 passed / 0 failed. 핵심 확인: ① **무삭제 보장 실증** — 실제 DB 임시 사본 2부에 `--apply` 유무만 바꿔 실행, 없는 쪽은 sha256 불변(1바이트도 미변경)·대화 유지, 있는 쪽은 삭제됨. 삭제 호출은 저장소 전체에서 `src/maintenance.py` 1곳뿐이고 `if not apply: return` 뒤에만 도달. 회귀 테스트로 고정(대조군 포함). ② 삭제 후 잔존 대화를 `get_state` 로 실제 조회해 본문 확인. ③ 경계는 진입점 수준에서도 `ts+14d` 보존 / `ts+14d+1초` 삭제까지 확인(`>` → `>=` 로 바뀌면 깨지는 테스트). ④ 패키지 내부 테이블명·raw SQL 없음(grep), `sqlite3` 사용은 `vacuum()` 한정으로 명세 허용 범위. ⑤ **실제 `data/checkpoints.sqlite` 는 전 과정에서 sha256 불변**(`cmp` IDENTICAL). ⑥ 런타임 코드·설정·의존성 diff 0, 기존 테스트 삭제 0줄. **결론: READY FOR REVIEW 선언.**
  - 비차단 의견 판단: ① 대상 0개일 때 `--apply` 안내 미출력 — 지울 게 없을 때 생략이 자연스러우므로 조치 없음. ② `--older-than -1` 을 argparse 가 허용해 오늘 대화까지 대상이 됨 — `--apply` 를 별도로 붙여야 삭제되고 목록이 먼저 보이므로 실사고 위험은 낮으나, **음수 거부는 후속 티켓 후보로 기록**. ③ `agent-09` 문서 상태 줄 1줄은 리드 편집. ④ 표시 날짜는 로컬 tz, 판정은 UTC — 자정 근처 표기 차이뿐이고 삭제 판정에 영향 없음, 조치 없음.
- Builder/Validator 가 설계와 충돌을 발견하면 추측으로 진행하지 말고 이 문서에 ESCALATE 항목을 추가하고 리드에게 보고한다.

## 검증 기록

- 2026-09-28 Validator, **검증 회차 1 — READY FOR REVIEW.** `pytest -q` **220 passed, 6 deselected**(HEAD 178 + Builder 28 + Validator 14). 실행: `.venv/bin/python -m pytest -q`.
- `[검증]` **M1** 열거 — `collect_threads` 가 thread 별 최신 체크포인트로 집계·오래된 순 정렬, `message_count`/`first_question` 이 실제 대화와 일치. (`test/test_maintenance.py:44-101`)
- `[검증]` **M2** 경계 — 13일/정확히 14일/15일 중 15일만 대상(`select_expired` 엄격 초과). 0개·전부·전무 케이스 포함. (`test/test_maintenance.py:106-130`)
- `[검증]` **M3** 무삭제 — 전부 만료 대상인 상황에서 `--apply` 없이 실행해도 **DB 파일 바이트가 그대로**이고 모든 대화 메시지가 정상 조회됨. 민감도 대조군(같은 입력에 `--apply=True`)에서는 바이트가 바뀌고 대화가 사라짐. (`test/test_maintenance.py` `test_default_mode_leaves_db_bytes_untouched`)
- `[검증]` **M4** 삭제 — `--apply` 로 대상만 삭제되고 남은 대화는 체크포인터 `get_state` 로 메시지가 그대로 읽힘. 삭제 개수 출력이 실제와 일치. 진입점 레벨 경계 테스트(실제 체크포인트 `ts` + `now` 고정, 14일=보존 / 14일+1초=삭제) 추가.
- `[검증]` **M5** 빈 상태 — DB 파일 없음 → 안내·종료 0·파일 미생성. 대화 0개 DB → `전체 대화 0개 · 정리 대상 0개`·종료 0.
- `[검증]` **M6** 출력 형식 — `thread_id`·날짜·`메시지 N개`·첫 질문(40자 절단, 줄바꿈 제거) 정렬 출력. `--apply` 시 `삭제 완료:` 줄, `--vacuum` 단독 시 안내 후 무시(CLI 실측). VACUUM 실패 시 `VACUUM 건너뜀` + 종료 코드 0 테스트 추가.
- `[검증]` **M7** 회귀·범위 — `git diff --stat HEAD` = `README.md`(+39/-4), `docs/plans/agent-09-*.md`(상태 줄 1줄). `src/**`·`main.py`·`web.py`·`resources/**`·`requirements.in/txt`·`pytest.ini` **diff 0**. 신규 파일은 `maintenance.py`, `src/maintenance.py`, `test/test_maintenance.py`, `docs/plans/agent-10-thread-purge.md` 뿐. 기존 테스트 삭제·수정 0줄. 새 import 는 전부 표준 라이브러리.
- `[검증]` **M8** 수동 확인 — `python maintenance.py --active-profile=local --older-than 0`(`--apply` 없음) 실행. 출력: `전체 대화 1개 · 정리 대상 1개 (마지막 활동 0일 경과 기준)` / `564621ed-…  2026-09-28  메시지 8개  메시지 등록 API 알려줘`. 실행 전후 `data/checkpoints.sqlite` **size 172032 · mtime 2026-09-28T14:41:18+0900 · sha256 68cfbfa7… 완전 동일**(사전 백업본과 `cmp` 결과 byte-identical). 이후 `--vacuum` 단독·기본(14일)·`--help` 도 실행했으나 sha 불변. **실제 DB 에는 `--apply` 를 실행하지 않았고 삭제·이동·이름변경도 하지 않았다.** 삭제 동작은 스크래치패드의 임시 사본 2부로만 검증.
- `[검증]` 공개 API 준수 — `src/maintenance.py` 에 `checkpoints`/`writes` 테이블명, raw `DELETE`/`INSERT` SQL 없음. 삭제는 `saver.delete_thread` 단 한 곳(`src/maintenance.py:67`). `sqlite3` 는 진입점의 `VACUUM` 전용(`maintenance.py:32-33`, 명세 허용 예외).
- `[검증]` 완료 기준 — M1~M8 전부 PASS / 새 의존성 0·새 설정 키 0·런타임 코드 변경 0 / `--apply` 없이는 어떤 경로로도 삭제 없음 / 삭제 후 잔존 대화 정상 조회 / README 에 사용법 + "되돌릴 수 없다" + "서버 실행 중 삭제 시 브라우저는 빈 대화로 시작" 기재 / 미커밋(HEAD `bca3b5a` 유지).
- Validator 추가 테스트 14건(`test/test_maintenance.py` 말미): `_parse_ts` naive→UTC·tz 보존·datetime 입력, `_preview` 40자 경계/HumanMessage 없음/첫 질문 선택, 진입점 13·14·15일 경계와 14일+1초, `--apply` 없이 바이트 불변(+민감도 대조군), VACUUM 실패 시 경고·종료 0, `main()` 정상 경로 0.
- 비차단 의견(판정 미반영): (1) 정리 대상이 0개면 `--apply 를 붙이면 삭제합니다.` 안내가 출력되지 않는다(명세 출력 예시는 대상이 있는 경우뿐이라 위반 아님). (2) `--older-than -1` 은 argparse 가 받아들이며 오늘 대화까지 대상이 된다 — 명세 미규정, 필요하면 후속에서 음수 거부를 검토. (3) `docs/plans/agent-09-*.md` 상태 줄 1줄 변경은 M7 목록 밖이나 문서 전용이라 영향 없음.

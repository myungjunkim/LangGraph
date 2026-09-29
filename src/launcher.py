"""서비스 기동/종료 판정. 네트워크·프로세스 의존은 전부 주입 가능하게 둔다.

중복 실행 방지가 목적이므로 판정 기준은 포트 점유나 프로세스 검색이 아니라 `/check` 200 이다
(실제로 서비스 가능한가를 직접 말해 준다). PID 파일은 "내가 띄운 것" 식별용으로만 쓴다.
"""
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import httpx

POLL_INTERVAL = 0.5
STOP_WAIT = 10.0
LOG_TAIL_LINES = 15
ALREADY_RUNNING_NOTE = "다른 프로세스가 띄웠을 수 있어 건드리지 않습니다"


@dataclass(frozen=True)
class Service:
    name: str
    health_url: str
    url: str
    cwd: Path
    command: list[str]
    log_path: Path
    pid_path: Path


@dataclass(frozen=True)
class Result:
    state: Literal["already", "started", "waiting", "failed", "stopped", "not_ours", "not_running"]
    message: str


def is_healthy(url: str, timeout: float = 2.0) -> bool:
    try:
        return httpx.get(url, timeout=timeout).status_code == 200
    except httpx.HTTPError:
        return False


def is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, ValueError):
        return False
    except PermissionError:
        return True          # 다른 사용자 소유여도 살아는 있다
    return True


def matches_command(pid: int, marker: str) -> bool:
    """PID 재사용을 걸러내기 위해 명령줄에 기대하는 진입점이 있는지 본다."""
    try:
        output = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return marker in output


def read_pid(pid_path: Path) -> int | None:
    """살아 있는 PID 만 돌려준다. 죽었거나 형식이 틀리면 파일을 지우고 None."""
    try:
        raw = pid_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw.isdigit():
        pid_path.unlink(missing_ok=True)
        return None
    pid = int(raw)
    if not is_alive(pid):
        pid_path.unlink(missing_ok=True)       # stale 정리
        return None
    return pid


def spawn_process(service: Service) -> subprocess.Popen:
    """백그라운드로 띄운다. 부모(run.py)가 끝나도 서버는 계속 돈다.

    Popen 을 그대로 돌려준다. 자식이 죽었는지 보려면 poll() 이 필요하다 —
    좀비 프로세스에 대해서는 os.kill(pid, 0) 이 성공해 PID 로는 감지할 수 없다.
    """
    service.log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(service.log_path, "a", encoding="utf-8") as log:
        return subprocess.Popen(service.command, cwd=str(service.cwd), stdout=log,
                                stderr=subprocess.STDOUT, start_new_session=True)


def tail_log(path: Path, offset: int = 0, lines: int = LOG_TAIL_LINES) -> str:
    """offset 바이트 이후에 쌓인 내용의 마지막 N 줄. 없으면 빈 문자열.

    offset 은 spawn 직전의 파일 크기다. 이전 실행의 오류가 섞이지 않게 한다.
    """
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as log:
            log.seek(offset)
            new_lines = log.read().splitlines()
    except OSError:
        return ""
    return "\n".join(line for line in new_lines[-lines:] if line.strip())


def _log_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _with_log_tail(message: str, path: Path, offset: int) -> str:
    tail = tail_log(path, offset)
    if not tail:
        return message
    body = "\n".join(f"  {line}" for line in tail.split("\n"))
    return f"{message}\n  ─ 로그 마지막 {LOG_TAIL_LINES}줄 ─\n{body}"


def _wait_healthy(service: Service, timeout: float, healthy, sleep,
                  child_exited=lambda: False) -> str:
    """"ok" | "died" | "timeout". 자식이 죽으면 남은 시간을 기다리지 않는다."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if healthy(service.health_url):
            return "ok"
        if child_exited():
            return "died"
        sleep(POLL_INTERVAL)
    return "ok" if healthy(service.health_url) else "timeout"


def start(service: Service, timeout: float, *, healthy=is_healthy, spawn=spawn_process,
          sleep=time.sleep) -> Result:
    """이미 떠 있으면 절대 새로 띄우지 않는다."""
    if healthy(service.health_url):
        return Result("already", f"이미 실행 중 {service.url} ({ALREADY_RUNNING_NOTE})")

    started_at = time.monotonic()
    pid = read_pid(service.pid_path)
    if pid is not None:                        # 앞서 띄운 프로세스가 아직 준비 중(Popen 이 없어 poll 불가)
        if _wait_healthy(service, timeout, healthy, sleep) == "ok":
            return Result("waiting", f"기동 중이던 프로세스 준비 완료 {service.url} (pid {pid})")
        return Result("failed", f"기동 대기 시간 초과 (pid {pid}) 로그 {service.log_path}")

    offset = _log_size(service.log_path)       # 이번 실행분만 꼬리로 보여주기 위한 기준점
    process = spawn(service)                   # 재시도하지 않는다
    service.pid_path.parent.mkdir(parents=True, exist_ok=True)
    service.pid_path.write_text(str(process.pid), encoding="utf-8")

    outcome = _wait_healthy(service, timeout, healthy, sleep,
                            child_exited=lambda: process.poll() is not None)
    if outcome == "ok":
        elapsed = int(time.monotonic() - started_at)
        return Result("started", f"기동 중... ok ({elapsed}초)  {service.url}   로그 {service.log_path}")

    # 실패한 pid 파일을 남기면 다음 실행이 "기동 중" 분기로 잘못 들어간다
    service.pid_path.unlink(missing_ok=True)
    if outcome == "died":
        message = (f"기동 실패: 프로세스가 즉시 종료되었습니다 (종료 코드 {process.poll()}). "
                   f"로그 {service.log_path}")
    else:
        message = f"기동 실패: {timeout:.0f}초 안에 응답이 없습니다. 로그 {service.log_path}"
    return Result("failed", _with_log_tail(message, service.log_path, offset))


def stop(service: Service, marker: str, *, healthy=is_healthy, killer=os.kill,
         sleep=time.sleep, wait: float = STOP_WAIT) -> Result:
    """PID 파일 + 명령줄 확인을 모두 통과한 프로세스만 SIGTERM. 강제 종료는 하지 않는다."""
    pid = read_pid(service.pid_path)
    if pid is None:
        if healthy(service.health_url):
            return Result("not_ours", f"다른 프로세스가 사용 중입니다. 건드리지 않습니다. {service.url}")
        return Result("not_running", "실행 중이 아닙니다")

    if not matches_command(pid, marker):       # PID 재사용 — 남의 프로세스일 수 있다
        service.pid_path.unlink(missing_ok=True)
        return Result("not_ours", f"pid {pid} 는 다른 프로그램입니다. 종료하지 않고 pid 파일만 지웁니다")

    killer(pid, signal.SIGTERM)
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        if not is_alive(pid):
            service.pid_path.unlink(missing_ok=True)
            return Result("stopped", f"종료했습니다 (pid {pid})")
        sleep(POLL_INTERVAL)
    return Result("waiting", f"pid {pid} 가 아직 종료되지 않았습니다. 잠시 후 다시 확인하세요")


def status(service: Service, *, healthy=is_healthy) -> Result:
    if healthy(service.health_url):
        pid = read_pid(service.pid_path)
        owner = f"이 스크립트가 띄움 (pid {pid})" if pid else ALREADY_RUNNING_NOTE
        return Result("already", f"실행 중 {service.url} ({owner})")
    return Result("not_running", f"실행 중이 아닙니다 {service.url}")

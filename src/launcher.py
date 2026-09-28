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


def spawn_process(service: Service) -> int:
    """백그라운드로 띄운다. 부모(run.py)가 끝나도 서버는 계속 돈다."""
    service.log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(service.log_path, "a", encoding="utf-8") as log:
        process = subprocess.Popen(service.command, cwd=str(service.cwd), stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    return process.pid


def _wait_healthy(service: Service, timeout: float, healthy, sleep) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if healthy(service.health_url):
            return True
        sleep(POLL_INTERVAL)
    return healthy(service.health_url)


def start(service: Service, timeout: float, *, healthy=is_healthy, spawn=spawn_process,
          sleep=time.sleep) -> Result:
    """이미 떠 있으면 절대 새로 띄우지 않는다."""
    if healthy(service.health_url):
        return Result("already", f"이미 실행 중 {service.url} ({ALREADY_RUNNING_NOTE})")

    started_at = time.monotonic()
    pid = read_pid(service.pid_path)
    if pid is not None:                        # 앞서 띄운 프로세스가 아직 준비 중
        if _wait_healthy(service, timeout, healthy, sleep):
            return Result("waiting", f"기동 중이던 프로세스 준비 완료 {service.url} (pid {pid})")
        return Result("failed", f"기동 대기 시간 초과 (pid {pid}) 로그 {service.log_path}")

    pid = spawn(service)                       # 재시도하지 않는다
    service.pid_path.parent.mkdir(parents=True, exist_ok=True)
    service.pid_path.write_text(str(pid), encoding="utf-8")
    if _wait_healthy(service, timeout, healthy, sleep):
        elapsed = int(time.monotonic() - started_at)
        return Result("started", f"기동 중... ok ({elapsed}초)  {service.url}   로그 {service.log_path}")
    return Result("failed", f"기동 실패: {timeout:.0f}초 안에 응답이 없습니다. 로그 {service.log_path}")


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

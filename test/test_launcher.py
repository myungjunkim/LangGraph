"""기동 스크립트 판정 로직. healthy/spawn/killer 를 주입해 네트워크·프로세스 없이 검증한다."""
import os
import signal
from pathlib import Path

import httpx
import pytest

import run
from src.launcher import Service, read_pid, start, status, stop


def _service(tmp_path, name="RAG") -> Service:
    return Service(
        name=name,
        health_url="http://127.0.0.1:5010/check",
        url="http://127.0.0.1:5010",
        cwd=tmp_path,
        command=["python", "main.py", "--active-profile=local"],
        log_path=tmp_path / "logs" / "rag.log",
        pid_path=tmp_path / "logs" / "rag.pid",
    )


class SpawnSpy:
    """호출 여부를 기록하는 가짜 spawn. 중복 실행 방지 단언의 근거."""

    def __init__(self, pid=4242):
        self.calls = []
        self._pid = pid

    def __call__(self, service):
        self.calls.append(service.name)
        return self._pid


class HealthScript:
    """호출 순서대로 미리 정한 값을 돌려주는 가짜 healthy."""

    def __init__(self, *values, default=None):
        self._values = list(values)
        self._default = values[-1] if default is None and values else default
        self.calls = []

    def __call__(self, url, timeout=2.0):
        self.calls.append(url)
        return self._values.pop(0) if self._values else self._default


def _noop_sleep(_seconds):
    pass


# --- L1. 이미 실행 중이면 기동하지 않는다 (중복 실행 방지의 핵심) ---

def test_start_skips_when_already_healthy(tmp_path):
    service = _service(tmp_path)
    spawn = SpawnSpy()

    result = start(service, timeout=5, healthy=HealthScript(True), spawn=spawn, sleep=_noop_sleep)

    assert result.state == "already"
    assert spawn.calls == []                      # 새 프로세스를 만들지 않았다
    assert not service.pid_path.exists()          # PID 파일도 남기지 않는다
    assert "건드리지 않습니다" in result.message


def test_start_does_not_spawn_even_if_stale_pid_file_exists(tmp_path):
    """이미 떠 있으면 PID 파일 상태와 무관하게 기동하지 않는다."""
    service = _service(tmp_path)
    service.pid_path.parent.mkdir(parents=True)
    service.pid_path.write_text("999999", encoding="utf-8")
    spawn = SpawnSpy()

    result = start(service, timeout=5, healthy=HealthScript(True), spawn=spawn, sleep=_noop_sleep)

    assert result.state == "already"
    assert spawn.calls == []


# --- L2. 미실행이면 기동한다 ---

def test_start_spawns_once_when_not_running(tmp_path):
    service = _service(tmp_path)
    spawn = SpawnSpy(pid=1234)

    result = start(service, timeout=5, healthy=HealthScript(False, True), spawn=spawn, sleep=_noop_sleep)

    assert result.state == "started"
    assert spawn.calls == ["RAG"]                 # 정확히 1회
    assert service.pid_path.read_text(encoding="utf-8") == "1234"
    assert str(service.log_path) in result.message


def test_start_waits_for_existing_starting_process(tmp_path, monkeypatch):
    """PID 파일의 프로세스가 살아 있으면 새로 띄우지 않고 기다린다."""
    service = _service(tmp_path)
    service.pid_path.parent.mkdir(parents=True)
    service.pid_path.write_text(str(os.getpid()), encoding="utf-8")
    spawn = SpawnSpy()

    result = start(service, timeout=5, healthy=HealthScript(False, True), spawn=spawn, sleep=_noop_sleep)

    assert result.state == "waiting"
    assert spawn.calls == []


# --- L3. PID 파일 처리 ---

def test_read_pid_returns_live_pid(tmp_path):
    pid_path = tmp_path / "x.pid"
    pid_path.write_text(str(os.getpid()), encoding="utf-8")
    assert read_pid(pid_path) == os.getpid()
    assert pid_path.exists()


def test_read_pid_removes_stale_file(tmp_path):
    pid_path = tmp_path / "x.pid"
    pid_path.write_text("999999", encoding="utf-8")     # 존재하지 않을 PID
    assert read_pid(pid_path) is None
    assert not pid_path.exists()


@pytest.mark.parametrize("content", ["", "  ", "pid-1", "abc"])
def test_read_pid_rejects_non_numeric(tmp_path, content):
    pid_path = tmp_path / "x.pid"
    pid_path.write_text(content, encoding="utf-8")
    assert read_pid(pid_path) is None
    assert not pid_path.exists()


def test_read_pid_without_file(tmp_path):
    assert read_pid(tmp_path / "없음.pid") is None


# --- L4. 기동 실패 ---

def test_start_reports_failure_after_timeout(tmp_path):
    service = _service(tmp_path)
    spawn = SpawnSpy()

    result = start(service, timeout=0, healthy=HealthScript(False), spawn=spawn, sleep=_noop_sleep)

    assert result.state == "failed"
    assert spawn.calls == ["RAG"]                 # 재시도 없음
    assert str(service.log_path) in result.message


def test_start_failure_of_existing_process_mentions_log(tmp_path):
    service = _service(tmp_path)
    service.pid_path.parent.mkdir(parents=True)
    service.pid_path.write_text(str(os.getpid()), encoding="utf-8")
    spawn = SpawnSpy()

    result = start(service, timeout=0, healthy=HealthScript(False), spawn=spawn, sleep=_noop_sleep)

    assert result.state == "failed"
    assert spawn.calls == []
    assert str(service.log_path) in result.message


# --- L5. --stop 안전 규칙 ---

class KillSpy:
    def __init__(self):
        self.calls = []

    def __call__(self, pid, sig):
        self.calls.append((pid, sig))


def test_stop_does_not_touch_other_process(tmp_path):
    """PID 파일이 없는데 서비스가 살아 있으면 아무것도 하지 않는다."""
    service = _service(tmp_path)
    killer = KillSpy()

    result = stop(service, marker="main.py", healthy=HealthScript(True), killer=killer,
                  sleep=_noop_sleep)

    assert result.state == "not_ours"
    assert killer.calls == []                     # 시그널 미발송
    assert "건드리지 않습니다" in result.message


def test_stop_when_nothing_is_running(tmp_path):
    service = _service(tmp_path)
    killer = KillSpy()

    result = stop(service, marker="main.py", healthy=HealthScript(False), killer=killer,
                  sleep=_noop_sleep)

    assert result.state == "not_running"
    assert killer.calls == []


def test_stop_skips_when_command_does_not_match(tmp_path, monkeypatch):
    """PID 가 재사용된 경우: 시그널 없이 pid 파일만 지운다."""
    import src.launcher as launcher

    service = _service(tmp_path)
    service.pid_path.parent.mkdir(parents=True)
    service.pid_path.write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr(launcher, "matches_command", lambda pid, marker: False)
    killer = KillSpy()

    result = stop(service, marker="main.py", healthy=HealthScript(True), killer=killer,
                  sleep=_noop_sleep)

    assert result.state == "not_ours"
    assert killer.calls == []
    assert not service.pid_path.exists()


def test_stop_sends_sigterm_once_when_ours(tmp_path, monkeypatch):
    import src.launcher as launcher

    service = _service(tmp_path)
    service.pid_path.parent.mkdir(parents=True)
    service.pid_path.write_text("4321", encoding="utf-8")
    monkeypatch.setattr(launcher, "is_alive", lambda pid: pid == 4321 and not killer.calls)
    monkeypatch.setattr(launcher, "matches_command", lambda pid, marker: True)
    killer = KillSpy()

    result = stop(service, marker="main.py", healthy=HealthScript(True), killer=killer,
                  sleep=_noop_sleep)

    assert result.state == "stopped"
    assert killer.calls == [(4321, signal.SIGTERM)]
    assert not service.pid_path.exists()


def test_stop_reports_when_process_stays_alive(tmp_path, monkeypatch):
    """내려가지 않아도 강제 종료하지 않고 안내만 한다."""
    import src.launcher as launcher

    service = _service(tmp_path)
    service.pid_path.parent.mkdir(parents=True)
    service.pid_path.write_text("4321", encoding="utf-8")
    monkeypatch.setattr(launcher, "is_alive", lambda pid: True)
    monkeypatch.setattr(launcher, "matches_command", lambda pid, marker: True)
    killer = KillSpy()

    result = stop(service, marker="main.py", healthy=HealthScript(True), killer=killer,
                  sleep=_noop_sleep, wait=0)

    assert result.state == "waiting"
    assert killer.calls == [(4321, signal.SIGTERM)]
    assert service.pid_path.exists()              # 아직 살아 있으므로 파일을 지우지 않는다


def test_no_sigkill_anywhere_in_launcher():
    """강제 종료 경로 자체를 두지 않는다."""
    source = Path("src/launcher.py").read_text(encoding="utf-8")
    assert "SIGKILL" not in source
    assert "kill -9" not in source
    assert "terminate()" not in source


# --- status ---

def test_status_reports_running_service(tmp_path):
    service = _service(tmp_path)
    result = status(service, healthy=HealthScript(True))
    assert result.state == "already"
    assert service.url in result.message


def test_status_marks_our_own_process(tmp_path):
    service = _service(tmp_path)
    service.pid_path.parent.mkdir(parents=True)
    service.pid_path.write_text(str(os.getpid()), encoding="utf-8")

    result = status(service, healthy=HealthScript(True))

    assert f"pid {os.getpid()}" in result.message


def test_status_reports_stopped_service(tmp_path):
    result = status(_service(tmp_path), healthy=HealthScript(False))
    assert result.state == "not_running"


# --- L6. 진입점 ---


@pytest.fixture
def entry(monkeypatch, write_config, tmp_path):
    """run.main 을 네트워크·프로세스 없이 돌리기 위한 준비."""
    from src.config.settings import load_settings

    settings = load_settings(write_config())
    monkeypatch.setattr(run, "load_settings", lambda path: settings)
    monkeypatch.setattr(run, "config_path_for", lambda profile: tmp_path / "ini")
    monkeypatch.setattr(run, "check_ollama", lambda settings, out=print: out("Ollama      : ok (테스트)"))

    rag_dir = tmp_path / "RAG"
    (rag_dir / ".venv" / "bin").mkdir(parents=True)
    (rag_dir / ".venv" / "bin" / "python").write_text("", encoding="utf-8")
    return rag_dir


def _main(argv, **kwargs):
    out = []
    code = run.main(argv, out=out.append)
    return code, out


def test_status_and_stop_together_is_an_error(entry):
    code, out = _main(["--status", "--stop", "--rag-dir", str(entry)])
    assert code == 2
    assert "함께 쓸 수 없습니다" in out[0]


def test_missing_rag_dir_is_reported(entry, tmp_path):
    code, out = _main(["--rag-dir", str(tmp_path / "없는디렉터리")])
    assert code == 1
    assert "RAG 저장소를 찾을 수 없습니다" in out[0]


def test_missing_rag_venv_python_is_reported(entry):
    (entry / ".venv" / "bin" / "python").unlink()
    code, out = _main(["--rag-dir", str(entry)])
    assert code == 1
    assert "RAG 가상환경 파이썬이 없습니다" in out[0]


def test_start_output_lists_both_services(entry, monkeypatch):
    from src.launcher import Result

    monkeypatch.setattr(run, "start", lambda service, timeout: Result("already", f"이미 실행 중 {service.url}"))

    code, out = _main(["--rag-dir", str(entry)])

    text = "\n".join(out)
    assert code == 0
    assert "RAG" in text and "LangGraph" in text
    assert "브라우저: http://127.0.0.1:5020" in text
    assert "종료: python run.py --stop" in text


def test_start_returns_1_when_a_service_fails(entry, monkeypatch):
    from src.launcher import Result

    monkeypatch.setattr(run, "start",
                        lambda service, timeout: Result("failed", "기동 실패: 로그 logs/rag.log")
                        if service.name == "RAG" else Result("already", "이미 실행 중"))

    code, out = _main(["--rag-dir", str(entry)])

    assert code == 1
    assert "기동 실패" in "\n".join(out)


def test_status_mode_does_not_start_anything(entry, monkeypatch):
    from src.launcher import Result

    spawned = []
    monkeypatch.setattr(run, "start", lambda service, timeout: spawned.append(service) or Result("started", "x"))
    monkeypatch.setattr(run, "status", lambda service: Result("not_running", f"실행 중이 아닙니다 {service.url}"))

    code, out = _main(["--status", "--rag-dir", str(entry)])

    assert code == 0
    assert spawned == []
    assert len(out) == 2 and all("실행 중이 아닙니다" in line for line in out)


def test_stop_mode_passes_expected_markers(entry, monkeypatch):
    from src.launcher import Result

    seen = []

    def fake_stop(service, marker):
        seen.append((service.name, marker))
        return Result("not_running", "실행 중이 아닙니다")

    monkeypatch.setattr(run, "stop", fake_stop)

    code, out = _main(["--stop", "--rag-dir", str(entry)])

    assert code == 0
    assert seen == [("LangGraph", "web.py"), ("RAG", "main.py")]   # 기동의 역순
    assert len(out) == 2


def test_services_are_built_from_settings(entry, write_config):
    from src.config.settings import load_settings

    settings = load_settings(write_config())
    rag, web = run.build_services(settings, entry, "local")

    assert rag.health_url == "http://127.0.0.1:5010/check"
    assert web.health_url == "http://127.0.0.1:5020/check"
    assert rag.command[1:] == ["main.py", "--active-profile=local"]
    assert web.command[1:] == ["web.py", "--active-profile=local"]
    assert rag.cwd == entry and web.cwd == run.PROJECT_ROOT
    assert rag.pid_path.name == "rag.pid" and web.pid_path.name == "web.pid"


def test_ollama_is_only_checked_never_started():
    source = Path("run.py").read_text(encoding="utf-8") + Path("src/launcher.py").read_text(encoding="utf-8")
    assert "ollama serve" not in source.replace("`ollama serve`", "")   # 안내 문구 외 실행 없음
    assert "brew services start" not in source.replace("`brew services start ollama`", "")
    # Ollama 를 Popen 으로 띄우는 경로가 없다
    assert "Popen" in source and "ollama" not in source.split("def spawn_process")[1].split("def ")[1]


def test_ollama_check_reports_missing_model(write_config, monkeypatch):
    from src.config.settings import load_settings

    settings = load_settings(write_config())
    monkeypatch.setattr(run.httpx, "get",
                        lambda url, timeout: httpx.Response(200, json={"models": [{"name": "llama3:8b"}]},
                                                            request=httpx.Request("GET", url)))
    out = []
    run.check_ollama(settings, out=out.append)
    assert "모델 없음" in out[0] and "ollama pull qwen3:14b" in out[0]


def test_ollama_check_reports_connection_failure(write_config, monkeypatch):
    from src.config.settings import load_settings

    settings = load_settings(write_config())

    def boom(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(run.httpx, "get", boom)
    out = []
    run.check_ollama(settings, out=out.append)
    assert "응답 없음" in out[0]


# --- (Validator 추가) 주입 없이 실제로 동작하는 저수준 함수들 ---


def test_is_healthy_true_only_for_200(monkeypatch):
    """/check 200 만 '실행 중'이다."""
    from src.launcher import is_healthy

    monkeypatch.setattr(httpx, "get",
                        lambda url, timeout: httpx.Response(200, request=httpx.Request("GET", url)))
    assert is_healthy("http://127.0.0.1:5010/check") is True

    monkeypatch.setattr(httpx, "get",
                        lambda url, timeout: httpx.Response(503, request=httpx.Request("GET", url)))
    assert is_healthy("http://127.0.0.1:5010/check") is False


def test_is_healthy_swallows_http_errors(monkeypatch):
    """네트워크 예외를 밖으로 내지 않는다(기동 판정이 예외로 중단되면 안 된다)."""
    from src.launcher import is_healthy

    def boom(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", boom)
    assert is_healthy("http://127.0.0.1:5010/check") is False


def test_is_alive_distinguishes_live_and_dead_pid():
    from src.launcher import is_alive

    assert is_alive(os.getpid()) is True
    assert is_alive(999999) is False


def test_matches_command_uses_real_ps():
    """실제 ps 로 명령줄을 확인한다(가짜 주입 없이)."""
    from src.launcher import matches_command

    assert matches_command(os.getpid(), "pytest") is True      # 지금 이 프로세스의 명령줄
    assert matches_command(os.getpid(), "존재하지않는진입점_xyz.py") is False
    assert matches_command(999999, "main.py") is False         # 없는 pid 는 항상 False


def test_matches_command_rejects_pid_zero():
    """pid 0 은 프로세스 그룹 전체를 뜻한다. 절대 '우리 것'으로 판정되면 안 된다."""
    from src.launcher import matches_command

    assert matches_command(0, "main.py") is False
    assert matches_command(0, "web.py") is False


def test_stop_with_pid_zero_sends_no_signal(tmp_path):
    """pid 파일이 0 이어도 시그널을 보내지 않는다(그룹 전체 종료 방지). 실제 matches_command 사용."""
    service = _service(tmp_path)
    service.pid_path.parent.mkdir(parents=True)
    service.pid_path.write_text("0", encoding="utf-8")
    killer = KillSpy()

    result = stop(service, marker="main.py", healthy=HealthScript(True), killer=killer,
                  sleep=_noop_sleep, wait=0)

    assert result.state == "not_ours"
    assert killer.calls == []
    assert not service.pid_path.exists()


def test_stop_uses_real_ps_to_reject_foreign_pid(tmp_path):
    """살아 있는 남의 PID 여도 명령줄이 다르면 시그널을 보내지 않는다(가짜 matches_command 없이)."""
    service = _service(tmp_path)
    service.pid_path.parent.mkdir(parents=True)
    service.pid_path.write_text(str(os.getpid()), encoding="utf-8")   # pytest 프로세스 = 남의 프로세스
    killer = KillSpy()

    result = stop(service, marker="정말없는진입점_abc.py", healthy=HealthScript(True), killer=killer,
                  sleep=_noop_sleep, wait=0)

    assert result.state == "not_ours"
    assert killer.calls == []                      # pytest 자신에게도 시그널을 보내지 않았다
    assert not service.pid_path.exists()


# --- (Validator 추가) 남의 프로세스를 찾아 죽이는 경로가 없음 ---


def _launcher_sources() -> str:
    root = Path(run.PROJECT_ROOT)
    return ((root / "src" / "launcher.py").read_text(encoding="utf-8")
            + (root / "run.py").read_text(encoding="utf-8"))


@pytest.mark.parametrize("forbidden", ["SIGKILL", "SIGQUIT", "kill -9", "terminate()", ".kill()",
                                       "killpg", "pkill", "killall", "os.system"])
def test_no_forceful_or_broadcast_kill_path(forbidden):
    assert forbidden not in _launcher_sources()


@pytest.mark.parametrize("forbidden", ["lsof", "pgrep", "fuser"])
def test_no_port_or_name_based_process_discovery(forbidden):
    """종료 대상은 PID 파일로만 고른다. 포트·이름 검색으로 남의 프로세스를 찾지 않는다."""
    assert forbidden not in _launcher_sources()


# --- (Validator 추가) Ollama 는 확인만 한다 ---


def test_spawn_process_body_never_mentions_ollama():
    """기동 대상은 RAG·LangGraph 뿐이다."""
    source = (Path(run.PROJECT_ROOT) / "src" / "launcher.py").read_text(encoding="utf-8")
    body = source.split("def spawn_process")[1].split("\ndef ")[0]
    assert "Popen" in body                                   # 여기가 유일한 기동 지점
    assert "ollama" not in body.lower()


def test_check_ollama_never_launches_a_process(write_config, monkeypatch):
    """Ollama 확인 경로에서 어떤 프로세스도 띄우지 않는다."""
    import subprocess

    from src.config.settings import load_settings

    settings = load_settings(write_config())

    def forbidden(*args, **kwargs):
        raise AssertionError("Ollama 확인 중 프로세스를 띄우면 안 된다")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(run.httpx, "get",
                        lambda url, timeout: httpx.Response(200, json={"models": [{"name": "qwen3:14b"}]},
                                                            request=httpx.Request("GET", url)))
    out = []
    run.check_ollama(settings, out=out.append)

    assert "ok (qwen3:14b)" in out[0]


def test_only_rag_and_web_entrypoints_are_spawnable(entry, write_config):
    """build_services 가 만드는 명령은 main.py / web.py 뿐이다(ollama 기동 명령 없음)."""
    from src.config.settings import load_settings

    settings = load_settings(write_config())
    rag, web = run.build_services(settings, entry, "local")

    for service in (rag, web):
        joined = " ".join(service.command).lower()
        assert "ollama" not in joined
        assert "11434" not in service.health_url
    assert rag.command[1] == "main.py" and web.command[1] == "web.py"


# --- (Validator 추가) 경계값 ---


def test_start_timeout_zero_still_checks_once(tmp_path):
    """timeout 0 이어도 기동 직후 한 번은 확인한다(경계값)."""
    service = _service(tmp_path)
    spawn = SpawnSpy(pid=777)

    result = start(service, timeout=0, healthy=HealthScript(False, True), spawn=spawn,
                   sleep=_noop_sleep)

    assert result.state == "started"
    assert spawn.calls == ["RAG"]


def test_status_cleans_stale_pid_file(tmp_path):
    """살아 있지만 PID 파일이 낡았으면 '남의 것' 으로 보고 파일을 정리한다."""
    service = _service(tmp_path)
    service.pid_path.parent.mkdir(parents=True)
    service.pid_path.write_text("999999", encoding="utf-8")

    result = status(service, healthy=HealthScript(True))

    assert result.state == "already"
    assert "건드리지 않습니다" in result.message
    assert not service.pid_path.exists()


def test_stop_does_not_signal_when_pid_file_missing_for_both_services(entry, monkeypatch, tmp_path):
    """진입점 경로 전체에서도 PID 파일이 없으면 시그널이 나가지 않는다(두 서비스 모두)."""
    from src.launcher import stop as real_stop

    monkeypatch.setattr(run, "LOG_DIR", tmp_path / "logs")     # 실제 logs/*.pid 와 격리
    killer = KillSpy()
    monkeypatch.setattr(run, "stop",
                        lambda service, marker: real_stop(service, marker,
                                                          healthy=HealthScript(True),
                                                          killer=killer, sleep=_noop_sleep, wait=0))

    code, out = _main(["--stop", "--rag-dir", str(entry)])

    assert code == 0
    assert killer.calls == []                                  # 두 서비스 모두 시그널 0회
    assert len(out) == 2
    assert all("건드리지 않습니다" in line for line in out)

"""RAG + LangGraph 웹 서버 기동 스크립트.

python run.py                 # 기동(이미 떠 있으면 건너뜀)
python run.py --status        # 상태만 출력
python run.py --stop          # 이 스크립트가 띄운 것만 종료
"""
import argparse
import sys
from pathlib import Path

import httpx

from src.config.settings import PROJECT_ROOT, config_path_for, load_settings
from src.launcher import Service, start, status, stop

LOG_DIR = PROJECT_ROOT / "logs"
RAG_MARKER = "main.py"
WEB_MARKER = "web.py"
LABEL_WIDTH = 12


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RAG + LangGraph 웹 서버 기동(중복 실행 방지)")
    parser.add_argument("--active-profile", default="local", help="두 서비스에 함께 넘길 프로파일 이름")
    parser.add_argument("--rag-dir", default=str(PROJECT_ROOT.parent / "RAG"), help="RAG 저장소 경로")
    parser.add_argument("--timeout", type=float, default=90, help="기동 후 /check 200 을 기다리는 최대 초")
    parser.add_argument("--status", action="store_true", help="상태만 출력한다")
    parser.add_argument("--stop", action="store_true", help="이 스크립트가 띄운 서비스만 종료한다")
    return parser


def build_services(settings, rag_dir: Path, profile: str) -> tuple[Service, Service]:
    """기동 순서대로 (RAG, LangGraph). LangGraph 가 뜨자마자 /check 로 RAG 를 본다."""
    rag = Service(
        name="RAG",
        health_url=f"{settings.rag_base_url.rstrip('/')}/check",
        url=settings.rag_base_url,
        cwd=rag_dir,
        command=[str(rag_dir / ".venv" / "bin" / "python"), "main.py", f"--active-profile={profile}"],
        log_path=LOG_DIR / "rag.log",
        pid_path=LOG_DIR / "rag.pid",
    )
    web_url = f"http://{settings.host}:{settings.port}"
    web = Service(
        name="LangGraph",
        health_url=f"{web_url}/check",
        url=web_url,
        cwd=PROJECT_ROOT,
        command=[sys.executable, "web.py", f"--active-profile={profile}"],
        log_path=LOG_DIR / "web.log",
        pid_path=LOG_DIR / "web.pid",
    )
    return rag, web


def check_ollama(settings, out=print) -> None:
    """확인만 한다. 시스템 데몬으로 떠 있는 경우가 많아 기동·종료하지 않는다."""
    label = "Ollama".ljust(LABEL_WIDTH)
    try:
        response = httpx.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags", timeout=3)
        response.raise_for_status()
        installed = {m.get("name", "") for m in response.json().get("models", [])}
    except (httpx.HTTPError, ValueError):
        out(f"{label}: 응답 없음 — `brew services start ollama` 또는 `ollama serve` 로 먼저 띄우세요")
        return
    if settings.llm_model in installed:
        out(f"{label}: ok ({settings.llm_model})")
    else:
        out(f"{label}: 모델 없음 — `ollama pull {settings.llm_model}` 이 필요합니다")


def do_start(services, timeout: float, out=print) -> int:
    failed = False
    for service in services:
        result = start(service, timeout)
        out(f"{service.name.ljust(LABEL_WIDTH)}: {result.message}")
        if result.state == "failed":
            failed = True          # 이미 성공한 서비스는 그대로 둔다(자동 롤백 없음)
    return 1 if failed else 0


def do_status(services, out=print) -> int:
    for service in services:
        out(f"{service.name.ljust(LABEL_WIDTH)}: {status(service).message}")
    return 0


def do_stop(services, out=print) -> int:
    # 기동의 역순으로 내린다
    for service, marker in reversed(list(zip(services, (RAG_MARKER, WEB_MARKER)))):
        out(f"{service.name.ljust(LABEL_WIDTH)}: {stop(service, marker).message}")
    return 0


def main(argv=None, out=print) -> int:
    args = build_arg_parser().parse_args(argv)
    if args.status and args.stop:
        out("--status 와 --stop 은 함께 쓸 수 없습니다.")
        return 2

    settings = load_settings(config_path_for(args.active_profile))
    rag_dir = Path(args.rag_dir).expanduser()
    if not rag_dir.is_dir():
        out(f"RAG 저장소를 찾을 수 없습니다: {rag_dir} (--rag-dir 로 경로를 지정하세요)")
        return 1

    services = build_services(settings, rag_dir.resolve(), args.active_profile)

    if args.status:
        return do_status(services, out=out)
    if args.stop:
        return do_stop(services, out=out)

    rag_python = Path(services[0].command[0])
    if not rag_python.exists():
        out(f"RAG 가상환경 파이썬이 없습니다: {rag_python} (RAG 저장소에서 venv 를 만들어 주세요)")
        return 1

    check_ollama(settings, out=out)
    code = do_start(services, args.timeout, out=out)
    out("")
    out(f"브라우저: {services[1].url}")
    out("종료: python run.py --stop  (이 스크립트가 띄운 것만 내려갑니다)")
    return code


if __name__ == "__main__":
    sys.exit(main())

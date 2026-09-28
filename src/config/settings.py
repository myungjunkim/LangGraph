"""에이전트 설정 로딩. 표준 configparser 만 사용한다."""
import configparser
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    rag_base_url: str
    rag_search_path: str
    rag_timeout: float
    rag_top_k: int
    ollama_base_url: str
    llm_model: str
    num_ctx: int
    temperature: float
    llm_timeout: float
    recursion_limit: int
    host: str
    port: int
    checkpoint_db: Path


def config_path_for(profile: str) -> Path:
    return PROJECT_ROOT / "resources" / f"config_{profile}.ini"


def _resolve_path(raw: str) -> Path:
    """상대 경로는 실행 디렉터리와 무관하게 프로젝트 루트 기준으로 해석한다."""
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def load_settings(path: str | Path) -> Settings:
    """ini 파일을 읽어 Settings 를 만든다.

    파일이 없으면 FileNotFoundError, 키가 없으면 KeyError 를 그대로 전파한다(설정 실수를 숨기지 않는다).
    """
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {path}")

    parser = configparser.ConfigParser()
    parser.read(path, encoding="utf-8")

    rag = parser["rag"]
    ollama = parser["ollama"]
    agent = parser["agent"]
    fastapi = parser["fastapi"]
    checkpoint = parser["checkpoint"]
    return Settings(
        rag_base_url=rag["base-url"],
        rag_search_path=rag["search-path"],
        rag_timeout=float(rag["timeout"]),
        rag_top_k=int(rag["top-k"]),
        ollama_base_url=ollama["base-url"],
        llm_model=ollama["llm-model"],
        num_ctx=int(ollama["num-ctx"]),
        temperature=float(ollama["temperature"]),
        llm_timeout=float(ollama["llm-timeout"]),
        recursion_limit=int(agent["recursion-limit"]),
        host=fastapi["host"],
        port=int(fastapi["port"]),
        checkpoint_db=_resolve_path(checkpoint["db-path"]),
    )

"""KUDOS RAG 검색 에이전트 웹 서버.

python web.py --active-profile=local
"""
import argparse

import uvicorn

from src.agent import build_default_graph
from src.config.settings import config_path_for, load_settings
from src.web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="KUDOS RAG 검색 에이전트 웹 서버")
    parser.add_argument("--active-profile", default="local", help="resources/config_{profile}.ini 프로파일 이름")
    args = parser.parse_args()

    settings = load_settings(config_path_for(args.active_profile))
    graph = build_default_graph(settings)
    uvicorn.run(create_app(graph, settings), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()

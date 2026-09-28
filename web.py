"""KUDOS RAG 검색 에이전트 웹 서버.

python web.py --active-profile=local
"""
import argparse
import asyncio

import uvicorn

from src.agent import build_default_graph
from src.checkpointer import async_sqlite_saver
from src.config.settings import config_path_for, load_settings
from src.web.app import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description="KUDOS RAG 검색 에이전트 웹 서버")
    parser.add_argument("--active-profile", default="local", help="resources/config_{profile}.ini 프로파일 이름")
    args = parser.parse_args()

    settings = load_settings(config_path_for(args.active_profile))

    async def serve() -> None:
        # 웹은 graph.astream/aget_state 를 쓰므로 비동기 saver 가 필요하다(동기 SqliteSaver 는 NotImplementedError)
        async with async_sqlite_saver(settings.checkpoint_db) as checkpointer:
            app = create_app(build_default_graph(settings, checkpointer), settings)
            config = uvicorn.Config(app, host=settings.host, port=settings.port)
            await uvicorn.Server(config).serve()

    asyncio.run(serve())


if __name__ == "__main__":
    main()

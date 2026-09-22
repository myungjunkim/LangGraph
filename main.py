"""KUDOS RAG 검색 에이전트 CLI.

python main.py --active-profile=local
"""
import argparse
import uuid

import httpx
import ollama
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError

from src.agent import build_graph
from src.config.settings import config_path_for, load_settings
from src.llm_factory import create_chat_model
from src.rag_client import RagClient
from src.tools import build_tools

EXIT_COMMANDS = ("exit", "quit")
RUNTIME_ERRORS = (httpx.HTTPError, ollama.ResponseError, ConnectionError, GraphRecursionError)


def run_turn(graph, config: dict, text: str, out=print) -> None:
    """한 턴을 실행하며 도구 호출과 최종 답변을 출력한다."""
    for state in graph.stream({"messages": [HumanMessage(text)]}, config=config, stream_mode="values"):
        message = state["messages"][-1]
        if not isinstance(message, AIMessage):
            continue
        if message.tool_calls:
            for call in message.tool_calls:
                out(f"[검색] {call['name']}({call['args'].get('query', '')})")
        else:
            out(message.content)


def warn_if_rag_down(base_url: str, timeout: float, out=print) -> None:
    try:
        httpx.get(f"{base_url.rstrip('/')}/check", timeout=timeout).raise_for_status()
    except httpx.HTTPError as e:
        out(f"[경고] RAG 서버({base_url}) 상태 확인에 실패했습니다. 검색 도구가 동작하지 않을 수 있습니다. ({e})")


def build_repl_graph(settings):
    client = RagClient(settings.rag_base_url, settings.rag_search_path, settings.rag_timeout)
    tools = build_tools(client, settings.rag_top_k)
    return build_graph(create_chat_model(settings), tools, InMemorySaver())


def main() -> None:
    parser = argparse.ArgumentParser(description="KUDOS RAG 검색 에이전트 CLI")
    parser.add_argument("--active-profile", default="local", help="resources/config_{profile}.ini 프로파일 이름")
    args = parser.parse_args()

    settings = load_settings(config_path_for(args.active_profile))
    graph = build_repl_graph(settings)
    config = {"configurable": {"thread_id": str(uuid.uuid4())}, "recursion_limit": settings.recursion_limit}

    warn_if_rag_down(settings.rag_base_url, settings.rag_timeout)
    print("질문을 입력하세요. 종료: exit / quit / Ctrl-D")
    while True:
        try:
            text = input("> ").strip()
        except EOFError:
            print()
            break
        if not text:
            continue
        if text.lower() in EXIT_COMMANDS:
            break
        try:
            run_turn(graph, config, text)
        except RUNTIME_ERRORS as e:
            print(f"[오류] {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()

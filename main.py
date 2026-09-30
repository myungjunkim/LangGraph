"""KUDOS RAG 검색 에이전트 CLI.

python main.py --active-profile=local
"""
import argparse
import uuid

import httpx
from langchain_core.messages import AIMessage, HumanMessage

from src.agent import API_TOOL_NAME, RUNTIME_ERRORS, build_default_graph, tool_call_summary
from src.checkpointer import sqlite_saver
from src.config.settings import Settings, config_path_for, load_settings

EXIT_COMMANDS = ("exit", "quit")


def run_turn(graph, config: dict, text: str, out=print) -> None:
    """한 턴을 실행하며 도구 호출을 실시간으로, 최종 답변은 끝에 한 번만 출력한다.

    강제 검색(agent-16) 직전의 임시 답변까지 출력하면 답변이 두 번 보이므로 마지막 것만 낸다.
    """
    answer = None
    for state in graph.stream({"messages": [HumanMessage(text)]}, config=config, stream_mode="values"):
        message = state["messages"][-1]
        if not isinstance(message, AIMessage):
            continue
        if message.tool_calls:
            for call in message.tool_calls:
                label = "[호출]" if call["name"] == API_TOOL_NAME else "[검색]"
                out(f"{label} {call['name']}({tool_call_summary(call['args'])})")
        else:
            answer = message.content
    if answer is not None:
        out(answer)


def warn_if_rag_down(base_url: str, timeout: float, out=print) -> None:
    try:
        httpx.get(f"{base_url.rstrip('/')}/check", timeout=timeout).raise_for_status()
    except httpx.HTTPError as e:
        out(f"[경고] RAG 서버({base_url}) 상태 확인에 실패했습니다. 검색 도구가 동작하지 않을 수 있습니다. ({e})")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="KUDOS RAG 검색 에이전트 CLI")
    parser.add_argument("--active-profile", default="local", help="resources/config_{profile}.ini 프로파일 이름")
    parser.add_argument("--thread", default=None, help="이어서 할 대화 ID(생략하면 새 대화)")
    return parser


def turn_config(thread_id: str | None, settings: Settings) -> dict:
    """대화 ID 를 주지 않으면 새 대화를 시작한다."""
    return {"configurable": {"thread_id": thread_id or str(uuid.uuid4())},
            "recursion_limit": settings.recursion_limit}


def thread_notice(thread_id: str) -> str:
    return f"대화 ID: {thread_id} (이어서 하려면 --thread {thread_id})"


def repl(graph, config: dict) -> None:
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


def main() -> None:
    args = build_arg_parser().parse_args()

    settings = load_settings(config_path_for(args.active_profile))
    config = turn_config(args.thread, settings)

    warn_if_rag_down(settings.rag_base_url, settings.rag_timeout)
    print(thread_notice(config["configurable"]["thread_id"]))
    # 연결 수명은 이 with 블록이 관리한다(프로세스 종료까지 열려 있음)
    with sqlite_saver(settings.checkpoint_db) as checkpointer:
        repl(build_default_graph(settings, checkpointer), config)


if __name__ == "__main__":
    main()

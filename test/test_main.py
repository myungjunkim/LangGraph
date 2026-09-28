import httpx
from langchain_core.messages import AIMessage, HumanMessage

import main
from test.test_agent import _graph, _tool_call_message


def _collect(graph, text="메시지 등록 API 호출 방법 알려줘"):
    lines = []
    main.run_turn(graph, {"configurable": {"thread_id": "t1"}, "recursion_limit": 12}, text, out=lines.append)
    return lines


def test_run_turn_prints_search_line_and_final_answer():
    graph, _ = _graph([_tool_call_message(query="메시지 등록"), AIMessage("POST /v1/messages 입니다.\n출처: 메시지 등록 https://x/2")])

    assert _collect(graph) == [
        "[검색] search_openapi(메시지 등록)",
        "POST /v1/messages 입니다.\n출처: 메시지 등록 https://x/2",
    ]


def test_run_turn_without_tool_call_prints_answer_only():
    graph, _ = _graph([AIMessage("안녕하세요.")])
    assert _collect(graph, "안녕") == ["안녕하세요."]


def test_run_turn_prints_every_tool_call():
    parallel = AIMessage(content="", tool_calls=[
        {"name": "search_confluence", "args": {"query": "정책"}, "id": "c1"},
        {"name": "search_openapi", "args": {"query": "엔드포인트"}, "id": "c2"},
    ])
    graph, _ = _graph([parallel, AIMessage("정리했습니다.")])

    assert _collect(graph) == [
        "[검색] search_confluence(정책)",
        "[검색] search_openapi(엔드포인트)",
        "정리했습니다.",
    ]


def test_run_turn_passes_human_message_to_graph():
    graph, model = _graph([AIMessage("답")])
    _collect(graph, "질문 원문")

    human = [m for m in model.received[0] if isinstance(m, HumanMessage)]
    assert [m.content for m in human] == ["질문 원문"]


def test_warn_if_rag_down_is_silent_when_healthy(monkeypatch):
    monkeypatch.setattr(main.httpx, "get", lambda url, timeout: httpx.Response(200, request=httpx.Request("GET", url)))
    lines = []
    main.warn_if_rag_down("http://rag.test", 3, out=lines.append)
    assert lines == []


def test_warn_if_rag_down_warns_on_connect_error(monkeypatch):
    def boom(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(main.httpx, "get", boom)
    lines = []
    main.warn_if_rag_down("http://rag.test", 3, out=lines.append)
    assert len(lines) == 1 and lines[0].startswith("[경고] RAG 서버(http://rag.test)")


def test_warn_if_rag_down_warns_on_error_status(monkeypatch):
    monkeypatch.setattr(main.httpx, "get",
                        lambda url, timeout: httpx.Response(503, request=httpx.Request("GET", url)))
    lines = []
    main.warn_if_rag_down("http://rag.test/", 3, out=lines.append)
    assert len(lines) == 1 and lines[0].startswith("[경고]")


# --- agent-08 보강 (Validator): 후속 턴에서도 CLI 출력이 그대로다 ---

def test_run_turn_output_is_unchanged_on_followup_turn():
    """rewrite 노드가 붙어도 후속 턴 CLI 출력은 검색 줄과 최종 답변뿐이다."""
    from langgraph.checkpoint.memory import InMemorySaver

    rewritten = "메시지 등록 API의 v1과 v2 차이"
    graph, _ = _graph([
        AIMessage("POST /v1/messages 입니다."),      # 1턴 agent
        AIMessage(rewritten),                        # 2턴 rewrite
        _tool_call_message(query=rewritten),         # 2턴 agent (도구 호출)
        AIMessage("v1 과 v2 차이는 …"),               # 2턴 agent (최종 답변)
    ], checkpointer=InMemorySaver())

    assert _collect(graph, "메시지 등록 API 알려줘") == ["POST /v1/messages 입니다."]
    assert _collect(graph, "방금 알려준 API의 v1이랑 v2 차이는?") == [
        f"[검색] search_openapi({rewritten})",
        "v1 과 v2 차이는 …",
    ]


# --- agent-09: 대화 이어하기(--thread) ---

def test_thread_option_is_used_as_thread_id(write_config):
    from src.config.settings import load_settings

    settings = load_settings(write_config())
    args = main.build_arg_parser().parse_args(["--thread", "abc"])

    config = main.turn_config(args.thread, settings)

    assert config["configurable"]["thread_id"] == "abc"
    assert config["recursion_limit"] == settings.recursion_limit


def test_thread_option_defaults_to_new_uuid(write_config):
    import uuid

    from src.config.settings import load_settings

    settings = load_settings(write_config())
    args = main.build_arg_parser().parse_args([])
    assert args.thread is None

    first = main.turn_config(args.thread, settings)["configurable"]["thread_id"]
    second = main.turn_config(args.thread, settings)["configurable"]["thread_id"]

    uuid.UUID(first)          # uuid 형식이 아니면 ValueError
    assert first != second    # 매번 새 대화


def test_thread_notice_tells_how_to_resume():
    assert main.thread_notice("abc-123") == "대화 ID: abc-123 (이어서 하려면 --thread abc-123)"


def test_active_profile_option_is_unchanged():
    """기존 CLI 인자는 그대로다(--thread 추가가 기존 사용법을 바꾸지 않는다)."""
    parser = main.build_arg_parser()
    assert parser.parse_args([]).active_profile == "local"
    assert parser.parse_args(["--active-profile=dev"]).active_profile == "dev"


def test_main_opens_sqlite_saver_at_configured_path_and_injects_it(monkeypatch, write_config, tmp_path):
    """CLI 진입점이 설정의 checkpoint_db 로 saver 를 열어 그래프에 넣는지 (RAG·LLM 없이)."""
    import sys
    from contextlib import contextmanager

    from test.conftest import CONFIG_TEXT

    db = tmp_path / "cli" / "checkpoints.sqlite"
    config_path = write_config(CONFIG_TEXT.replace("db-path=data/checkpoints.sqlite", f"db-path={db}"))
    monkeypatch.setattr(main, "config_path_for", lambda profile: config_path)
    monkeypatch.setattr(main, "warn_if_rag_down", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["main.py", "--thread", "cli-1"])

    opened = {}

    @contextmanager
    def fake_saver(path):
        opened["path"] = path
        yield "SAVER"

    monkeypatch.setattr(main, "sqlite_saver", fake_saver)
    monkeypatch.setattr(main, "build_default_graph", lambda settings, checkpointer=None: ("GRAPH", checkpointer))

    printed = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: printed.append(" ".join(str(x) for x in a)))
    monkeypatch.setattr(main, "repl", lambda graph, config: opened.update(graph=graph, config=config))

    main.main()

    assert opened["path"] == db
    assert opened["graph"] == ("GRAPH", "SAVER")
    assert opened["config"]["configurable"]["thread_id"] == "cli-1"
    assert printed == ["대화 ID: cli-1 (이어서 하려면 --thread cli-1)"]

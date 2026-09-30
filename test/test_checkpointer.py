"""SQLite 체크포인터: 프로세스 재시작 후 대화가 이어지는지 (네트워크 없이 가짜 모델로 확인)."""
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from src.agent import build_default_graph, build_graph
from src.checkpointer import sqlite_saver
from src.config.settings import load_settings
from src.tools import build_tools
from test.conftest import FakeClient
from test.test_agent import ScriptedChatModel

CONFIG = {"configurable": {"thread_id": "t1"}}


class _AnyText:
    """실행 시점마다 달라지는 값(현재 시각 등)을 비교에서 흡수하는 자리표시자."""

    def __eq__(self, other):
        return isinstance(other, str)

    def __repr__(self):
        return "<any text>"


ANY_TEXT = _AnyText()


def _turn(question, answer):
    """agent-16 이후 한 턴이 남기는 메시지 내용.

    강제 검색 직전의 임시 답변은 상태에서 지워지므로
    [질문, 합성 tool_calls(빈 content), 시스템 정보, 검색 결과×2, 최종 답변] 만 남는다.
    """
    from src.tools import NO_RESULT_TEXT

    return [question, "", ANY_TEXT, NO_RESULT_TEXT, NO_RESULT_TEXT, answer]


def _graph_with(saver, responses):
    model = ScriptedChatModel(responses=responses, received=[])
    return build_graph(model, build_tools(FakeClient([]), 6), saver), model


# --- S2. 재시작 후 대화 유지(동기) ---

def test_conversation_survives_new_saver_instance(tmp_path):
    db = tmp_path / "sub" / "checkpoints.sqlite"

    with sqlite_saver(db) as saver:                       # 1번째 프로세스
        graph, _ = _graph_with(saver, [AIMessage("첫 임시 답변"), AIMessage("첫 답변")])
        graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=CONFIG)

    with sqlite_saver(db) as saver:                       # 2번째 프로세스(새 인스턴스, 같은 파일)
        graph, model = _graph_with(saver, [AIMessage("독립 질문"), AIMessage("둘째 임시 답변"),
                                           AIMessage("두 번째 답변")])
        state = graph.get_state(CONFIG)
        assert [m.content for m in state.values["messages"]] == _turn("첫 질문", "첫 답변")

        state = graph.invoke({"messages": [HumanMessage("두 번째 질문")]}, config=CONFIG)

    assert [m.content for m in state["messages"]] == (
        _turn("첫 질문", "첫 답변") + _turn("두 번째 질문", "두 번째 답변"))
    # 2턴째 agent 호출(rewrite 다음)에 1턴 대화가 함께 전달됐다.
    # 마지막 질문은 독립 질문으로 치환된다(agent-16 F1) — 상태에는 원문이 남는다
    assert [m.content for m in model.received[1]][1:] == (
        _turn("첫 질문", "첫 답변") + ["독립 질문"])


def test_saver_creates_parent_directory(tmp_path):
    db = tmp_path / "없던폴더" / "db.sqlite"
    with sqlite_saver(db):
        pass
    assert db.parent.is_dir() and db.is_file()


def test_threads_are_isolated_in_one_file(tmp_path):
    db = tmp_path / "checkpoints.sqlite"
    with sqlite_saver(db) as saver:
        graph, _ = _graph_with(saver, [AIMessage("임시"), AIMessage("답")])
        graph.invoke({"messages": [HumanMessage("A 대화")]}, config={"configurable": {"thread_id": "a"}})
        graph.invoke({"messages": [HumanMessage("B 대화")]}, config={"configurable": {"thread_id": "b"}})

    with sqlite_saver(db) as saver:
        graph, _ = _graph_with(saver, [AIMessage("임시"), AIMessage("답")])
        a = graph.get_state({"configurable": {"thread_id": "a"}}).values["messages"]
        b = graph.get_state({"configurable": {"thread_id": "b"}}).values["messages"]
    assert [m.content for m in a] == _turn("A 대화", "답")
    assert [m.content for m in b] == _turn("B 대화", "답")


def test_unknown_thread_has_empty_state(tmp_path):
    with sqlite_saver(tmp_path / "db.sqlite") as saver:
        graph, _ = _graph_with(saver, [AIMessage("답")])
        assert graph.get_state({"configurable": {"thread_id": "없음"}}).values == {}


# --- S3. build_default_graph 기본값 보존 ---

def test_build_default_graph_uses_in_memory_saver_by_default(write_config):
    settings = load_settings(write_config())
    graph = build_default_graph(settings)
    assert isinstance(graph.checkpointer, InMemorySaver)


def test_build_default_graph_accepts_injected_saver(write_config, tmp_path):
    settings = load_settings(write_config())
    with sqlite_saver(tmp_path / "db.sqlite") as saver:
        graph = build_default_graph(settings, saver)
        assert graph.checkpointer is saver


def test_injected_saver_does_not_change_graph_shape(write_config, tmp_path):
    settings = load_settings(write_config())
    with sqlite_saver(tmp_path / "db.sqlite") as saver:
        drawable = build_default_graph(settings, saver).get_graph()
    edges = {(e.source, e.target) for e in drawable.edges}
    assert {"rewrite", "agent", "tools"} <= set(drawable.nodes)
    assert {("__start__", "rewrite"), ("rewrite", "agent"), ("agent", "tools"),
            ("tools", "agent"), ("agent", "__end__")} <= edges


# --- S5. 재시작 후 대화 유지(비동기, 웹 경로) ---

# AsyncSqliteSaver 의 연결은 만들어진 이벤트 루프에 묶이므로, 웹 진입점(web.py)과 같이
# 한 루프 안에서 앱을 돌린다. TestClient 는 별도 스레드·루프를 쓰므로 여기서는 ASGITransport 를 쓴다.

async def _web_turn(client, message, thread_id="web-1"):
    return await client.post("/v1/chat/stream", json={"thread_id": thread_id, "message": message})


def test_web_conversation_survives_app_restart(tmp_path, write_config):
    """AsyncSqliteSaver 로 astream 경로가 동작하고, 앱을 새로 만들어도 대화가 이어진다."""
    import asyncio
    from contextlib import asynccontextmanager

    import httpx

    from src.checkpointer import async_sqlite_saver
    from src.web.app import create_app

    settings = load_settings(write_config())
    db = tmp_path / "web.sqlite"

    @asynccontextmanager
    async def web_app(responses):
        async with async_sqlite_saver(db) as saver:
            graph, model = _graph_with(saver, responses)
            transport = httpx.ASGITransport(app=create_app(graph, settings))
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                yield client, model

    async def scenario():
        async with web_app([AIMessage("1턴 임시"), AIMessage("1턴 답변"),
                            AIMessage("독립"), AIMessage("2턴 임시"), AIMessage("2턴 답변")]) as (client, _):
            assert (await _web_turn(client, "첫 질문")).status_code == 200      # 1번째 서버 프로세스
            assert (await _web_turn(client, "둘째 질문")).status_code == 200

        async with web_app([AIMessage("독립"), AIMessage("3턴 임시"), AIMessage("3턴 답변")]) as (client, model):
            assert (await _web_turn(client, "셋째 질문")).status_code == 200    # 2번째 서버 프로세스
            history = (await client.get("/v1/threads/web-1/messages")).json()
        return model, history

    model, history = asyncio.run(scenario())

    # 3턴째 agent 호출에 이전 프로세스의 대화가 들어 있다
    assert [m.content for m in model.received[1]][1:] == (
        _turn("첫 질문", "1턴 답변") + _turn("둘째 질문", "2턴 답변") + ["독립"])   # F1 치환
    # agent-16 F1: 강제 검색 직전의 임시 답변은 지워지므로 턴마다 답변이 하나씩만 복원된다
    assert history == {"messages": [
        {"role": "user", "content": "첫 질문"},
        {"role": "assistant", "content": "1턴 답변"},
        {"role": "user", "content": "둘째 질문"},
        {"role": "assistant", "content": "2턴 답변"},
        {"role": "user", "content": "셋째 질문"},
        {"role": "assistant", "content": "3턴 답변"},
    ]}


def test_async_saver_creates_parent_directory(tmp_path):
    """비동기 saver 도 부모 디렉터리를 만든다(웹 첫 기동 시 data/ 가 없어도 떠야 한다)."""
    import asyncio

    from src.checkpointer import async_sqlite_saver

    db = tmp_path / "없던폴더" / "web.sqlite"

    async def open_and_close():
        async with async_sqlite_saver(db):
            pass

    asyncio.run(open_and_close())
    assert db.parent.is_dir() and db.is_file()


def test_sqlite_saver_reopens_existing_file(tmp_path):
    """이미 있는 파일·디렉터리를 다시 열어도 오류 없이 기존 대화를 그대로 본다."""
    db = tmp_path / "db.sqlite"
    with sqlite_saver(db) as saver:
        graph, _ = _graph_with(saver, [AIMessage("임시"), AIMessage("답")])
        graph.invoke({"messages": [HumanMessage("질문")]}, config=CONFIG)

    for _ in range(2):                                   # 세 번째 프로세스까지 재기동
        with sqlite_saver(db) as saver:
            graph, _ = _graph_with(saver, [AIMessage("임시"), AIMessage("답")])
            assert [m.content for m in graph.get_state(CONFIG).values["messages"]] == _turn("질문", "답")

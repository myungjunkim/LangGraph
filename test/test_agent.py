import httpx
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from src.agent import SYSTEM_PROMPT, build_graph
from src.tools import build_tools
from test.conftest import FakeClient


class ScriptedChatModel(FakeMessagesListChatModel):
    """bind_tools 가 자기 자신을 돌려주는 테스트 전용 모델. 받은 메시지를 기록한다."""

    received: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.received.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _tool_call_message(name="search_openapi", query="메시지 등록 API", call_id="call_1") -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": {"query": query}, "id": call_id}])


def _chunk(title="메시지 등록", url="https://x/2", source="openapi", content="POST /v1/messages"):
    return {"chunk_id": "2#0", "title": title, "url": url, "source": source, "content": content, "metadata": {}}


def _graph(responses, client=None, checkpointer=None):
    model = ScriptedChatModel(responses=responses, received=[])
    tools = build_tools(client or FakeClient([_chunk()]), 6)
    return build_graph(model, tools, checkpointer), model


def test_tool_call_then_final_answer():
    """(a) tool_call → 도구 실행 → 최종 답변까지 메시지 순서."""
    client = FakeClient([_chunk()])
    graph, model = _graph([_tool_call_message(), AIMessage("POST /v1/messages 로 호출합니다.\n출처: 메시지 등록 https://x/2")],
                          client=client)

    state = graph.invoke({"messages": [HumanMessage("메시지 등록 API 호출 방법 알려줘")]})

    kinds = [type(m).__name__ for m in state["messages"]]
    assert kinds == ["HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    assert state["messages"][1].tool_calls[0]["name"] == "search_openapi"
    assert state["messages"][2].name == "search_openapi"
    assert "POST /v1/messages" in state["messages"][2].content
    assert state["messages"][-1].content.startswith("POST /v1/messages 로 호출합니다.")
    assert client.calls == [("메시지 등록 API", "openapi", 6)]


def test_system_prompt_is_prepended_but_not_stored():
    graph, model = _graph([AIMessage("안녕하세요.")])

    state = graph.invoke({"messages": [HumanMessage("안녕")]})

    first_call = model.received[0]
    assert isinstance(first_call[0], SystemMessage)
    assert first_call[0].content == SYSTEM_PROMPT
    assert not any(isinstance(m, SystemMessage) for m in state["messages"])


def test_answer_without_tool_calls_goes_straight_to_end():
    """(b) tool_calls 없는 첫 응답은 바로 END."""
    client = FakeClient([_chunk()])
    graph, model = _graph([AIMessage("안녕하세요. 무엇을 도와드릴까요?")], client=client)

    state = graph.invoke({"messages": [HumanMessage("안녕")]})

    assert [type(m).__name__ for m in state["messages"]] == ["HumanMessage", "AIMessage"]
    assert client.calls == []
    assert len(model.received) == 1


def test_tool_error_becomes_error_tool_message_and_graph_continues():
    """(c) 도구가 예외를 던지면 ToolMessage(status="error") 가 기록되고 그래프는 계속된다."""
    client = FakeClient(error=httpx.ConnectError("connection refused"))
    graph, _ = _graph([_tool_call_message(), AIMessage("검색에 실패했습니다.")], client=client)

    state = graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]})

    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 1
    assert tool_messages[0].status == "error"
    assert "ConnectError" in tool_messages[0].content or "refused" in tool_messages[0].content
    assert state["messages"][-1].content == "검색에 실패했습니다."


def test_checkpointer_keeps_previous_turn():
    """(d) InMemorySaver + 같은 thread_id 로 2턴 호출 시 이전 메시지가 유지된다."""
    graph, model = _graph([AIMessage("첫 답변"), AIMessage("두 번째 답변")], checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}}

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=config)
    state = graph.invoke({"messages": [HumanMessage("두 번째 질문")]}, config=config)

    contents = [m.content for m in state["messages"]]
    assert contents == ["첫 질문", "첫 답변", "두 번째 질문", "두 번째 답변"]
    # 두 번째 호출 때 모델이 이전 대화를 함께 받았다
    assert [m.content for m in model.received[1]][1:] == ["첫 질문", "첫 답변", "두 번째 질문"]


def test_different_thread_id_does_not_share_history():
    graph, _ = _graph([AIMessage("첫 답변"), AIMessage("두 번째 답변")], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config={"configurable": {"thread_id": "t1"}})
    state = graph.invoke({"messages": [HumanMessage("다른 스레드 질문")]}, config={"configurable": {"thread_id": "t2"}})

    assert [m.content for m in state["messages"]] == ["다른 스레드 질문", "두 번째 답변"]


def test_without_checkpointer_history_is_not_kept():
    """체크포인터가 없으면(기본값) 호출 간 대화가 이어지지 않는다."""
    graph, _ = _graph([AIMessage("첫 답변"), AIMessage("두 번째 답변")])

    graph.invoke({"messages": [HumanMessage("첫 질문")]})
    state = graph.invoke({"messages": [HumanMessage("두 번째 질문")]})

    assert [m.content for m in state["messages"]] == ["두 번째 질문", "두 번째 답변"]


def test_graph_nodes_and_edges_match_design():
    """START → agent → (tools_condition) → tools → agent, 그리고 agent → END."""
    graph, _ = _graph([AIMessage("답")])
    drawable = graph.get_graph()

    assert {"agent", "tools"} <= set(drawable.nodes)
    edges = {(e.source, e.target) for e in drawable.edges}
    assert ("__start__", "agent") in edges
    assert ("agent", "tools") in edges
    assert ("tools", "agent") in edges
    assert ("agent", "__end__") in edges


def test_system_prompt_contains_required_rules():
    for keyword in ["search_confluence", "search_openapi", "관련 내용을 문서에서 찾지 못했습니다.", "출처:", "한국어"]:
        assert keyword in SYSTEM_PROMPT


def test_llm_factory_builds_chat_model_from_settings(write_config):
    from src.config.settings import load_settings
    from src.llm_factory import create_chat_model

    model = create_chat_model(load_settings(write_config()))

    assert model.model == "qwen3:14b"
    assert model.base_url == "http://127.0.0.1:11434"
    assert model.temperature == 0
    assert model.num_ctx == 16384
    assert model.reasoning is False
    assert model.client_kwargs == {"timeout": 120.0}


# --- Validator 추가 검증: 병렬 tool_call, 다중 라운드 ---

def test_parallel_tool_calls_produce_two_tool_messages():
    client = FakeClient([_chunk()])
    parallel = AIMessage(content="", tool_calls=[
        {"name": "search_confluence", "args": {"query": "정책"}, "id": "c1"},
        {"name": "search_openapi", "args": {"query": "엔드포인트"}, "id": "c2"},
    ])
    graph, _ = _graph([parallel, AIMessage("정리했습니다.")], client=client)

    state = graph.invoke({"messages": [HumanMessage("둘 다 알려줘")]})

    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert [m.name for m in tool_messages] == ["search_confluence", "search_openapi"]
    assert client.calls == [("정책", "confluence", 6), ("엔드포인트", "openapi", 6)]
    assert state["messages"][-1].content == "정리했습니다."


def test_agent_can_search_twice_before_answering():
    """첫 검색이 부족하면 다시 검색하는 흐름(agent→tools→agent→tools→agent)이 가능하다."""
    client = FakeClient([_chunk()])
    graph, _ = _graph([
        _tool_call_message(query="첫 검색어", call_id="c1"),
        _tool_call_message(query="두번째 검색어", call_id="c2"),
        AIMessage("최종 답변\n출처: 메시지 등록 https://x/2"),
    ], client=client)

    state = graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]})

    assert [type(m).__name__ for m in state["messages"]] == [
        "HumanMessage", "AIMessage", "ToolMessage", "AIMessage", "ToolMessage", "AIMessage"]
    assert [c[0] for c in client.calls] == ["첫 검색어", "두번째 검색어"]


def test_tool_error_message_is_tied_to_tool_call_id():
    client = FakeClient(error=httpx.ConnectError("connection refused"))
    graph, _ = _graph([_tool_call_message(call_id="call_9"), AIMessage("실패했습니다.")], client=client)

    state = graph.invoke({"messages": [HumanMessage("q")]})

    tool_message = [m for m in state["messages"] if isinstance(m, ToolMessage)][0]
    assert tool_message.tool_call_id == "call_9"


def test_system_prompt_requires_standalone_followup_query():
    """후속 질문 검색어에 앞 대화 대상을 넣으라는 규칙이 프롬프트에 있어야 한다(agent-06)."""
    assert "독립 검색어" in SYSTEM_PROMPT

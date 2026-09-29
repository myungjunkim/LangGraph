from datetime import datetime, timedelta, timezone

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from src.agent import HINT_PREFIX, REWRITE_PROMPT, SYSTEM_PROMPT, build_graph, current_time_line
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
    assert first_call[0].content.startswith("현재 시각: ")      # agent-14: 시각이 맨 앞
    assert SYSTEM_PROMPT in first_call[0].content
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
    """(d) InMemorySaver + 같은 thread_id 로 2턴 호출 시 이전 메시지가 유지된다.

    2턴째에는 rewrite 노드가 모델 응답을 먼저 하나 소비한다(agent-08).
    """
    graph, model = _graph([AIMessage("첫 답변"), AIMessage("독립 질문"), AIMessage("두 번째 답변")],
                          checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}}

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=config)
    state = graph.invoke({"messages": [HumanMessage("두 번째 질문")]}, config=config)

    contents = [m.content for m in state["messages"]]
    assert contents == ["첫 질문", "첫 답변", "두 번째 질문", "두 번째 답변"]
    # 두 번째 턴의 agent 호출(received[2]) 에 이전 대화가 함께 전달됐다
    assert [m.content for m in model.received[2]][1:] == ["첫 질문", "첫 답변", "두 번째 질문"]


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
    """START → rewrite → agent → (tools_condition) → tools → agent, 그리고 agent → END."""
    graph, _ = _graph([AIMessage("답")])
    drawable = graph.get_graph()

    assert {"rewrite", "agent", "tools"} <= set(drawable.nodes)
    edges = {(e.source, e.target) for e in drawable.edges}
    assert ("__start__", "rewrite") in edges
    assert ("rewrite", "agent") in edges
    assert ("__start__", "agent") not in edges  # rewrite 를 건너뛰는 경로는 없다
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


# --- agent-08: 후속 질문 질의 리라이팅 노드 ---

def _thread(n="t1"):
    return {"configurable": {"thread_id": n}}


def test_first_turn_skips_rewrite():
    """R1. 첫 턴은 rewrite 가 LLM 을 호출하지 않고 힌트도 비어 있다."""
    graph, model = _graph([AIMessage("첫 답변")], checkpointer=InMemorySaver())

    state = graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread())

    assert len(model.received) == 1                        # agent 호출 1회뿐
    assert SYSTEM_PROMPT in model.received[0][0].content
    assert state["standalone_question"] == ""


def test_followup_turn_rewrites_and_passes_hint_to_agent():
    """R2. 후속 턴: rewrite 가 먼저 호출되고 그 결과가 agent SystemMessage 에 힌트로 붙는다."""
    client = FakeClient([_chunk()])
    graph, model = _graph([
        _tool_call_message(query="메시지 등록 API"),           # 1턴 agent (도구 호출)
        AIMessage("POST /v1/messages 입니다."),                # 1턴 agent (최종 답변)
        AIMessage("메시지 등록 API의 v1과 v2 차이"),            # 2턴 rewrite
        AIMessage("v1 은 …, v2 는 …"),                        # 2턴 agent
    ], client=client, checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread())
    state = graph.invoke({"messages": [HumanMessage("방금 알려준 API의 v1이랑 v2 차이는?")]}, config=_thread())

    rewrite_input = model.received[2]
    assert rewrite_input[0].content == REWRITE_PROMPT
    # ToolMessage 와 tool_call 전용 AIMessage 는 리라이팅 입력에서 제외된다
    assert [type(m).__name__ for m in rewrite_input] == [
        "SystemMessage", "HumanMessage", "AIMessage", "HumanMessage"]
    assert [m.content for m in rewrite_input[1:]] == [
        "메시지 등록 API 알려줘", "POST /v1/messages 입니다.", "방금 알려준 API의 v1이랑 v2 차이는?"]

    agent_system = model.received[3][0]
    assert SYSTEM_PROMPT in agent_system.content
    assert HINT_PREFIX + "메시지 등록 API의 v1과 v2 차이" in agent_system.content

    assert state["standalone_question"] == "메시지 등록 API의 v1과 v2 차이"
    # 리라이팅 결과와 SystemMessage 는 대화 기록에 저장되지 않는다
    assert [type(m).__name__ for m in state["messages"]] == [
        "HumanMessage", "AIMessage", "ToolMessage", "AIMessage", "HumanMessage", "AIMessage"]
    assert not any(isinstance(m, SystemMessage) for m in state["messages"])
    assert all("메시지 등록 API의 v1과 v2 차이" != m.content for m in state["messages"])


def test_empty_rewrite_leaves_no_hint_and_does_not_reuse_previous_turn():
    """R3. rewrite 가 빈 문자열이면 힌트를 붙이지 않고, 앞 턴의 힌트도 남지 않는다."""
    graph, model = _graph([
        AIMessage("1턴 답변"),          # 1턴 agent
        AIMessage("2턴 독립 질문"),      # 2턴 rewrite
        AIMessage("2턴 답변"),          # 2턴 agent
        AIMessage("   "),              # 3턴 rewrite — 공백만
        AIMessage("3턴 답변"),          # 3턴 agent
    ], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread())
    graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread())
    state = graph.invoke({"messages": [HumanMessage("셋째 질문")]}, config=_thread())

    assert HINT_PREFIX in model.received[2][0].content        # 2턴 agent 에는 힌트가 있었다
    assert state["standalone_question"] == ""
    assert HINT_PREFIX not in model.received[4][0].content    # 3턴 agent 에는 힌트 없음
    assert SYSTEM_PROMPT in model.received[4][0].content


# --- agent-08 보강 (Validator) ---

class _BoundModelProxy:
    """`bind_tools` 결과 대역. 호출을 원본 모델로 넘기면서 '도구 바인딩 경유' 를 기록한다."""

    def __init__(self, model):
        self._model = model

    def invoke(self, messages, **kwargs):
        self._model.log.append("bound")
        return self._model.invoke(messages, **kwargs)


class _BindTrackingModel(ScriptedChatModel):
    """`bind_tools` 가 self 가 아닌 프록시를 돌려주는 모델. 모델 호출 경로를 log 로 남긴다."""

    log: list = []

    def bind_tools(self, tools, **kwargs):
        return _BoundModelProxy(self)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.log.append("call")
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_rewrite_uses_the_model_without_tools_bound():
    """설계: rewrite 는 bind_tools 하지 않은 원본 모델로 호출한다(리라이팅에서 도구 호출이 나오면 안 된다)."""
    model = _BindTrackingModel(
        responses=[AIMessage("1턴 답변"), AIMessage("독립 질문"), AIMessage("2턴 답변")],
        received=[], log=[])
    graph = build_graph(model, build_tools(FakeClient([_chunk()]), 6), InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("bind"))
    graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("bind"))

    # 1턴 agent(bound) / 2턴 rewrite(원본 직접 호출) / 2턴 agent(bound)
    assert model.log == ["bound", "call", "call", "bound", "call"]
    assert model.received[1][0].content == REWRITE_PROMPT


def test_agent_receives_original_question_text_not_the_rewritten_one():
    """힌트는 SystemMessage 로만 전달되고, 모델이 받는 마지막 HumanMessage 는 원문 그대로다(F1 은 범위 제외)."""
    graph, model = _graph([
        AIMessage("1턴 답변"),
        AIMessage("메시지 등록 API의 v1과 v2 차이"),   # 2턴 rewrite
        AIMessage("2턴 답변"),
    ], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread("orig"))
    graph.invoke({"messages": [HumanMessage("방금 알려준 API의 v1이랑 v2 차이는?")]}, config=_thread("orig"))

    agent_input = model.received[2]
    assert agent_input[-1].content == "방금 알려준 API의 v1이랑 v2 차이는?"
    assert isinstance(agent_input[-1], HumanMessage)
    assert [m.content for m in agent_input if isinstance(m, HumanMessage)] == [
        "메시지 등록 API 알려줘", "방금 알려준 API의 v1이랑 v2 차이는?"]
    # 힌트 문장은 설계 문구 그대로 SystemMessage 에만 한 번 붙는다
    assert agent_input[0].content.count(HINT_PREFIX) == 1
    assert (HINT_PREFIX + "메시지 등록 API의 v1과 v2 차이"
            + "\n검색이 필요할 때 query 는 이 독립 표현을 기준으로 만듭니다."
            + " 앞 대화의 답변만으로 충분하면 검색하지 않아도 됩니다.") in agent_input[0].content


def test_exactly_empty_rewrite_response_adds_no_hint():
    """rewrite 응답이 빈 문자열이면 힌트 없이 기존 동작(SYSTEM_PROMPT 단독)을 유지한다."""
    graph, model = _graph([
        AIMessage("1턴 답변"),
        AIMessage(""),          # 2턴 rewrite — 빈 응답
        AIMessage("2턴 답변"),
    ], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("empty"))
    state = graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("empty"))

    assert state["standalone_question"] == ""
    assert SYSTEM_PROMPT in model.received[2][0].content
    # agent-14 로 `== SYSTEM_PROMPT` 가 `in` 으로 완화되면서 빠진 "힌트 없음" 보장을 되살린다(Validator)
    assert HINT_PREFIX not in model.received[2][0].content


def test_build_graph_public_signature_is_unchanged():
    """완료 기준: build_graph 시그니처 불변(chat_model, tools, checkpointer=None)."""
    import inspect

    params = inspect.signature(build_graph).parameters
    assert list(params) == ["chat_model", "tools", "checkpointer"]
    assert params["checkpointer"].default is None


def test_standalone_question_is_checkpointed_without_touching_messages():
    """체크포인터에는 힌트가 상태 필드로만 남고 messages 원문은 그대로다."""
    saver = InMemorySaver()
    graph, _ = _graph([
        AIMessage("1턴 답변"),
        AIMessage("메시지 등록 API의 v1과 v2 차이"),
        AIMessage("2턴 답변"),
    ], checkpointer=saver)

    graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread("ckpt"))
    graph.invoke({"messages": [HumanMessage("v1이랑 v2 차이는?")]}, config=_thread("ckpt"))

    values = graph.get_state(_thread("ckpt")).values
    assert values["standalone_question"] == "메시지 등록 API의 v1과 v2 차이"
    assert [m.content for m in values["messages"]] == [
        "메시지 등록 API 알려줘", "1턴 답변", "v1이랑 v2 차이는?", "2턴 답변"]


# --- agent-14: 현재 시각 주입 + 근거 없는 추측 억제 ---

KST = timezone(timedelta(hours=9), "KST")


# T1. 시각 문자열

def test_current_time_line_format():
    assert current_time_line(datetime(2026, 9, 29, 14, 23, tzinfo=KST)) == \
        "현재 시각: 2026-09-29 (화) 14:23 KST"


@pytest.mark.parametrize("day, weekday", [(28, "월"), (29, "화"), (30, "수"),
                                          (1, "화"), (2, "수"), (3, "목"), (4, "금"),
                                          (5, "토"), (6, "일")])
def test_current_time_line_maps_every_weekday(day, weekday):
    """요일은 로케일이 아니라 직접 매핑한다(%a 면 'Tue' 가 된다)."""
    line = current_time_line(datetime(2026, 9, day, 9, 0, tzinfo=KST))
    assert f"({weekday})" in line
    assert "Tue" not in line and "Mon" not in line


def test_current_time_line_uses_offset_when_zone_name_is_empty():
    anonymous = timezone(timedelta(hours=9))        # 이름 없는 타임존
    assert current_time_line(datetime(2026, 9, 29, 14, 23, tzinfo=anonymous)) == \
        "현재 시각: 2026-09-29 (화) 14:23 UTC+09:00"


def test_current_time_line_without_argument_uses_now():
    line = current_time_line()
    assert line.startswith("현재 시각: ")
    assert datetime.now().astimezone().strftime("%Y-%m-%d") in line


# T2. 주입 위치

def test_system_message_starts_with_time_then_prompt():
    graph, model = _graph([AIMessage("답")])

    graph.invoke({"messages": [HumanMessage("오늘 며칠이야?")]})

    content = model.received[0][0].content
    assert content.startswith("현재 시각: ")
    assert SYSTEM_PROMPT in content
    assert content.index("현재 시각: ") < content.index(SYSTEM_PROMPT)


def test_hint_stays_at_the_end_after_time_injection():
    """순서: 시각 → SYSTEM_PROMPT → 힌트(agent-08)."""
    graph, model = _graph([
        AIMessage("1턴 답변"),
        AIMessage("메시지 등록 API의 v1과 v2 차이"),      # 2턴 rewrite
        AIMessage("2턴 답변"),
    ], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread("order"))
    graph.invoke({"messages": [HumanMessage("v1 이랑 v2 차이는?")]}, config=_thread("order"))

    content = model.received[2][0].content
    assert content.index("현재 시각: ") < content.index(SYSTEM_PROMPT) < content.index(HINT_PREFIX)
    assert content.rstrip().endswith("앞 대화의 답변만으로 충분하면 검색하지 않아도 됩니다.")


# T3. 매 턴 갱신

def test_time_is_recomputed_every_turn(monkeypatch):
    import src.agent as agent_module

    values = iter(["현재 시각: 2026-09-29 (화) 09:00 KST", "현재 시각: 2026-09-30 (수) 10:00 KST"])
    monkeypatch.setattr(agent_module, "current_time_line", lambda: next(values))

    graph, model = _graph([AIMessage("1턴 답변"), AIMessage("독립 질문"), AIMessage("2턴 답변")],
                          checkpointer=InMemorySaver())
    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("clock"))
    graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("clock"))

    first_turn = model.received[0][0].content
    second_turn = model.received[2][0].content          # rewrite 다음의 agent 호출
    assert first_turn.startswith("현재 시각: 2026-09-29 (화) 09:00 KST")
    assert second_turn.startswith("현재 시각: 2026-09-30 (수) 10:00 KST")
    assert "2026-09-29" not in second_turn              # 앞 턴 시각이 남지 않는다


# T4. 상태 미오염

def test_time_is_not_stored_in_conversation_state():
    graph, _ = _graph([AIMessage("1턴 답변"), AIMessage("독립 질문"), AIMessage("2턴 답변")],
                      checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("clean"))
    state = graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("clean"))

    assert [type(m).__name__ for m in state["messages"]] == [
        "HumanMessage", "AIMessage", "HumanMessage", "AIMessage"]
    assert not any(isinstance(m, SystemMessage) for m in state["messages"])
    assert all("현재 시각:" not in str(m.content) for m in state["messages"])


# T5. 프롬프트

def test_prompt_rules_are_unchanged_by_time_injection():
    """agent-14 는 시각 주입만 남기고 프롬프트는 agent-13 상태 그대로 둔다(리드 판단 (6))."""
    assert "1. 답하기 전에 반드시 검색 도구로 근거를 찾습니다. 인사말이나 일반 상식 질문은 예외입니다." in SYSTEM_PROMPT
    assert "현재 시각" not in SYSTEM_PROMPT          # 시각은 프롬프트가 아니라 런타임 주입
    # 거절 경로는 기존 규칙 5 가 담당한다
    assert '5. 검색 결과에 없는 내용은 추측하지 않습니다. 근거를 찾지 못하면 "관련 내용을 문서에서 찾지 못했습니다." 라고 답합니다.' \
        in SYSTEM_PROMPT


def test_previous_rules_keep_their_wording():
    """agent-06 규칙 3·agent-13 규칙 4 는 번호만 이동하고 문구는 그대로다."""
    assert ("3. 후속 질문(\"그 API\", \"방금 알려준 것\")이면 검색어를 앞 대화에서 다룬 대상 이름"
            "(API 이름·기능명·경로)으로 시작해,") in SYSTEM_PROMPT
    assert "앞 대화 없이도 이해되는 독립 검색어로 만듭니다. 앞 대화에 나오지 않은 이름을 검색어에 넣지 않습니다." in SYSTEM_PROMPT
    assert "4. 사용자가 실제 호출·시험·응답 확인을 요청하면 call_api 로 GET 요청을 보냅니다." in SYSTEM_PROMPT
    assert "요청하지 않았는데 임의로 호출하지 않습니다." in SYSTEM_PROMPT


@pytest.mark.parametrize("number", range(1, 9))
def test_prompt_has_eight_numbered_rules(number):
    assert f"\n{number}. " in SYSTEM_PROMPT
    assert "\n9. " not in SYSTEM_PROMPT


def test_rewrite_prompt_is_untouched():
    assert REWRITE_PROMPT.startswith("다음은 사용자와 어시스턴트의 대화입니다.")
    assert "현재 시각" not in REWRITE_PROMPT


# --- agent-14 보강 (Validator) ---

def test_current_time_line_uses_offset_for_negative_and_half_hour_zones():
    """이름 없는 타임존 대체 표기가 음수 오프셋·30분 오프셋에서도 UTC±HH:MM 형식이어야 한다."""
    assert current_time_line(datetime(2026, 9, 29, 14, 23, tzinfo=timezone(timedelta(hours=-5)))) == \
        "현재 시각: 2026-09-29 (화) 14:23 UTC-05:00"
    assert current_time_line(
        datetime(2026, 9, 29, 14, 23, tzinfo=timezone(timedelta(hours=5, minutes=30)))) == \
        "현재 시각: 2026-09-29 (화) 14:23 UTC+05:30"


def test_current_time_line_keeps_a_named_zone_as_is():
    """이름이 있으면 오프셋으로 바꾸지 않고 그 이름을 그대로 쓴다."""
    assert current_time_line(
        datetime(2026, 1, 1, 0, 5, tzinfo=timezone(timedelta(0), "UTC"))).endswith("00:05 UTC")


def test_current_time_line_pads_hour_and_minute():
    """한 자리 시/분도 두 자리로 찍혀 형식이 흔들리지 않는다."""
    assert current_time_line(datetime(2026, 9, 29, 4, 5, tzinfo=KST)) == \
        "현재 시각: 2026-09-29 (화) 04:05 KST"


def test_time_line_is_separated_from_the_prompt_by_a_blank_line():
    """시각 줄이 SYSTEM_PROMPT 첫 줄에 붙어버리지 않는다(빈 줄 1개로 분리)."""
    graph, model = _graph([AIMessage("답")])

    graph.invoke({"messages": [HumanMessage("오늘 며칠이야?")]})

    content = model.received[0][0].content
    first_line, blank, rest = content.split("\n", 2)
    assert first_line == current_time_line() or first_line.startswith("현재 시각: ")
    assert blank == ""
    assert rest.startswith(SYSTEM_PROMPT)


def test_only_one_time_line_is_injected_per_call():
    """멀티턴에서 시각 줄이 누적되지 않는다."""
    graph, model = _graph([AIMessage("1턴 답변"), AIMessage("독립 질문"), AIMessage("2턴 답변")],
                          checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("once"))
    graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("once"))

    agent_calls = [c[0].content for c in model.received if SYSTEM_PROMPT in c[0].content]
    assert len(agent_calls) == 2                           # 1턴·2턴 agent 호출
    for content in agent_calls:
        assert content.count("현재 시각: ") == 1


def test_rewrite_node_gets_no_time_line():
    """범위 제외: rewrite 노드에는 시각을 주입하지 않는다."""
    graph, model = _graph([AIMessage("1턴 답변"), AIMessage("독립 질문"), AIMessage("2턴 답변")],
                          checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("rw"))
    graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("rw"))

    rewrite_system = model.received[1][0]                  # 2턴 rewrite 호출
    assert isinstance(rewrite_system, SystemMessage)
    assert "현재 시각: " not in rewrite_system.content
    assert rewrite_system.content.startswith(REWRITE_PROMPT)


# --- agent-14 보강 (Validator, 검증 회차 2) ---

def test_withdrawn_prompt_rule_b_stays_out_of_the_system_prompt():
    """리드 판단 (6): B(프롬프트 변경)는 전면 철회다. 되살아나면 agent-06 P4 가 회귀한다."""
    assert "인사말처럼 정보가 필요 없는 말만 예외입니다." not in SYSTEM_PROMPT
    assert "지어내지 않습니다" not in SYSTEM_PROMPT          # 철회한 규칙 6(초안·개정안 공통)
    assert "모르는 것은 모른다고 답합니다" not in SYSTEM_PROMPT
    assert SYSTEM_PROMPT.rstrip().endswith("8. 한국어로 간결하게 답합니다.")


def test_time_line_is_reinjected_once_on_the_tool_loop_agent_call():
    """도구 호출 뒤 agent 로 되돌아오는 호출에도 시각이 1개만 들어가고 힌트는 맨 뒤를 지킨다."""
    graph, model = _graph([
        AIMessage("1턴 답변"),
        AIMessage("메시지 등록 API의 v1과 v2 차이"),          # 2턴 rewrite
        _tool_call_message(query="메시지 등록 API v1 v2"),     # 2턴 agent → 도구 호출
        AIMessage("2턴 최종 답변"),                            # 도구 결과 뒤 agent 재진입
    ], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread("loop"))
    graph.invoke({"messages": [HumanMessage("v1 이랑 v2 차이는?")]}, config=_thread("loop"))

    after_tool = model.received[3][0]                          # 도구 실행 뒤의 agent 호출
    assert isinstance(after_tool, SystemMessage)
    assert after_tool.content.count("현재 시각: ") == 1
    assert after_tool.content.startswith("현재 시각: ")
    assert after_tool.content.index(SYSTEM_PROMPT) < after_tool.content.index(HINT_PREFIX)

from datetime import datetime, timedelta, timezone

import httpx
import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from src.agent import REWRITE_PROMPT, SYSTEM_PROMPT, build_graph, current_time_line
from src.tools import build_tools, format_chunks
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


class _AnyText:
    """실행 시점마다 달라지는 값(현재 시각 등)을 비교에서 흡수하는 자리표시자."""

    def __eq__(self, other):
        return isinstance(other, str)

    def __repr__(self):
        return "<any text>"


ANY_TEXT = _AnyText()


def _turn(question, answer):
    """agent-16 이후 한 턴이 남기는 메시지 내용(도구 결과 없이 답하려 한 경우).

    강제 검색 직전의 임시 답변은 상태에서 지워지므로
    [질문, 합성 tool_calls(빈 content), 시스템 정보, 검색 결과×2, 최종 답변] 만 남는다.
    """
    chunk = format_chunks([_chunk()])
    return [question, "", ANY_TEXT, chunk, chunk, answer]


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


def test_groundless_answer_goes_through_a_forced_search(monkeypatch):
    """agent-16: 도구 없이 답하려 하면 강제 검색을 거친 뒤 답한다(agent-01 B5 계약을 대체).

    근거 없이 답하고 출처를 지어내는 경로를 막기 위해, 이번 턴에 도구 결과가 없으면
    그래프가 검색을 대신 실행해 결과를 넣고 다시 답하게 한다.
    """
    client = FakeClient([_chunk()])
    graph, model = _graph([AIMessage("근거 없는 첫 답변"), AIMessage("검색 결과를 반영한 답변")], client=client)

    state = graph.invoke({"messages": [HumanMessage("안녕")]})

    # 근거 없는 임시 답변은 상태에서 지워지고 합성 tool_calls·검색 결과·최종 답변만 남는다
    assert [type(m).__name__ for m in state["messages"]] == [
        "HumanMessage", "AIMessage", "ToolMessage", "ToolMessage", "ToolMessage", "AIMessage"]
    assert [c[1] for c in client.calls] == ["confluence", "openapi"]      # 검색 도구 2종을 직접 호출
    assert len(model.received) == 2                                      # 강제 후 한 번 더 답한다
    assert state["messages"][-1].content == "검색 결과를 반영한 답변"


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
    graph, model = _graph([AIMessage("첫 임시"), AIMessage("첫 답변"),
                           AIMessage("독립 질문"), AIMessage("둘째 임시"), AIMessage("두 번째 답변")],
                          checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "t1"}}

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=config)
    state = graph.invoke({"messages": [HumanMessage("두 번째 질문")]}, config=config)

    contents = [m.content for m in state["messages"]]
    assert contents == _turn("첫 질문", "첫 답변") + _turn("두 번째 질문", "두 번째 답변")
    # 두 번째 턴의 agent 호출(received[3]) 에 이전 대화가 함께 전달됐다.
    # 마지막 질문만 독립 질문으로 치환된다(F1) — 상태의 원문은 위에서 확인했다
    assert [m.content for m in model.received[3]][1:] == (
        _turn("첫 질문", "첫 답변") + ["독립 질문"])


def test_different_thread_id_does_not_share_history():
    graph, _ = _graph([AIMessage("첫 임시"), AIMessage("첫 답변"),
                       AIMessage("둘째 임시"), AIMessage("두 번째 답변")], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config={"configurable": {"thread_id": "t1"}})
    state = graph.invoke({"messages": [HumanMessage("다른 스레드 질문")]}, config={"configurable": {"thread_id": "t2"}})

    assert [m.content for m in state["messages"]] == _turn("다른 스레드 질문", "두 번째 답변")


def test_without_checkpointer_history_is_not_kept():
    """체크포인터가 없으면(기본값) 호출 간 대화가 이어지지 않는다."""
    graph, _ = _graph([AIMessage("첫 임시"), AIMessage("첫 답변"),
                       AIMessage("둘째 임시"), AIMessage("두 번째 답변")])

    graph.invoke({"messages": [HumanMessage("첫 질문")]})
    state = graph.invoke({"messages": [HumanMessage("두 번째 질문")]})

    assert [m.content for m in state["messages"]] == _turn("두 번째 질문", "두 번째 답변")


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
    graph, model = _graph([AIMessage("임시 답변"), AIMessage("첫 답변")], checkpointer=InMemorySaver())

    state = graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread())

    # rewrite 는 LLM 을 부르지 않는다. 호출 2회는 agent(강제 검색 전·후)뿐이다
    assert len(model.received) == 2
    assert all(SYSTEM_PROMPT in call[0].content for call in model.received)
    assert state["standalone_question"] == ""


def test_followup_turn_rewrites_and_substitutes_the_question():
    """R2. 후속 턴: rewrite 가 먼저 호출되고, 그 결과가 모델 입력의 마지막 질문을 대신한다(agent-16 F1)."""
    client = FakeClient([_chunk()])
    graph, model = _graph([
        _tool_call_message(query="메시지 등록 API"),           # 1턴 agent (도구 호출)
        AIMessage("POST /v1/messages 입니다."),                # 1턴 agent (최종 답변)
        AIMessage("메시지 등록 API의 v1과 v2 차이"),            # 2턴 rewrite
        AIMessage("근거 없는 임시 답변"),                       # 2턴 agent (강제 검색을 탄다)
        AIMessage("v1 은 …, v2 는 …"),                        # 2턴 agent (최종 답변)
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
    # agent-16 F1: 힌트 블록 대신 모델이 받는 마지막 질문을 독립 질문으로 치환한다
    assert "독립 표현" not in agent_system.content
    assert model.received[3][-1].content == "메시지 등록 API의 v1과 v2 차이"

    assert state["standalone_question"] == "메시지 등록 API의 v1과 v2 차이"
    # 리라이팅 결과와 SystemMessage 는 대화 기록에 저장되지 않는다
    assert [type(m).__name__ for m in state["messages"]] == [
        "HumanMessage", "AIMessage", "ToolMessage", "AIMessage",          # 1턴(모델이 스스로 검색)
        "HumanMessage", "AIMessage", "ToolMessage", "ToolMessage", "ToolMessage",
        "AIMessage"]                                                      # 2턴(강제 검색 + 시스템 정보)
    assert not any(isinstance(m, SystemMessage) for m in state["messages"])
    assert all("메시지 등록 API의 v1과 v2 차이" != m.content for m in state["messages"])


def test_empty_rewrite_keeps_the_question_and_does_not_reuse_previous_turn():
    """R3. rewrite 가 빈 문자열이면 치환하지 않고, 앞 턴의 독립 질문이 다음 턴에 재사용되지도 않는다."""
    graph, model = _graph([
        AIMessage("1턴 임시"), AIMessage("1턴 답변"),          # 1턴 agent (강제 검색 전·후)
        AIMessage("2턴 독립 질문"),                            # 2턴 rewrite
        AIMessage("2턴 임시"), AIMessage("2턴 답변"),          # 2턴 agent
        AIMessage("   "),                                     # 3턴 rewrite — 공백만
        AIMessage("3턴 임시"), AIMessage("3턴 답변"),          # 3턴 agent
    ], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread())
    graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread())
    state = graph.invoke({"messages": [HumanMessage("셋째 질문")]}, config=_thread())

    assert model.received[3][-1].content == "2턴 독립 질문"   # 2턴 모델 입력은 치환됐다
    assert state["standalone_question"] == ""
    # 3턴 rewrite 가 공백만 돌려줬으므로 치환 없이 원문이 그대로 간다
    assert model.received[6][-1].content == "셋째 질문"
    assert SYSTEM_PROMPT in model.received[6][0].content


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
        responses=[AIMessage("1턴 임시"), AIMessage("1턴 답변"),
                   AIMessage("독립 질문"), AIMessage("2턴 임시"), AIMessage("2턴 답변")],
        received=[], log=[])
    graph = build_graph(model, build_tools(FakeClient([_chunk()]), 6), InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("bind"))
    graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("bind"))

    # 1턴 agent(bound)×2(강제 검색 전·후) / 2턴 rewrite(원본 직접 호출 — bound 없음) / 2턴 agent(bound)×2
    assert model.log == ["bound", "call", "bound", "call",
                         "call",
                         "bound", "call", "bound", "call"]
    assert model.received[2][0].content == REWRITE_PROMPT    # rewrite 는 여전히 원본 모델


def test_agent_receives_the_rewritten_question_while_state_keeps_the_original():
    """agent-16 F1: 모델 입력의 마지막 질문만 독립 질문으로 바뀌고, 상태에는 원문이 남는다."""
    graph, model = _graph([
        AIMessage("1턴 임시"), AIMessage("1턴 답변"),
        AIMessage("메시지 등록 API의 v1과 v2 차이"),   # 2턴 rewrite
        AIMessage("2턴 임시"), AIMessage("2턴 답변"),
    ], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread("orig"))
    graph.invoke({"messages": [HumanMessage("방금 알려준 API의 v1이랑 v2 차이는?")]}, config=_thread("orig"))

    agent_input = model.received[3]
    assert isinstance(agent_input[-1], HumanMessage)
    assert agent_input[-1].content == "메시지 등록 API의 v1과 v2 차이"        # 모델이 받는 질문
    assert [m.content for m in agent_input if isinstance(m, HumanMessage)] == [
        "메시지 등록 API 알려줘", "메시지 등록 API의 v1과 v2 차이"]           # 앞 턴 질문은 그대로

    state = graph.get_state(_thread("orig")).values
    questions = [m.content for m in state["messages"] if isinstance(m, HumanMessage)]
    assert questions == ["메시지 등록 API 알려줘", "방금 알려준 API의 v1이랑 v2 차이는?"]   # 상태는 원문
    assert "독립 표현" not in agent_input[0].content                          # 힌트 블록은 없앴다


def test_exactly_empty_rewrite_response_leaves_the_question_untouched():
    """rewrite 응답이 빈 문자열이면 치환하지 않고 사용자 질문 원문을 그대로 넘긴다."""
    graph, model = _graph([
        AIMessage("1턴 임시"), AIMessage("1턴 답변"),
        AIMessage(""),          # 2턴 rewrite — 빈 응답
        AIMessage("2턴 임시"), AIMessage("2턴 답변"),
    ], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("empty"))
    state = graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("empty"))

    assert state["standalone_question"] == ""
    assert SYSTEM_PROMPT in model.received[3][0].content
    assert model.received[3][-1].content == "둘째 질문"        # 치환 없음
    assert "독립 표현" not in model.received[3][0].content     # 힌트 블록도 없다


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
        AIMessage("1턴 임시"), AIMessage("1턴 답변"),
        AIMessage("메시지 등록 API의 v1과 v2 차이"),
        AIMessage("2턴 임시"), AIMessage("2턴 답변"),
    ], checkpointer=saver)

    graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread("ckpt"))
    graph.invoke({"messages": [HumanMessage("v1이랑 v2 차이는?")]}, config=_thread("ckpt"))

    values = graph.get_state(_thread("ckpt")).values
    assert values["standalone_question"] == "메시지 등록 API의 v1과 v2 차이"
    assert [m.content for m in values["messages"]] == (
        _turn("메시지 등록 API 알려줘", "1턴 답변") + _turn("v1이랑 v2 차이는?", "2턴 답변"))


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


def test_system_message_is_only_time_then_prompt():
    """시스템 메시지는 시각 + SYSTEM_PROMPT 뿐이다(agent-16 F1 으로 힌트 블록을 없앴다)."""
    graph, model = _graph([
        AIMessage("1턴 임시"), AIMessage("1턴 답변"),
        AIMessage("메시지 등록 API의 v1과 v2 차이"),      # 2턴 rewrite
        AIMessage("2턴 임시"), AIMessage("2턴 답변"),
    ], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread("order"))
    graph.invoke({"messages": [HumanMessage("v1 이랑 v2 차이는?")]}, config=_thread("order"))

    content = model.received[3][0].content
    assert content.index("현재 시각: ") < content.index(SYSTEM_PROMPT)
    assert content.rstrip().endswith(SYSTEM_PROMPT.rstrip())     # 시스템 메시지는 시각 + 프롬프트뿐


# T3. 매 턴 갱신

def test_time_is_recomputed_every_turn(monkeypatch):
    import src.agent as agent_module

    turns = ["현재 시각: 2026-09-29 (화) 09:00 KST", "현재 시각: 2026-09-30 (수) 10:00 KST"]
    # 한 턴 안에서는 agent 가 두 번(강제 검색 전·후) 호출되므로 같은 값을 돌려준다
    # 한 턴에서 current_time_line 은 agent 2회 + force_search 1회로 3번 불린다
    calls = iter([turns[0], turns[0], turns[0], turns[1], turns[1], turns[1]])
    monkeypatch.setattr(agent_module, "current_time_line", lambda: next(calls))

    graph, model = _graph([AIMessage("1턴 임시"), AIMessage("1턴 답변"),
                           AIMessage("독립 질문"), AIMessage("2턴 임시"), AIMessage("2턴 답변")],
                          checkpointer=InMemorySaver())
    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("clock"))
    graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("clock"))

    first_turn = model.received[0][0].content
    second_turn = model.received[3][0].content          # rewrite 다음의 agent 호출
    assert first_turn.startswith("현재 시각: 2026-09-29 (화) 09:00 KST")
    assert second_turn.startswith("현재 시각: 2026-09-30 (수) 10:00 KST")
    assert "2026-09-29" not in second_turn              # 앞 턴 시각이 남지 않는다


# T4. 상태 미오염

def test_time_is_stored_only_as_a_system_facts_tool_result():
    """SystemMessage 는 여전히 저장되지 않고, 시각이 남는 곳은 강제 검색의 system_facts 결과뿐이다."""
    graph, _ = _graph([AIMessage("1턴 임시"), AIMessage("1턴 답변"),
                       AIMessage("독립 질문"), AIMessage("2턴 임시"), AIMessage("2턴 답변")],
                      checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("clean"))
    state = graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("clean"))

    assert [type(m).__name__ for m in state["messages"]] == [
        "HumanMessage", "AIMessage", "ToolMessage", "ToolMessage", "ToolMessage", "AIMessage"] * 2
    assert not any(isinstance(m, SystemMessage) for m in state["messages"])
    # 시각이 담기는 곳은 강제 검색이 넣은 시스템 정보 도구 결과뿐이다(SystemMessage 는 여전히 미저장)
    with_time = [m for m in state["messages"] if "현재 시각:" in str(m.content)]
    assert all(isinstance(m, ToolMessage) and m.name == "system_facts" for m in with_time)
    assert len(with_time) == 2                                   # 2턴 × 1건


# T5. 프롬프트

def test_prompt_rules_are_unchanged_by_time_injection():
    """agent-14 의 규칙 1·거절 경로는 그대로 두고, 시각 값 자체는 프롬프트에 넣지 않는다.

    agent-16 BUG1 로 규칙 5 에 단서 한 문장만 추가됐다(리드 판단 (9)).
    """
    assert "1. 답하기 전에 반드시 검색 도구로 근거를 찾습니다. 인사말이나 일반 상식 질문은 예외입니다." in SYSTEM_PROMPT
    assert "현재 시각: " not in SYSTEM_PROMPT        # 시각 값은 여전히 런타임 주입이다
    # 거절 경로는 기존 규칙 5 가 담당한다(첫 문장 문구 불변)
    assert '5. 검색 결과에 없는 내용은 추측하지 않습니다. 근거를 찾지 못하면 "관련 내용을 문서에서 찾지 못했습니다." 라고 답합니다.' \
        in SYSTEM_PROMPT


def test_prompt_is_back_to_the_agent_13_wording():
    """agent-16 (G): 프롬프트 노선을 포기했으므로 규칙 문구는 agent-13 상태 그대로다."""
    assert "다만 위에 주어진 현재 시각처럼" not in SYSTEM_PROMPT        # 규칙 5 단서 철회
    assert "인사말이나 일반 상식 질문은 예외입니다." in SYSTEM_PROMPT                      # 규칙 1
    assert "앞 대화 없이도 이해되는 독립 검색어로 만듭니다." in SYSTEM_PROMPT               # 규칙 3
    assert "요청하지 않았는데 임의로 호출하지 않습니다." in SYSTEM_PROMPT                   # 규칙 4
    assert SYSTEM_PROMPT.rstrip().endswith("8. 한국어로 간결하게 답합니다.")               # 번호 체계 유지


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
    graph, model = _graph([AIMessage("1턴 임시"), AIMessage("1턴 답변"),
                           AIMessage("독립 질문"), AIMessage("2턴 임시"), AIMessage("2턴 답변")],
                          checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("once"))
    graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("once"))

    agent_calls = [c[0].content for c in model.received if SYSTEM_PROMPT in c[0].content]
    assert len(agent_calls) == 4                           # 턴마다 강제 검색 전·후 2회씩
    for content in agent_calls:
        assert content.count("현재 시각: ") == 1


def test_rewrite_node_gets_no_time_line():
    """범위 제외: rewrite 노드에는 시각을 주입하지 않는다."""
    graph, model = _graph([AIMessage("1턴 임시"), AIMessage("1턴 답변"),
                           AIMessage("독립 질문"), AIMessage("2턴 임시"), AIMessage("2턴 답변")],
                          checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("rw"))
    graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("rw"))

    rewrite_system = model.received[2][0]                  # 2턴 rewrite 호출
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
        AIMessage("1턴 임시"), AIMessage("1턴 답변"),
        AIMessage("메시지 등록 API의 v1과 v2 차이"),          # 2턴 rewrite
        _tool_call_message(query="메시지 등록 API v1 v2"),     # 2턴 agent → 도구 호출
        AIMessage("2턴 최종 답변"),                            # 도구 결과 뒤 agent 재진입
    ], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread("loop"))
    graph.invoke({"messages": [HumanMessage("v1 이랑 v2 차이는?")]}, config=_thread("loop"))

    after_tool = model.received[4][0]                          # 도구 실행 뒤의 agent 호출
    assert isinstance(after_tool, SystemMessage)
    assert after_tool.content.count("현재 시각: ") == 1
    assert after_tool.content.startswith("현재 시각: ")
    assert after_tool.content.rstrip().endswith(SYSTEM_PROMPT.rstrip())


# --- agent-16: 검색 없이 답하지 않는다(강제 검색) ---

def test_forced_search_runs_only_once_per_turn():
    """강제는 턴당 1회. 강제 후에도 모델이 도구를 안 부르면 그대로 답하게 둔다(무한 루프 방지)."""
    client = FakeClient([_chunk()])
    graph, model = _graph([AIMessage("임시 답변"), AIMessage("강제 후에도 근거 없는 답변")], client=client)

    state = graph.invoke({"messages": [HumanMessage("질문")]})

    assert len(client.calls) == 2                       # 검색 도구 2종을 한 번씩만
    assert len(model.received) == 2                     # agent 호출 2회로 끝난다
    assert state["forced"] is True
    assert state["messages"][-1].content == "강제 후에도 근거 없는 답변"


def test_forced_search_is_skipped_when_the_turn_already_has_tool_results():
    """모델이 스스로 도구를 부른 턴에는 강제하지 않는다."""
    client = FakeClient([_chunk()])
    graph, model = _graph([_tool_call_message(query="메시지 등록"), AIMessage("근거 있는 답변")], client=client)

    state = graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]})

    assert [c[0] for c in client.calls] == ["메시지 등록"]        # 모델이 고른 검색어 1회뿐
    assert [type(m).__name__ for m in state["messages"]] == [
        "HumanMessage", "AIMessage", "ToolMessage", "AIMessage"]
    assert state.get("forced") is not True


def test_forced_search_result_reaches_the_next_agent_call():
    """강제로 넣은 도구 결과가 다음 agent 호출 입력에 들어간다(그 근거로 답할 수 있다)."""
    client = FakeClient([_chunk()])
    graph, model = _graph([AIMessage("임시 답변"), AIMessage("검색 결과 기반 답변")], client=client)

    graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]})

    second_call = model.received[1]
    tool_messages = [m for m in second_call if isinstance(m, ToolMessage)]
    assert [m.name for m in tool_messages] == ["system_facts", "search_confluence", "search_openapi"]
    assert "현재 시각: " in tool_messages[0].content              # 시스템이 아는 사실도 근거로 넣는다
    assert all(format_chunks([_chunk()]) == m.content for m in tool_messages[1:])


def test_forced_search_uses_the_standalone_question_when_available():
    """후속 턴에서는 rewrite 결과(독립 질문)를 검색어로 쓴다."""
    client = FakeClient([_chunk()])
    graph, _ = _graph([
        AIMessage("1턴 임시"), AIMessage("1턴 답변"),
        AIMessage("메시지 등록 API의 v1과 v2 차이"),      # 2턴 rewrite
        AIMessage("2턴 임시"), AIMessage("2턴 답변"),
    ], client=client, checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread("forced"))
    graph.invoke({"messages": [HumanMessage("v1 이랑 v2 차이는?")]}, config=_thread("forced"))

    assert [c[0] for c in client.calls] == [
        "메시지 등록 API 알려줘", "메시지 등록 API 알려줘",          # 1턴: 첫 턴이라 질문 그대로
        "메시지 등록 API의 v1과 v2 차이", "메시지 등록 API의 v1과 v2 차이"]   # 2턴: 독립 질문


def test_forced_flag_resets_every_turn():
    """rewrite 가 매 턴 forced 를 되돌리므로 다음 턴에도 강제가 동작한다."""
    client = FakeClient([_chunk()])
    graph, _ = _graph([
        AIMessage("1턴 임시"), AIMessage("1턴 답변"),
        AIMessage("독립 질문"),
        AIMessage("2턴 임시"), AIMessage("2턴 답변"),
    ], client=client, checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("reset"))
    graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("reset"))

    assert len(client.calls) == 4                       # 두 턴 모두 강제 검색이 돌았다


def test_forced_search_does_not_call_non_search_tools():
    """call_api 같은 실호출 도구는 강제 대상이 아니다(부작용 방지)."""
    from langchain_core.tools import tool as make_tool

    called = []

    @make_tool
    def call_api(service: str, path: str) -> str:
        """가짜 실호출 도구."""
        called.append((service, path))
        return "호출됨"

    client = FakeClient([_chunk()])
    model = ScriptedChatModel(responses=[AIMessage("임시"), AIMessage("답변")], received=[])
    graph = build_graph(model, build_tools(client, 6) + [call_api], None)

    graph.invoke({"messages": [HumanMessage("질문")]})

    assert len(client.calls) == 2
    assert called == []


def test_forced_search_survives_a_failing_tool():
    """검색이 실패해도 답변을 막지 않는다(오류 내용을 결과로 넣고 계속)."""
    client = FakeClient(error=httpx.ConnectError("refused"))
    graph, model = _graph([AIMessage("임시"), AIMessage("검색 실패를 설명하는 답변")], client=client)

    state = graph.invoke({"messages": [HumanMessage("질문")]})

    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 3                               # 시스템 정보 1 + 검색 2
    assert all("검색에 실패했습니다" in m.content for m in tool_messages[1:])
    assert state["messages"][-1].content == "검색 실패를 설명하는 답변"


def test_forced_search_survives_a_malformed_rag_response():
    """RAG 가 200 과 함께 깨진 본문을 줘도(KeyError 등) 턴이 끊기지 않는다. ToolNode 경로와 같은 수준."""
    client = FakeClient(error=KeyError("chunks"))
    graph, model = _graph([AIMessage("임시"), AIMessage("검색 실패를 설명하는 답변")], client=client)

    state = graph.invoke({"messages": [HumanMessage("질문")]})

    tool_messages = [m for m in state["messages"] if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 3                               # 시스템 정보 1 + 검색 2
    assert all("검색에 실패했습니다: KeyError" in m.content for m in tool_messages[1:])
    assert state["messages"][-1].content == "검색 실패를 설명하는 답변"


def test_turn_has_tool_results_only_looks_at_the_current_turn():
    from src.agent import turn_has_tool_results

    previous = [HumanMessage("이전 질문"), AIMessage(""),
                ToolMessage(content="결과", name="search_confluence", tool_call_id="x"),
                AIMessage("이전 답변"), HumanMessage("이번 질문")]

    assert turn_has_tool_results(previous) is False          # 이전 턴 결과는 근거로 치지 않는다
    assert turn_has_tool_results(previous + [
        AIMessage(""), ToolMessage(content="결과", name="search_confluence", tool_call_id="y")]) is True
    assert turn_has_tool_results([]) is False


# --- agent-16 F1·F2: 임시 답변이 남지 않는다 ---

def test_forced_search_removes_the_groundless_draft_from_state():
    """F1. 강제 검색 직전의 근거 없는 답변은 상태(=체크포인터)에 남지 않는다."""
    graph, _ = _graph([AIMessage("근거 없는 임시 답변"), AIMessage("검색 결과 기반 답변")],
                      checkpointer=InMemorySaver())

    state = graph.invoke({"messages": [HumanMessage("질문")]}, config=_thread("draft"))

    contents = [m.content for m in state["messages"]]
    assert "근거 없는 임시 답변" not in contents
    assert contents[-1] == "검색 결과 기반 답변"
    # 합성 tool_calls + (시스템 정보·검색 결과) 쌍은 그대로 남는다(모델이 근거를 다시 볼 수 있어야 한다)
    assert [type(m).__name__ for m in state["messages"]] == [
        "HumanMessage", "AIMessage", "ToolMessage", "ToolMessage", "ToolMessage", "AIMessage"]


def test_only_one_answer_per_turn_survives_in_state():
    """복원·요약 소비자가 한 턴에서 답변을 하나만 보게 된다."""
    graph, _ = _graph([
        AIMessage("1턴 임시"), AIMessage("1턴 답변"),
        AIMessage("독립 질문"),
        AIMessage("2턴 임시"), AIMessage("2턴 답변"),
    ], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("첫 질문")]}, config=_thread("one"))
    state = graph.invoke({"messages": [HumanMessage("둘째 질문")]}, config=_thread("one"))

    answers = [m.content for m in state["messages"]
               if isinstance(m, AIMessage) and not m.tool_calls and m.content]
    assert answers == ["1턴 답변", "2턴 답변"]        # 턴당 정확히 하나


def test_draft_removal_is_skipped_when_the_last_message_is_not_a_draft():
    """모델이 도구를 부른 턴에는 지울 임시 답변이 없다(잘못 지우지 않는다)."""
    client = FakeClient([_chunk()])
    graph, _ = _graph([_tool_call_message(query="q"), AIMessage("근거 있는 답변")], client=client)

    state = graph.invoke({"messages": [HumanMessage("질문")]})

    assert [m.content for m in state["messages"]][-1] == "근거 있는 답변"
    assert len([m for m in state["messages"] if isinstance(m, ToolMessage)]) == 1


def test_forced_search_stops_after_one_pass_even_without_search_tools():
    """검색 도구가 없는 구성에서도 강제는 턴당 1회로 끝난다(Validator).

    이 구성에서는 강제 후에도 ToolMessage 가 생기지 않으므로 `forced` 플래그만이 루프를 막는다.
    민감도: 플래그 판정을 뺀 사본에서는 같은 입력이 GraphRecursionError 로 끝났다.
    """
    from langchain_core.tools import tool as make_tool

    @make_tool
    def call_api(service: str, path: str) -> str:
        """검색 도구가 아닌 도구(강제 대상이 아니다)."""
        return "ok"

    # 응답 목록은 순환하므로 모델은 매번 도구 없이 답하려 한다
    model = ScriptedChatModel(responses=[AIMessage("근거 없는 답변")], received=[])
    graph = build_graph(model, [call_api], None)

    state = graph.invoke({"messages": [HumanMessage("질문")]}, config={"recursion_limit": 25})

    assert len(model.received) == 2                       # 강제 전·후 2회로 끝난다
    assert state["forced"] is True
    assert state["messages"][-1].content == "근거 없는 답변"


# --- agent-16 F1: 모델 입력의 마지막 질문만 독립 질문으로 치환 ---

def test_replace_last_question_only_touches_the_last_human_message():
    from src.agent import replace_last_question

    messages = [HumanMessage("첫 질문"), AIMessage("답변"), HumanMessage("둘째 질문")]
    replaced = replace_last_question(messages, "독립 질문")

    assert [m.content for m in replaced] == ["첫 질문", "답변", "독립 질문"]
    assert [m.content for m in messages] == ["첫 질문", "답변", "둘째 질문"]   # 원본 불변
    assert replaced[-1].id == messages[-1].id                               # id 는 유지한다


def test_replace_last_question_is_a_no_op_without_a_standalone_question():
    from src.agent import replace_last_question

    messages = [HumanMessage("질문")]
    assert [m.content for m in replace_last_question(messages, "")] == ["질문"]
    assert [m.content for m in replace_last_question([], "독립")] == []


def test_first_turn_sends_the_question_as_is():
    """첫 턴은 rewrite 가 빈 문자열을 주므로 치환하지 않는다."""
    graph, model = _graph([AIMessage("임시"), AIMessage("답변")], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread("f1-first"))

    assert model.received[0][-1].content == "메시지 등록 API 알려줘"


def test_state_keeps_the_original_question_while_model_sees_the_rewritten_one():
    """상태·체크포인터는 원문 그대로여서 UI·히스토리·CLI 가 바뀌지 않는다."""
    graph, model = _graph([
        AIMessage("1턴 임시"), AIMessage("1턴 답변"),
        AIMessage("메시지 등록 API의 v1과 v2 차이"),      # 2턴 rewrite
        AIMessage("2턴 임시"), AIMessage("2턴 답변"),
    ], checkpointer=InMemorySaver())

    graph.invoke({"messages": [HumanMessage("메시지 등록 API 알려줘")]}, config=_thread("f1"))
    state = graph.invoke({"messages": [HumanMessage("방금 알려준 API의 v1이랑 v2 차이는?")]},
                         config=_thread("f1"))

    questions = [m.content for m in state["messages"] if isinstance(m, HumanMessage)]
    assert questions == ["메시지 등록 API 알려줘", "방금 알려준 API의 v1이랑 v2 차이는?"]
    assert model.received[3][-1].content == "메시지 등록 API의 v1과 v2 차이"


def test_hint_block_is_gone_from_the_system_message():
    """치환으로 목적이 달성돼 agent-08 힌트 블록은 제거했다(프롬프트 길이 축소)."""
    import src.agent as agent_module

    assert not hasattr(agent_module, "HINT_PREFIX")
    graph, model = _graph([AIMessage("임시"), AIMessage("답변")])
    graph.invoke({"messages": [HumanMessage("질문")]})

    system = model.received[0][0].content
    assert "독립 표현" not in system
    assert system.rstrip().endswith(SYSTEM_PROMPT.rstrip())


def test_forced_search_injects_the_current_time_as_a_tool_result():
    """agent-16 (G): 모델은 검색 결과 안의 정보만 근거로 치므로 시각도 도구 결과로 넣는다."""
    graph, model = _graph([AIMessage("임시"), AIMessage("답변")])

    state = graph.invoke({"messages": [HumanMessage("오늘 몇월 몇일이야?")]})

    facts = [m for m in state["messages"] if isinstance(m, ToolMessage) and m.name == "system_facts"]
    assert len(facts) == 1
    assert "현재 시각: " in facts[0].content
    assert current_time_line()[:14] in facts[0].content          # 같은 날짜가 들어간다
    # 두 번째 agent 호출이 그 근거를 본다
    assert any(isinstance(m, ToolMessage) and m.name == "system_facts" for m in model.received[1])

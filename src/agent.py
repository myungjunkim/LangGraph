"""검색 도구를 쓰는 ReAct 형태의 LangGraph 그래프."""
from datetime import datetime

import httpx
import ollama
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (AIMessage, HumanMessage, RemoveMessage, SystemMessage,
                                     ToolMessage)
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from src.api_tool import build_api_tool
from src.config.settings import Settings
from src.llm_factory import create_chat_model
from src.rag_client import RagClient
from src.tools import build_tools

# 진입점(CLI/웹)이 한 턴을 감싸며 잡는 런타임 오류
RUNTIME_ERRORS = (httpx.HTTPError, ollama.ResponseError, ConnectionError, GraphRecursionError)

API_TOOL_NAME = "call_api"
WEEKDAYS = "월화수목금토일"


def current_time_line(now: datetime | None = None) -> str:
    """현재 시각 한 줄. 로컬 타임존 기준. 예) '현재 시각: 2026-09-29 (화) 14:23 KST'

    모델에 지시가 아니라 사실로 주어지는 값이라 매 턴 새로 계산한다.
    """
    moment = now or datetime.now().astimezone()
    # 요일은 로케일에 의존하지 않도록 직접 매핑한다(%a 는 환경에 따라 'Tue' 가 된다)
    weekday = WEEKDAYS[moment.weekday()]
    # 타임존 이름이 비면 UTC 오프셋(+09:00)으로 대체한다
    zone = moment.tzname() or moment.strftime("%z") or "UTC"
    if zone and zone[0] in "+-" and len(zone) == 5:
        zone = f"UTC{zone[:3]}:{zone[3:]}"
    return f"현재 시각: {moment:%Y-%m-%d} ({weekday}) {moment:%H:%M} {zone}"


def turn_has_tool_results(messages) -> bool:
    """이번 턴(마지막 HumanMessage 이후)에 도구 결과가 있는지.

    없으면 아직 근거가 없는 상태다. 이 판정으로 검색을 한 번 강제한다.
    """
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            return True
        if isinstance(message, HumanMessage):
            return False
    return False


def last_user_question(messages) -> str:
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            return str(message.content)
    return ""


def tool_call_summary(args: dict) -> str:
    """도구 호출을 한 줄로 보여줄 문자열. query 인자가 있으면 기존과 똑같이 그 값만 쓴다."""
    if "query" in args:
        return str(args["query"])
    return " ".join(str(value) for value in args.values())

SYSTEM_PROMPT = """당신은 팀 내부 문서(Confluence)와 사내 API 명세(OpenAPI)를 근거로 답하는 어시스턴트입니다.

규칙:
1. 답하기 전에 반드시 검색 도구로 근거를 찾습니다. 인사말이나 일반 상식 질문은 예외입니다.
2. 질문 성격에 따라 도구를 고릅니다. API 호출 방법은 search_openapi, 정책·설정값·운영 절차·용어는 search_confluence 를 쓰고,
   둘 다 필요하면 둘 다 호출합니다. 첫 검색 결과가 부족하면 검색어를 바꿔 다시 검색합니다.
3. 후속 질문("그 API", "방금 알려준 것")이면 검색어를 앞 대화에서 다룬 대상 이름(API 이름·기능명·경로)으로 시작해,
   앞 대화 없이도 이해되는 독립 검색어로 만듭니다. 앞 대화에 나오지 않은 이름을 검색어에 넣지 않습니다.
4. 사용자가 실제 호출·시험·응답 확인을 요청하면 call_api 로 GET 요청을 보냅니다.
   먼저 search_openapi 로 경로와 필수 파라미터를 확인한 뒤 호출합니다.
   요청하지 않았는데 임의로 호출하지 않습니다.
5. 검색 결과에 없는 내용은 추측하지 않습니다. 근거를 찾지 못하면 "관련 내용을 문서에서 찾지 못했습니다." 라고 답합니다.
   다만 위에 주어진 현재 시각처럼 시스템이 제공한 정보는 확실한 근거이므로 검색 결과와 무관하게 그대로 사용합니다.
6. API 관련 답변에는 HTTP 메서드, 경로, 필수 파라미터/필드를 명시합니다.
7. 답변 끝에 `출처:` 목록으로 사용한 문서의 제목과 url 을 적습니다.
8. 한국어로 간결하게 답합니다."""

REWRITE_PROMPT = """다음은 사용자와 어시스턴트의 대화입니다. 마지막 사용자 질문을 앞 대화 없이도 이해되도록 한 문장으로 다시 쓰세요.
규칙:
1. 앞 대화에서 다룬 대상(API 이름, 경로, 기능명, 문서 제목)을 질문에 명시합니다.
2. 대화에 나오지 않은 이름은 넣지 않습니다.
3. 질문의 의도는 바꾸지 않습니다.
4. 다시 쓴 질문 한 문장만 출력합니다. 설명·따옴표·접두어 없이."""

HINT_PREFIX = "[이번 질문의 독립 표현] "


class AgentState(MessagesState):
    """MessagesState + 후속 질문의 독립 표현. messages 자체는 rewrite 가 건드리지 않는다."""

    standalone_question: str
    forced: bool          # 이번 턴에 검색을 강제했는지(턴당 1회). rewrite 가 매 턴 False 로 되돌린다


def build_graph(chat_model: BaseChatModel, tools: list[BaseTool],
                checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    model_with_tools = chat_model.bind_tools(tools)
    # 강제 검색 대상은 검색 도구뿐이다(call_api 같은 실호출 도구를 임의로 부르지 않는다)
    search_tools = [t for t in tools if t.name.startswith("search_")]

    def rewrite(state: AgentState) -> dict:
        """후속 질문을 앞 대화 없이도 이해되는 한 문장으로 다시 쓴다. 첫 턴은 LLM 을 호출하지 않는다."""
        messages = state["messages"]
        if len([m for m in messages if isinstance(m, HumanMessage)]) < 2:
            return {"standalone_question": "", "forced": False}
        # ToolMessage(청크 원문)와 tool_call 전용 AIMessage 는 제외 — 컨텍스트 절약
        history = [m for m in messages
                   if isinstance(m, HumanMessage)
                   or (isinstance(m, AIMessage) and not m.tool_calls and m.content)]
        # 도구를 붙이지 않은 원본 모델로 호출한다(리라이팅에서 도구 호출이 나오면 안 된다)
        response = chat_model.invoke([SystemMessage(REWRITE_PROMPT)] + history)
        # rewrite 는 매 턴의 첫 노드다. 강제 플래그를 여기서 되돌린다
        return {"standalone_question": response.content.strip(), "forced": False}

    def agent(state: AgentState) -> dict:
        # SystemMessage 는 상태에 저장하지 않고 호출 때마다 앞에 붙인다.
        # 현재 시각은 맨 앞(사실), agent-08 힌트는 맨 뒤 순서를 지킨다
        system = f"{current_time_line()}\n\n{SYSTEM_PROMPT}"
        if state.get("standalone_question"):
            system += ("\n\n" + HINT_PREFIX + state["standalone_question"]
                       + "\n검색이 필요할 때 query 는 이 독립 표현을 기준으로 만듭니다."
                       + " 앞 대화의 답변만으로 충분하면 검색하지 않아도 됩니다.")
        response = model_with_tools.invoke([SystemMessage(system)] + state["messages"])
        return {"messages": [response]}

    def force_search(state: AgentState) -> dict:
        """근거 없이 답하려는 턴에서 검색을 대신 실행해 결과를 대화에 넣는다.

        모델이 도구를 부르지 않아도(ChatOllama 는 tool_choice 를 무시한다) 근거가 생기도록
        도구를 코드로 직접 호출하고, 표준 형태(tool_calls AIMessage + 대응 ToolMessage)로 주입한다.
        """
        messages = state["messages"]
        query = state.get("standalone_question") or last_user_question(messages)
        # 근거 없이 나온 직전 답변은 대화 기록에 남기지 않는다(복원·요약 소비자가 두 답변을 보게 된다).
        # route_after_agent 로만 들어오므로 마지막 메시지는 tool_calls 없는 AIMessage 이고, id 는 add_messages 가 붙인다
        removals = [RemoveMessage(id=messages[-1].id)]
        calls, results = [], []
        for index, tool in enumerate(search_tools):
            call_id = f"forced_{index}"
            calls.append({"name": tool.name, "args": {"query": query}, "id": call_id})
            try:
                content = tool.invoke({"query": query})
            # 검색 실패는 답변을 막지 않는다. ToolNode(handle_tool_errors=True)처럼 모든 예외를 흡수한다
            # (RAG 가 200 과 함께 깨진 본문을 주면 JSONDecodeError·KeyError 가 난다)
            except Exception as e:
                content = f"검색에 실패했습니다: {type(e).__name__}: {e}"
            results.append(ToolMessage(content=content, name=tool.name, tool_call_id=call_id))
        # 모델이 낸 것이 아니라 그래프가 만든 호출이므로 content 는 비워 둔다
        return {"messages": [*removals, AIMessage(content="", tool_calls=calls), *results], "forced": True}

    def route_after_agent(state: AgentState):
        """도구 호출이 있으면 tools, 없는데 이번 턴 근거가 없으면 force_search(턴당 1회), 아니면 END."""
        route = tools_condition(state)
        if route != END:
            return route
        if not state.get("forced") and not turn_has_tool_results(state["messages"]):
            return "force_search"
        return END

    graph = StateGraph(AgentState)
    graph.add_node("rewrite", rewrite)
    graph.add_node("agent", agent)
    graph.add_node("force_search", force_search)
    # langgraph-prebuilt 1.1.0 의 기본 핸들러는 ToolInvocationError 만 흡수하고 나머지는 다시 던진다.
    # 검색 도구 예외(RAG 서버 장애 등)도 ToolMessage 로 바꿔 LLM 이 인지하도록 True 를 명시한다.
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    graph.add_edge(START, "rewrite")
    graph.add_edge("rewrite", "agent")
    graph.add_conditional_edges("agent", route_after_agent,
                                {"tools": "tools", "force_search": "force_search", END: END})
    graph.add_edge("tools", "agent")
    graph.add_edge("force_search", "agent")
    return graph.compile(checkpointer=checkpointer)


def build_default_graph(settings: Settings,
                       checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    """설정만으로 기본 구성(RAG 검색 도구 + Ollama)의 그래프를 만든다.

    checkpointer 를 주지 않으면 기존대로 InMemorySaver(프로세스 종료 시 대화 소실)를 쓴다.
    """
    client = RagClient(settings.rag_base_url, settings.rag_search_path, settings.rag_timeout)
    tools = build_tools(client, settings.rag_top_k)
    if settings.api_services:              # 등재된 QA 서비스가 있을 때만 실호출 도구를 붙인다
        tools = tools + [build_api_tool(settings.api_services, settings.api_timeout,
                                        settings.api_max_chars)]
    return build_graph(create_chat_model(settings), tools, checkpointer or InMemorySaver())

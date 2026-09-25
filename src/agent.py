"""검색 도구를 쓰는 ReAct 형태의 LangGraph 그래프."""
import httpx
import ollama
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langchain_core.tools import BaseTool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from src.config.settings import Settings
from src.llm_factory import create_chat_model
from src.rag_client import RagClient
from src.tools import build_tools

# 진입점(CLI/웹)이 한 턴을 감싸며 잡는 런타임 오류
RUNTIME_ERRORS = (httpx.HTTPError, ollama.ResponseError, ConnectionError, GraphRecursionError)

SYSTEM_PROMPT = """당신은 팀 내부 문서(Confluence)와 사내 API 명세(OpenAPI)를 근거로 답하는 어시스턴트입니다.

규칙:
1. 답하기 전에 반드시 검색 도구로 근거를 찾습니다. 인사말이나 일반 상식 질문은 예외입니다.
2. 질문 성격에 따라 도구를 고릅니다. API 호출 방법은 search_openapi, 정책·설정값·운영 절차·용어는 search_confluence 를 쓰고,
   둘 다 필요하면 둘 다 호출합니다. 첫 검색 결과가 부족하면 검색어를 바꿔 다시 검색합니다.
3. 검색 결과에 없는 내용은 추측하지 않습니다. 근거를 찾지 못하면 "관련 내용을 문서에서 찾지 못했습니다." 라고 답합니다.
4. API 관련 답변에는 HTTP 메서드, 경로, 필수 파라미터/필드를 명시합니다.
5. 답변 끝에 `출처:` 목록으로 사용한 문서의 제목과 url 을 적습니다.
6. 한국어로 간결하게 답합니다."""


def build_graph(chat_model: BaseChatModel, tools: list[BaseTool],
                checkpointer: BaseCheckpointSaver | None = None) -> CompiledStateGraph:
    model_with_tools = chat_model.bind_tools(tools)

    def agent(state: MessagesState) -> dict:
        # SystemMessage 는 상태에 저장하지 않고 호출 때마다 앞에 붙인다
        response = model_with_tools.invoke([SystemMessage(SYSTEM_PROMPT)] + state["messages"])
        return {"messages": [response]}

    graph = StateGraph(MessagesState)
    graph.add_node("agent", agent)
    # langgraph-prebuilt 1.1.0 의 기본 핸들러는 ToolInvocationError 만 흡수하고 나머지는 다시 던진다.
    # 검색 도구 예외(RAG 서버 장애 등)도 ToolMessage 로 바꿔 LLM 이 인지하도록 True 를 명시한다.
    graph.add_node("tools", ToolNode(tools, handle_tool_errors=True))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile(checkpointer=checkpointer)


def build_default_graph(settings: Settings) -> CompiledStateGraph:
    """설정만으로 기본 구성(RAG 검색 도구 + Ollama + InMemorySaver)의 그래프를 만든다."""
    client = RagClient(settings.rag_base_url, settings.rag_search_path, settings.rag_timeout)
    tools = build_tools(client, settings.rag_top_k)
    return build_graph(create_chat_model(settings), tools, InMemorySaver())

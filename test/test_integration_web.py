"""실제 RAG 서버 + Ollama 를 사용하는 웹 계층 통합 테스트.

실행: pytest -m integration
사전 준비: RAG 서버 기동(`python main.py --active-profile=local`), `ollama serve`, qwen3:14b 설치.
"""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from src.agent import build_default_graph
from src.config.settings import config_path_for, load_settings
from src.web.app import create_app

pytestmark = pytest.mark.integration

THREAD_ID = "integration-web"


@pytest.fixture(scope="module")
def live_settings():
    path = config_path_for("local")
    if not path.is_file():
        pytest.skip(f"설정 파일이 없습니다: {path} (config_local.ini.example 을 복사하세요)")
    settings = load_settings(path)

    try:
        health = httpx.get(f"{settings.rag_base_url}/check", timeout=5)
    except httpx.HTTPError as e:
        pytest.skip(f"RAG 서버({settings.rag_base_url})에 연결할 수 없습니다: {e}")
    if health.status_code != 200:
        pytest.skip(f"RAG /check 가 {health.status_code} 를 반환했습니다.")

    try:
        tags = httpx.get(f"{settings.ollama_base_url}/api/tags", timeout=5)
    except httpx.HTTPError as e:
        pytest.skip(f"Ollama({settings.ollama_base_url})에 연결할 수 없습니다: {e}")
    installed = {m.get("name", "") for m in tags.json().get("models", [])}
    if settings.llm_model not in installed:
        pytest.skip(f"Ollama 에 {settings.llm_model} 모델이 없습니다: {sorted(installed)}")
    return settings


def _frames(text: str) -> list[tuple[str, object]]:
    parsed = []
    for block in text.strip().split("\n\n"):
        lines = block.split("\n")
        event = next(line[len("event: "):] for line in lines if line.startswith("event: "))
        data = next(line[len("data: "):] for line in lines if line.startswith("data: "))
        parsed.append((event, json.loads(data)))
    return parsed


def test_live_stream_searches_openapi_and_streams_tokens(live_settings, capsys):
    settings = live_settings
    graph = build_default_graph(settings)
    client = TestClient(create_app(graph, settings))

    res = client.post("/v1/chat/stream",
                      json={"thread_id": THREAD_ID, "message": "메시지 등록 API 호출 방법 알려줘"})

    assert res.status_code == 200
    frames = _frames(res.text)
    names = [name for name, _ in frames]
    assert "error" not in names, frames
    assert names[-1] == "done"

    searches = [data for name, data in frames if name == "search"]
    assert searches, "search 이벤트가 없다"
    assert any(s["tool"] == "search_openapi" for s in searches), searches

    tokens = [data for name, data in frames if name == "token"]
    answer = "".join(tokens)
    assert tokens, "token 이벤트가 없다"
    assert "출처:" in answer

    # 토큰을 이어붙인 결과가 체크포인터에 저장된 최종 답변과 정확히 같아야 한다
    # (완성 메시지가 뒤이어 한 번 더 흘러 토큰이 중복되지 않는지 확인)
    stored = graph.get_state({"configurable": {"thread_id": THREAD_ID}}).values["messages"][-1].content
    assert answer == stored

    # 검증 기록용: token 이 1건이면 스트리밍이 성립하지 않는다(ESCALATE 대상)
    with capsys.disabled():
        print(f"\n[W7] token 이벤트 {len(tokens)}건, search 이벤트 {len(searches)}건, 답변 {len(answer)}자")
    assert len(tokens) >= 2, f"token 이벤트가 {len(tokens)}건 — 스트리밍이 토큰 단위로 전달되지 않는다"


def test_live_check_reports_ok(live_settings):
    client = TestClient(create_app(build_default_graph(live_settings), live_settings))
    assert client.get("/check").json() == {"status": "ok", "rag": True, "ollama": True}

from pathlib import Path

import pytest

CONFIG_TEXT = """\
[rag]
base-url=http://127.0.0.1:5010
search-path=/v1/search
timeout=30
top-k=6

[ollama]
base-url=http://127.0.0.1:11434
llm-model=qwen3:14b
num-ctx=16384
temperature=0
llm-timeout=120

[agent]
recursion-limit=12
"""


class FakeClient:
    """RagClient 대역. 호출 인자를 기록하고 정해진 청크를 돌려준다."""

    def __init__(self, chunks=None, error=None):
        self._chunks = chunks or []
        self._error = error
        self.calls = []

    def search(self, query, source, top_k):
        self.calls.append((query, source, top_k))
        if self._error:
            raise self._error
        return self._chunks


@pytest.fixture
def write_config(tmp_path):
    """임시 디렉터리에 ini 파일을 써서 경로를 돌려준다."""

    def _write(text: str = CONFIG_TEXT, name: str = "config_test.ini") -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return _write

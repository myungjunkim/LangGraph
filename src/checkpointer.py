"""SQLite 체크포인터 생성. 부모 디렉터리를 만든 뒤 컨텍스트 매니저를 그대로 돌려준다.

동기 SqliteSaver 는 async 메서드에서 NotImplementedError 를 던지고, AsyncSqliteSaver 는
메인 스레드의 동기 호출에서 InvalidStateError 를 던진다(2026-09-28 실측). 그래서 진입점별로 나눈다:
CLI(`graph.stream`)는 sqlite_saver, 웹(`graph.astream`)은 async_sqlite_saver.
"""
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver


def sqlite_saver(path: Path):
    """동기(CLI)용. `with sqlite_saver(p) as saver:` 로 쓴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver.from_conn_string(str(path))


def async_sqlite_saver(path: Path):
    """비동기(웹)용. `async with async_sqlite_saver(p) as saver:` 로 쓴다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return AsyncSqliteSaver.from_conn_string(str(path))

"""대화 정리 명령. 임시 SQLite 파일과 가짜 모델만 쓴다(RAG·Ollama 불필요)."""
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage, HumanMessage

import maintenance
from src.checkpointer import sqlite_saver
from src.maintenance import (ThreadSummary, collect_threads, format_report, purge,
                             select_expired)

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=timezone.utc)


def _summary(thread_id, days_ago, count=2, question="질문"):
    return ThreadSummary(thread_id, NOW - timedelta(days=days_ago), count, question)


def _seed(db, conversations):
    """conversations: {thread_id: [질문, ...]} — 가짜 모델로 대화를 만들어 DB 에 남긴다."""
    from src.agent import build_graph
    from src.tools import build_tools
    from test.conftest import FakeClient
    from test.test_agent import ScriptedChatModel

    with sqlite_saver(db) as saver:
        for thread_id, questions in conversations.items():
            responses = []
            for i, _ in enumerate(questions):
                if i:
                    responses.append(AIMessage("독립 질문"))      # 후속 턴의 rewrite 응답
                responses.append(AIMessage(f"{thread_id} 답변 {i + 1}"))
            model = ScriptedChatModel(responses=responses, received=[])
            graph = build_graph(model, build_tools(FakeClient([]), 6), saver)
            for question in questions:
                graph.invoke({"messages": [HumanMessage(question)]},
                             config={"configurable": {"thread_id": thread_id}})


# --- M1. 열거 ---

def test_collect_threads_reads_every_conversation(tmp_path):
    db = tmp_path / "db.sqlite"
    _seed(db, {"a": ["A 첫 질문", "A 둘째 질문"], "b": ["B 첫 질문"]})

    with sqlite_saver(db) as saver:
        threads = collect_threads(saver)

    by_id = {t.thread_id: t for t in threads}
    assert set(by_id) == {"a", "b"}
    assert by_id["a"].message_count == 4          # 질문2 + 답변2
    assert by_id["b"].message_count == 2
    assert by_id["a"].first_question == "A 첫 질문"
    assert by_id["b"].first_question == "B 첫 질문"
    assert all(t.last_active.tzinfo is not None for t in threads)


def test_collect_threads_sorts_oldest_first(tmp_path):
    db = tmp_path / "db.sqlite"
    _seed(db, {"first": ["1"], "second": ["2"], "third": ["3"]})

    with sqlite_saver(db) as saver:
        threads = collect_threads(saver)

    assert [t.thread_id for t in threads] == ["first", "second", "third"]
    assert [t.last_active for t in threads] == sorted(t.last_active for t in threads)


def test_collect_threads_uses_latest_checkpoint_of_each_thread(tmp_path):
    """여러 턴이 쌓여도 마지막 활동 시각과 최신 메시지 수를 쓴다."""
    db = tmp_path / "db.sqlite"
    _seed(db, {"a": ["첫 질문"]})
    with sqlite_saver(db) as saver:
        before = collect_threads(saver)[0]

    _seed(db, {"a": ["첫 질문", "둘째 질문"]})     # 같은 thread 에 턴 추가
    with sqlite_saver(db) as saver:
        after = collect_threads(saver)[0]

    assert after.last_active >= before.last_active
    assert after.message_count > before.message_count
    assert after.first_question == "첫 질문"


def test_collect_threads_on_empty_db(tmp_path):
    with sqlite_saver(tmp_path / "db.sqlite") as saver:
        assert collect_threads(saver) == []


def test_long_first_question_is_truncated_to_one_line(tmp_path):
    db = tmp_path / "db.sqlite"
    _seed(db, {"a": ["줄바꿈\n포함 " + "가" * 80]})

    with sqlite_saver(db) as saver:
        thread = collect_threads(saver)[0]

    assert "\n" not in thread.first_question
    assert len(thread.first_question) <= 40
    assert thread.first_question.endswith("…")


# --- M2. 선택 경계 ---

def test_select_expired_uses_strict_comparison():
    threads = [_summary("13일", 13), _summary("정확히14일", 14), _summary("15일", 15)]

    expired = select_expired(threads, 14, NOW)

    assert [t.thread_id for t in expired] == ["15일"]


def test_select_expired_with_zero_days_takes_everything_past():
    threads = [_summary("방금", 0), _summary("하루전", 1)]
    assert [t.thread_id for t in select_expired(threads, 0, NOW)] == ["하루전"]


def test_select_expired_on_empty_list():
    assert select_expired([], 14, NOW) == []


def test_select_expired_can_take_all():
    threads = [_summary("a", 30), _summary("b", 40)]
    assert len(select_expired(threads, 14, NOW)) == 2


def test_select_expired_can_take_none():
    threads = [_summary("a", 1), _summary("b", 2)]
    assert select_expired(threads, 14, NOW) == []


# --- purge ---

def test_purge_deletes_only_selected_threads(tmp_path):
    db = tmp_path / "db.sqlite"
    _seed(db, {"오래된": ["Q1"], "남길것": ["Q2"]})

    with sqlite_saver(db) as saver:
        threads = collect_threads(saver)
        target = [t for t in threads if t.thread_id == "오래된"]
        assert purge(saver, target) == 1

    with sqlite_saver(db) as saver:
        assert [t.thread_id for t in collect_threads(saver)] == ["남길것"]


def test_purge_of_nothing_is_a_no_op(tmp_path):
    db = tmp_path / "db.sqlite"
    _seed(db, {"a": ["Q"]})

    with sqlite_saver(db) as saver:
        assert purge(saver, []) == 0
        assert len(collect_threads(saver)) == 1


# --- M6. 출력 형식 ---

def test_format_report_lists_each_target():
    all_threads = [_summary("aaa", 20, 8, "메시지 등록 API 알려줘"), _summary("bbb", 1)]
    expired = [all_threads[0]]

    report = format_report(all_threads, expired, 14)

    lines = report.split("\n")
    assert lines[0] == "전체 대화 2개 · 정리 대상 1개 (마지막 활동 14일 경과 기준)"
    assert lines[1].startswith("  aaa")
    assert "메시지 8개" in lines[1]
    assert "메시지 등록 API 알려줘" in lines[1]
    assert (NOW - timedelta(days=20)).astimezone().strftime("%Y-%m-%d") in lines[1]
    assert len(lines) == 2                      # 대상이 아닌 대화는 나열하지 않는다


def test_format_report_without_targets_is_header_only():
    report = format_report([_summary("a", 1)], [], 14)
    assert report == "전체 대화 1개 · 정리 대상 0개 (마지막 활동 14일 경과 기준)"


def test_format_report_aligns_thread_ids():
    expired = [_summary("짧은", 20), _summary("아주-긴-thread-id", 30)]
    lines = format_report(expired, expired, 14).split("\n")[1:]
    assert len({line.index("메시지") for line in lines}) == 1


@pytest.mark.parametrize("days", [0, 7, 30])
def test_format_report_shows_given_threshold(days):
    assert f"마지막 활동 {days}일 경과 기준" in format_report([], [], days)


# --- M3~M5. 진입점 ---


def _settings_with_db(write_config, db):
    from src.config.settings import load_settings

    from test.conftest import CONFIG_TEXT

    return load_settings(write_config(CONFIG_TEXT.replace("db-path=data/checkpoints.sqlite",
                                                          f"db-path={db}")))


def _run(settings, out, **kwargs):
    options = {"older_than_days": 0, "apply": False, "do_vacuum": False}
    options.update(kwargs)
    return maintenance.run(settings, out=out.append, **options)


def _messages(db, thread_id):
    from src.agent import build_graph
    from src.tools import build_tools
    from test.conftest import FakeClient
    from test.test_agent import ScriptedChatModel

    with sqlite_saver(db) as saver:
        graph = build_graph(ScriptedChatModel(responses=[AIMessage("x")], received=[]),
                            build_tools(FakeClient([]), 6), saver)
        state = graph.get_state({"configurable": {"thread_id": thread_id}})
    return [m.content for m in state.values.get("messages", [])]


def test_default_mode_lists_without_deleting(tmp_path, write_config):
    db = tmp_path / "db.sqlite"
    _seed(db, {"a": ["A 질문"], "b": ["B 질문"], "c": ["C 질문"]})
    settings = _settings_with_db(write_config, db)
    out = []

    assert _run(settings, out) == 0

    text = "\n".join(out)
    assert "전체 대화 3개" in text
    assert "--apply 를 붙이면 삭제합니다." in text
    assert "삭제 완료" not in text
    with sqlite_saver(db) as saver:
        assert len(collect_threads(saver)) == 3
    # 대화 내용이 손상되지 않았다
    assert _messages(db, "a") == ["A 질문", "a 답변 1"]
    assert _messages(db, "c") == ["C 질문", "c 답변 1"]


def test_apply_deletes_only_expired(tmp_path, write_config, monkeypatch):
    db = tmp_path / "db.sqlite"
    _seed(db, {"오래된": ["옛 질문"], "최근": ["새 질문"]})
    settings = _settings_with_db(write_config, db)

    # '오래된' 만 대상이 되도록 선택 단계를 고정한다(실제 시각에 의존하지 않기 위해)
    real_select = maintenance.select_expired
    monkeypatch.setattr(maintenance, "select_expired",
                        lambda threads, days, now: [t for t in threads if t.thread_id == "오래된"])
    out = []
    assert _run(settings, out, apply=True, older_than_days=14) == 0
    monkeypatch.setattr(maintenance, "select_expired", real_select)

    assert "삭제 완료: 1개 대화 (남은 대화 1개)" in "\n".join(out)
    with sqlite_saver(db) as saver:
        assert [t.thread_id for t in collect_threads(saver)] == ["최근"]
    assert _messages(db, "최근") == ["새 질문", "최근 답변 1"]     # 남은 대화는 그대로 읽힌다
    assert _messages(db, "오래된") == []


def test_apply_with_nothing_to_delete(tmp_path, write_config):
    db = tmp_path / "db.sqlite"
    _seed(db, {"a": ["질문"]})
    settings = _settings_with_db(write_config, db)
    out = []

    assert _run(settings, out, apply=True, older_than_days=365) == 0

    assert "삭제 완료: 0개 대화 (남은 대화 1개)" in "\n".join(out)
    assert _messages(db, "a") == ["질문", "a 답변 1"]


def test_missing_db_file_is_not_an_error(tmp_path, write_config):
    db = tmp_path / "없음.sqlite"
    settings = _settings_with_db(write_config, db)
    out = []

    assert _run(settings, out, apply=True) == 0

    assert "체크포인트 DB 가 없습니다" in out[0]
    assert not db.exists()                      # 파일을 새로 만들지 않는다


def test_empty_db_is_not_an_error(tmp_path, write_config):
    db = tmp_path / "db.sqlite"
    with sqlite_saver(db):
        pass
    settings = _settings_with_db(write_config, db)
    out = []

    assert _run(settings, out) == 0

    assert "전체 대화 0개 · 정리 대상 0개" in out[0]


def test_vacuum_without_apply_is_skipped_with_notice(tmp_path, write_config):
    db = tmp_path / "db.sqlite"
    _seed(db, {"a": ["질문"]})
    settings = _settings_with_db(write_config, db)
    out = []

    assert _run(settings, out, do_vacuum=True) == 0

    text = "\n".join(out)
    assert "--vacuum 은 --apply 와 함께일 때만 동작합니다" in text
    assert "VACUUM 완료" not in text


def test_vacuum_after_apply_reports_sizes(tmp_path, write_config, monkeypatch):
    db = tmp_path / "db.sqlite"
    _seed(db, {f"t{i}": [f"질문 {i}"] for i in range(5)})
    settings = _settings_with_db(write_config, db)
    monkeypatch.setattr(maintenance, "select_expired", lambda threads, days, now: list(threads))
    out = []

    assert _run(settings, out, apply=True, do_vacuum=True) == 0

    text = "\n".join(out)
    assert "삭제 완료: 5개 대화 (남은 대화 0개)" in text
    assert "VACUUM 완료:" in text and "B → " in text


def test_main_returns_1_when_run_fails(monkeypatch, write_config, tmp_path):
    settings = _settings_with_db(write_config, tmp_path / "db.sqlite")
    monkeypatch.setattr(maintenance, "load_settings", lambda path: settings)
    monkeypatch.setattr(maintenance, "config_path_for", lambda profile: tmp_path / "ini")
    monkeypatch.setattr(maintenance, "run",
                        lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError("database is locked")))
    monkeypatch.setattr(sys, "argv", ["maintenance.py"])

    assert maintenance.main() == 1


def test_arg_parser_defaults_are_safe():
    args = maintenance.build_arg_parser().parse_args([])
    assert args.older_than == 14
    assert args.apply is False
    assert args.vacuum is False
    assert args.active_profile == "local"


def test_arg_parser_reads_options():
    args = maintenance.build_arg_parser().parse_args(["--older-than", "30", "--apply", "--vacuum"])
    assert (args.older_than, args.apply, args.vacuum) == (30, True, True)


# --- Validator 추가: 시각 파싱(명세 "타임존 없으면 UTC 간주") ---

def test_parse_ts_treats_naive_timestamp_as_utc():
    from src.maintenance import _parse_ts

    parsed = _parse_ts("2026-09-28T06:20:37.784670")

    assert parsed.tzinfo is timezone.utc
    assert parsed == datetime(2026, 9, 28, 6, 20, 37, 784670, tzinfo=timezone.utc)


def test_parse_ts_keeps_explicit_timezone():
    from src.maintenance import _parse_ts

    parsed = _parse_ts("2026-09-28T06:20:37.784670+09:00")

    assert parsed.utcoffset() == timedelta(hours=9)
    assert parsed == datetime(2026, 9, 27, 21, 20, 37, 784670, tzinfo=timezone.utc)


def test_parse_ts_accepts_datetime_object():
    from src.maintenance import _parse_ts

    assert _parse_ts(datetime(2026, 1, 1)) == datetime(2026, 1, 1, tzinfo=timezone.utc)


# --- Validator 추가: 미리보기 경계 ---

def test_preview_returns_blank_when_no_human_message():
    from src.maintenance import _preview

    assert _preview([AIMessage("답변만 있다")]) == (1, "")
    assert _preview([]) == (0, "")


def test_preview_keeps_exactly_40_characters_intact():
    from src.maintenance import PREVIEW_LIMIT, _preview

    assert PREVIEW_LIMIT == 40
    count, text = _preview([HumanMessage("가" * 40)])
    assert (count, text) == (1, "가" * 40)             # 정확히 40자는 자르지 않는다

    _, cut = _preview([HumanMessage("가" * 41)])
    assert cut == "가" * 39 + "…" and len(cut) == 40


def test_preview_picks_the_first_human_message_not_a_later_one():
    messages = [HumanMessage("첫 질문"), AIMessage("답"), HumanMessage("둘째 질문")]
    from src.maintenance import _preview

    assert _preview(messages) == (3, "첫 질문")


# --- Validator 추가: 진입점 경계(13/14/15일)를 실제 DB·실제 체크포인트 시각으로 ---

def _freeze_now(monkeypatch, moment):
    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return moment

    monkeypatch.setattr(maintenance, "datetime", _Frozen)


@pytest.mark.parametrize("days, deleted", [(13, 0), (14, 0), (15, 1)])
def test_entrypoint_boundary_deletes_only_past_the_threshold(tmp_path, write_config, monkeypatch,
                                                             days, deleted):
    db = tmp_path / "db.sqlite"
    _seed(db, {"경계": ["경계 질문"]})
    with sqlite_saver(db) as saver:
        last_active = collect_threads(saver)[0].last_active
    settings = _settings_with_db(write_config, db)
    _freeze_now(monkeypatch, last_active + timedelta(days=days))
    out = []

    assert _run(settings, out, apply=True, older_than_days=14) == 0

    assert f"삭제 완료: {deleted}개 대화" in "\n".join(out)
    with sqlite_saver(db) as saver:
        assert len(collect_threads(saver)) == 1 - deleted


def test_entrypoint_deletes_one_second_past_the_threshold(tmp_path, write_config, monkeypatch):
    """정확히 14일은 남고, 1초만 넘겨도 대상이 된다."""
    db = tmp_path / "db.sqlite"
    _seed(db, {"경계": ["경계 질문"]})
    with sqlite_saver(db) as saver:
        last_active = collect_threads(saver)[0].last_active
    settings = _settings_with_db(write_config, db)

    _freeze_now(monkeypatch, last_active + timedelta(days=14))
    kept = []
    assert _run(settings, kept, apply=True, older_than_days=14) == 0
    assert "삭제 완료: 0개 대화 (남은 대화 1개)" in "\n".join(kept)
    assert _messages(db, "경계") == ["경계 질문", "경계 답변 1"]

    _freeze_now(monkeypatch, last_active + timedelta(days=14, seconds=1))
    gone = []
    assert _run(settings, gone, apply=True, older_than_days=14) == 0
    assert "삭제 완료: 1개 대화 (남은 대화 0개)" in "\n".join(gone)
    assert _messages(db, "경계") == []


# --- Validator 추가: --apply 없이는 DB 파일 바이트가 한 글자도 바뀌지 않는다 ---

def test_default_mode_leaves_db_bytes_untouched(tmp_path, write_config, monkeypatch):
    """전부 만료 대상인 상황에서도 --apply 가 없으면 파일이 그대로다(민감도: apply=True 면 바뀐다)."""
    db = tmp_path / "db.sqlite"
    _seed(db, {"a": ["A 질문"], "b": ["B 질문"]})
    settings = _settings_with_db(write_config, db)
    monkeypatch.setattr(maintenance, "select_expired", lambda threads, days, now: list(threads))
    before = db.read_bytes()

    assert _run(settings, [], older_than_days=0) == 0

    assert db.read_bytes() == before
    assert _messages(db, "a") == ["A 질문", "a 답변 1"]
    assert _messages(db, "b") == ["B 질문", "b 답변 1"]

    assert _run(settings, [], apply=True, older_than_days=0) == 0       # 민감도 대조군
    assert db.read_bytes() != before
    assert _messages(db, "a") == []


# --- Validator 추가: VACUUM 실패는 경고만 하고 종료 코드 0 (명세 "설계" 마지막 항목) ---

class _FailingSqlite:
    Error = sqlite3.Error

    @staticmethod
    def connect(path):
        raise sqlite3.OperationalError("database is locked")


def test_vacuum_failure_warns_and_keeps_exit_code_zero(tmp_path, write_config, monkeypatch):
    db = tmp_path / "db.sqlite"
    _seed(db, {"a": ["질문"]})
    settings = _settings_with_db(write_config, db)
    monkeypatch.setattr(maintenance, "select_expired", lambda threads, days, now: list(threads))
    monkeypatch.setattr(maintenance, "sqlite3", _FailingSqlite)
    out = []

    assert _run(settings, out, apply=True, do_vacuum=True) == 0          # 삭제는 성공했으므로 0

    text = "\n".join(out)
    assert "삭제 완료: 1개 대화" in text
    assert "VACUUM 건너뜀" in text and "database is locked" in text
    assert "VACUUM 완료" not in text


def test_vacuum_helper_on_a_non_database_file_does_not_raise(tmp_path):
    broken = tmp_path / "broken.sqlite"
    broken.write_bytes(b"not a database at all")
    out = []

    maintenance.vacuum(broken, out=out.append)

    assert out and out[0].startswith("VACUUM 건너뜀")


# --- Validator 추가: main() 정상 경로 ---

def test_main_returns_0_and_never_applies_without_the_flag(tmp_path, write_config, monkeypatch):
    db = tmp_path / "db.sqlite"
    _seed(db, {"a": ["A 질문"]})
    settings = _settings_with_db(write_config, db)
    monkeypatch.setattr(maintenance, "load_settings", lambda path: settings)
    monkeypatch.setattr(maintenance, "config_path_for", lambda profile: tmp_path / "ini")
    monkeypatch.setattr(sys, "argv", ["maintenance.py", "--active-profile=local", "--older-than", "0"])
    before = db.read_bytes()

    assert maintenance.main() == 0

    assert db.read_bytes() == before
    assert _messages(db, "a") == ["A 질문", "a 답변 1"]

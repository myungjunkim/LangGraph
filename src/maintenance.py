"""체크포인트 DB 정리 도구. 대화를 훑어 오래된 것을 골라내고, 요청하면 삭제한다.

삭제는 체크포인터의 공개 API(`delete_thread`)로만 한다. 내부 테이블에 직접 SQL 을 쓰지 않는다
(패키지 스키마에 결합되면 업그레이드 때 조용히 깨진다).
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

PREVIEW_LIMIT = 40


@dataclass(frozen=True)
class ThreadSummary:
    thread_id: str
    last_active: datetime
    message_count: int
    first_question: str


def _parse_ts(raw) -> datetime:
    """체크포인트 시각(ISO 문자열)을 tz-aware datetime 으로 바꾼다.

    설치 버전은 항상 `+00:00` 을 붙여 주지만(2026-09-28 실측), 타임존이 없으면 UTC 로 간주한다.
    """
    if isinstance(raw, datetime):
        parsed = raw
    else:
        parsed = datetime.fromisoformat(str(raw))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _preview(messages) -> tuple[int, str]:
    """메시지 개수와 첫 질문(한 줄, 40자 이내)."""
    first = next((m for m in messages if type(m).__name__ == "HumanMessage"), None)
    text = " ".join(str(getattr(first, "content", "")).split()) if first else ""
    if len(text) > PREVIEW_LIMIT:
        text = text[:PREVIEW_LIMIT - 1] + "…"
    return len(messages), text


def collect_threads(saver) -> list[ThreadSummary]:
    """DB 의 모든 대화를 마지막 활동이 오래된 순으로 모은다."""
    latest: dict[str, tuple[datetime, object]] = {}
    for item in saver.list(None):
        thread_id = item.config["configurable"]["thread_id"]
        ts = _parse_ts(item.checkpoint["ts"])
        # thread 간 정렬은 보장되지 않으므로 thread 별 최신 체크포인트를 직접 고른다
        if thread_id not in latest or ts > latest[thread_id][0]:
            latest[thread_id] = (ts, item.checkpoint)

    summaries = []
    for thread_id, (ts, checkpoint) in latest.items():
        count, question = _preview(checkpoint.get("channel_values", {}).get("messages", []))
        summaries.append(ThreadSummary(thread_id, ts, count, question))
    return sorted(summaries, key=lambda s: (s.last_active, s.thread_id))


def select_expired(threads: list[ThreadSummary], older_than_days: int,
                   now: datetime) -> list[ThreadSummary]:
    """마지막 활동 이후 경과가 기준일을 '넘긴' 대화만 고른다(정확히 N일은 대상 아님)."""
    cutoff = timedelta(days=older_than_days)
    return [t for t in threads if now - t.last_active > cutoff]


def purge(saver, threads: list[ThreadSummary]) -> int:
    for thread in threads:
        saver.delete_thread(thread.thread_id)
    return len(threads)


def format_report(all_threads: list[ThreadSummary], expired: list[ThreadSummary],
                  older_than_days: int) -> str:
    """목록 부분(마지막 안내 줄 제외)."""
    header = (f"전체 대화 {len(all_threads)}개 · 정리 대상 {len(expired)}개 "
              f"(마지막 활동 {older_than_days}일 경과 기준)")
    if not expired:
        return header
    width = max(len(t.thread_id) for t in expired)
    lines = [f"  {t.thread_id.ljust(width)}  {t.last_active.astimezone().strftime('%Y-%m-%d')}  "
             f"메시지 {t.message_count}개  {t.first_question}".rstrip()
             for t in expired]
    return "\n".join([header, *lines])

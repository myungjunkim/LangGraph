"""체크포인트 DB 정리 명령.

python maintenance.py --active-profile=local                  # 목록만(삭제 없음)
python maintenance.py --active-profile=local --apply          # 실제 삭제
python maintenance.py --active-profile=local --older-than 30 --apply --vacuum
"""
import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

from src.checkpointer import sqlite_saver
from src.config.settings import config_path_for, load_settings
from src.maintenance import collect_threads, format_report, purge, select_expired


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="오래된 대화 정리(기본은 목록만 출력)")
    parser.add_argument("--active-profile", default="local", help="resources/config_{profile}.ini 프로파일 이름")
    parser.add_argument("--older-than", type=int, default=14,
                        help="마지막 활동 이후 경과 일수(기본 14). 이 값을 넘긴 대화만 대상")
    parser.add_argument("--apply", action="store_true", help="붙였을 때만 실제로 삭제한다")
    parser.add_argument("--vacuum", action="store_true", help="--apply 와 함께 쓰면 삭제 후 파일 크기를 회수한다")
    return parser


def vacuum(path: Path, out=print) -> None:
    """saver 의 커넥션과 섞이지 않도록 with 블록을 빠져나온 뒤 별도 연결로 실행한다."""
    before = path.stat().st_size
    try:
        with sqlite3.connect(path) as connection:
            connection.execute("VACUUM")
    except sqlite3.Error as e:
        # 다른 프로세스가 DB 를 잠그고 있는 경우 등. 삭제 자체는 이미 성공했다
        out(f"VACUUM 건너뜀: {e}")
        return
    out(f"VACUUM 완료: {before:,}B → {path.stat().st_size:,}B")


def run(settings, older_than_days: int, apply: bool, do_vacuum: bool, out=print) -> int:
    db = settings.checkpoint_db
    if not db.is_file():
        out(f"체크포인트 DB 가 없습니다: {db} (정리할 대화가 없습니다)")
        return 0

    with sqlite_saver(db) as saver:
        threads = collect_threads(saver)
        expired = select_expired(threads, older_than_days, datetime.now(timezone.utc))
        out(format_report(threads, expired, older_than_days))

        if not apply:
            if do_vacuum:
                out("--vacuum 은 --apply 와 함께일 때만 동작합니다. 이번에는 건너뜁니다.")
            if expired:
                out("--apply 를 붙이면 삭제합니다.")
            return 0

        deleted = purge(saver, expired)
        out(f"삭제 완료: {deleted}개 대화 (남은 대화 {len(threads) - deleted}개)")

    if do_vacuum:
        vacuum(db, out=out)
    return 0


def main() -> int:
    args = build_arg_parser().parse_args()
    settings = load_settings(config_path_for(args.active_profile))
    try:
        return run(settings, args.older_than, args.apply, args.vacuum)
    except Exception as e:                       # 삭제 도중 실패는 조용히 넘기지 않는다
        print(f"[오류] {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())

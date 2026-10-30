from collections import defaultdict
from datetime import date, timedelta

import duckdb
from fastapi import APIRouter, HTTPException, Request

from app import exports
from app.auth import AuthenticatedUser
from app.practice import local_day

router = APIRouter(prefix="/api/v1", tags=["个人学习汇总"])
COUNT_FIELDS = ("attempts", "active_days", "correct", "review_attempts", "on_time_reviews")


def query_counts(db, group=None):
    # 分组字段只来自固定代码，不接收客户端 SQL。统计只读已核对的 Parquet 表。
    prefix = f"{group}, " if group else ""
    suffix = f" GROUP BY {group} ORDER BY {group}" if group else ""
    return db.execute(f"""SELECT {prefix}count(*), count(DISTINCT local_date),
        count(*) FILTER (WHERE correct), count(*) FILTER (WHERE source='review'),
        count(*) FILTER (WHERE source='review' AND local_date <= scheduled_due_date)
        FROM actual_attempts{suffix}""").fetchall()


def raw_counts(attempts):
    # 直接用 MySQL 原始记录独立重算，避免图表与总数都沿用同一个错误聚合。
    counts = defaultdict(lambda: [0, set(), 0, 0, 0])
    counts[("overall", "all")]
    for row in attempts:
        if row["score"] not in (0, 100):
            raise ValueError("存在当前单选统计口径不支持的成绩")
        review = row["source"] == "review"
        on_time = review and row["local_date"] <= row["scheduled_due_date"]
        for key in (
            ("overall", "all"),
            ("day", row["local_date"]),
            ("domain", row["domain_code"]),
            ("source", row["source"]),
        ):
            item = counts[key]
            item[0] += 1
            item[1].add(row["local_date"])
            item[2] += row["score"] == 100
            item[3] += review
            item[4] += on_time
    return {key: (v[0], len(v[1]), v[2], v[3], v[4]) for key, v in counts.items()}


def percentage(numerator, denominator):
    return round(numerator * 100 / denominator, 2) if denominator else None


def metrics(counts, days):
    result = dict(zip(COUNT_FIELDS, counts, strict=True))
    result.update(
        {
            "daily_attempts": result["attempts"] - result["review_attempts"],
            "late_reviews": result["review_attempts"] - result["on_time_reviews"],
            "learning_frequency": percentage(result["active_days"], days),
            "average_daily_attempts": round(result["attempts"] / days, 2),
            "accuracy": percentage(result["correct"], result["attempts"]),
            "completed_review_on_time_rate": percentage(
                result["on_time_reviews"], result["review_attempts"]
            ),
        }
    )
    return result


def summarize(engine, user_id, start, end, today):
    options = exports.ExportRequest(start_date=start, end_date=end)
    with exports.verified_snapshot(engine, user_id, options) as (info, db, report, _, attempts):
        overall = query_counts(db)[0]
        daily = query_counts(db, "local_date")
        domains = query_counts(db, "domain_code")
        sources = query_counts(db, "source")
        actual = {("overall", "all"): overall}
        for kind, rows in (("day", daily), ("domain", domains), ("source", sources)):
            actual.update({(kind, row[0]): tuple(row[1:]) for row in rows})
        if actual != raw_counts(attempts) or overall[1] != report["counts"]["check_ins"]:
            raise ValueError("学习汇总与原始记录不一致")
        days = (end - start).days + 1
        by_day = {row[0]: row[1:] for row in daily}
        by_source = {row[0]: row[1:] for row in sources}
        empty = (0, 0, 0, 0, 0)
        return {
            "metric_version": "learning-summary-v1",
            "snapshot_at_utc": info["captured_at"].isoformat() + "Z",
            "timezone": info["timezone"],
            "start_date": start,
            "end_date": end,
            "calendar_days": days,
            "includes_today": end == today,
            "scope": "current_user_all_domains",
            "verification": {"parquet_mysql": report["status"], "summary_raw_records": "passed"},
            "summary": metrics(overall, days),
            "by_source": [
                {"source": source, **metrics(by_source.get(source, empty), days)}
                for source in ("daily", "review")
            ],
            "by_domain": [{"domain_code": row[0], **metrics(row[1:], days)} for row in domains],
            "daily": [
                {"date": day, **metrics(by_day.get(day, empty), 1)}
                for day in (start + timedelta(days=i) for i in range(days))
            ],
        }


@router.get("/analytics/me")
def personal_summary(request: Request, user: AuthenticatedUser, start_date: date, end_date: date):
    # 明确拒绝身份选择器与不支持的筛选，避免用户误认为它们已经生效。
    if set(request.query_params) - {"start_date", "end_date"}:
        raise HTTPException(422, "仅支持本人全部领域的开始和结束日期")
    today = local_day(request.app.state.clock(), user)
    if not 0 <= (end_date - start_date).days < 366 or end_date > today:
        raise HTTPException(422, "请选择不超过今天、首尾包含且长度为 1 至 366 天的日期范围")
    if not exports.EXPORT_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "数据核对任务较多，请稍后重试", headers={"Retry-After": "5"})
    try:
        try:
            return summarize(request.app.state.db_engine, user.id, start_date, end_date, today)
        except (duckdb.Error, ValueError, OSError):
            raise HTTPException(503, "汇总或一致性核对失败，请稍后重试") from None
    finally:
        exports.EXPORT_SLOTS.release()

from datetime import date, datetime, timedelta
from threading import BoundedSemaphore

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from test_exports import export_directory as export_directory
from test_practice import get_daily, register, submission
from test_reviews import prepare_review, review_payload

from app import analytics, exports
from app.models import Attempt, QuestionVersion, ReviewPlan, User

pytestmark = pytest.mark.integration


def summary(client, start="2026-10-27", end="2026-10-30"):
    return client.get("/api/v1/analytics/me", params={"start_date": start, "end_date": end})


def test_summary_weighted_metrics_history_and_isolation(practice_env, export_directory):
    engine, app, clock = practice_env
    with TestClient(app) as client, TestClient(app) as other:
        register(client)
        register(other, "other_statistics")
        question, right, original = prepare_review(client, engine, clock)
        daily = get_daily(client, "english")
        with Session(engine) as session:
            english = session.get(QuestionVersion, daily["assignment"]["question_version_id"])
            english_id, english_answer = english.question_id, english.correct_answer
        payload = submission(daily, english_answer)
        assert client.post("/api/v1/attempts", json=payload).status_code == 200
        assert client.post("/api/v1/attempts", json=payload).status_code == 200
        assert other.post("/api/v1/attempts", json=submission(get_daily(other))).status_code == 200
        with Session(engine) as session, session.begin():
            user = session.scalar(select(User).where(User.username == "learner_a"))
            user.is_admin = True
            old = session.get(
                QuestionVersion, session.get(Attempt, original["id"]).question_version_id
            )
            session.add(
                QuestionVersion(
                    question_id=question,
                    version=2,
                    stem="新的统计验收版本",
                    options=old.options,
                    correct_answer=next(a for a in "ABCD" if a != right),
                    explanation="新版答案不同",
                    source="统计测试",
                    published_at=clock["now"],
                )
            )
        clock["now"] += timedelta(days=1)
        payload = review_payload(client, question, right)
        assert client.post(f"/api/v1/reviews/{question}/attempts", json=payload).status_code == 200
        assert client.post(f"/api/v1/reviews/{question}/attempts", json=payload).status_code == 200
        clock["now"] += timedelta(days=2)
        assert (
            client.post(
                f"/api/v1/reviews/{question}/attempts",
                json=review_payload(client, question, right),
            ).status_code
            == 200
        )
        assert (
            client.post(
                f"/api/v1/reviews/{english_id}/attempts",
                json=review_payload(
                    client, english_id, next(a for a in "ABCD" if a != english_answer)
                ),
            ).status_code
            == 200
        )
        # 当前计划改变不能改写当次原定日期，也不能成为按期率的分母。
        with Session(engine) as session, session.begin():
            user_id = session.scalar(select(User.id).where(User.username == "learner_a"))
            for plan in session.scalars(select(ReviewPlan).where(ReviewPlan.user_id == user_id)):
                plan.due_date = date(2099, 1, 1)
        response = summary(client)
        assert response.status_code == 200, response.text
        data = response.json()
        assert response.headers["cache-control"] == "no-store"
        assert data["scope"] == "current_user_all_domains"
        assert data["calendar_days"] == 4 and data["includes_today"] is True
        assert data["verification"] == {"parquet_mysql": "passed", "summary_raw_records": "passed"}
        assert data["summary"] == {
            "attempts": 5,
            "active_days": 3,
            "correct": 3,
            "daily_attempts": 2,
            "review_attempts": 3,
            "on_time_reviews": 1,
            "late_reviews": 2,
            "learning_frequency": 75.0,
            "average_daily_attempts": 1.25,
            "accuracy": 60.0,
            "completed_review_on_time_rate": 33.33,
        }
        assert [d["attempts"] for d in data["daily"]] == [2, 1, 0, 2]
        assert [d["correct"] for d in data["daily"]] == [1, 1, 0, 1]
        assert [d["on_time_reviews"] for d in data["daily"]] == [0, 1, 0, 0]
        assert [s["accuracy"] for s in data["by_source"]] == [50.0, 66.67]
        domains = {d["domain_code"]: d for d in data["by_domain"]}
        assert domains["computer"]["accuracy"] == 66.67
        assert domains["english"]["accuracy"] == 50.0
        assert sum(d["active_days"] for d in domains.values()) == 5
        for private in (
            "learner_a",
            "other_statistics",
            "answer",
            "attempt_ref",
            "user_id",
            "新版答案不同",
        ):
            assert private not in response.text
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count()).select_from(Attempt).where(Attempt.user_id == user_id)
                )
                == 5
            )
        # 原定日期、前次依据在范围外，仍按实际复习日归属，不能遗漏逾期复习。
        last = summary(client, "2026-10-30", "2026-10-30").json()
        assert last["summary"]["review_attempts"] == last["summary"]["late_reviews"] == 2
        assert last["summary"]["completed_review_on_time_rate"] == 0
        past = summary(client, "2026-10-27", "2026-10-27").json()
        assert past["summary"]["accuracy"] == 50
        assert past["summary"]["completed_review_on_time_rate"] is None
        assert past["includes_today"] is False


def test_summary_empty_and_auth_scope(practice_env, export_directory):
    _, app, _ = practice_env
    with TestClient(app) as client:
        assert summary(client, end="2026-10-27").status_code == 401
        register(client)
        for extra in ({"user_id": 2}, {"username": "someone"}, {"domain_code": "english"}):
            assert (
                client.get(
                    "/api/v1/analytics/me",
                    params={
                        "start_date": "2026-10-25",
                        "end_date": "2026-10-27",
                        **extra,
                    },
                ).status_code
                == 422
            )
        response = summary(client, "2026-10-25", "2026-10-27")
        assert response.status_code == 200
        data = response.json()
        assert data["summary"]["accuracy"] is None
        assert data["summary"]["completed_review_on_time_rate"] is None
        assert data["summary"]["learning_frequency"] == 0
        assert data["summary"]["attempts"] == 0
        assert data["by_domain"] == [] and len(data["by_source"]) == 2
        assert len(data["daily"]) == 3 and all(d["accuracy"] is None for d in data["daily"])
        assert summary(client, "2025-10-27", "2026-10-27").status_code == 200  # 首尾 366 天。


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-28", "2026-10-27"),
        ("2025-10-26", "2026-10-27"),
        ("2026-10-27", "2026-10-28"),
        ("bad", "2026-10-27"),
    ],
)
def test_summary_invalid_dates(practice_env, start, end):
    _, app, _ = practice_env
    with TestClient(app) as client:
        register(client)
        assert summary(client, start, end).status_code == 422


def test_summary_uses_stored_local_date_and_counts_incorrect(practice_env, export_directory):
    engine, app, clock = practice_env
    clock["now"] = datetime(2026, 10, 26, 16, 1)
    with TestClient(app) as client:
        register(client)
        prepare_review(client, engine, clock)
        data = summary(client, "2026-10-27", "2026-10-27").json()
        assert data["summary"]["attempts"] == 1
        assert data["summary"]["learning_frequency"] == 100
        assert data["summary"]["accuracy"] == 0
        assert data["summary"]["completed_review_on_time_rate"] is None
        earlier = summary(client, "2026-10-26", "2026-10-26").json()
        assert earlier["summary"]["attempts"] == 0


def test_summary_rejects_wrong_aggregate_and_releases_slot(
    practice_env, export_directory, monkeypatch
):
    engine, app, clock = practice_env
    original = analytics.query_counts

    def incorrect(db, group=None):
        rows = original(db, group)
        if group is None:
            row = list(rows[0])
            row[4] = 1  # 没有复习却返回 1 次按期；原始记录核对必须阻断。
            return [tuple(row)]
        return rows

    with TestClient(app) as client:
        register(client)
        prepare_review(client, engine, clock)
        with monkeypatch.context() as patch:
            patch.setattr(analytics, "query_counts", incorrect)
            response = summary(client, end="2026-10-27")
            assert response.status_code == 503
            assert "summary" not in response.json()
            assert list(exports.EXPORT_ROOT.iterdir()) == []
        assert summary(client, end="2026-10-27").status_code == 200


def test_summary_parquet_failure_limits_and_shared_concurrency(
    practice_env, export_directory, monkeypatch
):
    engine, app, clock = practice_env
    with TestClient(app) as client:
        register(client)
        prepare_review(client, engine, clock)
        with monkeypatch.context() as patch:
            patch.setattr(exports, "MAX_ATTEMPTS", 0)
            assert summary(client, end="2026-10-27").status_code == 413
        with monkeypatch.context() as patch:
            patch.setattr(exports, "EXPORT_SLOTS", BoundedSemaphore(0))
            assert summary(client, end="2026-10-27").status_code == 429

        def fail(*args):
            raise OSError("private-record-do-not-echo")

        with monkeypatch.context() as patch:
            patch.setattr(exports, "write_parquet", fail)
            response = summary(client, end="2026-10-27")
            assert response.status_code == 503 and "private-record" not in response.text
            assert list(exports.EXPORT_ROOT.iterdir()) == []
        assert summary(client, end="2026-10-27").status_code == 200


def test_summary_concurrent_submission_same_snapshot(practice_env, export_directory):
    engine, app, clock = practice_env
    with TestClient(app) as reader, TestClient(app) as writer:
        register(reader)
        writer.cookies.update(reader.cookies)
        writer.headers["X-CSRF-Token"] = reader.headers["X-CSRF-Token"]
        prepare_review(reader, engine, clock)
        daily = get_daily(writer, "english")
        inserted = False

        def concurrent(connection, cursor, statement, params, context, many):
            nonlocal inserted
            if "UTC_TIMESTAMP(6)" in statement and not inserted:
                inserted = True
                assert writer.post("/api/v1/attempts", json=submission(daily)).status_code == 200

        event.listen(engine, "after_cursor_execute", concurrent)
        try:
            response = summary(reader, end="2026-10-27")
            assert response.status_code == 200, response.text
        finally:
            event.remove(engine, "after_cursor_execute", concurrent)
        assert inserted and response.json()["summary"]["attempts"] == 1
        latest = summary(reader, end="2026-10-27").json()
        assert latest["summary"]["attempts"] == 2 and latest["summary"]["active_days"] == 1

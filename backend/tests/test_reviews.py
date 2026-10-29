from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session
from test_practice import get_daily, register, submission

from app.models import Attempt, CheckIn, DailyAssignment, Question, QuestionVersion, ReviewPlan

pytestmark = pytest.mark.integration


def prepare_review(client, engine, clock, domain="computer"):
    daily = get_daily(client, domain)
    with Session(engine) as session:
        version = session.get(QuestionVersion, daily["assignment"]["question_version_id"])
        question_id, right = version.question_id, version.correct_answer
    wrong = next(answer for answer in "ABCD" if answer != right)
    result = client.post("/api/v1/attempts", json=submission(daily, wrong))
    assert result.status_code == 200, result.text
    return question_id, right, result.json()


def review_payload(client, question_id, answer):
    detail = client.get(f"/api/v1/reviews/{question_id}")
    assert detail.status_code == 200, detail.text
    return {key: detail.json()[key] for key in ("question_version_id", "basis_attempt_id")} | {
        "answer": answer,
        "idempotency_key": str(uuid4()),
    }


def test_review_complete_flow_version_and_history(practice_env):
    engine, app, clock = practice_env
    with TestClient(app) as client, TestClient(app) as other:
        register(client)
        register(other, "other_learner")
        question_id, right, original = prepare_review(client, engine, clock)
        endpoint = f"/api/v1/reviews/{question_id}"
        data = review_payload(client, question_id, right)
        assert client.get("/api/v1/reviews?domain_code=computer").json()["total"] == 0
        assert client.post(endpoint + "/attempts", json=data).status_code == 409
        assert other.get(endpoint).status_code == 404
        assert other.post(endpoint + "/attempts", json=data).status_code == 404
        assert other.get("/api/v1/history?domain_code=computer").json()["items"] == []
        with Session(engine) as session, session.begin():
            old = session.get(QuestionVersion, data["question_version_id"])
            session.add(
                QuestionVersion(
                    question_id=question_id,
                    version=2,
                    stem="新的题干",
                    options=old.options,
                    correct_answer=right,
                    explanation="新版解析",
                    source="测试",
                    published_at=clock["now"],
                )
            )
        clock["now"] += timedelta(days=1)
        queue = client.get("/api/v1/reviews?domain_code=computer").json()
        assert queue["total"] == 1 and queue["items"][0]["question_id"] == question_id
        detail = client.get(endpoint).json()
        assert detail["can_submit"] and detail["question_version_id"] == data["question_version_id"]
        assert "correct_answer" not in str(detail) and "explanation" not in str(detail)
        response = client.post(endpoint + "/attempts", json=data)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["id"] != original["id"] and saved["score"] == 100
        assert saved["review_due_date"] == "2026-10-29"
        assert client.get(endpoint).json()["result"] == saved
        assert client.get("/api/v1/reviews?domain_code=computer").json()["total"] == 0
        assert client.get(f"/api/v1/attempts/{original['id']}").json() == original
        assert client.post(endpoint + "/attempts", json=data).json() == saved
        assert (
            client.post(
                endpoint + "/attempts", json={**data, "idempotency_key": str(uuid4())}
            ).status_code
            == 409
        )
        assert other.get(f"/api/v1/history/{saved['id']}").status_code == 404
        history = client.get("/api/v1/history?domain_code=computer&limit=1").json()
        assert history["items"][0]["id"] == saved["id"] and history["next_cursor"] == saved["id"]
        next_page = client.get(
            f"/api/v1/history?domain_code=computer&before_id={saved['id']}"
        ).json()
        assert [item["id"] for item in next_page["items"]] == [original["id"]]
        historical = client.get(f"/api/v1/history/{original['id']}").json()
        assert historical["version"] == 1 and historical["result"] == original
        assert historical["current_due_date"] == saved["review_due_date"]
        assert (
            client.get(f"/api/v1/history/{saved['id']}").json()["scheduled_due_date"]
            == "2026-10-28"
        )
        assert (
            client.get(
                "/api/v1/history?domain_code=computer&source=review&start_date=2026-10-28&end_date=2026-10-28"
            ).json()["items"][0]["id"]
            == saved["id"]
        )
        assert (
            len(client.get("/api/v1/history?domain_code=computer&source=daily").json()["items"])
            == 1
        )
        assert client.get("/api/v1/history?domain_code=english").json()["items"] == []
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Attempt)) == 2
            assert session.scalar(select(func.count()).select_from(CheckIn)) == 2
            assert session.scalar(select(func.count()).select_from(ReviewPlan)) == 1
            attempt = session.get(Attempt, saved["id"])
            assert (
                attempt.daily_assignment_id is None
                and attempt.review_basis_attempt_id == original["id"]
            )


def test_review_midnight_retry_wrong_and_stale_plan(practice_env):
    engine, app, clock = practice_env
    with TestClient(app) as client:
        register(client)
        question_id, right, _ = prepare_review(client, engine, clock)
        clock["now"] = datetime(2026, 10, 28, 15, 59, 59)
        endpoint = f"/api/v1/reviews/{question_id}/attempts"
        data = review_payload(client, question_id, next(a for a in "ABCD" if a != right))
        saved = client.post(endpoint, json=data).json()
        assert saved["check_in_date"] == "2026-10-28" and saved["review_due_date"] == "2026-10-29"
        clock["now"] += timedelta(seconds=2)
        assert client.post(endpoint, json=data).json() == saved
        assert (
            client.post(endpoint, json={**data, "idempotency_key": str(uuid4())}).status_code == 409
        )
        fresh = review_payload(client, question_id, right)
        second = client.post(endpoint, json=fresh)
        assert second.status_code == 200 and second.json()["check_in_date"] == "2026-10-29"
        assert second.json()["review_interval_days"] == 1


def test_review_concurrent_retries_and_new_keys(practice_env):
    engine, app, clock = practice_env
    with TestClient(app) as client:
        register(client)
        question_id, right, _ = prepare_review(client, engine, clock)
        clock["now"] += timedelta(days=1)
        endpoint = f"/api/v1/reviews/{question_id}/attempts"
        data = review_payload(client, question_id, right)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: client.post(endpoint, json=data), range(2)))
        assert [r.status_code for r in responses] == [200, 200]
        assert responses[0].json() == responses[1].json()
        clock["now"] += timedelta(days=1)
        data = review_payload(client, question_id, right)
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda _: client.post(endpoint, json={**data, "idempotency_key": str(uuid4())}),
                    range(2),
                )
            )
        assert sorted(r.status_code for r in responses) == [200, 409]
        success = next(r.json() for r in responses if r.status_code == 200)
        assert success["review_interval_days"] == 3
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Attempt)) == 3
            assert session.scalar(select(func.count()).select_from(CheckIn)) == 3


def test_review_failure_rolls_back_plan_checkin_and_attempt(practice_env):
    engine, app, clock = practice_env
    with TestClient(app) as client:
        register(client)
        question_id, right, original = prepare_review(client, engine, clock)
        clock["now"] += timedelta(days=1)
        data = review_payload(client, question_id, right)

        def fail_update(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("UPDATE review_plans"):
                raise OperationalError("injected_failure", {}, Exception("test"))

        event.listen(engine, "before_cursor_execute", fail_update)
        try:
            assert (
                client.post(f"/api/v1/reviews/{question_id}/attempts", json=data).status_code == 503
            )
        finally:
            event.remove(engine, "before_cursor_execute", fail_update)
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Attempt)) == 1
            assert session.scalar(select(func.count()).select_from(CheckIn)) == 1
            assert session.scalar(select(ReviewPlan.latest_attempt_id)) == original["id"]
        assert client.post(f"/api/v1/reviews/{question_id}/attempts", json=data).status_code == 200


def test_review_validation_csrf_and_source_collision(practice_env):
    engine, app, clock = practice_env
    with TestClient(app) as client, TestClient(app) as anonymous:
        assert anonymous.get("/api/v1/reviews?domain_code=computer").status_code == 401
        assert anonymous.get("/api/v1/history?domain_code=computer").status_code == 401
        register(client)
        question_id, right, original = prepare_review(client, engine, clock)
        clock["now"] += timedelta(days=1)
        data = review_payload(client, question_id, right)
        endpoint = f"/api/v1/reviews/{question_id}/attempts"
        csrf = client.headers.pop("X-CSRF-Token")
        assert client.post(endpoint, json=data).status_code == 403
        client.headers["X-CSRF-Token"] = csrf
        with Session(engine) as session:
            old_key = session.get(Attempt, original["id"]).idempotency_key
        for changes, status in [
            ({"answer": "Z"}, 422),
            ({"user_id": 10}, 422),
            ({"question_version_id": 99999}, 409),
            ({"basis_attempt_id": 99999}, 409),
            ({"idempotency_key": old_key}, 409),
        ]:
            assert client.post(endpoint, json=data | changes).status_code == status
        saved = client.post(endpoint, json=data)
        assert saved.status_code == 200
        assert (
            client.post(
                endpoint, json=data | {"answer": next(a for a in "ABCD" if a != right)}
            ).status_code
            == 409
        )
        daily = get_daily(client)
        assert (
            client.post(
                "/api/v1/attempts",
                json=submission(daily) | {"idempotency_key": data["idempotency_key"]},
            ).status_code
            == 409
        )
        for params in ("limit=0", "source=bad", "start_date=2026-10-29&end_date=2026-10-28"):
            assert client.get("/api/v1/history?domain_code=computer&" + params).status_code == 422
        assert client.get("/api/v1/reviews?domain_code=computer&offset=-1").status_code == 422


def test_due_sorting_filter_and_pagination(practice_env):
    engine, app, clock = practice_env
    with TestClient(app) as client:
        register(client)
        first, _, _ = prepare_review(client, engine, clock)
        english, _, _ = prepare_review(client, engine, clock, "english")
        clock["now"] += timedelta(days=1)
        second, _, _ = prepare_review(client, engine, clock)
        clock["now"] += timedelta(days=2)
        page = client.get("/api/v1/reviews?domain_code=computer&limit=1").json()
        assert page["total"] == 2 and page["items"][0]["question_id"] == first
        assert page["items"][0]["overdue_days"] == 2
        assert (
            client.get("/api/v1/reviews?domain_code=computer&limit=1&offset=1").json()["items"][0][
                "question_id"
            ]
            == second
        )
        assert (
            client.get("/api/v1/reviews?domain_code=english").json()["items"][0]["question_id"]
            == english
        )


@pytest.mark.parametrize("correct", [True, False])
def test_daily_after_review_does_not_double_advance(practice_env, correct):
    engine, app, clock = practice_env
    with TestClient(app) as client:
        register(client)
        question_id, right, _ = prepare_review(client, engine, clock)
        clock["now"] += timedelta(days=1)
        data = review_payload(client, question_id, right)
        first = client.post(f"/api/v1/reviews/{question_id}/attempts", json=data).json()
        with Session(engine) as session, session.begin():
            version = session.get(QuestionVersion, data["question_version_id"])
            track_id = session.get(Question, version.question_id).track_id
            assignment = DailyAssignment(
                track_id=track_id, local_date=clock["now"].date(), question_version_id=version.id
            )
            session.add(assignment)
        daily = get_daily(client)
        answer = right if correct else next(a for a in "ABCD" if a != right)
        saved = client.post("/api/v1/attempts", json=submission(daily, answer)).json()
        assert saved["review_due_date"] == first["review_due_date"]
        with Session(engine) as session:
            assert session.scalar(select(ReviewPlan.correct_streak)) == (1 if correct else 0)
            assert session.scalar(select(func.count()).select_from(CheckIn)) == 2
        assert not client.get(f"/api/v1/reviews/{question_id}").json()["can_submit"]
        assert client.get(f"/api/v1/reviews/{question_id}").json()["result"] == first


def test_database_constraints_and_downgrade_guard(practice_env, monkeypatch):
    engine, app, clock = practice_env
    with TestClient(app) as client:
        register(client)
        question_id, right, _ = prepare_review(client, engine, clock)
        clock["now"] += timedelta(days=1)
        data = review_payload(client, question_id, right)
        saved = client.post(f"/api/v1/reviews/{question_id}/attempts", json=data).json()
        with Session(engine) as session:
            attempt = session.get(Attempt, saved["id"])
            values = {
                c.name: getattr(attempt, c.name)
                for c in Attempt.__table__.columns
                if c.name != "id"
            }
            values["idempotency_key"] = str(uuid4())
            session.add(Attempt(**values))
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()
        with engine.connect() as connection:
            with pytest.raises(OperationalError, match="ck_attempt_source"):
                connection.execute(
                    text("UPDATE attempts SET review_question_id=NULL WHERE id=:id"),
                    {"id": saved["id"]},
                )
            connection.rollback()
        monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        with pytest.raises(RuntimeError, match="已有复习记录"):
            command.downgrade(config, "0002_daily_practice")
        assert client.get("/api/ready").status_code == 200


def test_populated_daily_database_upgrade_preserves_records(practice_env, monkeypatch):
    engine, app, clock = practice_env
    with TestClient(app) as client:
        register(client)
        question_id, _, original = prepare_review(client, engine, clock)
        monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.downgrade(config, "0002_daily_practice")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM attempts")).scalar() == 1
        command.upgrade(config, "head")
        command.check(config)
        assert client.get(f"/api/v1/attempts/{original['id']}").json() == original
        assert client.get(f"/api/v1/reviews/{question_id}").status_code == 200


def test_daily_update_invalidates_open_review(practice_env):
    engine, app, clock = practice_env
    with TestClient(app) as client:
        register(client)
        question_id, right, _ = prepare_review(client, engine, clock)
        clock["now"] += timedelta(days=1)
        data = review_payload(client, question_id, right)
        with Session(engine) as session, session.begin():
            track_id = session.get(Question, question_id).track_id
            session.add(
                DailyAssignment(
                    track_id=track_id,
                    local_date=clock["now"].date(),
                    question_version_id=data["question_version_id"],
                )
            )
        assert (
            client.post("/api/v1/attempts", json=submission(get_daily(client), right)).status_code
            == 200
        )
        assert client.post(f"/api/v1/reviews/{question_id}/attempts", json=data).status_code == 409
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Attempt)) == 2

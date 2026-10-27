from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models import Attempt, CheckIn, DailyAssignment, QuestionVersion, ReviewPlan, Track
from app.practice import review_schedule
from app.seed import seed_samples

PASSWORD = "test-password-only-42"


def register(client, name="learner_a"):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "username": name,
            "password": PASSWORD,
        },
        headers={"X-Requested-With": "learning-platform"},
    )
    assert response.status_code == 201, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return response


def get_daily(client, domain="computer"):
    track = client.get(f"/api/v1/tracks?domain_code={domain}").json()[0]
    response = client.get(f"/api/v1/today?track_id={track['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def submission(daily, answer="A"):
    assignment = daily["assignment"]
    return {
        "daily_assignment_id": assignment["id"],
        "question_version_id": assignment["question_version_id"],
        "answer": answer,
        "idempotency_key": str(uuid4()),
    }


@pytest.mark.integration
def test_authentication_csrf_expiry_and_logout(practice_env):
    _, app, clock = practice_env
    with TestClient(app) as client:
        assert client.get("/api/v1/today?track_id=1").status_code == 401
        data = {"username": "learner_a", "password": PASSWORD}
        assert client.post("/api/v1/auth/register", json=data).status_code == 403
        response = register(client)
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api" in cookie
        assert "password" not in client.get("/api/v1/auth/me").text
        daily = get_daily(client)
        csrf = client.headers.pop("X-CSRF-Token")
        assert client.post("/api/v1/attempts", json=submission(daily)).status_code == 403
        client.headers["X-CSRF-Token"] = csrf
        assert client.post("/api/v1/auth/logout").status_code == 204
        assert client.get("/api/v1/auth/me").status_code == 401
        headers = {"X-Requested-With": "learning-platform"}
        bad = client.post(
            "/api/v1/auth/login", json={**data, "password": "wrong-password"}, headers=headers
        )
        assert bad.status_code == 401
        good = client.post("/api/v1/auth/login", json=data, headers=headers)
        assert good.status_code == 200
        clock["now"] += timedelta(days=8)
        assert client.get("/api/v1/auth/me").status_code == 401


@pytest.mark.integration
def test_answer_grading_persistence_and_user_isolation(practice_env):
    engine, app, _ = practice_env
    with TestClient(app) as first, TestClient(app) as second:
        register(first)
        register(second, "learner_b")
        daily = get_daily(first)
        assert daily == get_daily(first) == get_daily(second)
        assert "correct_answer" not in str(daily) and "explanation" not in str(daily)
        with Session(engine) as session:
            version = session.get(QuestionVersion, daily["assignment"]["question_version_id"])
            right = version.correct_answer
        wrong = next(answer for answer in "ABCD" if answer != right)
        data = submission(daily, wrong)
        response = first.post("/api/v1/attempts", json=data)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["score"] == 0 and result["correct_answer"] == right
        assert result["check_in_date"] == "2026-10-27"
        assert result["review_due_date"] == "2026-10-28"
        assert get_daily(first)["assignment"]["result"] == result
        assert get_daily(second)["assignment"]["result"] is None
        assert second.get(f"/api/v1/attempts/{result['id']}").status_code == 404
        other = second.post("/api/v1/attempts", json=submission(daily, right))
        assert other.json()["score"] == 100
        assert first.post("/api/v1/attempts", json=data).json() == result
        assert first.post("/api/v1/attempts", json={**data, "answer": right}).status_code == 409
        assert first.post("/api/v1/attempts", json=submission(daily, right)).status_code == 409
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Attempt)) == 2
            assert session.scalar(select(func.count()).select_from(CheckIn)) == 2
            assert session.scalar(select(func.count()).select_from(ReviewPlan)) == 2


@pytest.mark.integration
def test_invalid_payloads_cannot_change_records(practice_env):
    engine, app, _ = practice_env
    with TestClient(app) as client:
        register(client)
        daily = get_daily(client)
        data = submission(daily)
        for changes, status in [
            ({"user_id": 2}, 422),
            ({"answer": "Z"}, 422),
            ({"idempotency_key": "bad"}, 422),
            ({"question_version_id": 99999}, 409),
            ({"daily_assignment_id": 99999}, 404),
        ]:
            assert client.post("/api/v1/attempts", json={**data, **changes}).status_code == status
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Attempt)) == 0
            assert session.scalar(select(func.count()).select_from(CheckIn)) == 0


@pytest.mark.integration
def test_concurrent_daily_assignment_and_submission(practice_env):
    engine, app, _ = practice_env
    with TestClient(app) as client:
        register(client)
        with ThreadPoolExecutor(max_workers=2) as pool:
            daily = list(pool.map(lambda _: get_daily(client), range(2)))
            assert daily[0] == daily[1]
            data = submission(daily[0])
            responses = list(
                pool.map(
                    lambda _: client.post("/api/v1/attempts", json=data),
                    range(2),
                )
            )
            assert [response.status_code for response in responses] == [200, 200]
            assert responses[0].json() == responses[1].json()
            english = get_daily(client, "english")
            responses = list(
                pool.map(
                    lambda _: client.post("/api/v1/attempts", json=submission(english)),
                    range(2),
                )
            )
            assert sorted(response.status_code for response in responses) == [200, 409]
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Attempt)) == 2
            assert session.scalar(select(func.count()).select_from(CheckIn)) == 1
            assert session.scalar(select(func.count()).select_from(DailyAssignment)) == 2


@pytest.mark.integration
def test_submission_rolls_back_as_one_transaction(practice_env):
    engine, app, _ = practice_env
    with TestClient(app) as client:
        register(client)
        data = submission(get_daily(client))

        def fail_checkin(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("INSERT INTO check_ins"):
                raise OperationalError("injected_failure", {}, Exception("test"))

        event.listen(engine, "before_cursor_execute", fail_checkin)
        try:
            assert client.post("/api/v1/attempts", json=data).status_code == 503
        finally:
            event.remove(engine, "before_cursor_execute", fail_checkin)
        with Session(engine) as session:
            for model in (Attempt, CheckIn, ReviewPlan):
                assert session.scalar(select(func.count()).select_from(model)) == 0
        assert client.post("/api/v1/attempts", json=data).status_code == 200


@pytest.mark.integration
def test_midnight_and_published_versions(practice_env):
    engine, app, clock = practice_env
    clock["now"] = datetime(2026, 10, 27, 15, 59, 59)
    with TestClient(app) as first, TestClient(app) as second:
        register(first)
        register(second, "learner_b")
        daily = get_daily(first)
        data = submission(daily)
        with Session(engine) as session, session.begin():
            old = session.get(QuestionVersion, data["question_version_id"])
            session.add(
                QuestionVersion(
                    question_id=old.question_id,
                    version=2,
                    stem="新版题干",
                    options=old.options,
                    correct_answer="D",
                    explanation="新版解析",
                    source="测试版本",
                    published_at=clock["now"],
                )
            )
        assert get_daily(first) == daily
        saved = first.post("/api/v1/attempts", json=data)
        assert saved.status_code == 200
        clock["now"] += timedelta(seconds=2)
        assert first.post("/api/v1/attempts", json=data).json() == saved.json()
        assert second.post("/api/v1/attempts", json=submission(daily)).status_code == 409
        assert get_daily(second)["date"] == "2026-10-28"
        assert saved.json()["check_in_date"] == "2026-10-27"
        assert first.get(f"/api/v1/attempts/{saved.json()['id']}").json() == saved.json()


@pytest.mark.integration
def test_empty_track_and_sample_import_is_idempotent(practice_env):
    engine, app, _ = practice_env
    seed_samples(engine)
    with Session(engine) as session, session.begin():
        assert session.scalar(select(func.count()).select_from(QuestionVersion)) == 12
        session.add(Track(domain_id=1, code="empty", name="暂无题目的方向"))
    with TestClient(app) as client:
        register(client)
        track = client.get("/api/v1/tracks?domain_code=computer").json()[-1]
        response = client.get(f"/api/v1/today?track_id={track['id']}")
        assert response.json()["assignment"] is None
        assert client.get("/api/v1/today?track_id=99999").status_code == 404


@pytest.mark.parametrize(
    "correct,streak,expected",
    [
        (False, 3, (0, 1)),
        (True, 0, (1, 1)),
        (True, 1, (2, 3)),
        (True, 2, (3, 7)),
        (True, 3, (4, 14)),
        (True, 9, (10, 14)),
    ],
)
def test_review_rule(correct, streak, expected):
    assert review_schedule(correct, streak)[:2] == expected

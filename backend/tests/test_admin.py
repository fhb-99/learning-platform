from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session
from test_practice import get_daily, register, submission
from test_reviews import review_payload

from app.admin_role import set_admin
from app.models import Attempt, Question, QuestionDraft, QuestionVersion, ReviewPlan, Track, User

pytestmark = pytest.mark.integration


def editor(client, engine, name="editor_one"):
    register(client, name)
    set_admin(engine, name, True)
    assert client.get("/api/v1/auth/me").json()["is_admin"]


def content(stem="新版题干", right="A"):
    return {
        "stem": stem,
        "options": [{"id": key, "text": f"选项{key}"} for key in "ABCD"],
        "correct_answer": right,
        "explanation": "新版解析",
        "source": "自编测试题",
    }


def new_question(client, track_id=1, code="test-published", **values):
    response = client.post(
        "/api/v1/admin/questions",
        json={
            **content(),
            "code": code,
            "track_id": track_id,
            **values,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_permissions_csrf_and_revoked_session(practice_env):
    engine, app, _ = practice_env
    with TestClient(app) as client:
        endpoint = "/api/v1/admin/questions"
        assert client.get(endpoint + "?track_id=1").status_code == 401
        response = register(client)
        assert response.json()["is_admin"] is False
        assert client.get(endpoint + "?track_id=1").status_code == 403
        assert client.get(endpoint + "/1").status_code == 403
        assert client.get(endpoint + "/1/versions").status_code == 403
        assert (
            client.post(endpoint, json={**content(), "code": "no-admin", "track_id": 1}).status_code
            == 403
        )
        assert (
            client.put(endpoint + "/1/draft", json={**content(), "revision": 0}).status_code == 403
        )
        assert client.post(endpoint + "/1/publish", json={"revision": 1}).status_code == 403
        injected = client.post(
            "/api/v1/auth/register",
            json={
                "username": "intruder",
                "password": "test-password-only",
                "is_admin": True,
            },
            headers={"X-Requested-With": "learning-platform"},
        )
        assert injected.status_code == 422
        set_admin(engine, "learner_a", True)
        assert client.get(endpoint + "?track_id=1").status_code == 200
        token = client.headers.pop("X-CSRF-Token")
        assert client.post(endpoint + "/1/publish", json={"revision": 1}).status_code == 403
        client.headers["X-CSRF-Token"] = token
        set_admin(engine, "learner_a", False)
        assert not client.get("/api/v1/auth/me").json()["is_admin"]
        assert client.get(endpoint + "?track_id=1").status_code == 403
        assert client.post(endpoint + "/1/publish", json={"revision": 1}).status_code == 403
        assert client.get("/api/v1/today?track_id=1").status_code == 200
        with pytest.raises(ValueError, match="账号不存在"):
            set_admin(engine, "missing_account", True)


def test_draft_validation_publish_retry_and_immutable_versions(practice_env):
    engine, app, _ = practice_env
    with TestClient(app) as client:
        editor(client, engine)
        qid = new_question(client, stem="", correct_answer="")
        endpoint = f"/api/v1/admin/questions/{qid}"
        assert client.get(endpoint + "/versions").json()["items"] == []
        assert client.post(endpoint + "/publish", json={"revision": 1}).status_code == 422
        data = {**content(), "revision": 1}
        bad_options = {**data, "options": [{"id": "A", "text": "x"}] * 4}
        assert client.put(endpoint + "/draft", json=bad_options).status_code == 422
        assert client.put(endpoint + "/draft", json={**data, "track_id": 2}).status_code == 422
        assert client.put(endpoint + "/draft", json=data).json()["revision"] == 2
        assert client.put(endpoint + "/draft", json=data).status_code == 409
        assert client.post(endpoint + "/publish", json={"revision": 1}).status_code == 409
        published = client.post(endpoint + "/publish", json={"revision": 2})
        assert published.status_code == 200 and published.json()["version"] == 1
        assert client.post(endpoint + "/publish", json={"revision": 2}).json() == published.json()
        old = client.get(endpoint + "/versions").json()["items"][0]
        assert (
            client.put(endpoint + "/draft", json={**content("第二版"), "revision": 2}).status_code
            == 200
        )
        assert client.post(endpoint + "/publish", json={"revision": 3}).json()["version"] == 2
        history = client.get(endpoint + "/versions").json()["items"]
        assert history[1] == old and history[0]["stem"] == "第二版"
        assert client.put(endpoint + "/versions/1", json=content()).status_code == 404
        assert client.delete(endpoint).status_code == 405
        assert (
            client.post(
                "/api/v1/admin/questions",
                json={
                    **content(),
                    "code": "test-published",
                    "track_id": 1,
                },
            ).status_code
            == 409
        )


@pytest.mark.parametrize("field", ["stem", "correct_answer", "explanation", "source", "options"])
def test_incomplete_or_duplicate_content_cannot_publish(practice_env, field):
    engine, app, _ = practice_env
    with TestClient(app) as client:
        editor(client, engine)
        values = {field: "   "}
        if field == "options":
            values = {"options": [{"id": key, "text": "重复选项"} for key in "ABCD"]}
        if field == "correct_answer":
            values = {field: ""}
        qid = new_question(client, **values)
        result = client.post(f"/api/v1/admin/questions/{qid}/publish", json={"revision": 1})
        assert result.status_code == 422
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(QuestionVersion)
                    .where(QuestionVersion.question_id == qid)
                )
                == 0
            )


@pytest.mark.parametrize("domain", ["computer", "english"])
def test_real_publication_keeps_daily_attempt_and_review_versions(practice_env, domain):
    engine, app, clock = practice_env
    with TestClient(app) as admin, TestClient(app) as learner, TestClient(app) as later:
        editor(admin, engine)
        register(learner)
        register(later, "later_learner")
        daily = get_daily(learner, domain)
        old_id = daily["assignment"]["question_version_id"]
        with Session(engine) as session:
            old = session.get(QuestionVersion, old_id)
            qid, right = old.question_id, old.correct_answer
        wrong = next(key for key in "ABCD" if key != right)
        saved = learner.post("/api/v1/attempts", json=submission(daily, right)).json()
        endpoint = f"/api/v1/admin/questions/{qid}"
        assert admin.get(endpoint).json()["draft"]["revision"] == 0
        assert (
            admin.put(endpoint + "/draft", json={**content(right=wrong), "revision": 0}).status_code
            == 200
        )
        published = admin.post(endpoint + "/publish", json={"revision": 1}).json()
        assert published["version"] == 2 and published["id"] != old_id
        # 同日未作答用户也拿到已固定的旧版，答题判断仍按旧答案。
        same_day = get_daily(later, domain)
        assert same_day["assignment"]["question_version_id"] == old_id
        assert (
            later.post("/api/v1/attempts", json=submission(same_day, right)).json()["score"] == 100
        )
        history = learner.get(f"/api/v1/history/{saved['id']}").json()
        assert history["version"] == 1 and history["result"] == saved
        with Session(engine) as session:
            plan = session.scalar(
                select(ReviewPlan).where(ReviewPlan.latest_attempt_id == saved["id"])
            )
            assert str(plan.due_date) == saved["review_due_date"]
        clock["now"] += timedelta(days=1)
        payload = review_payload(learner, qid, right)
        assert payload["question_version_id"] == old_id
        reviewed = learner.post(f"/api/v1/reviews/{qid}/attempts", json=payload)
        assert reviewed.status_code == 200 and reviewed.json()["score"] == 100
        assert learner.get(f"/api/v1/history/{reviewed.json()['id']}").json()["version"] == 1
        # 轮转一周必定重新选中同题；新每日分配使用最新发布版。
        for _ in range(7):
            future = get_daily(later, domain)
            if future["assignment"]["question_version_id"] == published["id"]:
                break
            clock["now"] += timedelta(days=1)
        else:
            pytest.fail("新每日题未选到最新版本")


def test_concurrent_edit_publish_and_transaction_rollback(practice_env):
    engine, app, _ = practice_env
    with TestClient(app) as first, TestClient(app) as second:
        editor(first, engine)
        editor(second, engine, "editor_two")
        qid = new_question(first)
        endpoint = f"/api/v1/admin/questions/{qid}"
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(
                    lambda c: c.put(
                        endpoint + "/draft",
                        json={
                            **content(c.get("/api/v1/auth/me").json()["username"]),
                            "revision": 1,
                        },
                    ),
                    [first, second],
                )
            )
        assert sorted(result.status_code for result in results) == [200, 409]
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(
                    lambda c: c.post(endpoint + "/publish", json={"revision": 2}), [first, second]
                )
            )
        assert all(result.status_code == 200 for result in results)
        assert results[0].json() == results[1].json()
        first.put(endpoint + "/draft", json={**content(), "revision": 2})

        def fail(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("UPDATE question_drafts"):
                raise OperationalError(statement, None, Exception("模拟发布指针落库失败"))

        event.listen(engine, "before_cursor_execute", fail)
        try:
            assert first.post(endpoint + "/publish", json={"revision": 3}).status_code == 503
        finally:
            event.remove(engine, "before_cursor_execute", fail)
        with Session(engine) as session:
            assert (
                session.scalar(
                    select(func.count())
                    .select_from(QuestionVersion)
                    .where(QuestionVersion.question_id == qid)
                )
                == 1
            )
            assert session.get(QuestionDraft, qid).published_version_id is None
        assert first.post(endpoint + "/publish", json={"revision": 3}).json()["version"] == 2


def test_unpublished_isolation_filters_and_version_pagination(practice_env):
    engine, app, _ = practice_env
    with TestClient(app) as admin, TestClient(app) as learner:
        editor(admin, engine)
        register(learner)
        with Session(engine) as session, session.begin():
            track = Track(domain_id=1, code="draft-test", name="独立草稿方向")
            session.add(track)
            session.flush()
            track_id = track.id
        qid = new_question(admin, track_id=track_id)
        assert learner.get(f"/api/v1/today?track_id={track_id}").json()["assignment"] is None
        assert admin.get(f"/api/v1/admin/questions?track_id={track_id}").json()["total"] == 1
        assert admin.get("/api/v1/admin/questions?track_id=2").json()["total"] == 6
        endpoint = f"/api/v1/admin/questions/{qid}"
        for revision in range(1, 23):
            assert admin.post(endpoint + "/publish", json={"revision": revision}).status_code == 200
            if revision < 22:
                assert (
                    admin.put(
                        endpoint + "/draft",
                        json={
                            **content(f"第{revision + 1}版"),
                            "revision": revision,
                        },
                    ).status_code
                    == 200
                )
        page = admin.get(endpoint + "/versions").json()
        assert len(page["items"]) == 20 and page["next_cursor"] == 3
        page2 = admin.get(endpoint + "/versions?before_version=3").json()
        assert [row["version"] for row in page2["items"]] == [2, 1]
        assert page2["next_cursor"] is None
        assert learner.get(f"/api/v1/today?track_id={track_id}").json()["assignment"] is not None


def test_migration_preserves_existing_data_and_guards_downgrade(practice_env, monkeypatch):
    engine, app, _ = practice_env
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    monkeypatch.setenv("DATABASE_URL", engine.url.render_as_string(hide_password=False))
    with TestClient(app) as client:
        register(client)
        daily = get_daily(client)
        saved = client.post("/api/v1/attempts", json=submission(daily)).json()
        command.downgrade(config, "0003_personal_review")
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM attempts")).scalar() == 1
        command.upgrade(config, "head")
        command.check(config)
        assert client.get(f"/api/v1/history/{saved['id']}").json()["result"] == saved
        with Session(engine) as session:
            assert not session.scalar(select(User.is_admin))
            assert session.scalar(select(func.count()).select_from(QuestionVersion)) == 12
            assert session.scalar(select(func.count()).select_from(Attempt)) == 1
        set_admin(engine, "learner_a", True)
        new_question(client)
        with pytest.raises(RuntimeError, match="已有题库管理数据"):
            command.downgrade(config, "0003_personal_review")
        assert client.get("/api/ready").status_code == 200
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Question)) == 13

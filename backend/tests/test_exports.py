import hashlib
import io
import json
import tempfile
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from threading import BoundedSemaphore

import duckdb
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session
from test_practice import get_daily, register, submission
from test_reviews import prepare_review, review_payload

from app import exports
from app.models import Attempt, QuestionVersion, ReviewPlan, User

pytestmark = pytest.mark.integration
RANGE = {"start_date": "2026-10-27", "end_date": "2026-10-28"}


@pytest.fixture
def export_directory(monkeypatch):
    root = Path(__file__).resolve().parents[2] / "build" / "verification" / "exports"
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=root) as temporary:
        directory = Path(temporary)
        monkeypatch.setattr(exports, "EXPORT_ROOT", directory / "server")
        yield directory
        assert not list((directory / "server").glob("export-*"))


def unpack(response, directory):
    assert response.status_code == 200, response.text if response.status_code != 200 else ""
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="learning-data-'
    )
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {
            "attempts.parquet",
            "check_ins.parquet",
            "review_plans.parquet",
            "manifest.json",
            "verification.json",
            "说明.txt",
        }
        manifest = json.loads(archive.read("manifest.json"))
        report = json.loads(archive.read("verification.json"))
        tables = {}
        with duckdb.connect(config={"threads": 1}) as db:
            for name, metadata in manifest["files"].items():
                content = archive.read(name)
                assert hashlib.sha256(content).hexdigest() == metadata["sha256"]
                path = directory / name
                path.write_bytes(content)
                result = db.execute("SELECT * FROM read_parquet(?)", [str(path)])
                columns = [item[0] for item in result.description]
                assert columns == metadata["columns"]
                tables[name.removesuffix(".parquet")] = [
                    dict(zip(columns, row, strict=True)) for row in result.fetchall()
                ]
        assert report["status"] == "passed"
        assert report["mysql"] == report["parquet"]
        assert report["counts"] == manifest["counts"] == {k: len(v) for k, v in tables.items()}
        return tables, manifest, report


def test_export_own_data_privacy_versions_and_exact_content(practice_env, export_directory):
    engine, app, clock = practice_env
    with TestClient(app) as client, TestClient(app) as other:
        register(client)
        register(other, "private_other")
        question_id, right, original = prepare_review(client, engine, clock)
        other.post("/api/v1/attempts", json=submission(get_daily(other)))
        client.post("/api/v1/attempts", json=submission(get_daily(client, "english"), "C"))
        # 发布另一答案的 v2，导出必须沿用实际作答版本，而不是最新题库内容。
        with Session(engine) as session, session.begin():
            old = session.get(
                QuestionVersion, session.get(Attempt, original["id"]).question_version_id
            )
            session.add(
                QuestionVersion(
                    question_id=question_id,
                    version=2,
                    stem="不应导出的新题干",
                    options=old.options,
                    correct_answer=next(a for a in "ABCD" if a != right),
                    explanation="保密解析",
                    source="测试来源",
                    published_at=clock["now"],
                )
            )
            user = session.scalar(select(User).where(User.username == "learner_a"))
            user.is_admin = True
        clock["now"] += timedelta(days=1)
        reviewed = client.post(
            f"/api/v1/reviews/{question_id}/attempts",
            json=review_payload(client, question_id, right),
        )
        assert reviewed.status_code == 200
        response = client.post("/api/v1/exports", json=RANGE)
        tables, manifest, report = unpack(response, export_directory)
        assert manifest["include_answers"] is False
        assert manifest["scope"] == "current_user_all_domains"
        assert manifest["account_timezone"] == "Asia/Singapore"
        assert manifest["snapshot_at_utc"].endswith("Z")
        assert report["counts"] == {"attempts": 3, "check_ins": 2, "review_plans": 2}
        rows = tables["attempts"]
        assert all(r["selected_answer"] is None for r in rows)
        assert {r["domain_code"] for r in rows} == {"english", "computer"}
        assert {r["version"] for r in rows} == {1}
        assert rows[2]["review_basis_ref"] == rows[0]["attempt_ref"]
        assert rows[2]["question_version_ref"] == rows[0]["question_version_ref"]
        assert rows[2]["scheduled_due_date"] == date(2026, 10, 28)
        assert rows[2]["correct"] is True and rows[0]["correct"] is False
        assert tables["check_ins"][0]["first_attempt_ref"] == rows[0]["attempt_ref"]
        assert tables["check_ins"][1]["first_attempt_ref"] == rows[2]["attempt_ref"]
        with Session(engine) as session:
            user_id = session.scalar(select(User.id).where(User.username == "learner_a"))
            saved = session.scalars(
                select(Attempt).where(Attempt.user_id == user_id).order_by(Attempt.id)
            ).all()
            for row, attempt in zip(rows, saved, strict=True):
                for output, column in {
                    "score": "score",
                    "submitted_at_utc": "submitted_at",
                    "local_date": "local_date",
                    "review_due_date": "review_due_date",
                    "review_interval_days": "review_interval_days",
                    "review_reason": "review_reason",
                    "rule_version": "rule_version",
                }.items():
                    assert row[output] == getattr(attempt, column)
                assert row["source"] == ("daily" if attempt.daily_assignment_id else "review")
            for plan in session.scalars(select(ReviewPlan).where(ReviewPlan.user_id == user_id)):
                index = next(i for i, a in enumerate(saved) if a.id == plan.latest_attempt_id)
                exported = next(
                    p
                    for p in tables["review_plans"]
                    if p["latest_attempt_ref"] == rows[index]["attempt_ref"]
                )
                assert exported["current_due_date"] == plan.due_date
                assert exported["correct_streak"] == plan.correct_streak
            expected_answers = [a.answer for a in saved]
            secrets_to_exclude = [a.idempotency_key for a in saved]
            secrets_to_exclude.append(session.get(User, user_id).password_hash)
        decoded = str(tables) + str(manifest) + str(report)
        for secret in [
            "learner_a",
            "private_other",
            "保密解析",
            "不应导出的新题干",
            *secrets_to_exclude,
        ]:
            assert secret not in decoded
        for forbidden in (
            "user_id",
            "idempotency_key",
            "password_hash",
            "correct_answer",
            "question_version_id",
        ):
            assert forbidden not in rows[0]
        included, _, _ = unpack(
            client.post("/api/v1/exports", json={**RANGE, "include_answers": True}),
            export_directory,
        )
        assert [r["selected_answer"] for r in included["attempts"]] == expected_answers
        assert {r["attempt_ref"] for r in rows}.isdisjoint(
            r["attempt_ref"] for r in included["attempts"]
        )


def test_export_date_boundaries_and_current_plan(practice_env, export_directory):
    engine, app, clock = practice_env
    clock["now"] = datetime(2026, 10, 26, 16, 1)  # UTC 前一天，本地已是 27 日。
    with TestClient(app) as client:
        register(client)
        question_id, right, _ = prepare_review(client, engine, clock)
        clock["now"] += timedelta(days=1)
        assert (
            client.post(
                f"/api/v1/reviews/{question_id}/attempts",
                json=review_payload(client, question_id, right),
            ).status_code
            == 200
        )
        first, _, _ = unpack(
            client.post(
                "/api/v1/exports",
                json={
                    "start_date": "2026-10-27",
                    "end_date": "2026-10-27",
                },
            ),
            export_directory,
        )
        assert len(first["attempts"]) == 1
        assert first["attempts"][0]["submitted_at_utc"].date() == date(2026, 10, 26)
        assert first["review_plans"][0]["latest_attempt_ref"] is None
        assert first["review_plans"][0]["latest_outside_range"] is True
        assert first["review_plans"][0]["current_due_date"] == date(2026, 10, 29)
        second, _, _ = unpack(
            client.post(
                "/api/v1/exports",
                json={
                    "start_date": "2026-10-28",
                    "end_date": "2026-10-28",
                },
            ),
            export_directory,
        )
        assert len(second["attempts"]) == 1
        assert second["attempts"][0]["review_basis_ref"] is None
        assert second["attempts"][0]["basis_outside_range"] is True
        assert second["review_plans"][0]["latest_outside_range"] is False


def test_export_auth_csrf_scope_and_empty_files(practice_env, export_directory):
    _, app, _ = practice_env
    with TestClient(app) as client:
        assert client.post("/api/v1/exports", json=RANGE).status_code == 401
        register(client)
        csrf = client.headers.pop("X-CSRF-Token")
        assert client.post("/api/v1/exports", json=RANGE).status_code == 403
        client.headers["X-CSRF-Token"] = csrf
        for extra in ({"user_id": 2}, {"domain_code": "computer"}, {"username": "private_other"}):
            assert client.post("/api/v1/exports", json={**RANGE, **extra}).status_code == 422
        tables, _, report = unpack(client.post("/api/v1/exports", json=RANGE), export_directory)
        assert tables == {"attempts": [], "check_ins": [], "review_plans": []}
        assert report["mysql"] == []


@pytest.mark.parametrize(
    "start,end",
    [
        ("2026-10-28", "2026-10-27"),
        ("2025-01-01", "2026-01-02"),
        ("invalid", "2026-10-28"),
    ],
)
def test_export_invalid_dates(practice_env, start, end):
    _, app, _ = practice_env
    with TestClient(app) as client:
        register(client)
        assert (
            client.post("/api/v1/exports", json={"start_date": start, "end_date": end}).status_code
            == 422
        )


def test_export_limits_and_busy_fail_without_partial_data(
    practice_env, export_directory, monkeypatch
):
    _, app, _ = practice_env
    with TestClient(app) as client:
        register(client)
        client.post("/api/v1/attempts", json=submission(get_daily(client)))
        monkeypatch.setattr(exports, "MAX_ATTEMPTS", 0)
        response = client.post("/api/v1/exports", json=RANGE)
        assert response.status_code == 413
        assert "content-disposition" not in response.headers
        monkeypatch.setattr(exports, "EXPORT_SLOTS", BoundedSemaphore(0))
        response = client.post("/api/v1/exports", json=RANGE)
        assert response.status_code == 429 and response.headers["retry-after"] == "5"


@pytest.mark.parametrize("corruption", ["value", "schema", "aggregate", "reference", "io"])
def test_export_fails_closed_on_corruption(practice_env, export_directory, monkeypatch, corruption):
    _, app, _ = practice_env
    original_write = exports.write_parquet
    original_rows = exports.export_rows

    def corrupt_file(db, table, path):
        if corruption == "io":
            raise OSError("secret-record-should-not-leak")
        if table == "expected_attempts":
            if corruption == "value":
                db.execute(
                    "CREATE TABLE changed AS SELECT * REPLACE ('tampered' AS rule_version) "
                    "FROM expected_attempts"
                )
                return original_write(db, "changed", path)
            if corruption == "schema":
                db.execute(
                    "CREATE TABLE changed AS SELECT * EXCLUDE (rule_version) FROM expected_attempts"
                )
                return original_write(db, "changed", path)
        return original_write(db, table, path)

    def corrupt_source(*args):
        rows = original_rows(*args)
        if corruption == "aggregate":
            changed = list(rows["attempts"][0])
            changed[10] = 42  # 写盘前破坏数据，逐行比较会一致，独立 MySQL 聚合仍应拦截。
            rows["attempts"][0] = tuple(changed)
        elif corruption == "reference":
            rows["check_ins"][0] = (rows["check_ins"][0][0], "missing-ref")
        return rows

    with TestClient(app) as client:
        register(client)
        client.post("/api/v1/attempts", json=submission(get_daily(client)))
        with monkeypatch.context() as context:
            context.setattr(exports, "write_parquet", corrupt_file)
            context.setattr(exports, "export_rows", corrupt_source)
            response = client.post("/api/v1/exports", json=RANGE)
            assert response.status_code == 503
            assert "content-disposition" not in response.headers
            assert "secret-record" not in response.text
            assert not list(exports.EXPORT_ROOT.iterdir())
        # 失败必须释放并发名额，后续导出仍可正常运行。
        unpack(client.post("/api/v1/exports", json=RANGE), export_directory)


def test_export_concurrent_submission_keeps_single_mysql_snapshot(practice_env, export_directory):
    engine, app, _ = practice_env
    with TestClient(app) as client, TestClient(app) as writer:
        register(client)
        writer.cookies.update(client.cookies)
        writer.headers["X-CSRF-Token"] = client.headers["X-CSRF-Token"]
        client.post("/api/v1/attempts", json=submission(get_daily(client)))
        daily = get_daily(writer, "english")
        inserted = False

        def insert_during_read(connection, cursor, statement, params, context, many):
            nonlocal inserted
            if "UTC_TIMESTAMP(6)" in statement and not inserted:
                inserted = True
                assert writer.post("/api/v1/attempts", json=submission(daily)).status_code == 200

        event.listen(engine, "after_cursor_execute", insert_during_read)
        try:
            tables, _, _ = unpack(client.post("/api/v1/exports", json=RANGE), export_directory)
        finally:
            event.remove(engine, "after_cursor_execute", insert_during_read)
        assert inserted and len(tables["attempts"]) == len(tables["review_plans"]) == 1
        with Session(engine) as session:
            assert session.scalar(select(func.count()).select_from(Attempt)) == 2
        latest, _, _ = unpack(client.post("/api/v1/exports", json=RANGE), export_directory)
        assert len(latest["attempts"]) == 2

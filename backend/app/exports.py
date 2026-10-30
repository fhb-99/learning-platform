import hashlib
import hmac
import io
import json
import secrets
import tempfile
import zipfile
from datetime import date
from pathlib import Path
from threading import BoundedSemaphore

import duckdb
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import text

from app.auth import AuthenticatedUser

router = APIRouter(prefix="/api/v1", tags=["个人学习数据导出"])
MAX_ATTEMPTS = 10_000
EXPORT_ROOT = Path(__file__).resolve().parents[2] / "build" / "exports"
EXPORT_SLOTS = BoundedSemaphore(2)

# 固定字段白名单与类型；空导出也保留完整结构，不通过模型序列化带出身份字段。
SCHEMAS = {
    "attempts": """attempt_ref VARCHAR, question_ref VARCHAR, question_version_ref VARCHAR,
        version INTEGER, domain_code VARCHAR, track_code VARCHAR, source VARCHAR,
        submitted_at_utc TIMESTAMP, local_date DATE, selected_answer VARCHAR,
        score INTEGER, correct BOOLEAN, scheduled_due_date DATE, review_due_date DATE,
        review_interval_days INTEGER, review_reason VARCHAR, rule_version VARCHAR,
        review_basis_ref VARCHAR, basis_outside_range BOOLEAN""",
    "check_ins": "local_date DATE, first_attempt_ref VARCHAR",
    "review_plans": """question_ref VARCHAR, current_due_date DATE, correct_streak INTEGER,
        latest_attempt_ref VARCHAR, latest_outside_range BOOLEAN""",
}
ATTEMPT_FROM = """FROM attempts a
    JOIN question_versions v ON v.id = a.question_version_id
    JOIN questions q ON q.id = v.question_id
    JOIN tracks t ON t.id = q.track_id
    JOIN domains d ON d.id = t.domain_id
    WHERE a.user_id = :user_id AND a.local_date BETWEEN :start AND :end"""
SOURCE = "CASE WHEN a.daily_assignment_id IS NOT NULL THEN 'daily' ELSE 'review' END"


class ExportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: date
    end_date: date
    include_answers: bool = False

    @model_validator(mode="after")
    def valid_range(self):
        if not 0 <= (self.end_date - self.start_date).days < 366:
            raise ValueError("日期范围必须为 1 至 366 天")
        return self


def read_snapshot(engine, user_id, options):
    params = {
        "user_id": user_id,
        "start": options.start_date,
        "end": options.end_date,
        "include_answers": options.include_answers,
        "limit": MAX_ATTEMPTS + 1,
    }
    # 第一次普通 SELECT 建立 InnoDB 一致性快照，后续明细和独立聚合共用该事务。
    # 不使用锁定读、不写业务数据；读取结束后释放连接，再生成文件。
    with engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection:
        with connection.begin():
            info = (
                connection.execute(
                    text(
                        "SELECT timezone, UTC_TIMESTAMP(6) AS captured_at "
                        "FROM users WHERE id=:user_id"
                    ),
                    params,
                )
                .mappings()
                .one()
            )
            attempts = (
                connection.execute(
                    text(f"""SELECT a.id, v.question_id, a.question_version_id, v.version,
                    d.code AS domain_code, t.code AS track_code, {SOURCE} AS source,
                    a.submitted_at, a.local_date,
                    IF(:include_answers, a.answer, NULL) AS answer, a.score,
                    a.scheduled_due_date, a.review_due_date, a.review_interval_days,
                    a.review_reason, a.rule_version, a.review_basis_attempt_id
                    {ATTEMPT_FROM} ORDER BY a.id LIMIT :limit"""),
                    params,
                )
                .mappings()
                .all()
            )
            if len(attempts) > MAX_ATTEMPTS:
                raise HTTPException(413, "范围内作答超过 10000 条，请缩小日期范围")
            check_ins = (
                connection.execute(
                    text("""SELECT local_date, first_attempt_id FROM check_ins
                    WHERE user_id=:user_id AND local_date BETWEEN :start AND :end
                    ORDER BY local_date"""),
                    params,
                )
                .mappings()
                .all()
            )
            plans = (
                connection.execute(
                    text("""SELECT p.question_id, p.due_date, p.correct_streak, p.latest_attempt_id
                    FROM review_plans p WHERE p.user_id=:user_id AND p.question_id IN
                    (SELECT v.question_id FROM attempts a JOIN question_versions v
                     ON v.id=a.question_version_id WHERE a.user_id=:user_id
                     AND a.local_date BETWEEN :start AND :end) ORDER BY p.question_id"""),
                    params,
                )
                .mappings()
                .all()
            )
            # 独立执行 MySQL 聚合，避免只核对从同一份 Python 列表计算出的行数。
            aggregates = connection.execute(
                text(f"""SELECT a.local_date, d.code AS domain_code, {SOURCE} AS source,
                    COUNT(*) AS attempts, SUM(a.score=100) AS correct, SUM(a.score) AS score_sum
                    {ATTEMPT_FROM} GROUP BY a.local_date, d.code, {SOURCE}
                    ORDER BY a.local_date, d.code, {SOURCE}"""),
                params,
            ).all()
    return info, attempts, check_ins, plans, [tuple(row) for row in aggregates]


def export_rows(attempts, check_ins, plans):
    # 随机批次密钥只在本次请求内存中使用，不输出或持久化，跨批次编号不可关联。
    key = secrets.token_bytes(32)

    def ref(kind, value):
        return hmac.new(key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()

    included = {row["id"] for row in attempts}
    return {
        "attempts": [
            (
                ref("attempt", a["id"]),
                ref("question", a["question_id"]),
                ref("version", a["question_version_id"]),
                a["version"],
                a["domain_code"],
                a["track_code"],
                a["source"],
                a["submitted_at"],
                a["local_date"],
                a["answer"],
                a["score"],
                a["score"] == 100,
                a["scheduled_due_date"],
                a["review_due_date"],
                a["review_interval_days"],
                a["review_reason"],
                a["rule_version"],
                ref("attempt", a["review_basis_attempt_id"])
                if a["review_basis_attempt_id"] in included
                else None,
                a["review_basis_attempt_id"] is not None
                and a["review_basis_attempt_id"] not in included,
            )
            for a in attempts
        ],
        "check_ins": [(c["local_date"], ref("attempt", c["first_attempt_id"])) for c in check_ins],
        "review_plans": [
            (
                ref("question", p["question_id"]),
                p["due_date"],
                p["correct_streak"],
                ref("attempt", p["latest_attempt_id"])
                if p["latest_attempt_id"] in included
                else None,
                p["latest_attempt_id"] not in included,
            )
            for p in plans
        ],
    }


def write_parquet(db, table, path):
    # 表名来自固定白名单；文件名通过绑定参数传递。
    db.execute(f"COPY {table} TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(path)])


def verify_parquet(db, directory, rows, mysql_aggregates):
    counts = {}
    for name, schema in SCHEMAS.items():
        db.execute(f"CREATE TABLE expected_{name} ({schema})")
        if rows[name]:
            placeholders = ",".join("?" for _ in rows[name][0])
            db.executemany(f"INSERT INTO expected_{name} VALUES ({placeholders})", rows[name])
        write_parquet(db, f"expected_{name}", directory / f"{name}.parquet")
        db.execute(
            f"CREATE TABLE actual_{name} AS SELECT * FROM read_parquet(?)",
            [str(directory / f"{name}.parquet")],
        )
        expected_schema = db.execute(f"DESCRIBE expected_{name}").fetchall()
        actual_schema = db.execute(f"DESCRIBE actual_{name}").fetchall()
        if expected_schema != actual_schema:
            raise ValueError("导出字段或类型不一致")
        for left, right in (("expected", "actual"), ("actual", "expected")):
            difference = db.execute(
                f"SELECT * FROM {left}_{name} EXCEPT ALL SELECT * FROM {right}_{name} LIMIT 1"
            ).fetchone()
            if difference is not None:
                raise ValueError("导出内容不一致")
        counts[name] = db.execute(f"SELECT count(*) FROM actual_{name}").fetchone()[0]

    invalid_refs = db.execute("""
        SELECT 1 FROM actual_check_ins c LEFT JOIN actual_attempts a
        ON a.attempt_ref=c.first_attempt_ref AND a.local_date=c.local_date
        WHERE a.attempt_ref IS NULL
        UNION ALL
        SELECT 1 FROM actual_attempts a LEFT JOIN actual_attempts b
        ON b.attempt_ref=a.review_basis_ref AND b.question_ref=a.question_ref
        WHERE a.review_basis_ref IS NOT NULL AND b.attempt_ref IS NULL
        UNION ALL
        SELECT 1 FROM actual_review_plans p LEFT JOIN actual_attempts a
        ON a.attempt_ref=p.latest_attempt_ref AND a.question_ref=p.question_ref
        WHERE p.latest_attempt_ref IS NOT NULL AND a.attempt_ref IS NULL
        LIMIT 1
    """).fetchone()
    if invalid_refs:
        raise ValueError("导出关联不一致")
    actual_aggregates = db.execute("""SELECT local_date, domain_code, source,
        COUNT(*), SUM(CASE WHEN correct THEN 1 ELSE 0 END), SUM(score)
        FROM actual_attempts GROUP BY local_date, domain_code, source
        ORDER BY local_date, domain_code, source""").fetchall()
    if actual_aggregates != mysql_aggregates:
        raise ValueError("MySQL 与 Parquet 汇总不一致")
    return {
        "status": "passed",
        "schema": "passed",
        "exact_rows": "passed",
        "references": "passed",
        "mysql_aggregates": "passed",
        "counts": counts,
        "aggregate_columns": [
            "local_date",
            "domain_code",
            "source",
            "attempts",
            "correct",
            "score_sum",
        ],
        "mysql": [
            [str(r[0]), r[1], r[2], int(r[3]), int(r[4]), int(r[5])] for r in mysql_aggregates
        ],
        "parquet": [
            [str(r[0]), r[1], r[2], int(r[3]), int(r[4]), int(r[5])] for r in actual_aggregates
        ],
    }


def build_archive(engine, user_id, options):
    info, attempts, check_ins, plans, aggregates = read_snapshot(engine, user_id, options)
    rows = export_rows(attempts, check_ins, plans)
    EXPORT_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="export-", dir=EXPORT_ROOT) as temporary:
        directory = Path(temporary)
        with duckdb.connect(
            config={
                "threads": 1,
                "memory_limit": "128MB",
                "temp_directory": str(directory / "spill"),
            }
        ) as db:
            report = verify_parquet(db, directory, rows, aggregates)
        manifest = {
            "schema_version": "learning-export-v1",
            "export_id": secrets.token_hex(16),
            "snapshot_at_utc": info["captured_at"].isoformat() + "Z",
            "account_timezone": info["timezone"],
            "start_date": options.start_date.isoformat(),
            "end_date": options.end_date.isoformat(),
            "include_answers": options.include_answers,
            "date_basis": "stored_attempt_local_date_inclusive",
            "scope": "current_user_all_domains",
            "review_plans_scope": "current_snapshot_for_questions_attempted_in_range",
            "counts": report["counts"],
            "duckdb_version": duckdb.__version__,
            "files": {
                f"{name}.parquet": {
                    "sha256": hashlib.sha256(
                        (directory / f"{name}.parquet").read_bytes()
                    ).hexdigest(),
                    "columns": [part.strip().split()[0] for part in schema.split(",")],
                }
                for name, schema in SCHEMAS.items()
            },
        }
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in SCHEMAS:
                archive.write(directory / f"{name}.parquet", f"{name}.parquet")
            for name, content in (("manifest", manifest), ("verification", report)):
                archive.writestr(f"{name}.json", json.dumps(content, ensure_ascii=False, indent=2))
            archive.writestr("说明.txt", EXPORT_README)
        return output.getvalue()


EXPORT_README = """个人学习数据 · learning-export-v1
范围：当前账号、所有领域，按作答入库时的本地日期筛选，开始和结束日均包含。
attempts.parquet：每日练习/复习、原题目版本号、UTC 提交时间、对错与调度依据。
selected_answer 默认全为 NULL；只有主动勾选才包含自己提交的选项。不含标准答案和题目正文。
check_ins.parquet：范围内打卡日期和当日首个作答的批次编号。
review_plans.parquet：范围内作答涉及题目的当前计划，不是结束日期当天的历史计划。
*_ref：仅本批次可关联的随机化编号，不是数据库 ID，跨批次不能直接拼接。
review_basis_ref / latest_attempt_ref：关联作答在范围外时为 NULL，对应 *_outside_range 为 true。
scheduled_due_date：本次复习原定日期；review_due_date：本次作答产生的下次复习日期。
review_interval_days / review_reason / rule_version：当次间隔、调度理由、规则版本。
current_due_date / correct_streak：导出快照时的计划到期日与连续正确阶段。
manifest.json：范围、快照时间、时区、字段清单、行数、Parquet 文件 SHA-256。
verification.json：DuckDB 重读 Parquet，类型、双向逐行多重集、引用检查和独立 MySQL 聚合结果。
所有 MySQL 读取使用同一个 REPEATABLE READ 一致性快照。快照之后的新作答不在该批次内。
文件不含用户名、账号 ID、密码、会话、幂等键、题目标准答案或其他用户记录。
时间和学习轨迹仍属于个人数据，这不是完全匿名的数据集，请妥善保存，不提交到公开仓库。
不包含点击、浏览、停留时长；本项目尚未采集这些日志。文件未加密，不自动上传到第三方。
"""


@router.post("/exports")
def learning_export(options: ExportRequest, request: Request, user: AuthenticatedUser):
    if not EXPORT_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "导出任务较多，请稍后重试", headers={"Retry-After": "5"})
    try:
        try:
            content = build_archive(request.app.state.db_engine, user.id, options)
        except (duckdb.Error, ValueError, OSError):
            # 核对失败不交付半成品，不回显可能包含个人记录的底层异常。
            raise HTTPException(503, "导出或一致性核对失败，请稍后重试") from None
        filename = f"learning-data-{options.start_date}-{options.end_date}.zip"
        return Response(
            content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Content-Type-Options": "nosniff",
            },
        )
    finally:
        EXPORT_SLOTS.release()

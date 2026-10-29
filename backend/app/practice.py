from datetime import date, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert
from sqlalchemy.orm import Session

from app.auth import AuthenticatedUser
from app.models import (
    Attempt,
    CheckIn,
    DailyAssignment,
    Domain,
    Question,
    QuestionVersion,
    ReviewPlan,
    Track,
    User,
)

router = APIRouter(prefix="/api/v1", tags=["每日练习"])
RULE_VERSION = "review-v2"


class Submission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    daily_assignment_id: int = Field(gt=0)
    question_version_id: int = Field(gt=0)
    answer: str = Field(pattern=r"^[A-D]$")
    idempotency_key: UUID


def local_day(now, user: User) -> date:
    return now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(user.timezone)).date()


def review_schedule(correct: bool, previous_streak: int):
    streak = previous_streak + 1 if correct else 0
    interval = (1, 3, 7, 14)[min(streak - 1, 3)] if correct else 1
    reason = f"连续答对 {streak} 次，{interval} 天后复习" if correct else "本次答错，次日复习"
    return streak, interval, reason


def attempt_result(attempt: Attempt, version: QuestionVersion):
    return {
        "id": attempt.id,
        "answer": attempt.answer,
        "score": attempt.score,
        "correct_answer": version.correct_answer,
        "explanation": version.explanation,
        "check_in_date": attempt.local_date,
        "review_due_date": attempt.review_due_date,
        "review_interval_days": attempt.review_interval_days,
        "review_reason": attempt.review_reason,
        "rule_version": attempt.rule_version,
    }


@router.get("/tracks")
def tracks(request: Request, domain_code: str):
    with Session(request.app.state.db_engine) as session:
        return [
            dict(row)
            for row in session.execute(
                select(Track.id, Track.name)
                .join(Domain)
                .where(Domain.code == domain_code)
                .order_by(Track.id)
            ).mappings()
        ]


@router.get("/today")
def today(request: Request, user: AuthenticatedUser, track_id: int):
    now = request.app.state.clock()
    day = local_day(now, user)
    with Session(request.app.state.db_engine) as session, session.begin():
        track = session.get(Track, track_id)
        if track is None:
            raise HTTPException(404, "学习方向不存在")
        assignment = session.scalar(
            select(DailyAssignment).where(
                DailyAssignment.track_id == track.id,
                DailyAssignment.local_date == day,
            )
        )
        if assignment is None:
            # 只选各题已发布的最新版本；一旦分配，不受后续新版本影响。
            latest = (
                select(func.max(QuestionVersion.version))
                .where(
                    QuestionVersion.question_id == Question.id,
                    QuestionVersion.published_at <= now,
                )
                .correlate(Question)
                .scalar_subquery()
            )
            candidates = session.scalars(
                select(QuestionVersion)
                .join(Question)
                .where(
                    Question.track_id == track.id,
                    QuestionVersion.version == latest,
                    QuestionVersion.published_at <= now,
                )
                .order_by(Question.id)
            ).all()
            if not candidates:
                return {"date": day, "track_id": track.id, "assignment": None}
            chosen = candidates[day.toordinal() % len(candidates)]
            statement = insert(DailyAssignment).values(
                track_id=track.id,
                local_date=day,
                question_version_id=chosen.id,
            )
            session.execute(statement.on_duplicate_key_update(id=DailyAssignment.id))
            # 锁定读可看到并发请求刚提交的唯一记录，避免 REPEATABLE READ 旧快照。
            assignment = session.scalar(
                select(DailyAssignment)
                .where(
                    DailyAssignment.track_id == track.id,
                    DailyAssignment.local_date == day,
                )
                .with_for_update()
            )
        version = session.get(QuestionVersion, assignment.question_version_id)
        attempt = session.scalar(
            select(Attempt).where(
                Attempt.user_id == user.id,
                Attempt.daily_assignment_id == assignment.id,
            )
        )
        return {
            "date": day,
            "track_id": track.id,
            "assignment": {
                "id": assignment.id,
                "question_version_id": version.id,
                "stem": version.stem,
                "options": version.options,
                "source": version.source,
                "result": attempt_result(attempt, version) if attempt else None,
            },
        }


@router.post("/attempts")
def submit(data: Submission, request: Request, user: AuthenticatedUser):
    with Session(request.app.state.db_engine) as session, session.begin():
        # 同一用户的提交串行化，保护每日完成、打卡与复习计划；不同用户互不阻塞。
        locked_user = session.scalar(select(User).where(User.id == user.id).with_for_update())
        existing = session.scalar(
            select(Attempt).where(
                Attempt.user_id == user.id,
                Attempt.idempotency_key == str(data.idempotency_key),
            )
        )
        if existing:
            if (existing.daily_assignment_id, existing.question_version_id, existing.answer) != (
                data.daily_assignment_id,
                data.question_version_id,
                data.answer,
            ):
                raise HTTPException(409, "提交标识已用于其他答案，请重新加载题目")
            return attempt_result(
                existing, session.get(QuestionVersion, existing.question_version_id)
            )
        assignment = session.get(DailyAssignment, data.daily_assignment_id)
        if assignment is None:
            raise HTTPException(404, "每日题不存在")
        now = request.app.state.clock()
        day = local_day(now, locked_user)
        if assignment.local_date != day:
            raise HTTPException(409, "日期已变更，请重新加载今日题目")
        if assignment.question_version_id != data.question_version_id:
            raise HTTPException(409, "题目版本不匹配，请重新加载")
        completed = session.scalar(
            select(Attempt.id).where(
                Attempt.user_id == user.id,
                Attempt.daily_assignment_id == assignment.id,
            )
        )
        if completed:
            raise HTTPException(409, "今日题目已提交，请重新加载查看结果")
        version = session.get(QuestionVersion, assignment.question_version_id)
        if data.answer not in {option["id"] for option in version.options}:
            raise HTTPException(422, "请选择有效选项")
        return save_attempt(
            session, locked_user, version, data, now, day, daily_assignment_id=assignment.id
        )


def save_attempt(session, user, version, data, now, day, *, daily_assignment_id=None, plan=None):
    # 所有练习共用一次事务：作答、打卡与计划一起成功或回滚。
    if plan is None:
        plan = session.get(ReviewPlan, (user.id, version.question_id))
    previous = session.get(Attempt, plan.latest_attempt_id) if plan else None
    correct = data.answer == version.correct_answer
    streak, interval, reason = review_schedule(correct, plan.correct_streak if plan else 0)
    due_date = day + timedelta(days=interval)
    if previous and previous.local_date == day:
        # 每日题与复习碰到同一道题时，第二次答对不重复推进阶段；答错仍重置。
        if correct:
            streak, due_date = plan.correct_streak, plan.due_date
            interval = (due_date - day).days
            reason = "同题今日已调度，答对保留已有复习安排"
    attempt = Attempt(
        user_id=user.id,
        daily_assignment_id=daily_assignment_id,
        review_question_id=version.question_id if daily_assignment_id is None else None,
        review_basis_attempt_id=previous.id if daily_assignment_id is None else None,
        scheduled_due_date=plan.due_date if daily_assignment_id is None else None,
        question_version_id=version.id,
        idempotency_key=str(data.idempotency_key),
        answer=data.answer,
        score=100 if correct else 0,
        submitted_at=now,
        local_date=day,
        review_due_date=due_date,
        review_interval_days=interval,
        review_reason=reason,
        rule_version=RULE_VERSION,
    )
    session.add(attempt)
    session.flush()
    if session.get(CheckIn, (user.id, day)) is None:
        session.add(CheckIn(user_id=user.id, local_date=day, first_attempt_id=attempt.id))
    if plan is None:
        plan = ReviewPlan(user_id=user.id, question_id=version.question_id)
        session.add(plan)
    plan.correct_streak = streak
    plan.due_date = attempt.review_due_date
    plan.latest_attempt_id = attempt.id
    session.flush()
    return attempt_result(attempt, version)


@router.get("/attempts/{attempt_id}")
def result(attempt_id: int, request: Request, user: AuthenticatedUser):
    with Session(request.app.state.db_engine) as session:
        attempt = session.scalar(
            select(Attempt).where(
                Attempt.id == attempt_id,
                Attempt.user_id == user.id,
            )
        )
        if attempt is None:
            raise HTTPException(404, "作答记录不存在")
        return attempt_result(attempt, session.get(QuestionVersion, attempt.question_version_id))

from datetime import date
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import AuthenticatedUser
from app.models import Attempt, Domain, Question, QuestionVersion, ReviewPlan, Track, User
from app.practice import attempt_result, local_day, save_attempt

router = APIRouter(prefix="/api/v1", tags=["个人复习与学习记录"])


class ReviewSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_version_id: int = Field(gt=0)
    basis_attempt_id: int = Field(gt=0)
    answer: str = Field(pattern=r"^[A-D]$")
    idempotency_key: UUID


def question_content(version):
    return {
        "question_version_id": version.id,
        "stem": version.stem,
        "options": version.options,
        "source": version.source,
    }


@router.get("/reviews")
def reviews(
    request: Request,
    user: AuthenticatedUser,
    domain_code: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    day = local_day(request.app.state.clock(), user)
    with Session(request.app.state.db_engine) as session:
        query = (
            select(ReviewPlan, Attempt, QuestionVersion, Track)
            .join(Attempt, Attempt.id == ReviewPlan.latest_attempt_id)
            .join(QuestionVersion, QuestionVersion.id == Attempt.question_version_id)
            .join(Question, Question.id == ReviewPlan.question_id)
            .join(Track, Track.id == Question.track_id)
            .join(Domain)
            .where(
                ReviewPlan.user_id == user.id,
                ReviewPlan.due_date <= day,
                Domain.code == domain_code,
            )
        )
        total = session.scalar(select(func.count()).select_from(query.subquery()))
        rows = session.execute(
            query.order_by(ReviewPlan.due_date, ReviewPlan.question_id).offset(offset).limit(limit)
        ).all()
        return {
            "date": day,
            "total": total,
            "items": [
                {
                    "question_id": plan.question_id,
                    "stem": version.stem,
                    "track_name": track.name,
                    "due_date": plan.due_date,
                    "overdue_days": (day - plan.due_date).days,
                    "reason": attempt.review_reason,
                }
                for plan, attempt, version, track in rows
            ],
        }


@router.get("/reviews/{question_id}")
def review_detail(question_id: int, request: Request, user: AuthenticatedUser):
    day = local_day(request.app.state.clock(), user)
    with Session(request.app.state.db_engine) as session:
        plan = session.get(ReviewPlan, (user.id, question_id))
        if plan is None:
            raise HTTPException(404, "复习任务不存在")
        previous = session.get(Attempt, plan.latest_attempt_id)
        completed = session.scalar(
            select(Attempt).where(
                Attempt.user_id == user.id,
                Attempt.review_question_id == question_id,
                Attempt.local_date == day,
            )
        )
        # 今日已完成时返回当次结果，刷新不能重新开启一轮复习。
        version = session.get(QuestionVersion, (completed or previous).question_version_id)
        return {
            **question_content(version),
            "question_id": question_id,
            "date": day,
            "basis_attempt_id": previous.id,
            "due_date": plan.due_date,
            "reason": previous.review_reason,
            "can_submit": completed is None and plan.due_date <= day,
            "result": attempt_result(completed, version) if completed else None,
        }


@router.post("/reviews/{question_id}/attempts")
def submit_review(
    question_id: int, data: ReviewSubmission, request: Request, user: AuthenticatedUser
):
    with Session(request.app.state.db_engine) as session, session.begin():
        locked_user = session.scalar(select(User).where(User.id == user.id).with_for_update())
        existing = session.scalar(
            select(Attempt).where(
                Attempt.user_id == user.id,
                Attempt.idempotency_key == str(data.idempotency_key),
            )
        )
        if existing:
            if (
                existing.review_question_id,
                existing.review_basis_attempt_id,
                existing.question_version_id,
                existing.answer,
            ) != (
                question_id,
                data.basis_attempt_id,
                data.question_version_id,
                data.answer,
            ):
                raise HTTPException(409, "提交标识已用于其他答案，请重新加载")
            return attempt_result(
                existing, session.get(QuestionVersion, existing.question_version_id)
            )
        plan = session.get(ReviewPlan, (user.id, question_id))
        if plan is None:
            raise HTTPException(404, "复习任务不存在")
        now = request.app.state.clock()
        day = local_day(now, locked_user)
        previous = session.get(Attempt, plan.latest_attempt_id)
        if (
            plan.latest_attempt_id != data.basis_attempt_id
            or previous.question_version_id != data.question_version_id
        ):
            raise HTTPException(409, "复习安排已更新，请重新加载")
        if plan.due_date > day:
            raise HTTPException(409, "尚未到复习日期，请按计划再来")
        completed = session.scalar(
            select(Attempt.id).where(
                Attempt.user_id == user.id,
                Attempt.review_question_id == question_id,
                Attempt.local_date == day,
            )
        )
        if completed:
            raise HTTPException(409, "这道题今天已复习，请重新加载查看结果")
        version = session.get(QuestionVersion, previous.question_version_id)
        if data.answer not in {option["id"] for option in version.options}:
            raise HTTPException(422, "请选择有效选项")
        return save_attempt(session, locked_user, version, data, now, day, plan=plan)


@router.get("/history")
def history(
    request: Request,
    user: AuthenticatedUser,
    domain_code: str,
    before_id: int | None = Query(None, gt=0),
    limit: int = Query(20, ge=1, le=100),
    source: str = Query("all", pattern="^(all|daily|review)$"),
    start_date: date | None = None,
    end_date: date | None = None,
):
    if start_date and end_date and start_date > end_date:
        raise HTTPException(422, "开始日期不能晚于结束日期")
    with Session(request.app.state.db_engine) as session:
        query = (
            select(Attempt, QuestionVersion, Track)
            .join(QuestionVersion, QuestionVersion.id == Attempt.question_version_id)
            .join(Question, Question.id == QuestionVersion.question_id)
            .join(Track, Track.id == Question.track_id)
            .join(Domain)
            .where(Attempt.user_id == user.id, Domain.code == domain_code)
        )
        if source == "daily":
            query = query.where(Attempt.daily_assignment_id.is_not(None))
        elif source == "review":
            query = query.where(Attempt.review_question_id.is_not(None))
        if start_date:
            query = query.where(Attempt.local_date >= start_date)
        if end_date:
            query = query.where(Attempt.local_date <= end_date)
        if before_id:
            query = query.where(Attempt.id < before_id)
        rows = session.execute(query.order_by(Attempt.id.desc()).limit(limit + 1)).all()
        items = [
            {
                "id": attempt.id,
                "stem": version.stem,
                "track_name": track.name,
                "date": attempt.local_date,
                "source": "daily" if attempt.daily_assignment_id else "review",
                "correct": attempt.score == 100,
                "review_due_date": attempt.review_due_date,
            }
            for attempt, version, track in rows[:limit]
        ]
        return {"items": items, "next_cursor": items[-1]["id"] if len(rows) > limit else None}


@router.get("/history/{attempt_id}")
def history_detail(attempt_id: int, request: Request, user: AuthenticatedUser):
    with Session(request.app.state.db_engine) as session:
        attempt = session.scalar(
            select(Attempt).where(Attempt.id == attempt_id, Attempt.user_id == user.id)
        )
        if attempt is None:
            raise HTTPException(404, "作答记录不存在")
        version = session.get(QuestionVersion, attempt.question_version_id)
        plan = session.get(ReviewPlan, (user.id, version.question_id))
        return {
            **question_content(version),
            "version": version.version,
            "source": "daily" if attempt.daily_assignment_id else "review",
            "result": attempt_result(attempt, version),
            "scheduled_due_date": attempt.scheduled_due_date,
            "current_due_date": plan.due_date if plan else None,
        }

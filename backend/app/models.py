from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Domain(Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(64))


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(32), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Singapore")


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class Track(Base):
    __tablename__ = "tracks"
    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(ForeignKey("domains.id"))
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(64))


class Question(Base):
    __tablename__ = "questions"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"))


class QuestionVersion(Base):
    __tablename__ = "question_versions"
    __table_args__ = (UniqueConstraint("question_id", "version", name="uq_question_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"))
    version: Mapped[int] = mapped_column(default=1)
    stem: Mapped[str] = mapped_column(Text)
    options: Mapped[list] = mapped_column(JSON)
    correct_answer: Mapped[str] = mapped_column(String(1))
    explanation: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(255))
    published_at: Mapped[datetime] = mapped_column(DateTime)


class DailyAssignment(Base):
    __tablename__ = "daily_assignments"
    __table_args__ = (UniqueConstraint("track_id", "local_date", name="uq_daily_track_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    track_id: Mapped[int] = mapped_column(ForeignKey("tracks.id"))
    local_date: Mapped[date] = mapped_column(Date)
    question_version_id: Mapped[int] = mapped_column(ForeignKey("question_versions.id"))


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_attempt_user_key"),
        UniqueConstraint("user_id", "daily_assignment_id", name="uq_attempt_user_daily"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    daily_assignment_id: Mapped[int] = mapped_column(ForeignKey("daily_assignments.id"))
    question_version_id: Mapped[int] = mapped_column(ForeignKey("question_versions.id"))
    idempotency_key: Mapped[str] = mapped_column(String(36))
    answer: Mapped[str] = mapped_column(String(1))
    score: Mapped[int]
    submitted_at: Mapped[datetime] = mapped_column(DateTime)
    local_date: Mapped[date] = mapped_column(Date)
    # 每次调度依据保存在不可变的作答记录中，计划变化不会覆盖历史。
    review_due_date: Mapped[date] = mapped_column(Date)
    review_interval_days: Mapped[int]
    review_reason: Mapped[str] = mapped_column(String(128))
    rule_version: Mapped[str] = mapped_column(String(32))


class CheckIn(Base):
    __tablename__ = "check_ins"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    local_date: Mapped[date] = mapped_column(Date, primary_key=True)
    first_attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"))


class ReviewPlan(Base):
    __tablename__ = "review_plans"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id"), primary_key=True)
    correct_streak: Mapped[int]
    due_date: Mapped[date] = mapped_column(Date)
    latest_attempt_id: Mapped[int] = mapped_column(ForeignKey("attempts.id"))

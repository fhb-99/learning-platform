"""题库管理：草稿可修改，发布只追加快照，不触碰已分配题目与作答。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.auth import AuthenticatedUser
from app.models import Domain, Question, QuestionDraft, QuestionVersion, Track, User

router = APIRouter(prefix="/api/v1/admin", tags=["题库管理"])
CONTENT_FIELDS = ("stem", "options", "correct_answer", "explanation", "source")


def require_admin(user: AuthenticatedUser):
    if not user.is_admin:
        raise HTTPException(403, "需要题库管理员权限")
    return user


AdminUser = Annotated[User, Depends(require_admin)]


class Option(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    id: Literal["A", "B", "C", "D"]
    text: str = Field(max_length=2000)


class Content(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    stem: str = Field(max_length=10000)
    options: list[Option] = Field(min_length=4, max_length=4)
    correct_answer: Literal["", "A", "B", "C", "D"]
    explanation: str = Field(max_length=10000)
    source: str = Field(max_length=255)

    @model_validator(mode="after")
    def ordered_options(self):
        if [option.id for option in self.options] != list("ABCD"):
            raise ValueError("选项必须按 A、B、C、D 排列且不重复")
        return self


class NewQuestion(Content):
    code: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{2,63}$")
    track_id: int = Field(gt=0)


class DraftUpdate(Content):
    revision: int = Field(ge=0)


class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(gt=0)


def content_dict(content):
    return {field: getattr(content, field) for field in CONTENT_FIELDS}


def lock_editor(session, user):
    # 与本机撤权命令使用同一行锁；撤权完成后，旧会话不能继续修改题库。
    editor = session.scalar(select(User).where(User.id == user.id).with_for_update())
    if editor is None or not editor.is_admin:
        raise HTTPException(403, "需要题库管理员权限")


def get_question(session, question_id, lock=False):
    query = select(Question).where(Question.id == question_id)
    question = session.scalar(query.with_for_update() if lock else query)
    if question is None:
        raise HTTPException(404, "题目不存在")
    return question


def draft_response(draft):
    return {
        **content_dict(draft),
        "revision": draft.revision,
        "published_version_id": draft.published_version_id,
        "updated_by": draft.updated_by,
        "updated_at": draft.updated_at,
    }


@router.get("/questions")
def questions(
    request: Request,
    user: AdminUser,
    track_id: int = Query(gt=0),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=50),
):
    with Session(request.app.state.db_engine) as session:
        total = session.scalar(
            select(func.count()).select_from(Question).where(Question.track_id == track_id)
        )
        rows = session.scalars(
            select(Question)
            .where(Question.track_id == track_id)
            .order_by(Question.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
        items = []
        for question in rows:
            latest = session.scalar(
                select(QuestionVersion)
                .where(QuestionVersion.question_id == question.id)
                .order_by(QuestionVersion.version.desc())
                .limit(1)
            )
            draft = session.get(QuestionDraft, question.id)
            items.append(
                {
                    "id": question.id,
                    "code": question.code,
                    "stem": draft.stem if draft else latest.stem if latest else "",
                    "latest_version": latest.version if latest else None,
                    "has_draft": draft is not None and draft.published_version_id is None,
                }
            )
        return {"total": total, "items": items}


@router.post("/questions", status_code=201)
def create_question(data: NewQuestion, request: Request, user: AdminUser):
    try:
        with Session(request.app.state.db_engine) as session, session.begin():
            lock_editor(session, user)
            if session.get(Track, data.track_id) is None:
                raise HTTPException(404, "学习方向不存在")
            question = Question(code=data.code, track_id=data.track_id)
            session.add(question)
            session.flush()
            session.add(
                QuestionDraft(
                    question_id=question.id,
                    revision=1,
                    updated_by=user.id,
                    updated_at=request.app.state.clock(),
                    **data.model_dump(include=set(CONTENT_FIELDS)),
                )
            )
            return {"id": question.id}
    except IntegrityError:
        raise HTTPException(409, "题目标识已存在，请在题目列表中查看，勿重复创建") from None


@router.get("/questions/{question_id}")
def question_detail(question_id: int, request: Request, user: AdminUser):
    with Session(request.app.state.db_engine) as session:
        question = get_question(session, question_id)
        domain = session.scalar(
            select(Domain.code).join(Track).where(Track.id == question.track_id)
        )
        draft = session.get(QuestionDraft, question.id)
        latest = session.scalar(
            select(QuestionVersion)
            .where(QuestionVersion.question_id == question.id)
            .order_by(QuestionVersion.version.desc())
            .limit(1)
        )
        return {
            "id": question.id,
            "code": question.code,
            "track_id": question.track_id,
            "domain_code": domain,
            "latest_version": latest.version if latest else None,
            "draft": draft_response(draft)
            if draft
            else {
                **content_dict(latest),
                "revision": 0,
                "published_version_id": latest.id,
                "updated_by": None,
                "updated_at": None,
            }
            if latest
            else None,
        }


@router.put("/questions/{question_id}/draft")
def save_draft(question_id: int, data: DraftUpdate, request: Request, user: AdminUser):
    with Session(request.app.state.db_engine) as session, session.begin():
        lock_editor(session, user)
        get_question(session, question_id, lock=True)
        draft = session.get(QuestionDraft, question_id)
        if data.revision != (draft.revision if draft else 0):
            raise HTTPException(409, "草稿已在其他窗口更新，请重新加载后编辑")
        values = data.model_dump(include=set(CONTENT_FIELDS))
        if draft is None:
            draft = QuestionDraft(question_id=question_id, revision=0)
            session.add(draft)
        for field, value in values.items():
            setattr(draft, field, value)
        draft.revision += 1
        draft.updated_at = request.app.state.clock()
        draft.updated_by = user.id
        draft.published_version_id = None
        return draft_response(draft)


@router.post("/questions/{question_id}/publish")
def publish(question_id: int, data: Publication, request: Request, user: AdminUser):
    with Session(request.app.state.db_engine) as session, session.begin():
        lock_editor(session, user)
        get_question(session, question_id, lock=True)
        draft = session.get(QuestionDraft, question_id)
        if draft is None or draft.revision != data.revision:
            raise HTTPException(409, "草稿已变化或尚未保存，请重新加载并保存后发布")
        if draft.published_version_id is not None:
            version = session.get(QuestionVersion, draft.published_version_id)
            return {"id": version.id, "version": version.version}
        # 草稿允许不完整；只有完整、有效的四选一内容才能成为发布版本。
        content = Content.model_validate(content_dict(draft))
        if (
            not all((content.stem, content.correct_answer, content.explanation, content.source))
            or not all(option.text for option in content.options)
            or len({option.text for option in content.options}) != 4
        ):
            raise HTTPException(422, "发布前请填写题干、四个不同选项、正确答案、解析与来源")
        latest = (
            session.scalar(
                select(func.max(QuestionVersion.version)).where(
                    QuestionVersion.question_id == question_id
                )
            )
            or 0
        )
        version = QuestionVersion(
            question_id=question_id,
            version=latest + 1,
            **content.model_dump(),
            published_at=request.app.state.clock(),
            published_by=user.id,
        )
        session.add(version)
        session.flush()
        draft.published_version_id = version.id
        return {"id": version.id, "version": version.version}


@router.get("/questions/{question_id}/versions")
def versions(
    question_id: int,
    request: Request,
    user: AdminUser,
    before_version: int | None = Query(None, gt=0),
):
    with Session(request.app.state.db_engine) as session:
        get_question(session, question_id)
        query = select(QuestionVersion).where(QuestionVersion.question_id == question_id)
        if before_version is not None:
            query = query.where(QuestionVersion.version < before_version)
        rows = session.scalars(query.order_by(QuestionVersion.version.desc()).limit(21)).all()
        return {
            "items": [
                {
                    **content_dict(row),
                    "id": row.id,
                    "version": row.version,
                    "published_at": row.published_at,
                    "published_by": row.published_by,
                }
                for row in rows[:20]
            ],
            "next_cursor": rows[19].version if len(rows) > 20 else None,
        }

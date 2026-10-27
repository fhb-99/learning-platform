import hashlib
import hmac
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import AuthSession, User

router = APIRouter(prefix="/api/v1/auth", tags=["认证"])
COOKIE_NAME = "learning_session"
SESSION_SECONDS = 7 * 24 * 3600


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(pattern=r"^[a-z][a-z0-9_]{2,31}$")
    password: str = Field(min_length=10, max_length=128)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(),
        salt=bytes.fromhex(salt),
        n=2**15,
        r=8,
        p=3,
        maxmem=64 * 1024 * 1024,
    ).hex()
    return f"scrypt-v1${salt}${digest}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def csrf_token(token: str) -> str:
    return hashlib.sha256(f"csrf:{token}".encode()).hexdigest()


def require_browser_header(request: Request):
    # 自定义请求头配合不开放跨域，阻止第三方页面提交登录/注册表单。
    if request.headers.get("X-Requested-With") != "learning-platform":
        raise HTTPException(403, "请求来源校验失败")


def current_user(request: Request) -> User:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 128:
        raise HTTPException(401, "请先登录")
    with Session(request.app.state.db_engine) as session:
        user = session.scalar(
            select(User)
            .join(AuthSession)
            .where(
                AuthSession.token_hash == token_hash(token),
                AuthSession.expires_at > request.app.state.clock(),
            )
        )
        if user is None:
            raise HTTPException(401, "登录已失效，请重新登录")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        supplied = request.headers.get("X-CSRF-Token", "")
        if not hmac.compare_digest(supplied, csrf_token(token)):
            raise HTTPException(403, "请求校验失败，请刷新页面")
    return user


AuthenticatedUser = Annotated[User, Depends(current_user)]


def account_response(user: User, token: str):
    return {"username": user.username, "timezone": user.timezone, "csrf_token": csrf_token(token)}


def establish_session(session: Session, user: User, request: Request, response: Response):
    token = secrets.token_urlsafe(32)
    # 在同一浏览器切换账号时撤销旧会话，不在数据库保存原始令牌。
    old_token = request.cookies.get(COOKIE_NAME)
    if old_token:
        session.execute(delete(AuthSession).where(AuthSession.token_hash == token_hash(old_token)))
    session.add(
        AuthSession(
            token_hash=token_hash(token),
            user_id=user.id,
            expires_at=request.app.state.clock() + timedelta(seconds=SESSION_SECONDS),
        )
    )
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=SESSION_SECONDS,
        httponly=True,
        secure=request.app.state.cookie_secure,
        samesite="strict",
        path="/api",
    )
    return account_response(user, token)


@router.post("/register", status_code=201, dependencies=[Depends(require_browser_header)])
def register(data: Credentials, request: Request, response: Response):
    password_hash = hash_password(data.password)
    try:
        with Session(request.app.state.db_engine) as session, session.begin():
            user = User(username=data.username, password_hash=password_hash)
            session.add(user)
            session.flush()
            return establish_session(session, user, request, response)
    except IntegrityError:
        raise HTTPException(409, "用户名已被使用") from None


@router.post("/login", dependencies=[Depends(require_browser_header)])
def login(data: Credentials, request: Request, response: Response):
    with Session(request.app.state.db_engine) as session, session.begin():
        user = session.scalar(select(User).where(User.username == data.username))
        # 不存在的账号也执行哈希，避免直接通过耗时区别账号是否存在。
        salt = user.password_hash.split("$")[1] if user else "00" * 16
        candidate = hash_password(data.password, salt)
        if user is None or not hmac.compare_digest(candidate, user.password_hash):
            raise HTTPException(401, "用户名或密码不正确")
        return establish_session(session, user, request, response)


@router.get("/me")
def me(request: Request, user: AuthenticatedUser):
    return account_response(user, request.cookies[COOKIE_NAME])


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, user: AuthenticatedUser):
    with Session(request.app.state.db_engine) as session, session.begin():
        session.execute(
            delete(AuthSession).where(
                AuthSession.token_hash == token_hash(request.cookies[COOKIE_NAME])
            )
        )
    response.delete_cookie(COOKIE_NAME, path="/api", samesite="strict")

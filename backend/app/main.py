from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.admin import router as admin_router
from app.auth import router as auth_router
from app.config import Settings
from app.database import create_db_engine
from app.models import Domain
from app.practice import router as practice_router
from app.reviews import router as reviews_router

SCHEMA_REVISION = "0004_question_publishing"


class Readiness(BaseModel):
    status: Literal["ok", "unavailable"]
    database: Literal["connected", "unavailable"]
    schema_status: Literal["ready", "pending", "unknown"]


class DomainResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str


def create_app(engine: Engine | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 测试可注入连接引擎，正式运行只读取后端配置，退出时释放连接池。
        db_engine = engine or create_db_engine(Settings().database_url.get_secret_value())
        app.state.db_engine = db_engine
        app.state.cookie_secure = Settings().cookie_secure if engine is None else False
        try:
            yield
        finally:
            if engine is None:
                db_engine.dispose()

    app = FastAPI(title="知识练习与复习系统", version="0.1.0", lifespan=lifespan)
    # MySQL DateTime 统一保存无时区 UTC；业务日期按用户时区单独计算。
    app.state.clock = lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    app.include_router(auth_router)
    app.include_router(practice_router)
    app.include_router(reviews_router)
    app.include_router(admin_router)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        # 不回显请求原文，尤其不能在认证校验错误中返回密码。
        return JSONResponse(status_code=422, content={"detail": "填写内容不符合要求，请检查后重试"})

    @app.middleware("http")
    async def private_responses(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, error: SQLAlchemyError):
        return JSONResponse(status_code=503, content={"detail": "数据服务暂不可用，请稍后重试"})

    @app.get("/api/health")
    def health():
        # 存活检查不依赖数据库，就绪检查另外确认数据库和迁移状态。
        return {"status": "ok", "service": "learning-platform"}

    @app.get("/api/ready", response_model=Readiness, responses={503: {"model": Readiness}})
    def ready(request: Request):
        database = "unavailable"
        try:
            with request.app.state.db_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
                database = "connected"
                revision = connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar()
                if revision == SCHEMA_REVISION:
                    return Readiness(status="ok", database="connected", schema_status="ready")
        except SQLAlchemyError:
            # 响应不得泄露连接串、账号或驱动异常；未迁移与连接失败均返回 503。
            pass
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "database": database,
                "schema_status": "pending" if database == "connected" else "unknown",
            },
        )

    @app.get("/api/v1/domains", response_model=list[DomainResponse])
    def domains(request: Request):
        try:
            with request.app.state.db_engine.connect() as connection:
                return (
                    connection.execute(select(Domain.code, Domain.name).order_by(Domain.id))
                    .mappings()
                    .all()
                )
        except SQLAlchemyError:
            return JSONResponse(status_code=503, content={"detail": "学习领域暂不可用"})

    return app


app = create_app()

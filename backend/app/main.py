from contextlib import asynccontextmanager
from typing import Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.database import create_db_engine
from app.models import Domain

SCHEMA_REVISION = "0001_domains"


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
        try:
            yield
        finally:
            if engine is None:
                db_engine.dispose()

    app = FastAPI(title="知识练习与复习系统", version="0.1.0", lifespan=lifespan)

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

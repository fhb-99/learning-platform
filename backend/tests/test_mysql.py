import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError

from app.database import create_db_engine
from app.main import SCHEMA_REVISION, create_app

pytestmark = pytest.mark.integration


def test_empty_database_migration_roundtrip_and_api(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("设置 TEST_DATABASE_URL 后运行独立 MySQL 集成测试")
    # 该测试包含降级，只允许显式指定以 _test 结尾的全新专用库。
    assert make_url(url).database.endswith("_test"), "测试库名必须以 _test 结尾"
    engine = create_db_engine(url)
    owns_tables = False
    try:
        assert inspect(engine).get_table_names() == [], "请使用空测试库，拒绝改动已有数据"
        owns_tables = True
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        with TestClient(create_app(engine)) as client:
            assert client.get("/api/ready").status_code == 503
            command.upgrade(config, "head")
            command.upgrade(config, "head")
            assert client.get("/api/ready").status_code == 200
            assert client.get("/api/v1/domains").json() == [
                {"code": "computer", "name": "计算机"},
                {"code": "english", "name": "英语"},
            ]
            with engine.connect() as connection:
                assert (
                    connection.execute(text("SELECT version_num FROM alembic_version")).scalar()
                    == SCHEMA_REVISION
                )
                with pytest.raises(IntegrityError):
                    connection.execute(
                        text("INSERT INTO domains (code, name) VALUES ('computer', '重复')")
                    )
                connection.rollback()
            command.check(config)
            command.downgrade(config, "base")
            assert "domains" not in inspect(engine).get_table_names()
            assert client.get("/api/ready").status_code == 503
            command.upgrade(config, "head")
            assert len(client.get("/api/v1/domains").json()) == 2
    finally:
        if owns_tables:
            # 只清理本次测试在空库中创建的两张表，允许重复运行。
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE IF EXISTS domains"))
                connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()

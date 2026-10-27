import os
from datetime import datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.engine import make_url

from app.database import Base, create_db_engine
from app.main import create_app
from app.seed import seed_samples


@pytest.fixture
def practice_env(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("需要显式配置独立 MySQL 测试库")
    assert make_url(url).database.endswith("_test")
    engine = create_db_engine(url)
    owned = False
    try:
        assert inspect(engine).get_table_names() == [], "拒绝改动非空测试库"
        owned = True
        monkeypatch.setenv("DATABASE_URL", url)
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        command.upgrade(config, "head")
        seed_samples(engine)
        clock = {"now": datetime(2026, 10, 27, 8)}
        app = create_app(engine)
        app.state.clock = lambda: clock["now"]
        yield engine, app, clock
    finally:
        if owned:
            # 仅清理在空的专用测试库中由本测试创建的表，不禁用外键检查。
            Base.metadata.drop_all(engine)
            with engine.begin() as connection:
                connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
        engine.dispose()

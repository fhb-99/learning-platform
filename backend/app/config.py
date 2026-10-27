from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    database_url: SecretStr
    cookie_secure: bool = False

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("database_url")
    @classmethod
    def require_mysql(cls, value: SecretStr) -> SecretStr:
        # 固定驱动，避免开发或 CI 意外换成 SQLite 而遗漏 MySQL 行为差异。
        url = make_url(value.get_secret_value())
        if url.drivername != "mysql+pymysql" or not url.database:
            raise ValueError("DATABASE_URL 必须指向 mysql+pymysql 的独立数据库")
        return value

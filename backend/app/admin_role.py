"""本机维护已有账号的题库权限：python -m app.admin_role 用户名 grant|revoke。"""

import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import create_db_engine
from app.models import User


def set_admin(engine, username: str, enabled: bool):
    with Session(engine) as session, session.begin():
        user = session.scalar(select(User).where(User.username == username).with_for_update())
        if user is None:
            raise ValueError("账号不存在，请先通过注册页创建普通账号")
        user.is_admin = enabled


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("action", choices=["grant", "revoke"])
    args = parser.parse_args()
    engine = create_db_engine(Settings().database_url.get_secret_value())
    try:
        set_admin(engine, args.username, args.action == "grant")
    except ValueError as error:
        raise SystemExit(str(error)) from None
    finally:
        engine.dispose()
    print("题库管理权限已更新，下次请求生效；页面入口请刷新。")


if __name__ == "__main__":
    main()

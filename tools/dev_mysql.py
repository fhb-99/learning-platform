"""使用已安装的 MySQL 8.0 程序，管理仅属于本项目的本地开发实例。"""

import argparse
import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build" / "mysql"
PORT = 3307


def connect(password: str):
    return pymysql.connect(
        host="127.0.0.1",
        port=PORT,
        user="root",
        password=password,
        connect_timeout=2,
        autocommit=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop"])
    parser.add_argument("--bin", type=Path, help="MySQL 安装目录下的 bin 路径")
    args = parser.parse_args()
    credentials_path = WORK / "credentials.json"
    credentials = (
        json.loads(credentials_path.read_text()) if credentials_path.exists() else None
    )

    # 先核对数据目录，不能停止或接管其他实例。
    if credentials:
        try:
            connection = connect(credentials["root"])
        except pymysql.OperationalError:
            connection = None
        if connection:
            with connection, connection.cursor() as cursor:
                cursor.execute("SELECT @@datadir")
                if Path(cursor.fetchone()[0]).resolve() != (WORK / "data").resolve():
                    raise SystemExit("数据目录不属于本项目，拒绝操作。")
                if args.action == "stop":
                    cursor.execute("SHUTDOWN")
            print(
                "项目 MySQL 已停止"
                if args.action == "stop"
                else "项目 MySQL 已在 127.0.0.1:3307 运行"
            )
            return
    if args.action == "stop":
        raise SystemExit("项目 MySQL 未运行；未改动其他实例。")
    if not args.bin or not (args.bin / "mysqld.exe").is_file():
        raise SystemExit("请用 --bin 指定本机 MySQL 8.0 的 bin 目录。")
    args.bin = args.bin.resolve()
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1", PORT)) == 0:
            raise SystemExit("3307 已被其他进程占用，拒绝继续。")

    WORK.mkdir(parents=True, exist_ok=True)
    data = WORK / "data"
    fresh = not data.exists()
    if fresh and (ROOT / "backend" / ".env").exists():
        raise SystemExit("backend/.env 已存在，请先核对其归属；脚本不会覆盖现有配置。")
    if not fresh and not credentials:
        raise SystemExit("数据目录存在但缺少项目凭据，拒绝重新初始化。")
    config = WORK / "my.ini"
    config.write_text(
        "[mysqld]\n"
        f"basedir={args.bin.parent.as_posix()}\n"
        f"datadir={data.as_posix()}\n"
        f"port={PORT}\nbind-address=127.0.0.1\nmysqlx=0\nskip-log-bin\n"
        f"pid-file={(WORK / 'mysql.pid').as_posix()}\n"
        f"log-error={(WORK / 'mysql-error.log').as_posix()}\n"
        "character-set-server=utf8mb4\nmax_connections=30\n",
        encoding="utf-8",
    )
    command = [str(args.bin / "mysqld.exe"), f"--defaults-file={config}"]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    if fresh:
        subprocess.run(
            command + ["--initialize-insecure"], check=True, creationflags=flags
        )
    subprocess.Popen(
        command,
        creationflags=flags,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(30):
        try:
            connection = connect("" if fresh else credentials["root"])
            break
        except pymysql.OperationalError:
            time.sleep(1)
    else:
        raise SystemExit(
            "MySQL 未就绪，请查看 build/mysql/mysql-error.log；未修改其他实例。"
        )
    with connection:
        if fresh:
            credentials = {
                name: secrets.token_hex(24) for name in ("root", "app", "test")
            }
            # 只授权本项目的开发库和独立测试库，凭据不进入命令行或 Git。
            with connection.cursor() as cursor:
                for database, user, key in [
                    ("learning_platform", "learning_app", "app"),
                    ("learning_platform_test", "learning_test", "test"),
                ]:
                    cursor.execute(
                        f"CREATE DATABASE `{database}` CHARACTER SET utf8mb4"
                    )
                    cursor.execute(
                        f"CREATE USER '{user}'@'127.0.0.1' IDENTIFIED BY %s",
                        (credentials[key],),
                    )
                    cursor.execute(
                        f"GRANT ALL ON `{database}`.* TO '{user}'@'127.0.0.1'"
                    )
                cursor.execute(
                    "ALTER USER 'root'@'localhost' IDENTIFIED BY %s",
                    (credentials["root"],),
                )
            credentials_path.write_text(json.dumps(credentials), encoding="utf-8")
            for filename, key, user, database, variable in [
                (
                    ROOT / "backend" / ".env",
                    "app",
                    "learning_app",
                    "learning_platform",
                    "DATABASE_URL",
                ),
                (
                    WORK / "test.env",
                    "test",
                    "learning_test",
                    "learning_platform_test",
                    "TEST_DATABASE_URL",
                ),
            ]:
                filename.write_text(
                    f"{variable}=mysql+pymysql://{user}:{credentials[key]}@127.0.0.1:{PORT}/{database}?charset=utf8mb4\n",
                    encoding="utf-8",
                )
    print(
        "项目 MySQL 已在 127.0.0.1:3307 运行；配置保存在 backend/.env 和 build/mysql/。"
    )


if __name__ == "__main__":
    main()

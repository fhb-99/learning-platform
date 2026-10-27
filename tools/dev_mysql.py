"""通过 Docker Compose 管理本项目的开发和测试 MySQL，不启动宿主机数据库。"""

import argparse
import os
import secrets
import shutil
import subprocess
from pathlib import Path

from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "build" / "docker"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status"])
    args = parser.parse_args()
    docker = shutil.which("docker")
    if not docker and os.name == "nt":
        candidate = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / (
            "Docker/Docker/resources/bin/docker.exe"
        )
        if candidate.is_file():
            docker = str(candidate)
    if not docker:
        raise SystemExit("未找到 Docker，请先安装并启动 Docker Desktop（Linux 容器）。")
    try:
        subprocess.run([docker, "info", "--format", "{{.ServerVersion}}"], check=True, timeout=20)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise SystemExit(
            "Docker 引擎未就绪，请先检查 Docker Desktop；未启动宿主机数据库。"
        ) from None
    command = [docker, "compose", "-p", "learning-platform", "-f", str(ROOT / "compose.yaml")]
    if args.action != "start":
        subprocess.run(command + (["stop"] if args.action == "stop" else ["ps"]), check=True)
        return

    backend_env = ROOT / "backend" / ".env"
    previous = backend_env.read_text(encoding="utf-8") if backend_env.exists() else ""
    # 只允许替换本项目旧连接，保留 Cookie 等无关设置；不接管其他数据库。
    for line in previous.splitlines():
        if line.startswith("DATABASE_URL="):
            url = make_url(line.removeprefix("DATABASE_URL="))
            if (url.host, url.port, url.database) != ("127.0.0.1", 3307, "learning_platform"):
                raise SystemExit("backend/.env 指向其他数据库，拒绝覆盖。")

    WORK.mkdir(parents=True, exist_ok=True)
    for kind, port, database in [
        ("dev", 3307, "learning_platform"),
        ("test", 3308, "learning_platform_test"),
    ]:
        env_path = WORK / f"mysql-{kind}.env"
        if not env_path.exists():
            if kind == "dev" and (WORK / "mysql-data").exists():
                raise SystemExit("容器数据目录已存在但缺少凭据，拒绝重新初始化。")
            env_path.write_text(
                f"MYSQL_ROOT_PASSWORD={secrets.token_hex(24)}\n"
                f"MYSQL_DATABASE={database}\nMYSQL_USER=learning_{kind}\n"
                f"MYSQL_PASSWORD={secrets.token_hex(24)}\n",
                encoding="utf-8",
            )
        values = dict(line.split("=", 1) for line in env_path.read_text().splitlines())
        url = (
            f"mysql+pymysql://{values['MYSQL_USER']}:{values['MYSQL_PASSWORD']}"
            f"@127.0.0.1:{port}/{database}?charset=utf8mb4"
        )
        if kind == "dev":
            other_lines = [
                line for line in previous.splitlines() if not line.startswith("DATABASE_URL=")
            ]
            backend_env.write_text(
                "\n".join([f"DATABASE_URL={url}", *other_lines]) + "\n", encoding="utf-8"
            )
        else:
            (WORK / "test.env").write_text(f"TEST_DATABASE_URL={url}\n", encoding="utf-8")

    subprocess.run(command + ["up", "-d", "--wait", "--wait-timeout", "180"], check=True)
    print("Docker MySQL 已就绪：开发 127.0.0.1:3307，测试 127.0.0.1:3308。")
    print("连接配置位于 backend/.env 与 build/docker/test.env，均不纳入 Git。")


if __name__ == "__main__":
    main()

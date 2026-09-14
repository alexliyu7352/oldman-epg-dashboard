#!/usr/bin/env python3
"""初始化或更新 Oldman 后台管理员。"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
while str(ROOT) in sys.path:
    sys.path.remove(str(ROOT))
sys.path.insert(0, str(ROOT))

from oldman import bootstrap_service  # noqa: E402
from oldman.auth import ensure_superuser  # noqa: E402
from oldman.auth.apps import app as auth_app  # noqa: E402
from oldman.db import db_manager  # noqa: E402


async def create_admin(username: str, password: str, email: str | None) -> None:
    """在已经完成迁移的数据库中确保管理员账号存在。"""
    try:
        await ensure_superuser(
            username,
            password,
            email,
            auth_settings=auth_app.settings,
            db_manager=db_manager,
        )
    finally:
        await db_manager.close()


def main() -> int:
    """解析命令行参数并执行管理员初始化。"""
    parser = argparse.ArgumentParser(description="创建或更新 Oldman 后台管理员")
    parser.add_argument("--username", default="admin", help="管理员用户名")
    parser.add_argument("--email", help="管理员邮箱")
    parser.add_argument("--password", help="管理员密码；不提供时交互输入")
    parser.add_argument(
        "--config",
        type=Path,
        help="测试或 IDE 调试使用的显式 Web settings 文件",
    )
    args = parser.parse_args()

    bootstrap_service("web", config_file=args.config)
    password = args.password or getpass.getpass("管理员密码: ")
    if not password:
        raise SystemExit("密码不能为空")
    asyncio.run(create_admin(args.username, password, args.email))
    print(f"管理员 {args.username} 已准备好")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

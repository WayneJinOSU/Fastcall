"""兼容 shim：存储层已迁移至 storage/db.py。

历史调用点统一 import MysqlClient，实际是 DBClient 的别名；
后端通过 DATABASE_URL 环境变量选择（默认 SQLite）。
"""
from storage.db import DBClient, connection_and_select  # noqa: F401

MysqlClient = DBClient

if __name__ == "__main__":
    connection_and_select()
    print("\n所有基础测试通过！")

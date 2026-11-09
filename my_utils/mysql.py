"""兼容层：历史代码统一 import MysqlClient，实际由 my_utils.db 提供存储。

存储后端通过 DATABASE_URL 环境变量选择（默认 SQLite），详见 my_utils/db.py。
"""

from my_utils.db import DBClient, connection_and_select

# 保持历史调用点（main.py / call_metrics / notes / asr_compare / copilot）不变。
MysqlClient = DBClient

if __name__ == "__main__":
    connection_and_select()
    print("\n所有基础测试通过！")

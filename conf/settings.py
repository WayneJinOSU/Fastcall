import os


class Settings:
    """MySQL 配置（仅在使用 MySQL 存储后端时需要）。

    全部通过环境变量注入；代码库不再携带任何真实主机/账号/密码。
    默认存储后端是 SQLite（见 my_utils/db.py），无需配置本类。
    """

    MYSQL_CONFIG = {
        "host": os.environ.get("MYSQL_HOST", ""),
        "port": int(os.environ.get("MYSQL_PORT", "3306")),
        "username": os.environ.get("MYSQL_USERNAME", ""),
        "password": os.environ.get("MYSQL_PASSWORD", ""),
        "database": os.environ.get("MYSQL_DATABASE", "collection_agents"),
    }

    # 测试环境数据库配置（MODE=test 时生效，同样全部走环境变量）
    MYSQL_TEST_CONFIG = {
        "host": os.environ.get("MYSQL_TEST_HOST", ""),
        "port": int(os.environ.get("MYSQL_TEST_PORT", "3306")),
        "username": os.environ.get("MYSQL_TEST_USERNAME", ""),
        "password": os.environ.get("MYSQL_TEST_PASSWORD", ""),
        "database": os.environ.get("MYSQL_TEST_DATABASE", "collection_agents"),
    }

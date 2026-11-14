import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from fastcall.api.console.auth import hash_password
from fastcall_core.infrastructure.orm import Base, RobotConfigAuditLogModel
from fastcall.infrastructure.unit_of_work import SqlAlchemyUnitOfWork
from fastcall.main import create_app
from fastcall.settings import Settings


class ConsoleApiTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        settings = Settings(
            "test", "mysql+pymysql://unused:unused@localhost/unused",
            "management-token", "resolve-token", "admin",
            hash_password("correct-password"), "x" * 48,
        )
        self.app = create_app(
            settings=settings,
            uow_factory=lambda: SqlAlchemyUnitOfWork(self.sessions()),
            validate_startup=False,
        )
        self.client = TestClient(self.app)

    def tearDown(self):
        self.engine.dispose()

    def login(self):
        response = self.client.post("/console/api/v1/login", json={"actor_id": "admin", "password": "correct-password"})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["csrf_token"]

    def test_session_login_and_csrf(self):
        self.assertEqual(self.client.get("/console/api/v1/robot-apps").status_code, 401)
        bad = self.client.post("/console/api/v1/login", json={"actor_id": "admin", "password": "wrong"})
        self.assertEqual(bad.status_code, 401)
        csrf = self.login()
        session = self.client.get("/console/api/v1/session")
        self.assertEqual(session.status_code, 200)
        self.assertEqual(session.json()["actor_id"], "admin")
        self.assertNotIn("management-token", session.text)
        self.assertNotIn("resolve-token", session.text)
        payload = {"app_code": "console-app", "name": "Console App"}
        self.assertEqual(self.client.post("/console/api/v1/robot-apps", json=payload).status_code, 403)
        created = self.client.post("/console/api/v1/robot-apps", json=payload, headers={"X-CSRF-Token": csrf})
        self.assertEqual(created.status_code, 201, created.text)
        with self.sessions() as session_db:
            audit = session_db.execute(select(RobotConfigAuditLogModel).where(RobotConfigAuditLogModel.action == "app.create")).scalar_one()
            self.assertEqual(audit.actor, "admin")

    def test_logout_deletes_fastcall_cookie(self):
        csrf = self.login()
        response = self.client.post(
            "/console/api/v1/logout", headers={"X-CSRF-Token": csrf}
        )
        self.assertEqual(response.status_code, 200)
        cookies = response.headers.get_list("set-cookie")
        self.assertTrue(any("fastcall_console_session" in cookie for cookie in cookies))

    def test_legacy_cookie_name_is_not_accepted(self):
        self.login()
        token = self.client.cookies.get("fastcall_console_session")
        self.client.cookies.delete("fastcall_console_session")
        self.client.cookies.set("robot_config_console_session", token)
        response = self.client.get("/console/api/v1/robot-apps")
        self.assertEqual(response.status_code, 401)

    def test_default_uow_factory_uses_database_session(self):
        from fastcall.infrastructure.database import Database

        app = create_app(
            settings=self.app.state.settings,
            database=Database(self.engine, self.sessions),
            validate_startup=False,
        )
        client = TestClient(app)
        client.post("/console/api/v1/login", json={"actor_id": "admin", "password": "correct-password"})
        response = client.get("/console/api/v1/robot-apps")
        self.assertEqual(response.status_code, 200, response.text)

    def test_app_code_is_generated_from_name_only(self):
        csrf = self.login()
        headers = {"X-CSRF-Token": csrf}
        first = self.client.post("/console/api/v1/robot-apps", json={"name": "Duplicate Name"}, headers=headers)
        second = self.client.post("/console/api/v1/robot-apps", json={"name": "Duplicate Name"}, headers=headers)
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        first_code = first.json()["app_code"]
        self.assertRegex(first_code, r"^robot_[0-9a-f]{32}$")
        self.assertNotEqual(first_code, second.json()["app_code"])
        self.assertEqual(first.json()["draft"]["app"]["code"], first_code)


class ConsoleStaticTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        (root / "assets").mkdir()
        (root / "index.html").write_text("<html><body>Robot Config</body></html>")
        (root / "assets" / "app.js").write_text("console.log('ok')")
        settings = Settings("test", "mysql+pymysql://u:p@host/db", "a", "b", "admin", "hash", "x" * 48)
        self.client = TestClient(create_app(settings=settings, console_static_dir=root, validate_startup=False))

    def tearDown(self):
        self.temp.cleanup()

    def test_spa_assets_cache_and_api_404(self):
        root = self.client.get("/", follow_redirects=False)
        self.assertEqual(root.headers["location"], "/console/apps")
        deep = self.client.get("/console/apps/app_1/edit")
        self.assertEqual(deep.status_code, 200)
        self.assertEqual(deep.headers["cache-control"], "no-cache")
        asset = self.client.get("/console/assets/app.js")
        self.assertEqual(asset.status_code, 200)
        self.assertIn("immutable", asset.headers["cache-control"])
        missing = self.client.get("/console/api/v1/not-found")
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.json()["error"]["code"], "NOT_FOUND")


if __name__ == "__main__":
    unittest.main()


class ConsoleLayeringTests(unittest.TestCase):
    def test_console_controller_does_not_import_management_controller(self):
        import ast

        path = Path("src/fastcall/api/console/routes.py")
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imported.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        )
        self.assertNotIn("fastcall.api.management", imported)
        self.assertIn("fastcall.application.publication", imported)
        self.assertIn("fastcall.application.robot_apps", imported)

import unittest

from fastcall_adapter.api.app import create_app as create_adapter_app
from fastcall_adapter.settings import Settings as AdapterSettings
from fastcall.main import create_app as create_control_app
from fastcall.settings import Settings as ControlSettings


def route_paths(app):
    return {route.path for route in app.routes}


class ApplicationRouteSetTests(unittest.TestCase):
    def test_adapter_contains_only_runtime_routes(self):
        app = create_adapter_app(
            settings=AdapterSettings("test", "mysql+pymysql://unused:unused@localhost/unused"),
            validate_startup=False,
        )
        paths = route_paths(app)
        for path in (
            "/internal/v1/calls/{call_id}/dispatch",
            "/internal/v1/calls/{call_id}/session-claim",
            "/internal/v1/calls/{call_id}/session-events/{action}",
            "/internal/v1/calls/{call_id}/hangup",
            "/internal/media/{call_id}",
            "/adminapi/v1/calls/{call_id}/review",
        ):
            self.assertIn(path, paths)
        for path in paths:
            self.assertFalse(path.startswith(("/console", "/openapi/", "/api/v1/robot-apps")), path)

    def test_control_plane_does_not_expose_adapter_runtime_routes(self):
        app = create_control_app(
            settings=ControlSettings(
                "test", "mysql+pymysql://unused:unused@localhost/unused", "management", "resolve"
            ),
            validate_startup=False,
            verify_console_assets=False,
        )
        paths = route_paths(app)
        self.assertIn("/openapi/v1/outbound-tasks/{task_id}/cancel", paths)
        self.assertIn("/adminapi/v1/calls/{call_id}/review", paths)
        for path in (
            "/internal/v1/calls/{call_id}/dispatch",
            "/internal/v1/calls/{call_id}/session-claim",
            "/internal/v1/calls/{call_id}/hangup",
            "/internal/media/{call_id}",
        ):
            self.assertNotIn(path, paths)


if __name__ == "__main__":
    unittest.main()

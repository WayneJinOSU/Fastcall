import ast
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).resolve().parents[3] / "src"


def imported_modules(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def depends_on(modules, packages):
    return any(
        module == package or module.startswith(f"{package}.")
        for module in modules
        for package in packages
    )


class PackageBoundaryTests(unittest.TestCase):
    def test_core_does_not_depend_on_service_packages_or_environment(self):
        for path in (SOURCE_ROOT / "fastcall_core").rglob("*.py"):
            modules = imported_modules(path)
            self.assertFalse(
                depends_on(modules, ("fastcall", "fastcall_adapter")),
                f"{path} has a service dependency: {sorted(modules)}",
            )
            content = path.read_text(encoding="utf-8")
            self.assertNotIn("os.getenv", content)
            self.assertNotIn("os.environ", content)

    def test_agent_does_not_depend_on_fastcall_packages(self):
        for path in (SOURCE_ROOT / "collection_agents").rglob("*.py"):
            modules = imported_modules(path)
            self.assertFalse(
                depends_on(modules, ("fastcall", "fastcall_adapter", "fastcall_core")),
                f"{path} has a Fast-Call dependency: {sorted(modules)}",
            )

    def test_control_plane_and_adapter_do_not_import_each_other(self):
        for package, forbidden in (
            ("fastcall_adapter", ("fastcall",)),
            ("fastcall", ("fastcall_adapter",)),
        ):
            for path in (SOURCE_ROOT / package).rglob("*.py"):
                modules = imported_modules(path)
                self.assertFalse(
                    depends_on(modules, forbidden),
                    f"{path} imports {forbidden}: {sorted(modules)}",
                )


if __name__ == "__main__":
    unittest.main()

import ast
import unittest
from pathlib import Path


API_ROOT = Path(__file__).resolve().parents[3] / "src" / "fastcall" / "api"


class TransactionOwnershipTests(unittest.TestCase):
    def test_controllers_do_not_commit_or_rollback_transactions(self):
        for path in API_ROOT.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in {"commit", "rollback"}:
                    continue
                receiver = node.func.value
                is_business_rollback = (
                    node.func.attr == "rollback"
                    and isinstance(receiver, ast.Call)
                    and isinstance(receiver.func, ast.Name)
                    and receiver.func.id == "PublicationUseCase"
                )
                if not is_business_rollback:
                    self.fail(f"{path}:{node.lineno} owns a transaction")


if __name__ == "__main__":
    unittest.main()

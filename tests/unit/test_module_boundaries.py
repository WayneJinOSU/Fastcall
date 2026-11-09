import ast
import unittest
from dataclasses import fields
from pathlib import Path

from collection_agents.events import VoicemailDetectedEvent


PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "collection_agents"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class ModuleBoundaryTests(unittest.TestCase):
    def test_handlers_do_not_depend_on_bot_assembly(self):
        for path in (PACKAGE_ROOT / "handlers").glob("*.py"):
            imports = imported_modules(path)
            self.assertFalse(
                any(
                    module == "collection_agents.bots"
                    or module.startswith("collection_agents.bots.")
                    for module in imports
                ),
                f"{path.name} imports bot assembly: {sorted(imports)}",
            )

    def test_voicemail_detector_only_depends_on_detection_contract(self):
        imports = imported_modules(
            PACKAGE_ROOT / "processors" / "voicemail_detector.py"
        )
        forbidden_modules = (
            "collection_agents.bots",
            "collection_agents.handlers",
            "collection_agents.services.voicemail_player",
            "collection_agents.services.voicemail_prompts",
        )
        self.assertFalse(
            any(
                module == forbidden or module.startswith(f"{forbidden}.")
                for module in imports
                for forbidden in forbidden_modules
            ),
            f"VoicemailDetector has action-layer imports: {sorted(imports)}",
        )

    def test_voicemail_event_does_not_contain_response_plan(self):
        field_names = {field.name for field in fields(VoicemailDetectedEvent)}
        self.assertNotIn("prompt", field_names)
        self.assertTrue(
            {"transcript_text", "matched_text", "source"}.issubset(field_names)
        )


if __name__ == "__main__":
    unittest.main()

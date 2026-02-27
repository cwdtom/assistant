import os
import subprocess
import unittest
from pathlib import Path
from uuid import uuid4


class AssistantScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root_dir = Path(__file__).resolve().parents[1]
        cls.script_path = cls.root_dir / "scripts" / "assistant.sh"

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["ASSISTANT_AUTO_PULL"] = "false"
        return subprocess.run(
            ["bash", str(self.script_path), *args],
            cwd=self.root_dir,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_status_supports_positional_alias(self) -> None:
        alias = "test-script-alias"
        result = self.run_script("status", alias)

        self.assertEqual(result.returncode, 0)
        self.assertIn(f"Assistant ({alias}) is not running.", result.stdout)

    def test_status_supports_alias_option(self) -> None:
        alias = "test_script_option"
        result = self.run_script("--alias", alias, "status")

        self.assertEqual(result.returncode, 0)
        self.assertIn(f"Assistant ({alias}) is not running.", result.stdout)

    def test_rejects_invalid_alias(self) -> None:
        result = self.run_script("status", "bad/alias")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Invalid assistant alias", result.stderr)

    def test_rejects_duplicate_alias_inputs(self) -> None:
        result = self.run_script("--alias", "one", "status", "two")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Alias provided twice", result.stderr)

    def test_list_shows_output(self) -> None:
        result = self.run_script("list")

        self.assertEqual(result.returncode, 0)
        self.assertTrue(
            "No assistant instances found." in result.stdout or "ALIAS" in result.stdout
        )

    def test_list_filters_alias(self) -> None:
        alias = f"test-list-{uuid4().hex[:8]}"
        pid_file = self.root_dir / "logs" / f"assistant.{alias}.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        pid_file.write_text("999999\n", encoding="utf-8")

        try:
            result = self.run_script("list", alias)
        finally:
            pid_file.unlink(missing_ok=True)

        self.assertEqual(result.returncode, 0)
        self.assertIn("ALIAS", result.stdout)
        self.assertIn(alias, result.stdout)
        self.assertIn("stopped", result.stdout)


if __name__ == "__main__":
    unittest.main()

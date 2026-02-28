import subprocess
import unittest
from pathlib import Path


class BootstrapScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.root_dir = Path(__file__).resolve().parents[1]
        cls.script_path = cls.root_dir / "scripts" / "bootstrap.sh"

    def run_script(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(self.script_path), *args],
            cwd=self.root_dir,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_help_option_prints_usage(self) -> None:
        result = self.run_script("--help")

        self.assertEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stdout)
        self.assertIn("--skip-install", result.stdout)
        self.assertIn("--skip-db", result.stdout)

    def test_unknown_option_fails_fast(self) -> None:
        result = self.run_script("--unknown-option")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unknown option", result.stderr)
        self.assertIn("Usage:", result.stdout)


if __name__ == "__main__":
    unittest.main()

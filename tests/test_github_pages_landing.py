from __future__ import annotations

import unittest
from html.parser import HTMLParser
from pathlib import Path


class _LandingPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = {key: value or "" for key, value in attrs}
        self.tags.append((tag, attr_map))


class GitHubPagesLandingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.repo_root = Path(__file__).resolve().parents[1]
        cls.index_path = cls.repo_root / "index.html"
        cls.nojekyll_path = cls.repo_root / ".nojekyll"
        cls.content = cls.index_path.read_text(encoding="utf-8")
        cls.parser = _LandingPageParser()
        cls.parser.feed(cls.content)

    def test_required_files_exist(self) -> None:
        self.assertTrue(self.index_path.exists())
        self.assertTrue(self.nojekyll_path.exists())

    def test_page_has_expected_language_metadata_and_sections(self) -> None:
        self.assertIn('<html lang="zh-CN">', self.content)
        self.assertTrue(
            any(
                tag == "meta" and attrs.get("name") == "viewport" and "width=device-width" in attrs.get("content", "")
                for tag, attrs in self.parser.tags
            )
        )

        section_ids = {attrs.get("id", "") for tag, attrs in self.parser.tags if tag == "section"}
        self.assertTrue({"hero", "capabilities", "quickstart", "commands", "repo"}.issubset(section_ids))
        self.assertTrue(
            any(
                tag == "link" and attrs.get("rel") == "icon" and attrs.get("href", "").startswith("data:image/svg+xml,")
                for tag, attrs in self.parser.tags
            )
        )

    def test_page_links_to_repository_and_readme(self) -> None:
        hrefs = {attrs.get("href", "") for tag, attrs in self.parser.tags if tag == "a"}
        self.assertIn("https://github.com/cwdtom/assistant", hrefs)
        self.assertIn("https://github.com/cwdtom/assistant#readme", hrefs)

    def test_page_uses_visual_placeholders_without_image_dependencies(self) -> None:
        placeholder_count = sum(1 for _, attrs in self.parser.tags if "data-placeholder" in attrs)
        self.assertGreaterEqual(placeholder_count, 1)
        self.assertFalse(any(tag == "img" for tag, _ in self.parser.tags))
        self.assertIn("sync-mock", self.content)
        self.assertNotIn('src=""', self.content)

    def test_page_emits_load_log_marker(self) -> None:
        self.assertIn("github_pages_landing_loaded", self.content)
        self.assertIn('page: "assistant-github-pages"', self.content)
        self.assertIn('theme: "apple-inspired"', self.content)

    def test_quickstart_commands_use_preformatted_code_blocks(self) -> None:
        code_shell_pre_count = sum(
            1 for tag, attrs in self.parser.tags if tag == "pre" and "code-shell" in attrs.get("class", "").split()
        )
        self.assertGreaterEqual(code_shell_pre_count, 3)
        self.assertNotIn('<div class="code-shell">', self.content)


if __name__ == "__main__":
    unittest.main()

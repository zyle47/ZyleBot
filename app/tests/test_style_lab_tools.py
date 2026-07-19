import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.tools import get_openai_tool_schemas, get_tool_risk_tier
from app.tools import style_lab_tools
from app.tools.base import RiskTier


class StyleLabToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.static_dir = Path(self.temp_dir.name).resolve() / "static"
        self.static_dir.mkdir()
        self.target = self.static_dir / "style-lab.css"
        self.default = self.static_dir / "style-lab.default.css"
        self.target.write_text(".old { color: red; }\n", encoding="utf-8")
        self.default.write_text(".default { color: green; }\n", encoding="utf-8")

        patches = (
            patch.object(style_lab_tools, "_STATIC_DIR", self.static_dir),
            patch.object(style_lab_tools, "_STYLE_LAB_CSS", self.target),
            patch.object(style_lab_tools, "_STYLE_LAB_DEFAULT_CSS", self.default),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_update_replaces_only_fixed_target(self) -> None:
        content = '.lab-card { color: "white"; }\n'

        result = style_lab_tools.update_style_lab_css(content)

        self.assertNotIn("error", result)
        self.assertEqual(self.target.read_text(encoding="utf-8"), content)
        self.assertEqual(result["scope"], "isolated /style-lab preview only")

    def test_reset_restores_protected_default(self) -> None:
        self.target.write_text(".changed { opacity: 0; }\n", encoding="utf-8")

        result = style_lab_tools.reset_style_lab_css()

        self.assertNotIn("error", result)
        self.assertEqual(
            self.target.read_text(encoding="utf-8"),
            self.default.read_text(encoding="utf-8"),
        )

    def test_external_resource_syntax_is_rejected_without_changing_file(self) -> None:
        original = self.target.read_text(encoding="utf-8")
        rejected = (
            '@import "https://example.com/theme.css";',
            '.x { background: url("https://example.com/pixel"); }',
            '.x { behavior: url(thing.htc); }',
            '.x::before { content: "https://example.com/pixel"; }',
        )

        for content in rejected:
            with self.subTest(content=content):
                result = style_lab_tools.update_style_lab_css(content)
                self.assertIn("error", result)
                self.assertEqual(self.target.read_text(encoding="utf-8"), original)

    def test_incomplete_css_is_rejected_without_changing_file(self) -> None:
        original = self.target.read_text(encoding="utf-8")

        result = style_lab_tools.update_style_lab_css(".lab-card { color: pink;")

        self.assertIn("unbalanced", result["error"])
        self.assertEqual(self.target.read_text(encoding="utf-8"), original)

    def test_oversized_css_is_rejected(self) -> None:
        result = style_lab_tools.update_style_lab_css("a" * (128 * 1024 + 1))

        self.assertIn("limit", result["error"])

    def test_linked_target_is_rejected_when_supported(self) -> None:
        outside = self.static_dir.parent / "outside.css"
        outside.write_text(".secret { color: red; }", encoding="utf-8")
        self.target.unlink()
        try:
            self.target.symlink_to(outside)
        except OSError as exc:
            self.skipTest(f"file links are unavailable: {exc}")

        result = style_lab_tools.update_style_lab_css(".safe { color: green; }")

        self.assertIn("path safety", result["error"])
        self.assertEqual(outside.read_text(encoding="utf-8"), ".secret { color: red; }")

    def test_tools_are_scoped_writes_with_no_path_argument(self) -> None:
        self.assertEqual(
            get_tool_risk_tier("update_style_lab_css"),
            RiskTier.SCOPED_WRITE,
        )
        schemas = get_openai_tool_schemas({RiskTier.SCOPED_WRITE})
        by_name = {schema["function"]["name"]: schema["function"] for schema in schemas}
        self.assertEqual(set(by_name), {"update_style_lab_css", "reset_style_lab_css"})
        self.assertNotIn("path", by_name["update_style_lab_css"]["parameters"]["properties"])


if __name__ == "__main__":
    unittest.main()

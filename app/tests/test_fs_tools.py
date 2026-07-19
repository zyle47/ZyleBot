import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.tools import fs_tools


class ListDirectoryBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.base = Path(self.temp_dir.name).resolve()
        self.workspace = self.base / "local_mythos"
        self.workspace.mkdir()
        (self.workspace / "inside").mkdir()
        (self.workspace / "inside" / "note.txt").write_text("hello", encoding="utf-8")
        self.outside = self.base / "outside"
        self.outside.mkdir()
        (self.outside / "secret.txt").write_text("secret", encoding="utf-8")

        root_patch = patch.object(fs_tools, "_LIST_DIRECTORY_ROOT", self.workspace)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def test_relative_path_is_resolved_from_workspace(self) -> None:
        result = fs_tools.list_directory("inside")

        self.assertEqual(result["path"], str(self.workspace / "inside"))
        self.assertEqual([entry["name"] for entry in result["entries"]], ["note.txt"])

    def test_workspace_root_is_allowed(self) -> None:
        result = fs_tools.list_directory(str(self.workspace))

        self.assertNotIn("error", result)
        self.assertEqual(result["path"], str(self.workspace))

    def test_absolute_path_outside_workspace_is_denied(self) -> None:
        result = fs_tools.list_directory(str(self.outside))

        self.assertIn("access denied", result["error"])
        self.assertEqual(result["allowed_root"], str(self.workspace))
        self.assertNotIn("entries", result)

    def test_parent_traversal_outside_workspace_is_denied(self) -> None:
        result = fs_tools.list_directory("..\\outside")

        self.assertIn("access denied", result["error"])
        self.assertNotIn("entries", result)

    def test_similarly_prefixed_sibling_is_denied(self) -> None:
        sibling = self.base / "local_mythos_backup"
        sibling.mkdir()

        result = fs_tools.list_directory(str(sibling))

        self.assertIn("access denied", result["error"])

    def test_directory_link_escape_is_denied_when_supported(self) -> None:
        link = self.workspace / "outside-link"
        try:
            link.symlink_to(self.outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory links are unavailable: {exc}")

        result = fs_tools.list_directory("outside-link")

        self.assertIn("access denied", result["error"])
        self.assertNotIn("entries", result)


if __name__ == "__main__":
    unittest.main()

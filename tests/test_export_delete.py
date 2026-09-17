import tempfile
import unittest
from pathlib import Path
from backend.exports import delete_exports


class DeleteTests(unittest.TestCase):
    def test_deletes_only_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "one.mp4").write_bytes(b"one")
            (root / "two.mp4").write_bytes(b"two")
            self.assertEqual(delete_exports(root, ["one.mp4"]), {"deleted": ["one.mp4"], "failed": []})
            self.assertFalse((root / "one.mp4").exists())
            self.assertTrue((root / "two.mp4").exists())

    def test_rejects_links_and_traversal_before_deleting_anything(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "one.mp4").write_bytes(b"one")
            (root / "link.mp4").symlink_to(root / "one.mp4")
            for invalid in ("link.mp4", "../one.mp4", "notes.txt"):
                with self.assertRaises(ValueError):
                    delete_exports(root, ["one.mp4", invalid])
                self.assertTrue((root / "one.mp4").exists())

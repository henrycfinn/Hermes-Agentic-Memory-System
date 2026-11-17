import importlib.util
import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "chatgpt_desktop_mcp.py"
SPEC = importlib.util.spec_from_file_location("chatgpt_desktop_mcp", SCRIPT)
assert SPEC and SPEC.loader
chatgpt_desktop_mcp = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = chatgpt_desktop_mcp
SPEC.loader.exec_module(chatgpt_desktop_mcp)


class VaultReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "wiki"
        self.root.mkdir()
        (self.root / "projects").mkdir()
        (self.root / "projects" / "alpha.md").write_text("# Alpha\nThe durable decision is to use QMD for retrieval.\n", encoding="utf-8")
        (self.root / "projects" / "beta.md").write_text("# Beta\nUnrelated operational notes.\n", encoding="utf-8")
        (self.root / ".obsidian").mkdir()
        (self.root / ".obsidian" / "private.md").write_text("must not be returned", encoding="utf-8")
        self.reader = chatgpt_desktop_mcp.VaultReader.from_path(self.root)

    def tearDown(self):
        self.temp.cleanup()

    def test_search_returns_canonical_notes_and_excludes_metadata(self):
        results = self.reader.search("QMD retrieval")
        self.assertEqual([item["path"] for item in results], ["projects/alpha.md"])
        self.assertIn("QMD", results[0]["excerpt"])

    def test_read_rejects_traversal_and_metadata(self):
        with self.assertRaises(chatgpt_desktop_mcp.VaultAccessError):
            self.reader.read_note("../outside.md")
        with self.assertRaises(chatgpt_desktop_mcp.VaultAccessError):
            self.reader.read_note(".obsidian/private.md")

    def test_proposals_and_receipts_are_not_retrievable(self):
        from unittest import mock
        for folder in ("plugin-data", "proposals", "receipts"):
            directory = self.root / folder
            directory.mkdir()
            (directory / "hidden.md").write_text("noncanonical-private-probe", encoding="utf-8")
            with self.assertRaises(chatgpt_desktop_mcp.VaultAccessError):
                self.reader.read_note(f"{folder}/hidden.md")
        with mock.patch.object(chatgpt_desktop_mcp.integrations, "qmd_available", return_value=False):
            self.assertEqual(self.reader.search("noncanonical-private-probe"), [])

    def test_read_returns_relative_path_and_content(self):
        note = self.reader.read_note("projects/alpha.md")
        self.assertEqual(note["path"], "projects/alpha.md")
        self.assertIn("durable decision", note["content"])

    def test_fallback_search_rejects_symlink_outside_root(self):
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("private-symlink-probe", encoding="utf-8")
        link = self.root / "leak.md"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("symlink creation unavailable")
        from unittest import mock
        with mock.patch.object(chatgpt_desktop_mcp.integrations, "qmd_available", return_value=False):
            self.assertEqual(self.reader.search("private-symlink-probe"), [])

    def test_fallback_rejects_resolved_path_outside_root_on_windows(self):
        from unittest import mock
        candidate = self.root / "probe.md"
        candidate.write_text("outside-probe-secret", encoding="utf-8")
        real_resolve = Path.resolve
        def resolve(path, *args, **kwargs):
            return Path(self.temp.name) / "outside.md" if path.name == candidate.name else real_resolve(path, *args, **kwargs)
        with mock.patch.object(chatgpt_desktop_mcp.integrations, "qmd_available", return_value=False), \
             mock.patch.object(Path, "resolve", resolve):
            self.assertEqual(self.reader.search("outside-probe-secret"), [])

    def test_qmd_candidates_filtered_out_fall_back_to_vault_search(self):
        from unittest import mock
        with mock.patch.object(chatgpt_desktop_mcp.integrations, "qmd_available", return_value=True), \
             mock.patch.object(chatgpt_desktop_mcp.integrations, "_run", return_value=(0, json.dumps([{"path": str(self.root / ".obsidian" / "private.md")}]), "")):
            self.assertEqual(self.reader.search("QMD retrieval")[0]["path"], "projects/alpha.md")

    def test_search_prefers_qmd_index_when_available(self):
        old_available = chatgpt_desktop_mcp.integrations.qmd_available
        old_run = chatgpt_desktop_mcp.integrations._run
        try:
            chatgpt_desktop_mcp.integrations.qmd_available = lambda: True
            chatgpt_desktop_mcp.integrations._run = lambda *args, **kwargs: (
                0,
                json.dumps([{"path": str(self.root / "projects" / "alpha.md")}]),
                "",
            )
            results = self.reader.search("QMD retrieval")
        finally:
            chatgpt_desktop_mcp.integrations.qmd_available = old_available
            chatgpt_desktop_mcp.integrations._run = old_run
        self.assertEqual([item["path"] for item in results], ["projects/alpha.md"])
        self.assertIsNone(results[0]["score"])

    def test_mcp_server_registers_read_only_retrieval_tools(self):
        try:
            server = chatgpt_desktop_mcp.create_server(self.reader)
        except RuntimeError as exc:
            self.skipTest(str(exc))
        names = {tool.name for tool in asyncio.run(server.list_tools())}
        self.assertEqual(names, {"memory_status", "read_memory", "search_memory"})


if __name__ == "__main__":
    unittest.main()

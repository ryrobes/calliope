"""Exercise real file guards and generated execute_code RPC stubs offline."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, "/opt/hermes")
from tools import file_tools as files
from tools import code_execution_tool as code


class ProgrammaticReads(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "data.json"
        self.path.write_text('{"count": 42, "text": "1|keep this"}')
        self.task = "read-test-" + self.tmp.name

    def read(self, **kwargs):
        return json.loads(files.read_file_tool(str(self.path), task_id=self.task, **kwargs))

    def test_raw_recovers_after_display_read_and_never_elides_content(self):
        self.assertIn("1|", self.read()["content"])
        self.assertTrue(self.read().get("dedup"))
        for _ in range(6):
            result = self.read(raw=True)
            self.assertEqual(json.loads(result["content"]), {"count": 42, "text": "1|keep this"})
            self.assertEqual(result["content_format"], "plain")

    def test_raw_keeps_pagination_and_byte_budget(self):
        self.path.write_text("first\n  indented\n\nlast\n")
        page = self.read(raw=True, offset=2, limit=2)
        self.assertEqual(page["content"], "  indented\n")
        self.assertTrue(page["truncated"])
        self.path.write_text("x" * 300 + "\n" + "y" * 300)
        with patch.object(files, "_get_max_read_chars", return_value=100):
            page = self.read(raw=True)
        self.assertTrue(page["truncated"])
        self.assertLess(len(page["content"]), 500)

    def test_raw_preserves_access_guard_and_redaction(self):
        with patch.object(files, "get_read_block_error", return_value="protected fixture"):
            denied = self.read(raw=True)
        self.assertIn("protected fixture", str(denied))
        self.assertNotIn("content", denied)
        with patch.object(files, "redact_sensitive_text", side_effect=lambda text, **kw: text.replace("42", "[REDACTED]")):
            self.assertIn("[REDACTED]", self.read(raw=True)["content"])

    def test_generated_code_defaults_to_plain_reads_and_supports_result_reader(self):
        self.assertIn("read_tool_result", code.SANDBOX_ALLOWED_TOOLS)
        namespace = {"__name__": "hermes_tools"}
        exec(code.generate_hermes_tools_module(["read_file", "read_tool_result"]), namespace)
        calls = []
        namespace["_call"] = lambda name, args: calls.append((name, args)) or args
        self.assertTrue(namespace["read_file"]("data.json")["raw"])
        self.assertFalse(namespace["read_file"]("data.json", raw=False)["raw"])
        namespace["read_tool_result"]("saved.txt", pointer="/results/check", offset=20, limit=100)
        self.assertEqual(calls[-1], ("read_tool_result", {"path": "saved.txt", "pointer": "/results/check", "offset": 20, "limit": 100}))
        self.assertNotIn("def read_tool_result(", code.generate_hermes_tools_module(["read_file"]))


if __name__ == "__main__":
    unittest.main()

"""Keep bounded, guarded file reads usable from execute_code; fail on seam drift."""
import os
from pathlib import Path

root = Path(os.environ.get("HERMES_SOURCE_ROOT", "/opt/hermes"))


def patch(file, old, new, count=1):
    path = root / file
    source = path.read_text()
    if source.count(old) != count:
        raise SystemExit("Programmatic read seam changed: " + file + ": " + old[:90])
    path.write_text(source.replace(old, new))


patch("tools/file_tools.py",
      'limit: int = DEFAULT_READ_LIMIT, task_id: str = "default") -> str:',
      'limit: int = DEFAULT_READ_LIMIT, task_id: str = "default", raw: bool = False) -> str:')
patch("tools/file_tools.py", 'if cached_mtime is not None and not is_background_review():',
      'if cached_mtime is not None and not is_background_review() and not raw:')
patch("tools/file_tools.py", 'if count >= 4:\n            return tool_error(\n                f"BLOCKED: You have read',
      'if count >= 4 and not raw:\n            return tool_error(\n                f"BLOCKED: You have read')
patch("tools/file_tools.py", 'if count >= 3:\n            result_dict["_warning"] = (\n                f"You have read',
      'if count >= 3 and not raw:\n            result_dict["_warning"] = (\n                f"You have read')
# Keep all original access guards, redaction, pagination, budget and read-before-write
# tracking. Only remove the display prefix after those protections have run.
patch("tools/file_tools.py", '''        return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        return tool_error(str(e))


# ── Shared write/patch plumbing''', '''        if raw and result_dict.get("content"):
            result_dict["content"] = re.sub(r"(?m)^[ \t]*[0-9]+\\|", "", result_dict["content"])
            result_dict["content_format"] = "plain"
        return json.dumps(result_dict, ensure_ascii=False)
    except Exception as e:
        return tool_error(str(e))


# ── Shared write/patch plumbing''')
patch("tools/file_tools.py", '"description": "Read a text file with line numbers and pagination.',
      '"description": "Read a text file with line numbers and pagination. raw=true removes display line numbers and always returns the requested content, including repeated reads. Pagination, truncation and redaction still apply. For saved oversized JSON tool results, prefer read_tool_result with a JSON pointer.')
patch("tools/file_tools.py", '''            "path": {"type": "string", "description": "Path to the file to read (absolute, relative, or ~/path)"},''',
      '''            "path": {"type": "string", "description": "Path to the file to read (absolute, relative, or ~/path)"},
            "raw": {"type": "boolean", "default": False, "description": "Plain content without display line numbers or repeat suppression; still bounded and redacted. Check truncated/next_offset before parsing."},''')
patch("tools/file_tools.py", 'limit=args.get("limit", DEFAULT_READ_LIMIT), task_id=tid)',
      'limit=args.get("limit", DEFAULT_READ_LIMIT), task_id=tid, raw=args.get("raw", False))')
patch("tools/code_execution_tool.py", '"web_search", "web_extract", "read_file", "write_file",',
      '"web_search", "web_extract", "read_file", "read_tool_result", "write_file",')
patch("tools/code_execution_tool.py", '''    "read_file": ("path: str, offset: int = 1, limit: int = 2000",
        '\"\"\"Read a file (1-indexed lines). Returns dict with "content" and "total_lines".\"\"\"',
        '{"path": path, "offset": offset, "limit": limit}'),''', '''    "read_file": ("path: str, offset: int = 1, limit: int = 2000, raw: bool = True",
        '\"\"\"Read bounded file pages (1-indexed lines). Defaults to plain content, no repeat suppression. Check truncated/next_offset before parsing. Saved tool JSON: prefer read_tool_result.\"\"\"',
        '{"path": path, "offset": offset, "limit": limit, "raw": raw}'),
    "read_tool_result": ('path: str, pointer: str = "", offset: int = 0, limit: int = 6000',
        '\"\"\"Read saved tool JSON: transport wrappers removed; /results/name selects a batch member. Returns content on every call. Join pages until next_offset is None before json.loads(content).\"\"\"',
        '{"path": path, "pointer": pointer, "offset": offset, "limit": limit}'),''')
patch("tools/code_execution_tool.py", '''    ("read_file", "  read_file(path: str, offset: int = 1, limit: int = 2000) -> dict\\n"
     "    Lines are 1-indexed. Returns {\\"content\\": \\"...\\", \\"total_lines\\": N}"),''', '''    ("read_file", "  read_file(path: str, offset: int = 1, limit: int = 2000, raw: bool = True) -> dict\\n"
     "    Lines are 1-indexed. Plain content by default; repeated reads return content. Check truncated/next_offset."),
    ("read_tool_result", "  read_tool_result(path: str, pointer: str = '', offset: int = 0, limit: int = 6000) -> dict\\n"
     "    Saved tool outputs: decoded JSON pointers, e.g. /results/name; character pages without repeat suppression. Join content until next_offset is None before parsing."),''')

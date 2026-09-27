from __future__ import annotations

import json
import os
import re
import typing
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from biscuit.extensions import Extension

if typing.TYPE_CHECKING:
    from biscuit.api import ExtensionsAPI


@dataclass
class Snippet:
    name: str
    prefix: str
    body: str | list[str]
    description: str = ""
    scope: str = ""

    def get_body_text(self) -> str:
        """Return body formatted as a single string."""
        if isinstance(self.body, list):
            return "\n".join(self.body)
        return str(self.body)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "prefix": self.prefix,
            "body": self.body,
            "description": self.description,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> Snippet:
        return cls(
            name=name,
            prefix=data.get("prefix", name),
            body=data.get("body", ""),
            description=data.get("description", ""),
            scope=data.get("scope", ""),
        )


@dataclass
class TabStop:
    index: int
    default_value: str
    start: int
    end: int


@dataclass
class ExpansionResult:
    text: str
    tabstops: list[TabStop] = field(default_factory=list)
    cursor_offset: int = -1


def expand_snippet_template(
    template: str,
    variables: dict[str, str] | None = None,
    filename: str = "",
) -> ExpansionResult:
    """Expand snippet body substituting variables and resolving tabstops.

    Supported variables:
    - $DATE, ${DATE}: YYYY-MM-DD
    - $CURRENT_YEAR, ${CURRENT_YEAR}: YYYY
    - $CURRENT_MONTH, ${CURRENT_MONTH}: MM
    - $CURRENT_DAY, ${CURRENT_DAY}: DD
    - $TM_FILENAME, ${TM_FILENAME}: current file name
    - $TM_FILENAME_BASE, ${TM_FILENAME_BASE}: file name without extension
    - $1, $2, ... tabstops with optional default: ${1:default}
    - $0: final cursor position
    """
    now = datetime.now()
    fn = Path(filename).name if filename else "untitled"
    fn_base = Path(filename).stem if filename else "untitled"

    sys_vars: dict[str, str] = {
        "DATE": now.strftime("%Y-%m-%d"),
        "CURRENT_YEAR": now.strftime("%Y"),
        "CURRENT_MONTH": now.strftime("%m"),
        "CURRENT_DAY": now.strftime("%d"),
        "TM_FILENAME": fn,
        "TM_FILENAME_BASE": fn_base,
    }
    if variables:
        sys_vars.update(variables)

    # Substitute system variables
    text = template
    for var_name, var_val in sys_vars.items():
        text = text.replace(f"${{{var_name}}}", var_val)
        text = re.sub(rf"\${var_name}\b", var_val, text)

    # Parse and resolve tabstops
    tabstop_pattern = re.compile(r"\$(?:(\d+)|\{(\d+)(?::([^}]*))?\})")

    result_parts: list[str] = []
    tabstops: list[TabStop] = []
    last_idx = 0
    cursor_pos = -1

    for match in tabstop_pattern.finditer(text):
        match_start = match.start()
        result_parts.append(text[last_idx:match_start])
        curr_offset = sum(len(p) for p in result_parts)

        # Group 1: $1, $2, $0
        # Group 2, 3: ${1:default}
        raw_idx = match.group(1) or match.group(2)
        ts_index = int(raw_idx)
        default_val = match.group(3) if match.group(3) is not None else ""

        if ts_index == 0:
            cursor_pos = curr_offset
            # $0 does not emit text
        else:
            ts_start = curr_offset
            result_parts.append(default_val)
            ts_end = curr_offset + len(default_val)
            tabstops.append(
                TabStop(
                    index=ts_index,
                    default_value=default_val,
                    start=ts_start,
                    end=ts_end,
                )
            )

        last_idx = match.end()

    result_parts.append(text[last_idx:])
    final_text = "".join(result_parts)

    if cursor_pos == -1:
        cursor_pos = len(final_text)

    # Sort tabstops by tabstop index
    tabstops.sort(key=lambda ts: ts.index)

    return ExpansionResult(
        text=final_text,
        tabstops=tabstops,
        cursor_offset=cursor_pos,
    )


class SnippetRepository:
    """Manages custom and built-in snippets mapped by language scope."""

    def __init__(self) -> None:
        # scope -> dict of snippet name -> Snippet
        self._snippets: dict[str, dict[str, Snippet]] = {"": {}}
        self._load_defaults()

    def _load_defaults(self) -> None:
        """Load standard default snippets for common file types."""
        defaults = [
            Snippet(
                name="python_function",
                prefix="def",
                body=["def ${1:function_name}(${2:args}):", "    ${0:pass}"],
                description="Python function definition",
                scope="python",
            ),
            Snippet(
                name="python_class",
                prefix="class",
                body=[
                    "class ${1:ClassName}:",
                    "    def __init__(self${2:, args}) -> None:",
                    "        ${0:pass}",
                ],
                description="Python class definition",
                scope="python",
            ),
            Snippet(
                name="python_main",
                prefix="main",
                body=[
                    'if __name__ == "__main__":',
                    "    ${0:main()}",
                ],
                description="Python __main__ guard",
                scope="python",
            ),
            Snippet(
                name="markdown_table",
                prefix="table",
                body=[
                    "| ${1:Header 1} | ${2:Header 2} |",
                    "| --- | --- |",
                    "| ${3:Value 1} | ${4:Value 2} |",
                    "${0}",
                ],
                description="Markdown table skeleton",
                scope="markdown",
            ),
            Snippet(
                name="markdown_link",
                prefix="link",
                body="[${1:text}](${2:url})${0}",
                description="Markdown link",
                scope="markdown",
            ),
            Snippet(
                name="json_key_value",
                prefix="kv",
                body='"${1:key}": "${2:value}"${0}',
                description="JSON key-value pair",
                scope="json",
            ),
        ]
        for s in defaults:
            self.add_snippet(s)

    def add_snippet(self, snippet: Snippet) -> None:
        """Register a new or updated snippet."""
        scope_key = snippet.scope.strip().lower()
        if scope_key not in self._snippets:
            self._snippets[scope_key] = {}
        self._snippets[scope_key][snippet.name] = snippet

    def remove_snippet(self, name: str, scope: str = "") -> bool:
        """Remove a snippet from repository."""
        scope_key = scope.strip().lower()
        if scope_key in self._snippets and name in self._snippets[scope_key]:
            del self._snippets[scope_key][name]
            return True
        return False

    def get_snippet(self, name: str, scope: str = "") -> Snippet | None:
        """Retrieve snippet by name within scope or global fallback."""
        scope_key = scope.strip().lower()
        if scope_key in self._snippets and name in self._snippets[scope_key]:
            return self._snippets[scope_key][name]
        return self._snippets.get("", {}).get(name)

    def find_matching_snippet(self, prefix: str, scope: str = "") -> Snippet | None:
        """Find snippet matching exact prefix for given scope, with fallback to global."""
        scope_key = scope.strip().lower()
        if scope_key in self._snippets:
            for s in self._snippets[scope_key].values():
                if s.prefix == prefix:
                    return s

        for s in self._snippets.get("", {}).values():
            if s.prefix == prefix:
                return s

        return None

    def find_snippets_by_prefix(self, prefix: str, scope: str = "") -> list[Snippet]:
        """Find all snippets whose prefix starts with the search prefix."""
        matches: list[Snippet] = []
        scope_key = scope.strip().lower()

        if scope_key in self._snippets:
            for s in self._snippets[scope_key].values():
                if s.prefix.startswith(prefix):
                    matches.append(s)

        for s in self._snippets.get("", {}).values():
            if s.prefix.startswith(prefix) and s not in matches:
                matches.append(s)

        return matches

    def list_snippets(self, scope: str | None = None) -> list[Snippet]:
        """Return all registered snippets, optionally filtered by scope."""
        if scope is not None:
            scope_key = scope.strip().lower()
            return list(self._snippets.get(scope_key, {}).values())

        all_snippets: list[Snippet] = []
        for s_dict in self._snippets.values():
            all_snippets.extend(s_dict.values())
        return all_snippets

    def count(self) -> int:
        return sum(len(s_dict) for s_dict in self._snippets.values())

    def load_from_dict(self, data: dict[str, Any], scope: str = "") -> int:
        """Load multiple snippets from a nested dictionary."""
        loaded = 0
        for name, entry in data.items():
            if isinstance(entry, dict):
                s_scope = entry.get("scope", scope)
                snip = Snippet.from_dict(name, entry)
                snip.scope = s_scope
                self.add_snippet(snip)
                loaded += 1
        return loaded

    def load_from_file(self, file_path: str | Path, scope: str = "") -> int:
        """Load snippets from a JSON file."""
        p = Path(file_path)
        if not p.is_file():
            return 0
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return self.load_from_dict(data, scope=scope)
        except Exception:
            pass
        return 0

    def save_to_file(self, file_path: str | Path, scope: str = "") -> bool:
        """Save snippets of a scope (or all) to a JSON file."""
        p = Path(file_path)
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            snippets = self.list_snippets(scope if scope else None)
            data = {s.name: s.to_dict() for s in snippets}
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception:
            return False


class SnippetManager(Extension):
    """Manage, expand, and query custom code snippets."""

    def __init__(self, api: ExtensionsAPI) -> None:
        super().__init__(api)
        self.api = api
        self.repository = SnippetRepository()

        # Workspace snippet directory
        ws_dir = getattr(getattr(self.api, "base", None), "active_directory", None)
        if isinstance(ws_dir, (str, Path)):
            try:
                snip_dir = Path(ws_dir) / ".biscuit" / "snippets"
                if snip_dir.is_dir():
                    for json_file in snip_dir.glob("*.json"):
                        scope = json_file.stem
                        self.repository.load_from_file(json_file, scope=scope)
            except Exception:
                pass

        if hasattr(self.api, "logger") and self.api.logger:
            self.api.logger.info("Snippet Manager extension initialised.")

    def _get_active_editor_context(self) -> tuple[Any, str, int, str]:
        """Retrieve editor widget, full content, cursor index, and filename."""
        editor = getattr(getattr(self.api, "editors", None), "active_editor", None)
        if not editor:
            return None, "", 0, ""

        filepath = str(getattr(editor, "path", ""))
        text_widget = getattr(editor, "text", None) or getattr(getattr(editor, "content", None), "text", None)
        if not text_widget:
            return None, "", 0, filepath

        content = ""
        cur_line = 1
        cur_col = 0
        if hasattr(text_widget, "get"):
            try:
                content = text_widget.get("1.0", "end-1c")
            except Exception:
                pass
        if hasattr(text_widget, "index"):
            try:
                idx_str = str(text_widget.index("insert"))
                parts = idx_str.split(".")
                cur_line = int(parts[0])
                cur_col = int(parts[1]) if len(parts) > 1 else 0
            except Exception:
                pass

        return text_widget, content, cur_col, filepath

    def _detect_language(self, filepath: str) -> str:
        """Infer language scope from file extension."""
        ext = Path(filepath).suffix.lower()
        ext_map = {
            ".py": "python",
            ".md": "markdown",
            ".markdown": "markdown",
            ".json": "json",
            ".js": "javascript",
            ".ts": "typescript",
            ".html": "html",
            ".css": "css",
            ".rs": "rust",
            ".go": "go",
            ".sh": "shell",
        }
        return ext_map.get(ext, "")

    def expand_snippet(
        self,
        snippet_body: str | list[str],
        variables: dict[str, str] | None = None,
        filename: str = "",
    ) -> ExpansionResult:
        """Public API to expand snippet body with variable substitution and tabstops."""
        template = "\n".join(snippet_body) if isinstance(snippet_body, list) else str(snippet_body)
        return expand_snippet_template(template, variables=variables, filename=filename)

    def insert_snippet(self, snippet_name: str, scope: str = "") -> bool:
        """Insert named snippet into active editor at cursor position."""
        text_widget, _, _, filepath = self._get_active_editor_context()
        if not text_widget:
            return False

        if not scope:
            scope = self._detect_language(filepath)

        snippet = self.repository.get_snippet(snippet_name, scope=scope)
        if not snippet:
            return False

        res = self.expand_snippet(snippet.get_body_text(), filename=filepath)
        if hasattr(text_widget, "insert"):
            text_widget.insert("insert", res.text)
            if hasattr(self.api, "notifications") and self.api.notifications:
                self.api.notifications.info(f"Inserted snippet '{snippet.name}'")
            return True
        return False

    def expand_at_cursor(self) -> bool:
        """Scan line before cursor in active editor, expand prefix if snippet matches."""
        text_widget, _, _, filepath = self._get_active_editor_context()
        if not text_widget or not hasattr(text_widget, "get") or not hasattr(text_widget, "index"):
            return False

        try:
            line_str = text_widget.get("insert linestart", "insert")
            match = re.search(r"(\w+)$", line_str)
            if not match:
                return False

            prefix = match.group(1)
            scope = self._detect_language(filepath)
            snippet = self.repository.find_matching_snippet(prefix, scope=scope)
            if not snippet:
                return False

            # Delete the prefix word from editor
            prefix_len = len(prefix)
            text_widget.delete(f"insert - {prefix_len} chars", "insert")

            # Insert expanded snippet
            res = self.expand_snippet(snippet.get_body_text(), filename=filepath)
            text_widget.insert("insert", res.text)

            if hasattr(self.api, "notifications") and self.api.notifications:
                self.api.notifications.info(f"Expanded snippet '{snippet.name}'")
            return True
        except Exception:
            return False

    def create_snippet(
        self,
        name: str,
        prefix: str,
        body: str | list[str],
        description: str = "",
        scope: str = "",
    ) -> Snippet:
        """Create and register a new custom snippet."""
        snippet = Snippet(
            name=name,
            prefix=prefix,
            body=body,
            description=description,
            scope=scope,
        )
        self.repository.add_snippet(snippet)
        if hasattr(self.api, "notifications") and self.api.notifications:
            self.api.notifications.info(f"Snippet '{name}' created.")
        return snippet

    def install(self) -> None:
        """Register snippet commands with Biscuit."""
        if hasattr(self.api, "commands") and hasattr(self.api.commands, "register_command"):
            self.api.commands.register_command("snippets.insert", self.insert_snippet)
            self.api.commands.register_command("snippets.create", self.create_snippet)
            self.api.commands.register_command("snippets.list", self.repository.list_snippets)
            self.api.commands.register_command("snippets.expand", self.expand_at_cursor)

        if hasattr(self.api, "logger") and self.api.logger:
            self.api.logger.info("Snippet Manager extension installed.")

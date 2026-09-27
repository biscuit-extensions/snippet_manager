from __future__ import annotations

import sys
import tempfile
import tomllib
from pathlib import Path
from unittest.mock import MagicMock

# Fix sys.path to ensure module is importable from extension root
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.snippet_manager import setup
from src.snippet_manager.extension import (
    ExpansionResult,
    Snippet,
    SnippetManager,
    SnippetRepository,
    TabStop,
    expand_snippet_template,
)


class TestSnippetManagerManifest:
    def test_manifest_schema_and_dependencies(self) -> None:
        manifest_path = Path(__file__).parent.parent / "extension.toml"
        assert manifest_path.exists(), "extension.toml must exist"
        manifest = tomllib.loads(manifest_path.read_text(encoding="utf-8"))

        ext_meta = manifest.get("extension", {})
        assert ext_meta.get("id") == "snippet_manager"
        assert ext_meta.get("author") == "tomlin7"
        assert ext_meta.get("version") == "0.2.0"

        deps = manifest.get("dependencies", {})
        assert isinstance(deps.get("python"), list)
        assert isinstance(deps.get("tools"), list)

        contributes = manifest.get("contributes", {})
        commands = contributes.get("commands", [])
        assert len(commands) >= 4
        cmd_ids = [cmd["id"] for cmd in commands]
        assert "snippets.insert" in cmd_ids
        assert "snippets.create" in cmd_ids
        assert "snippets.list" in cmd_ids
        assert "snippets.expand" in cmd_ids


class TestSnippetManagerLogic:
    def test_setup_and_registration(self, app_instance) -> None:
        ext = setup(app_instance.api)
        assert ext is not None
        assert isinstance(ext, SnippetManager)

    def test_snippet_dataclass(self) -> None:
        s = Snippet(
            name="test_snip",
            prefix="ts",
            body=["line 1", "line 2"],
            description="A test snippet",
            scope="python",
        )
        assert s.get_body_text() == "line 1\nline 2"
        data = s.to_dict()
        assert data["name"] == "test_snip"
        assert data["prefix"] == "ts"

        restored = Snippet.from_dict("test_snip", data)
        assert restored.prefix == "ts"
        assert restored.get_body_text() == "line 1\nline 2"

    def test_expand_snippet_tabstops(self) -> None:
        template = "def ${1:my_func}(${2:arg}):\n    ${0:pass}"
        res = expand_snippet_template(template)
        assert "def my_func(arg):" in res.text
        assert len(res.tabstops) == 2
        assert res.tabstops[0].index == 1
        assert res.tabstops[0].default_value == "my_func"
        assert res.tabstops[1].index == 2
        assert res.tabstops[1].default_value == "arg"
        assert res.cursor_offset >= 0

    def test_expand_snippet_system_variables(self) -> None:
        template = "// File: $TM_FILENAME on $DATE\n// Base: $TM_FILENAME_BASE"
        res = expand_snippet_template(template, filename="/path/to/my_script.py")
        assert "my_script.py" in res.text
        assert "my_script" in res.text
        assert "$DATE" not in res.text

    def test_expand_snippet_custom_variables(self) -> None:
        template = "const ${VAR_NAME} = '${DEFAULT_VAL}';"
        custom_vars = {"VAR_NAME": "port", "DEFAULT_VAL": "8080"}
        res = expand_snippet_template(template, variables=custom_vars)
        assert res.text == "const port = '8080';"

    def test_repository_default_snippets(self) -> None:
        repo = SnippetRepository()
        assert repo.count() >= 5
        py_def = repo.find_matching_snippet("def", scope="python")
        assert py_def is not None
        assert py_def.name == "python_function"

        md_tbl = repo.find_matching_snippet("table", scope="markdown")
        assert md_tbl is not None
        assert md_tbl.name == "markdown_table"

    def test_repository_crud_operations(self) -> None:
        repo = SnippetRepository()
        new_snip = Snippet(
            name="console_log",
            prefix="clg",
            body="console.log(${1:item});$0",
            description="Log to console",
            scope="javascript",
        )
        repo.add_snippet(new_snip)
        assert repo.get_snippet("console_log", scope="javascript") is not None
        assert repo.find_matching_snippet("clg", scope="javascript") is not None

        # Autocomplete search
        matches = repo.find_snippets_by_prefix("cl", scope="javascript")
        assert len(matches) >= 1
        assert matches[0].prefix == "clg"

        # Removal
        removed = repo.remove_snippet("console_log", scope="javascript")
        assert removed is True
        assert repo.get_snippet("console_log", scope="javascript") is None

    def test_repository_scope_fallback(self) -> None:
        repo = SnippetRepository()
        global_snip = Snippet(
            name="todo_comment",
            prefix="todo",
            body="// TODO: ${1:note}$0",
            scope="",
        )
        repo.add_snippet(global_snip)

        # Should be found in python scope via fallback
        matched = repo.find_matching_snippet("todo", scope="python")
        assert matched is not None
        assert matched.name == "todo_comment"

    def test_repository_json_persistence(self) -> None:
        repo = SnippetRepository()
        with tempfile.TemporaryDirectory() as tmp_dir:
            file_path = Path(tmp_dir) / "custom_snippets.json"
            snip = Snippet(
                name="custom_math",
                prefix="cm",
                body="x = ${1:10} + ${2:20}$0",
                scope="python",
            )
            repo.add_snippet(snip)
            assert repo.save_to_file(file_path, scope="python") is True
            assert file_path.exists()

            # Load into fresh repo
            repo2 = SnippetRepository()
            loaded = repo2.load_from_file(file_path, scope="python")
            assert loaded >= 1
            retrieved = repo2.get_snippet("custom_math", scope="python")
            assert retrieved is not None
            assert retrieved.prefix == "cm"

    def test_language_detection(self, app_instance) -> None:
        ext = SnippetManager(app_instance.api)
        assert ext._detect_language("script.py") == "python"
        assert ext._detect_language("README.md") == "markdown"
        assert ext._detect_language("config.json") == "json"
        assert ext._detect_language("app.ts") == "typescript"
        assert ext._detect_language("unknown.xyz") == ""

    def test_editor_insert_snippet(self, app_instance) -> None:
        ext = SnippetManager(app_instance.api)
        ext.install()

        mock_editor = MagicMock()
        mock_editor.path = "app.py"
        mock_text = MagicMock()
        mock_text.get.return_value = ""
        mock_text.index.return_value = "1.0"
        mock_editor.text = mock_text
        app_instance.api.editors.active_editor = mock_editor

        inserted = ext.insert_snippet("python_function", scope="python")
        assert inserted is True
        mock_text.insert.assert_called()

    def test_editor_expand_at_cursor(self, app_instance) -> None:
        ext = SnippetManager(app_instance.api)
        ext.install()

        mock_editor = MagicMock()
        mock_editor.path = "example.py"
        mock_text = MagicMock()
        mock_text.get.return_value = "def"
        mock_text.index.return_value = "1.3"
        mock_editor.text = mock_text
        app_instance.api.editors.active_editor = mock_editor

        expanded = ext.expand_at_cursor()
        assert expanded is True
        mock_text.delete.assert_called_with("insert - 3 chars", "insert")
        mock_text.insert.assert_called()

    def test_create_snippet_method(self, app_instance) -> None:
        ext = SnippetManager(app_instance.api)
        snip = ext.create_snippet(
            name="new_macro",
            prefix="nm",
            body="macro(${1:val});",
            scope="c",
        )
        assert snip.name == "new_macro"
        assert ext.repository.find_matching_snippet("nm", scope="c") is not None
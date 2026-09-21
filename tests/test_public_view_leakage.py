"""
Codifies the Phase 0 (revised) acceptance criteria that are naturally
expressed as source-level checks rather than runtime behavior:
  - No code path in the app imports demo_data or accepts file uploads.
  - No stake, return, or profit figure appears outside the is_admin gate.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _admin_gated_line_ranges(source: str) -> list[tuple[int, int]]:
    """Every (start, end) line range covered by an `if admin:` (or
    `if admin and ...:`) block's body in app.py -- walks the AST rather
    than hand-rolling indentation tracking, so it's correct regardless
    of how many separate admin-gated blocks exist or how deeply nested
    they are."""
    tree = ast.parse(source)
    ranges = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_admin_test = (
            (isinstance(test, ast.Name) and test.id == "admin")
            or (isinstance(test, ast.BoolOp) and any(isinstance(v, ast.Name) and v.id == "admin" for v in test.values))
        )
        if is_admin_test and node.body:
            ranges.append((node.body[0].lineno, node.body[-1].end_lineno))
    return ranges


def _line_in_any_range(lineno: int, ranges: list[tuple[int, int]]) -> bool:
    return any(start <= lineno <= end for start, end in ranges)


def _read(path: str) -> str:
    return (REPO_ROOT / path).read_text()


class TestNoDemoOrUploadInLiveApp:
    def test_app_and_backtest_page_have_no_demo_data_or_upload_references(self):
        for path in ("app.py", "pages/2_Backtest.py"):
            text = _read(path)
            for needle in ("demo_data", "file_uploader", "use_demo_history", "generate_demo_matches"):
                assert needle not in text, f"{needle!r} found in {path}"

    def test_demo_data_lives_under_tests_fixtures_not_src(self):
        assert not (REPO_ROOT / "src" / "ingestion" / "demo_data.py").exists()
        assert (REPO_ROOT / "tests" / "fixtures" / "demo_data.py").exists()


class TestNoXgOrUnderstatInLiveApp:
    def test_app_has_no_xg_or_understat_controls(self):
        text = _read("app.py")
        for needle in ("fit_on_xg", "understat", "SOURCE_UNDERSTAT", "blend_mode", "load_and_blend_sources"):
            assert needle.lower() not in text.lower(), f"{needle!r} found in app.py"


class TestNoStakeFiguresOutsideAdminGate:
    def test_stake_pct_field_name_is_gone(self):
        # stake_pct was the old (pre-Phase-0-revised) column name;
        # stake_shadow replaced it everywhere it's actually used.
        assert "stake_pct" not in _read("app.py")

    def test_every_stake_shadow_or_ev_entry_reference_is_inside_an_admin_block(self):
        source = _read("app.py")
        ranges = _admin_gated_line_ranges(source)
        assert ranges, "expected at least one `if admin:` block in app.py"

        for i, line in enumerate(source.splitlines(), start=1):
            if "stake_shadow" in line or "ev_entry" in line:
                assert _line_in_any_range(i, ranges), (
                    f"app.py:{i} references stake/EV outside every `if admin:` block: {line!r}"
                )

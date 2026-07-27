"""The most common minimal Python repo — a tests/ tree of test_*.py with no
runner named in pyproject/pytest.ini/package.json/Makefile — detected as 'no
test command', so init reported Verification 0/5 and the manifest carried no
verification backbone. Detection now falls back to `pytest` when a tests/ (or
test/) dir contains pytest-shaped files, and the python stack is recognized
from root-level *.py files without a packaging manifest.

fb-2d9bd441e70a.

moat-finding: fb-2d9bd441e70a
"""

from __future__ import annotations

from prusik.detect import _detect_general_test_command, _detect_stacks


def test_plain_tests_dir_yields_pytest(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x():\n    assert 1\n")
    assert _detect_general_test_command(tmp_path) == "pytest"


def test_test_singular_dir_and_suffix_shape(tmp_path):
    (tmp_path / "test").mkdir()
    (tmp_path / "test" / "app_test.py").write_text("def test_x():\n    assert 1\n")
    assert _detect_general_test_command(tmp_path) == "pytest"


def test_explicit_config_still_wins_over_fallback(tmp_path):
    # a Makefile test target names the runner; the tests/ fallback must not
    # shadow the project's explicit choice
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x():\n    assert 1\n")
    (tmp_path / "Makefile").write_text("test:\n\tmytool run\n")
    assert _detect_general_test_command(tmp_path) == "make test"


def test_tests_dir_without_test_files_stays_none(tmp_path):
    # adversarial: an empty tests/ dir (or one holding fixtures only) must not
    # invent a runner — unproven stays undetected
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fixtures.json").write_text("{}")
    assert _detect_general_test_command(tmp_path) is None


def test_root_py_files_detect_python_stack(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n")
    assert _detect_stacks(tmp_path) == ["python"]


def test_nested_py_files_do_not_flip_a_foreign_stack(tmp_path):
    # a go repo with a helper script under scripts/ stays go — the *.py
    # marker is root-level only by design
    (tmp_path / "go.mod").write_text("module x\n")
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "gen.py").write_text("x = 1\n")
    assert _detect_stacks(tmp_path) == ["go"]

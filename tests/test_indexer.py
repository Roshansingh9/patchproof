import os
from pathlib import Path

import pytest
from conftest import write_repo

from patchproof.config import Limits
from patchproof.errors import ErrorCode, PatchProofError
from patchproof.indexer import index_repository, module_name_for
from patchproof.paths import resolve_repository_root


def rels(result):
    return [f.rel_path for f in result.files]


def test_module_names():
    assert module_name_for("pkg/mod.py") == ("pkg.mod", False)
    assert module_name_for("pkg/__init__.py") == ("pkg", True)
    assert module_name_for("src/pkg/a/b.py") == ("pkg.a.b", False)
    assert module_name_for("top.py") == ("top", False)


@pytest.mark.parametrize("raw,code", [
    ("", ErrorCode.INVALID_ARGUMENT),
    ("   ", ErrorCode.INVALID_ARGUMENT),
    (None, ErrorCode.INVALID_ARGUMENT),
    ("relative/path", ErrorCode.INVALID_ARGUMENT),
    ("a\x00b", ErrorCode.INVALID_ARGUMENT),
])
def test_root_rejects_bad_values(raw, code):
    with pytest.raises(PatchProofError) as ei:
        resolve_repository_root(raw)
    assert ei.value.code == code


def test_root_missing_and_file(tmp_path):
    with pytest.raises(PatchProofError) as ei:
        resolve_repository_root(str(tmp_path / "nope"))
    assert ei.value.code == ErrorCode.REPOSITORY_NOT_FOUND
    f = tmp_path / "file.py"
    f.write_text("x = 1")
    with pytest.raises(PatchProofError) as ei:
        resolve_repository_root(str(f))
    assert ei.value.code == ErrorCode.REPOSITORY_INVALID


def test_exclusions_and_order(tmp_path):
    write_repo(tmp_path, {
        "b.py": "x = 1",
        "a/mod.py": "y = 2",
        ".venv/lib/x.py": "z = 3",
        "node_modules/n.py": "",
        "pkg.egg-info/e.py": "",
        "__pycache__/c.py": "",
        "build/b.py": "",
        "notes.txt": "not python",
    })
    result = index_repository(tmp_path.resolve())
    assert rels(result) == ["a/mod.py", "b.py"]
    assert result.coverage.skipped_excluded_dirs == 5
    assert not result.coverage.partial


def test_file_count_limit_is_disclosed(tmp_path):
    write_repo(tmp_path, {f"m{i}.py": "x = 1" for i in range(5)})
    result = index_repository(tmp_path.resolve(), Limits(max_python_files=3))
    assert len(result.files) == 3
    cov = result.coverage.to_dict(10)
    assert cov["partial"] and cov["file_limit_reached"]
    assert any("file limit" in n for n in cov["notes"])


def test_oversized_file_skipped(tmp_path):
    write_repo(tmp_path, {"big.py": "x = 1\n" * 100, "small.py": "y = 2"})
    result = index_repository(tmp_path.resolve(), Limits(max_file_bytes=50))
    assert rels(result) == ["small.py"]
    assert result.coverage.skipped_oversized_files == 1
    assert result.coverage.partial


def test_total_size_limit(tmp_path):
    write_repo(tmp_path, {"a.py": "a = 1\n" * 5, "b.py": "b = 1\n" * 5, "c.py": "c = 1\n" * 5})
    result = index_repository(tmp_path.resolve(), Limits(max_total_source_bytes=70))
    assert len(result.files) == 2
    assert result.coverage.total_bytes_limit_reached


def test_parse_errors_and_non_utf8_disclosed(tmp_path):
    write_repo(tmp_path, {"ok.py": "x = 1", "broken.py": "def f(:\n"})
    (tmp_path / "latin.py").write_bytes(b"s = '\xff\xfe'")
    result = index_repository(tmp_path.resolve())
    assert rels(result) == ["ok.py"]
    assert result.coverage.parse_errors[0]["file"] == "broken.py"
    assert result.coverage.skipped_unreadable_files == 1
    assert result.coverage.partial


def _try_symlink(target: Path, link: Path, is_dir: bool) -> None:
    try:
        os.symlink(target, link, target_is_directory=is_dir)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this system")


def test_symlinks_not_followed(tmp_path):
    outside = tmp_path / "outside"
    write_repo(outside, {"secret.py": "TOKEN = 'outside'"})
    repo = tmp_path / "repo"
    write_repo(repo, {"main.py": "x = 1"})
    _try_symlink(outside, repo / "linked_dir", True)
    _try_symlink(outside / "secret.py", repo / "linked_file.py", False)
    result = index_repository(repo.resolve())
    assert rels(result) == ["main.py"]
    assert result.coverage.skipped_symlinks == 2


def test_symlink_root_rejected(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    _try_symlink(real, link, True)
    with pytest.raises(PatchProofError) as ei:
        resolve_repository_root(str(link))
    assert ei.value.code == ErrorCode.REPOSITORY_INVALID


@pytest.mark.skipif(os.name != "nt", reason="directory junctions are Windows-only")
def test_windows_junction_not_followed(tmp_path):
    import _winapi

    outside = tmp_path / "outside"
    write_repo(outside, {"secret.py": "TOKEN = 'outside'"})
    repo = tmp_path / "repo"
    write_repo(repo, {"main.py": "x = 1"})
    try:
        _winapi.CreateJunction(str(outside), str(repo / "junction"))
    except OSError:
        pytest.skip("junction creation failed")
    result = index_repository(repo.resolve())
    assert rels(result) == ["main.py"]
    assert result.coverage.skipped_symlinks == 1

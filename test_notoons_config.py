"""Tests for named output-directory configuration."""

from notoons.app.config import _parse_output_dirs


def test_parse_named_output_directories():
    directories = _parse_output_dirs(
        '[{"nickname": "Books", "path": "outputs/books"}, '
        '{"nickname": "Archive", "path": "D:/archive"}]'
    )

    assert directories == [
        {"nickname": "Books", "path": "outputs/books"},
        {"nickname": "Archive", "path": "D:/archive"},
    ]


def test_parse_legacy_single_output_directory():
    assert _parse_output_dirs("outputs") == [
        {"nickname": "outputs", "path": "outputs"}
    ]

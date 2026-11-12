"""Tests for converting selected CBZ outputs to bounded PDF downloads."""

import io
import os
import zipfile

import pymupdf
from falcon import testing
from PIL import Image

from notoons.app.api.routes import files
from notoons.app.auth import OIDCClient
from notoons.app.main import create_app


def _create_cbz(path):
    with zipfile.ZipFile(path, "w") as archive:
        for name, color in (("page10.png", "blue"), ("page2.png", "red")):
            image_data = io.BytesIO()
            Image.new("RGB", (240, 320), color).save(image_data, format="PNG")
            archive.writestr(name, image_data.getvalue())


def _authenticated_client():
    client = testing.TestClient(create_app())
    cookie = OIDCClient().make_cookie({"sub": "test-user"}, 3600)
    return client, {"Cookie": f"notoons_session={cookie}"}


def test_selected_cbz_downloads_as_pdf_and_cleans_temp_file(
    tmp_path, monkeypatch
):
    output_dir = tmp_path / "outputs"
    temp_dir = tmp_path / "temp"
    output_dir.mkdir()
    temp_dir.mkdir()
    _create_cbz(output_dir / "comic.cbz")
    monkeypatch.setitem(
        files.CONFIG,
        "outputs_dir",
        [{"nickname": "Test", "path": str(output_dir)}],
    )
    monkeypatch.setitem(files.CONFIG, "temp_dir", str(temp_dir))

    client, headers = _authenticated_client()
    response = client.simulate_post(
        "/outputs/pdf",
        json={"file": "0:comic.cbz"},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.content_type == "application/pdf"
    assert response.headers["content-disposition"] == (
        'attachment; filename="comic.pdf"'
    )
    document = pymupdf.open(stream=response.content, filetype="pdf")
    assert len(document) == 2
    assert document[0].get_images()
    document.close()
    assert os.listdir(temp_dir) == []


def test_pdf_download_rejects_non_cbz_and_leaves_no_temp_file(
    tmp_path, monkeypatch
):
    output_dir = tmp_path / "outputs"
    temp_dir = tmp_path / "temp"
    output_dir.mkdir()
    temp_dir.mkdir()
    (output_dir / "notes.txt").write_text("not a comic", encoding="utf-8")
    monkeypatch.setitem(
        files.CONFIG,
        "outputs_dir",
        [{"nickname": "Test", "path": str(output_dir)}],
    )
    monkeypatch.setitem(files.CONFIG, "temp_dir", str(temp_dir))

    client, headers = _authenticated_client()
    response = client.simulate_post(
        "/outputs/pdf",
        json={"file": "0:notes.txt"},
        headers=headers,
    )

    assert response.status_code == 400
    assert "only supported for CBZ" in response.json["error"]
    assert os.listdir(temp_dir) == []


def test_pdf_download_rejects_invalid_cbz_archive(tmp_path, monkeypatch):
    output_dir = tmp_path / "outputs"
    temp_dir = tmp_path / "temp"
    output_dir.mkdir()
    temp_dir.mkdir()
    (output_dir / "broken.cbz").write_text(
        "not a ZIP archive", encoding="utf-8"
    )
    monkeypatch.setitem(
        files.CONFIG,
        "outputs_dir",
        [{"nickname": "Test", "path": str(output_dir)}],
    )
    monkeypatch.setitem(files.CONFIG, "temp_dir", str(temp_dir))

    client, headers = _authenticated_client()
    response = client.simulate_post(
        "/outputs/pdf",
        json={"file": "0:broken.cbz"},
        headers=headers,
    )

    assert response.status_code == 400
    assert "not a valid archive" in response.json["error"]
    assert os.listdir(temp_dir) == []


def test_pdf_download_rejects_output_over_size_limit(tmp_path, monkeypatch):
    output_dir = tmp_path / "outputs"
    temp_dir = tmp_path / "temp"
    output_dir.mkdir()
    temp_dir.mkdir()
    _create_cbz(output_dir / "comic.cbz")
    monkeypatch.setitem(
        files.CONFIG,
        "outputs_dir",
        [{"nickname": "Test", "path": str(output_dir)}],
    )
    monkeypatch.setitem(files.CONFIG, "temp_dir", str(temp_dir))
    monkeypatch.setattr(files, "MAX_PDF_SIZE_BYTES", 1)

    client, headers = _authenticated_client()
    response = client.simulate_post(
        "/outputs/pdf",
        json={"file": "0:comic.cbz"},
        headers=headers,
    )

    assert response.status_code == 413
    assert "100 MiB size limit" in response.json["error"]
    assert os.listdir(temp_dir) == []

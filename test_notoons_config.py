"""Tests for named output-directory configuration and custom subdirectories."""

import os
from notoons.app.config import _parse_output_dirs
from notoons.app.api.routes.jobs import sanitize_custom_subdir, resolve_job_output_dir


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


def test_sanitize_custom_subdir():
    # Empty or whitespace returns empty
    assert sanitize_custom_subdir("") == ""
    assert sanitize_custom_subdir("   ") == ""
    assert sanitize_custom_subdir(None) == ""

    # Normal relative path
    assert sanitize_custom_subdir("manga") == "manga"
    assert sanitize_custom_subdir("manga/vol1") == os.path.join("manga", "vol1")

    # Leading / trailing slashes stripped
    assert sanitize_custom_subdir("/comics/batman/") == os.path.join("comics", "batman")
    assert sanitize_custom_subdir(r"\comics\batman\\") == os.path.join("comics", "batman")

    # Directory traversal neutralized
    assert sanitize_custom_subdir("../../etc/passwd") == os.path.join("etc", "passwd")
    assert sanitize_custom_subdir("foo/../bar") == os.path.join("foo", "bar")

    # Invalid characters removed
    assert sanitize_custom_subdir('test:*?"<>|folder') == "testfolder"


def test_resolve_job_output_dir():
    base_dir = os.path.abspath("test_base_outputs")

    # Empty subfolder returns base_dir
    target, sub = resolve_job_output_dir(base_dir, "")
    assert target == base_dir
    assert sub == ""

    target, sub = resolve_job_output_dir(base_dir, "   ")
    assert target == base_dir
    assert sub == ""

    # Valid subfolder returns child path
    target, sub = resolve_job_output_dir(base_dir, "season1/episode2")
    assert target == os.path.join(base_dir, "season1", "episode2")
    assert sub == os.path.join("season1", "episode2")

    # Traversal cannot escape base directory
    target, sub = resolve_job_output_dir(base_dir, "../../../secret")
    assert target.startswith(base_dir)


def test_upload_job_with_custom_subdir():
    import time
    import pymupdf
    from falcon import testing
    from notoons.app.main import create_app
    from notoons.app.auth import OIDCClient
    from notoons.app.api.routes.auth import AUTH_SESSION_COOKIE
    from notoons.app.state import JOBS

    app = create_app()
    client = testing.TestClient(app)
    oidc = OIDCClient()
    auth_cookie = oidc.make_cookie({"sub": "tester"}, expires_in=3600)

    # Create a small valid 1-page PDF
    pdf_doc = pymupdf.open()
    pdf_doc.new_page(width=200, height=200)
    pdf_bytes = pdf_doc.tobytes()
    pdf_doc.close()

    boundary = "TestBoundary12345"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="mode"\r\n\r\n'
        f"pages\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="dpi"\r\n\r\n'
        f"150\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="output_dir"\r\n\r\n'
        f"Default\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="custom_subdir"\r\n\r\n'
        f"test_subfolder\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="test_sample.pdf"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + pdf_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    headers = {
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "Cookie": f"{AUTH_SESSION_COOKIE}={auth_cookie}",
    }
    res = client.simulate_post("/jobs", body=body, headers=headers)
    assert res.status_code == 200, f"Error: {res.text}"
    data = res.json
    assert data["status"] == "ok"
    job_id = data["job_id"]

    # Verify cbz_path points into the custom subfolder
    job = JOBS[job_id]
    assert "test_subfolder" in job["cbz_path"]
    assert job["custom_subdir"] == "test_subfolder"
    assert job["output_dir"] == "Default"

    # Wait briefly for conversion to complete
    for _ in range(50):
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)

    assert job["status"] == "completed"
    assert os.path.isfile(job["cbz_path"])
    assert "test_subfolder" in os.path.dirname(job["cbz_path"])

    # Verify GET /jobs/{job_id}
    res_job = client.simulate_get(f"/jobs/{job_id}", headers=headers)
    assert res_job.status_code == 200
    job_data = res_job.json
    assert job_data["output_dir"] == "Default"
    assert job_data["custom_subdir"] == "test_subfolder"
    assert job_data["status"] == "completed"

    # Verify GET /jobs
    res_jobs = client.simulate_get("/jobs", headers=headers)
    assert res_jobs.status_code == 200
    matching = next((j for j in res_jobs.json["jobs"] if j["id"] == job_id), None)
    assert matching is not None
    assert matching["output_dir"] == "Default"
    assert matching["custom_subdir"] == "test_subfolder"

    # Clean up the test job and file
    client.simulate_delete(f"/jobs/{job_id}", headers=headers)
    assert not os.path.isfile(job["cbz_path"])


if __name__ == "__main__":
    test_parse_named_output_directories()
    test_parse_legacy_single_output_directory()
    test_sanitize_custom_subdir()
    test_resolve_job_output_dir()
    test_upload_job_with_custom_subdir()
    print("ALL OUTPUT DIR & SUBFOLDER TESTS PASSED!")


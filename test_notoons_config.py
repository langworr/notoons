"""Tests for named output-directory configuration and custom subdirectories."""

import logging
import os
import pytest
from notoons.app.config import _parse_output_dirs
from notoons.app.api.routes.jobs import (
    reserve_output_path,
    sanitize_custom_subdir,
    resolve_job_output_dir,
)


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


def test_server_logging_configuration(tmp_path, monkeypatch):
    from notoons.app import config as app_config
    from notoons.app.server_logging import configure_server_logging

    monkeypatch.setattr(app_config, "BASE_DIR", str(tmp_path))
    monkeypatch.setenv("NOTOONS_SERVER_LOG_LEVEL", "debug")
    monkeypatch.setenv("NOTOONS_SERVER_LOG_FILE", "custom/server-output.log")
    for name in (
        "NOTOONS_OUTPUTS_DIR", "OUTPUTS_DIR", "NOTOONS_LOGS_DIR", "LOGS_DIR",
        "NOTOONS_TEMP_DIR", "TEMP_DIR",
    ):
        monkeypatch.delenv(name, raising=False)
    config_file = tmp_path / "config" / "config.txt"
    config_file.parent.mkdir()
    config_file.write_text("logs_dir = service-logs\ntemp_dir = service-temp\n", encoding="utf-8")

    config = app_config.load_config()
    assert config["server_log_level"] == "DEBUG"
    assert config["server_log_file"] == str(tmp_path / "service-logs" / "custom" / "server-output.log")

    loggers = [
        logging.getLogger(),
        logging.getLogger("uvicorn.error"),
        logging.getLogger("uvicorn.access"),
    ]
    original_levels = [logger.level for logger in loggers]
    original_handlers = [list(logger.handlers) for logger in loggers]
    original_propagation = [logger.propagate for logger in loggers]
    try:
        logging.getLogger("uvicorn.error").propagate = False
        configure_server_logging(config)
        test_logger = logging.getLogger("notoons.test_server_logging")
        test_logger.debug("configured server log entry")
        logging.getLogger("uvicorn.error").debug("configured Uvicorn log entry")
        for handler in logging.getLogger().handlers:
            if getattr(handler, "_notoons_server_log", False):
                handler.flush()
        for handler in logging.getLogger("uvicorn.error").handlers:
            if getattr(handler, "_notoons_server_log", False):
                handler.flush()

        log_contents = (
            tmp_path / "service-logs" / "custom" / "server-output.log"
        ).read_text(encoding="utf-8")
        assert "DEBUG [notoons.test_server_logging] configured server log entry" in log_contents
        assert "DEBUG [uvicorn.error] configured Uvicorn log entry" in log_contents
    finally:
        for logger, level, handlers, propagate in zip(
            loggers, original_levels, original_handlers, original_propagation
        ):
            for handler in logger.handlers[:]:
                if handler not in handlers:
                    logger.removeHandler(handler)
                    handler.close()
            logger.setLevel(level)
            logger.propagate = propagate


def test_invalid_server_log_level_fails(tmp_path, monkeypatch):
    from notoons.app import config as app_config

    monkeypatch.setattr(app_config, "BASE_DIR", str(tmp_path))
    monkeypatch.setenv("NOTOONS_SERVER_LOG_LEVEL", "verbose")
    for name in ("NOTOONS_OUTPUTS_DIR", "OUTPUTS_DIR", "NOTOONS_LOGS_DIR", "LOGS_DIR",
                 "NOTOONS_TEMP_DIR", "TEMP_DIR"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(ValueError, match="Invalid server_log_level"):
        app_config.load_config()


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


def test_reserve_output_path_uses_suffix_only_on_collision(tmp_path):
    first_path = reserve_output_path(str(tmp_path), "sample.cbz", "first")
    assert os.path.basename(first_path) == "sample.cbz"

    second_path = reserve_output_path(str(tmp_path), "sample.cbz", "second")
    assert os.path.basename(second_path) == "sample_second.cbz"

    third_path = reserve_output_path(str(tmp_path), "sample.cbz", "second")
    assert os.path.basename(third_path) == "sample_second_2.cbz"


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

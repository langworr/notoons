"""Tests for named output-directory configuration and custom subdirectories."""

import logging
import os
from datetime import date, timedelta
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
    from notoons.app import server_logging

    monkeypatch.setattr(app_config, "BASE_DIR", str(tmp_path))
    config_file = tmp_path / "config" / "config.txt"
    config_file.parent.mkdir()
    config_file.write_text(
        "logs_dir = service-logs\n"
        "temp_dir = service-temp\n"
        "server_log_level = debug\n"
        "server_log_file = custom/server-output.log\n",
        encoding="utf-8",
    )

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
        server_logging.configure_server_logging(config)
        test_logger = logging.getLogger("notoons.test_server_logging")
        test_logger.debug("configured server log entry")
        logging.getLogger("uvicorn.error").debug("configured Uvicorn log entry")
        for handler in logging.getLogger().handlers:
            if getattr(handler, "_notoons_server_log", False):
                handler.flush()
        for handler in logging.getLogger("uvicorn.error").handlers:
            if getattr(handler, "_notoons_server_log", False):
                handler.flush()

        log_path = tmp_path / "service-logs" / "custom" / f"server-output_{date.today().isoformat()}.log"
        log_contents = log_path.read_text(encoding="utf-8")
        assert "DEBUG [notoons.test_server_logging] configured server log entry" in log_contents
        assert "DEBUG [uvicorn.error] configured Uvicorn log entry" in log_contents

        next_day = date.today() + timedelta(days=1)

        class NextDay:
            @staticmethod
            def today():
                return next_day

        monkeypatch.setattr(server_logging, "date", NextDay)
        test_logger.debug("next day log entry")
        assert "next day log entry" in (
            tmp_path / "service-logs" / "custom" / f"server-output_{next_day.isoformat()}.log"
        ).read_text(encoding="utf-8")
        assert "next day log entry" not in log_path.read_text(encoding="utf-8")
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
    config_file = tmp_path / "config" / "config.txt"
    config_file.parent.mkdir()
    config_file.write_text("server_log_level = verbose\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid server_log_level"):
        app_config.load_config()


def test_environment_variables_do_not_override_config(tmp_path, monkeypatch):
    from notoons.app import config as app_config

    monkeypatch.setattr(app_config, "BASE_DIR", str(tmp_path))
    config_file = tmp_path / "config" / "config.txt"
    config_file.parent.mkdir()
    config_file.write_text(
        'outputs_dir = [{"nickname": "Configured", "path": "configured-output"}]\n'
        "logs_dir = configured-logs\n"
        "temp_dir = configured-temp\n"
        "server_log_file = configured.log\n"
        "server_log_level = WARNING\n"
        "oidc_issuer_url = https://configured.example\n"
        "oidc_client_id = configured-client\n"
        "oidc_client_secret = configured-secret\n"
        "oidc_redirect_uri = https://configured.example/callback\n"
        "oidc_post_logout_redirect_uri = https://configured.example/logout\n"
        "oidc_session_secret = configured-session-secret\n"
        "oidc_scopes = openid email\n"
        "oidc_cookie_secure = true\n",
        encoding="utf-8",
    )

    for name, value in {
        "NOTOONS_OUTPUTS_DIR": "environment-output",
        "OUTPUTS_DIR": "legacy-output",
        "NOTOONS_LOGS_DIR": "environment-logs",
        "LOGS_DIR": "legacy-logs",
        "NOTOONS_TEMP_DIR": "environment-temp",
        "TEMP_DIR": "legacy-temp",
        "NOTOONS_SERVER_LOG_FILE": "environment.log",
        "NOTOONS_SERVER_LOG_LEVEL": "verbose",
        "NOTOONS_OIDC_ISSUER_URL": "https://environment.example",
        "NOTOONS_OIDC_CLIENT_ID": "environment-client",
        "NOTOONS_OIDC_CLIENT_SECRET": "environment-secret",
        "NOTOONS_OIDC_REDIRECT_URI": "https://environment.example/callback",
        "NOTOONS_OIDC_POST_LOGOUT_REDIRECT_URI": "https://environment.example/logout",
        "NOTOONS_OIDC_SESSION_SECRET": "environment-session-secret",
        "NOTOONS_OIDC_SCOPES": "environment",
        "NOTOONS_OIDC_COOKIE_SECURE": "false",
    }.items():
        monkeypatch.setenv(name, value)

    config = app_config.load_config()

    assert config["outputs_dir"] == [{
        "nickname": "Configured",
        "path": str(tmp_path / "configured-output"),
    }]
    assert config["logs_dir"] == str(tmp_path / "configured-logs")
    assert config["temp_dir"] == str(tmp_path / "configured-temp")
    assert config["server_log_file"] == str(tmp_path / "configured-logs" / "configured.log")
    assert config["server_log_level"] == "WARNING"
    assert config["oidc_issuer_url"] == "https://configured.example"
    assert config["oidc_client_id"] == "configured-client"
    assert config["oidc_client_secret"] == "configured-secret"
    assert config["oidc_redirect_uri"] == "https://configured.example/callback"
    assert config["oidc_post_logout_redirect_uri"] == "https://configured.example/logout"
    assert config["oidc_session_secret"] == "configured-session-secret"
    assert config["oidc_scopes"] == "openid email"
    assert config["oidc_cookie_secure"] == "true"


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


def test_delete_job_is_limited_to_default_output_directory(tmp_path, monkeypatch):
    from falcon import testing
    from notoons.app.api.routes import jobs as jobs_route
    from notoons.app.api.routes.auth import AUTH_SESSION_COOKIE
    from notoons.app.auth import OIDCClient
    from notoons.app.main import create_app
    from notoons.app.state import JOBS, JOBS_ORDER

    default_dir = tmp_path / "default"
    archive_dir = tmp_path / "archive"
    default_dir.mkdir()
    archive_dir.mkdir()
    monkeypatch.setitem(
        jobs_route.CONFIG,
        "outputs_dir",
        [
            {"nickname": "Default", "path": str(default_dir)},
            {"nickname": "Archive", "path": str(archive_dir)},
        ],
    )

    default_job_id = "default-delete-test"
    archive_job_id = "archive-delete-test"
    default_file = default_dir / "default.cbz"
    archive_file = archive_dir / "archive.cbz"
    default_file.touch()
    archive_file.touch()
    monkeypatch.setitem(
        JOBS,
        default_job_id,
        {"output_dir": "Default", "cbz_path": str(default_file)},
    )
    monkeypatch.setitem(
        JOBS,
        archive_job_id,
        {"output_dir": "Archive", "cbz_path": str(archive_file)},
    )
    JOBS_ORDER.extend([default_job_id, archive_job_id])

    oidc = OIDCClient()
    auth_cookie = oidc.make_cookie({"sub": "tester"}, expires_in=3600)
    client = testing.TestClient(create_app())
    headers = {"Cookie": f"{AUTH_SESSION_COOKIE}={auth_cookie}"}

    try:
        default_response = client.simulate_delete(
            f"/jobs/{default_job_id}", headers=headers
        )
        assert default_response.status_code == 200
        assert not default_file.exists()

        archive_response = client.simulate_delete(
            f"/jobs/{archive_job_id}", headers=headers
        )
        assert archive_response.status_code == 403
        assert archive_file.is_file()
        assert archive_job_id in JOBS
    finally:
        for job_id in (default_job_id, archive_job_id):
            if job_id in JOBS_ORDER:
                JOBS_ORDER.remove(job_id)


def test_delete_all_conversion_logs_preserves_server_logs(tmp_path, monkeypatch):
    from falcon import testing
    from notoons.app.api.routes import jobs as jobs_route
    from notoons.app.api.routes.auth import AUTH_SESSION_COOKIE
    from notoons.app.auth import OIDCClient
    from notoons.app.main import create_app
    from notoons.app.state import JOBS

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    monkeypatch.setitem(jobs_route.CONFIG, "logs_dir", str(logs_dir))
    monkeypatch.setitem(
        jobs_route.CONFIG, "server_log_file", str(logs_dir / "server.log")
    )

    job_log = logs_dir / "0123456789_notes.log"
    orphaned_log = logs_dir / "abcdef0123_orphan.log"
    server_log = logs_dir / "server_2026-11-11.log"
    unrelated_file = logs_dir / "readme.txt"
    for path in (job_log, orphaned_log, server_log, unrelated_file):
        path.write_text("log contents", encoding="utf-8")

    job = {"logs": ["in-memory log"], "log_path": str(job_log), "status": "completed"}
    monkeypatch.setitem(JOBS, "0123456789", job)

    oidc = OIDCClient()
    auth_cookie = oidc.make_cookie({"sub": "tester"}, expires_in=3600)
    client = testing.TestClient(create_app())
    response = client.simulate_delete(
        "/logs",
        headers={"Cookie": f"{AUTH_SESSION_COOKIE}={auth_cookie}"},
    )

    assert response.status_code == 200
    assert response.json["deleted_count"] == 2
    assert not job_log.exists()
    assert not orphaned_log.exists()
    assert server_log.is_file()
    assert unrelated_file.is_file()
    assert job["logs"] == []
    assert job["log_path"] is None


def test_delete_all_conversion_logs_rejected_while_jobs_are_active(
    tmp_path, monkeypatch
):
    from falcon import testing
    from notoons.app.api.routes import jobs as jobs_route
    from notoons.app.api.routes.auth import AUTH_SESSION_COOKIE
    from notoons.app.auth import OIDCClient
    from notoons.app.main import create_app
    from notoons.app.state import JOBS

    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    monkeypatch.setitem(jobs_route.CONFIG, "logs_dir", str(logs_dir))
    log_path = logs_dir / "0123456789_active.log"
    log_path.write_text("active log", encoding="utf-8")
    monkeypatch.setitem(
        JOBS,
        "0123456789",
        {"logs": ["active log"], "log_path": str(log_path), "status": "processing"},
    )

    oidc = OIDCClient()
    auth_cookie = oidc.make_cookie({"sub": "tester"}, expires_in=3600)
    client = testing.TestClient(create_app())
    response = client.simulate_delete(
        "/logs",
        headers={"Cookie": f"{AUTH_SESSION_COOKIE}={auth_cookie}"},
    )

    assert response.status_code == 409
    assert log_path.is_file()


def test_management_page_and_paginated_output_browser(tmp_path, monkeypatch):
    from io import BytesIO
    from zipfile import ZipFile
    from falcon import testing
    from notoons.app.api.routes import files as files_route
    from notoons.app.api.routes.auth import AUTH_SESSION_COOKIE
    from notoons.app.auth import OIDCClient
    from notoons.app.main import create_app

    default_dir = tmp_path / "default"
    archive_dir = default_dir / "archive"
    temp_dir = tmp_path / "temp"
    (default_dir / "nested" / "deeper").mkdir(parents=True)
    (archive_dir / "volumes").mkdir(parents=True)
    temp_dir.mkdir()
    for filename in ("a.cbz", "b.cbz", "c.cbz"):
        (default_dir / filename).write_bytes(filename.encode())
    (default_dir / "nested" / "deeper" / "comic.cbz").write_bytes(b"default")
    (archive_dir / "volumes" / "book.cbz").write_bytes(b"archive")
    monkeypatch.setitem(
        files_route.CONFIG,
        "outputs_dir",
        [
            {"nickname": "Default", "path": str(default_dir)},
            {"nickname": "Archive", "path": str(archive_dir)},
        ],
    )
    monkeypatch.setitem(files_route.CONFIG, "temp_dir", str(temp_dir))

    oidc = OIDCClient()
    auth_cookie = oidc.make_cookie({"sub": "tester"}, expires_in=3600)
    client = testing.TestClient(create_app())
    headers = {"Cookie": f"{AUTH_SESSION_COOKIE}={auth_cookie}"}

    page_response = client.simulate_get("/manage", headers=headers)
    assert page_response.status_code == 200
    assert "Delete server logs" in page_response.text
    assert "Download selected files" in page_response.text

    page_one = client.simulate_get(
        "/outputs/files?directory=Default&path=&page=1&per_page=2",
        headers=headers,
    )
    assert page_one.status_code == 200
    assert page_one.json["total"] == 4
    assert page_one.json["total_pages"] == 2
    assert [entry["name"] for entry in page_one.json["entries"]] == ["nested", "a.cbz"]
    assert page_one.json["entries"][0]["kind"] == "directory"

    page_two = client.simulate_get(
        "/outputs/files?directory=0&path=&page=2&per_page=2",
        headers=headers,
    )
    assert [entry["name"] for entry in page_two.json["entries"]] == ["b.cbz", "c.cbz"]

    nested_page = client.simulate_get(
        "/outputs/files?directory=Default&path=nested%2Fdeeper&page=1&per_page=2",
        headers=headers,
    )
    assert nested_page.status_code == 200
    assert nested_page.json["entries"][0]["id"] == "0:nested/deeper/comic.cbz"

    archive_root = client.simulate_get(
        "/outputs/files?directory=Archive&page=1",
        headers=headers,
    )
    assert archive_root.status_code == 200
    assert archive_root.json["directory"] == "Archive"
    assert archive_root.json["entries"][0]["name"] == "volumes"

    archive_response = client.simulate_post(
        "/outputs/archive",
        json={"files": ["0:nested/deeper/comic.cbz"]},
        headers=headers,
    )
    assert archive_response.status_code == 200
    with ZipFile(BytesIO(archive_response.content)) as archive:
        assert archive.namelist() == ["Default/nested/deeper/comic.cbz"]
        assert archive.read("Default/nested/deeper/comic.cbz") == b"default"

    full_archive_response = client.simulate_get(
        "/outputs/archive", headers=headers
    )
    assert full_archive_response.status_code == 200
    with ZipFile(BytesIO(full_archive_response.content)) as archive:
        assert set(archive.namelist()) == {
            "Default/a.cbz",
            "Default/b.cbz",
            "Default/c.cbz",
            "Default/nested/deeper/comic.cbz",
            "Archive/volumes/book.cbz",
        }


def test_output_browser_paginates_large_folders_and_navigates_subfolders(
    tmp_path, monkeypatch
):
    from falcon import testing
    from notoons.app.api.routes import files as files_route
    from notoons.app.api.routes.auth import AUTH_SESSION_COOKIE
    from notoons.app.auth import OIDCClient
    from notoons.app.main import create_app

    output_dir = tmp_path / "output"
    (output_dir / "folder_000" / "nested").mkdir(parents=True)
    for index in range(1, 55):
        (output_dir / f"folder_{index:03}").mkdir()
    for index in range(51):
        (output_dir / f"file_{index:03}.cbz").write_bytes(b"comic")
    (output_dir / "folder_000" / "nested" / "comic.cbz").write_bytes(b"nested")
    monkeypatch.setitem(
        files_route.CONFIG,
        "outputs_dir",
        [{"nickname": "Default", "path": str(output_dir)}],
    )

    oidc = OIDCClient()
    auth_cookie = oidc.make_cookie({"sub": "tester"}, expires_in=3600)
    client = testing.TestClient(create_app())
    headers = {"Cookie": f"{AUTH_SESSION_COOKIE}={auth_cookie}"}

    first_page = client.simulate_get(
        "/outputs/files?directory=0&page=1&per_page=10",
        headers=headers,
    )
    assert first_page.status_code == 200
    assert len(first_page.json["entries"]) == 10
    assert first_page.json["total"] == 106
    assert first_page.json["total_pages"] == 11
    assert first_page.json["entries"][0]["kind"] == "directory"

    last_page = client.simulate_get(
        "/outputs/files?directory=0&page=11&per_page=10",
        headers=headers,
    )
    assert last_page.status_code == 200
    assert len(last_page.json["entries"]) == 6
    assert all(entry["kind"] == "file" for entry in last_page.json["entries"])

    nested_page = client.simulate_get(
        "/outputs/files?directory=0&path=folder_000%2Fnested&page=1",
        headers=headers,
    )
    assert nested_page.status_code == 200
    assert nested_page.json["entries"][0]["id"] == "0:folder_000/nested/comic.cbz"


def test_delete_default_outputs_only_deletes_top_level_files(
    tmp_path, monkeypatch
):
    from falcon import testing
    from notoons.app.api.routes import files as files_route
    from notoons.app.api.routes.auth import AUTH_SESSION_COOKIE
    from notoons.app.auth import OIDCClient
    from notoons.app.main import create_app

    default_dir = tmp_path / "default"
    archive_dir = tmp_path / "archive"
    (default_dir / "nested").mkdir(parents=True)
    archive_dir.mkdir()
    root_file = default_dir / "root.cbz"
    default_file = default_dir / "nested" / "comic.cbz"
    archive_file = archive_dir / "book.cbz"
    root_file.write_bytes(b"root")
    default_file.write_bytes(b"default")
    archive_file.write_bytes(b"archive")
    monkeypatch.setitem(
        files_route.CONFIG,
        "outputs_dir",
        [
            {"nickname": "Default", "path": str(default_dir)},
            {"nickname": "Archive", "path": str(archive_dir)},
        ],
    )

    oidc = OIDCClient()
    auth_cookie = oidc.make_cookie({"sub": "tester"}, expires_in=3600)
    client = testing.TestClient(create_app())
    response = client.simulate_delete(
        "/outputs/default",
        headers={"Cookie": f"{AUTH_SESSION_COOKIE}={auth_cookie}"},
    )

    assert response.status_code == 200
    assert response.json["deleted_count"] == 1
    assert not root_file.exists()
    assert default_file.is_file()
    assert archive_file.is_file()


def test_remove_job_entry_keeps_output_file(tmp_path, monkeypatch):
    from falcon import testing
    from notoons.app.api.routes.auth import AUTH_SESSION_COOKIE
    from notoons.app.auth import OIDCClient
    from notoons.app.main import create_app
    from notoons.app.state import JOBS, JOBS_ORDER

    job_id = "entry-remove-test"
    output_file = tmp_path / "entry.cbz"
    output_file.write_bytes(b"keep output")
    monkeypatch.setitem(
        JOBS,
        job_id,
        {"status": "completed", "cbz_path": str(output_file)},
    )
    JOBS_ORDER.insert(0, job_id)

    oidc = OIDCClient()
    auth_cookie = oidc.make_cookie({"sub": "tester"}, expires_in=3600)
    client = testing.TestClient(create_app())

    try:
        response = client.simulate_delete(
            f"/jobs/{job_id}/entry",
            headers={"Cookie": f"{AUTH_SESSION_COOKIE}={auth_cookie}"},
        )
        assert response.status_code == 200
        assert job_id not in JOBS
        assert job_id not in JOBS_ORDER
        assert output_file.is_file()
    finally:
        JOBS.pop(job_id, None)
        if job_id in JOBS_ORDER:
            JOBS_ORDER.remove(job_id)


def test_clear_server_logs_truncates_active_file_and_removes_rotated_files(tmp_path):
    import logging
    from notoons.app.server_logging import DailyFileHandler, clear_server_logs

    config = {"server_log_file": str(tmp_path / "server.log")}
    handler = DailyFileHandler(config["server_log_file"], encoding="utf-8")
    handler._notoons_server_log = True
    logger = logging.getLogger()
    logger.addHandler(handler)
    old_log = tmp_path / "server_2020-01-01.log"
    old_log.write_text("old server log", encoding="utf-8")
    unrelated = tmp_path / "server-notes.log"
    unrelated.write_text("keep", encoding="utf-8")

    try:
        handler.stream.write("active server log\n")
        handler.flush()
        assert clear_server_logs(config) == 2
        assert handler.stream is not None
        assert handler.stream.tell() == 0
        assert not old_log.exists()
        assert unrelated.is_file()

        handler.handle(
            logging.LogRecord(
                "notoons.test",
                logging.INFO,
                __file__,
                1,
                "new server log entry",
                (),
                None,
            )
        )
        with open(handler.baseFilename, encoding="utf-8") as active_log:
            assert "new server log entry" in active_log.read()
    finally:
        logger.removeHandler(handler)
        handler.close()


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

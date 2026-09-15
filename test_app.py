"""Integration tests for the Notoons PDF conversion web app."""

import os
import time

import pymupdf
from falcon import testing

import app


def test_async_and_folder_cleanup():
    """Verify config, async conversion, temp cleanup, and output cleanup across the app lifecycle."""
    client = testing.TestClient(app.app)

    # 1. Test GET /config
    res_cfg = client.simulate_get("/config")
    assert res_cfg.status_code == 200
    cfg = res_cfg.json
    print("[PASS] GET /config:", cfg)
    assert os.path.isdir(cfg["outputs_dir"])
    assert os.path.isdir(cfg["logs_dir"])
    assert os.path.isdir(cfg["temp_dir"])

    # 2. Verify temp directory is clean
    temp_files_before = os.listdir(cfg["temp_dir"])
    print(f"[PASS] Temp dir before upload has {len(temp_files_before)} files")

    # 3. Create a test PDF with 2 pages
    doc = pymupdf.open()
    p1 = doc.new_page(width=800, height=600)
    p1.draw_rect(pymupdf.Rect(40, 40, 760, 560), fill=(0.9, 0.9, 1))
    p1.insert_text((100, 100), "Lecture Part 1", fontsize=30)

    p2 = doc.new_page(width=1000, height=800)
    p2.draw_rect(pymupdf.Rect(50, 50, 450, 350), fill=(0.8, 0.8, 0.8))
    p2.draw_rect(pymupdf.Rect(550, 50, 950, 350), fill=(0.8, 0.8, 0.8))
    pdf_bytes = doc.tobytes()
    doc.close()

    # 4. Enqueue Job via POST /jobs
    boundary = "BoundaryConfiguredDirs99"
    body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="mode"\r\n\r\n'
        f"auto\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="dpi"\r\n\r\n'
        f"150\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="config_test.pdf"\r\n'
        f"Content-Type: application/pdf\r\n\r\n"
    ).encode("utf-8") + pdf_bytes + f"\r\n--{boundary}--\r\n".encode("utf-8")

    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    res_upload = client.simulate_post("/jobs", body=body, headers=headers)
    assert res_upload.status_code == 200, f"Upload error: {res_upload.status_code} {res_upload.text}"
    data = res_upload.json
    assert data["status"] == "ok"
    job_id = data["job_id"]
    print(f"[PASS] POST /jobs: Job enqueued with ID '{job_id}'")

    # 5. Poll until completed
    start_wait = time.time()
    job_record = None
    while time.time() - start_wait < 15:
        res_poll = client.simulate_get(f"/jobs/{job_id}")
        assert res_poll.status_code == 200
        job_record = res_poll.json
        status = job_record["status"]
        if status in ("completed", "failed"):
            break
        time.sleep(0.3)

    assert job_record is not None
    assert job_record["status"] == "completed", f"Job failed: {job_record.get('error')}"
    print(f"[PASS] Job completed: {job_record['pages_processed']} pages -> {job_record['slides_extracted']} slides")

    # 6. Verify temp files are DELETED immediately after execution
    temp_pdf_expected = os.path.join(cfg["temp_dir"], f"upload_{job_id}.pdf")
    assert not os.path.exists(temp_pdf_expected), f"Temp file {temp_pdf_expected} was NOT deleted!"
    print(f"[PASS] Temp file successfully deleted: {temp_pdf_expected} does not exist.")

    # 7. Verify CBZ file exists in configured outputs_dir
    expected_cbz = os.path.join(cfg["outputs_dir"], f"{job_id}_config_test.cbz")
    assert os.path.isfile(expected_cbz), f"Expected CBZ not found at: {expected_cbz}"
    assert os.path.getsize(expected_cbz) == job_record["cbz_size_bytes"]
    print(f"[PASS] Output CBZ verified on disk in outputs_dir: {expected_cbz} ({os.path.getsize(expected_cbz)} bytes)")

    # 8. Verify Log file exists in configured logs_dir
    expected_log = os.path.join(cfg["logs_dir"], f"{job_id}_config_test.log")
    assert os.path.isfile(expected_log), f"Expected log not found at: {expected_log}"
    print(f"[PASS] Execution log verified on disk in logs_dir: {expected_log} ({os.path.getsize(expected_log)} bytes)")

    # 9. Test streaming download from disk
    res_dl = client.simulate_get(f"/download/{job_id}")
    assert res_dl.status_code == 200
    assert len(res_dl.content) == job_record["cbz_size_bytes"]
    print(f"[PASS] GET /download/{job_id}: Streamed valid CBZ from disk ({len(res_dl.content)} bytes)")

    # 10. Test DELETE /jobs/{job_id} cleans up the output file from outputs_dir
    res_del = client.simulate_delete(f"/jobs/{job_id}")
    assert res_del.status_code == 200
    assert not os.path.exists(expected_cbz), f"CBZ output file was NOT deleted after DELETE /jobs/{job_id}"
    print("[PASS] Output file cleaned up from outputs_dir on job deletion")

    print("\nALL FOLDER CONFIGURATION & CLEANUP TESTS PASSED WITH 100% SUCCESS!")


if __name__ == "__main__":
    test_async_and_folder_cleanup()

"""Shared in-memory job state and conversion lifecycle helpers.

The module stores active conversion jobs in process memory and coordinates
their temporary uploads, generated CBZ files, progress updates, and logs.
Conversion work is performed in a worker thread so Falcon's async request
handlers remain responsive while a PDF is being processed.
"""

import asyncio
import os
import time
import traceback
from .config import CONFIG
from .services import converter

JOBS: dict[str, dict] = {}
"""Conversion-job records keyed by their unique job identifiers."""
JOBS_ORDER: list[str] = []
"""Job identifiers ordered from newest to oldest for list responses."""
CACHE_TTL = 7200  # 2 ore
"""Maximum age in seconds before an in-memory job is eligible for cleanup."""


def cleanup_temp_dir():
    """Delete regular files left in the configured temporary directory.

    This cleanup is intentionally limited to files directly inside
    ``CONFIG["temp_dir"]``.  Subdirectories are left untouched, and missing
    directories or individual deletion failures are ignored so startup can
    continue when a stale file is locked by another process.
    """
    temp_dir = CONFIG["temp_dir"]
    if os.path.isdir(temp_dir):
        for fname in os.listdir(temp_dir):
            fpath = os.path.join(temp_dir, fname)
            try:
                if os.path.isfile(fpath):
                    os.remove(fpath)
            except OSError:
                pass


def cleanup_jobs():
    """Remove expired jobs and their associated files from disk.

    A job is considered expired when its ``created_at`` timestamp is older
    than :data:`CACHE_TTL`.  Expired records are removed from both
    :data:`JOBS` and :data:`JOBS_ORDER`; their generated CBZ file and pending
    temporary PDF are also deleted when present.  Missing files and ordinary
    filesystem deletion errors are ignored.
    """
    now = time.time()
    expired = [jid for jid, j in JOBS.items() if now - j.get("created_at", 0) > CACHE_TTL]
    for jid in expired:
        j = JOBS.pop(jid, None)
        if jid in JOBS_ORDER:
            JOBS_ORDER.remove(jid)
        if j:
            cbz_p = j.get("cbz_path")
            if cbz_p and os.path.isfile(cbz_p):
                try:
                    os.remove(cbz_p)
                except OSError:
                    pass
            tmp_p = j.get("temp_pdf_path")
            if tmp_p and os.path.isfile(tmp_p):
                try:
                    os.remove(tmp_p)
                except OSError:
                    pass


def run_job_sync(job_id: str, temp_pdf_path: str, cbz_path: str, log_path: str, mode: str, dpi: int):
    """Run one PDF conversion and update its in-memory job record.

    The job transitions from ``pending`` to ``processing`` and then to either
    ``completed`` or ``failed``.  While conversion is running, the progress
    callback updates page counts, percentage, slide counts, and the timestamp
    of the latest update.  The logging callback appends messages to the job's
    in-memory log and to its log file when possible.

    On success, conversion statistics and the generated CBZ path are stored
    in the job record.  On a handled conversion error, the error message and
    traceback are recorded, any partial CBZ is removed, and the job is marked
    ``failed``.  The temporary uploaded PDF is removed in all paths,
    including when the job no longer exists before execution starts.

    Args:
        job_id: Identifier of the job in :data:`JOBS`.
        temp_pdf_path: Path to the uploaded PDF used as conversion input.
        cbz_path: Destination path for the generated CBZ archive.
        log_path: Path to the job's append-only text log.
        mode: Conversion mode passed to :func:`converter.convert_pdf`.
        dpi: Rendering resolution passed to :func:`converter.convert_pdf`.

    Returns:
        ``None``.  Results are written directly into the corresponding job
        dictionary.
    """
    job = JOBS.get(job_id)
    if not job:
        if os.path.isfile(temp_pdf_path):
            try:
                os.remove(temp_pdf_path)
            except OSError:
                pass
        return

    job["status"] = "processing"
    job["updated_at"] = time.time()

    def on_progress(cur_page, total_pages, slides_so_far):
        pct = int((cur_page / max(1, total_pages)) * 100)
        job["current_page"] = cur_page
        job["total_pages"] = total_pages
        job["progress_percent"] = pct
        job["slides_extracted"] = slides_so_far
        job["updated_at"] = time.time()

    def on_log(line):
        job["logs"].append(line)
        try:
            with open(log_path, "a", encoding="utf-8") as lf:
                lf.write(line + "\n")
        except OSError:
            pass

    try:
        res = converter.convert_pdf(
            temp_pdf_path,
            mode=mode,
            dpi=dpi,
            output_cbz_path=cbz_path,
            logger=on_log,
            progress_callback=on_progress,
        )
        job["status"] = "completed"
        job["pages_processed"] = res["pages_processed"]
        job["slides_extracted"] = res["slides_extracted"]
        job["elapsed_ms"] = res["elapsed_ms"]
        job["cbz_size_bytes"] = res["cbz_size_bytes"]
        job["cbz_path"] = cbz_path
        job["log_path"] = log_path
        job["progress_percent"] = 100
        job["updated_at"] = time.time()
    except (OSError, ValueError, TypeError, KeyError, IndexError, RuntimeError) as e:
        err_msg = str(e)
        tb = traceback.format_exc()
        job["status"] = "failed"
        job["error"] = err_msg
        job["traceback"] = tb
        on_log(f"[ERROR] {err_msg}")
        on_log(tb)
        job["updated_at"] = time.time()
        if os.path.isfile(cbz_path):
            try:
                os.remove(cbz_path)
            except OSError:
                pass
    finally:
        if os.path.isfile(temp_pdf_path):
            try:
                os.remove(temp_pdf_path)
            except OSError:
                pass


async def start_job_background(job_id: str, temp_pdf_path: str, cbz_path: str, log_path: str, mode: str, dpi: int):
    """Run :func:`run_job_sync` without blocking the async event loop.

    The synchronous conversion is submitted to the default worker-thread
    executor through :func:`asyncio.to_thread`.  The coroutine completes when
    the conversion finishes and propagates unexpected exceptions raised by
    the worker or thread dispatch layer.

    Args:
        job_id: Identifier of the job in :data:`JOBS`.
        temp_pdf_path: Path to the uploaded PDF used as conversion input.
        cbz_path: Destination path for the generated CBZ archive.
        log_path: Path to the job's text log.
        mode: Conversion mode passed to :func:`converter.convert_pdf`.
        dpi: Rendering resolution passed to :func:`converter.convert_pdf`.

    Returns:
        ``None`` after the worker has completed and updated the job record.
    """
    await asyncio.to_thread(run_job_sync, job_id, temp_pdf_path, cbz_path, log_path, mode, dpi)

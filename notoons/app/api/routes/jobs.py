"""Falcon resources for creating, tracking, deleting, and downloading jobs.

The resources in this module expose the conversion-job lifecycle over HTTP.
Jobs are stored in the process-local state managed by :mod:`notoons.app.state`;
uploads are written to the configured temporary directory and completed CBZ
archives are served from the configured output directory.
"""

import asyncio
import os
import time
import traceback
import uuid
import falcon
import falcon.asgi

from ...config import CONFIG
from ...state import JOBS, JOBS_ORDER, cleanup_jobs, start_job_background


class JobsResource:
    """List existing conversion jobs and enqueue new PDF conversions.

    ``GET`` returns a compact summary of every non-expired job.  ``POST``
    accepts a multipart form containing a PDF and optional conversion
    settings, creates a pending job, and starts conversion in the background.
    """

    async def on_get(self, _req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        """Return summaries for all currently retained conversion jobs.

        Expired jobs are removed before the response is built.  Jobs are
        returned in :data:`JOBS_ORDER` order, newest first.  The response
        contains progress, output, timing, error, and download metadata but
        does not include the uploaded PDF contents.

        Args:
            _req: Falcon request object, unused by this handler.
            resp: Falcon response populated with a JSON object containing a
                ``jobs`` list.

        Returns:
            ``None``.  The response status is ``200 OK``.
        """
        cleanup_jobs()
        summary_list = []
        for jid in JOBS_ORDER:
            j = JOBS.get(jid)
            if not j:
                continue
            summary_list.append({
                "id": j["id"],
                "filename": j["filename"],
                "cbz_filename": j["cbz_filename"],
                "status": j["status"],
                "mode": j["mode"],
                "dpi": j["dpi"],
                "file_size_bytes": j["file_size_bytes"],
                "total_pages": j["total_pages"],
                "pages_processed": j.get("pages_processed", j["total_pages"]),
                "current_page": j["current_page"],
                "progress_percent": j["progress_percent"],
                "slides_extracted": j["slides_extracted"],
                "elapsed_ms": j["elapsed_ms"],
                "cbz_size_bytes": j["cbz_size_bytes"],
                "download_url": j["download_url"],
                "error": j["error"],
                "created_at": j["created_at"],
            })
        resp.status = falcon.HTTP_200
        resp.media = {"jobs": summary_list}

    async def on_post(self, req: falcon.asgi.Request, resp: falcon.asgi.Response) -> None:
        """Accept a PDF upload and enqueue an asynchronous conversion job.

        The multipart form may contain the following fields:

        ``file``
            The uploaded PDF.  This field is required and is streamed to the
            configured temporary directory in 1 MiB chunks.
        ``mode``
            Optional conversion mode.  Accepted values are ``auto``,
            ``slides``, and ``pages``; other values use ``auto``.
        ``dpi``
            Optional integer rendering resolution.  The default is ``200``;
            values that cannot be parsed as integers leave the default in
            place.

        A valid upload creates a pending job, writes an initial log entry,
        schedules background conversion, and returns its identifier.  An
        empty or missing file returns a ``400 Bad Request`` response.  Errors
        encountered while reading the form or preparing the job return a
        ``500 Internal Server Error`` response with an error message and
        traceback.

        Args:
            req: Falcon request containing the multipart form data.
            resp: Falcon response populated with the enqueue result.

        Returns:
            ``None``.  Successful responses use ``200 OK`` and contain
            ``status``, ``job_id``, ``filename``, and ``message`` fields.
        """
        cleanup_jobs()
        try:
            form = await req.get_media()
            job_id = uuid.uuid4().hex[:10]
            temp_pdf_path = os.path.join(CONFIG["temp_dir"], f"upload_{job_id}.pdf")
            filename = "document.pdf"
            mode = "auto"
            dpi = 200
            bytes_written = 0

            async for part in form:
                if part.name == "file":
                    filename = part.filename or "document.pdf"
                    with open(temp_pdf_path, "wb") as f_out:
                        while True:
                            chunk = await part.stream.read(1024 * 1024)
                            if not chunk:
                                break
                            f_out.write(chunk)
                            bytes_written += len(chunk)
                elif part.name == "mode":
                    val = await part.get_text()
                    if val in ("auto", "slides", "pages"):
                        mode = val
                elif part.name == "dpi":
                    val = await part.get_text()
                    try:
                        dpi = int(val)
                    except ValueError:
                        pass

            if bytes_written == 0 or not os.path.isfile(temp_pdf_path):
                if os.path.isfile(temp_pdf_path):
                    os.remove(temp_pdf_path)
                resp.status = falcon.HTTP_400
                resp.media = {"status": "error", "error": "No file uploaded or file was empty."}
                return

            base_name = os.path.splitext(filename)[0]
            cbz_name = f"{base_name}.cbz"
            cbz_path = os.path.join(CONFIG["outputs_dir"], f"{job_id}_{cbz_name}")
            log_path = os.path.join(CONFIG["logs_dir"], f"{job_id}_{base_name}.log")

            job = {
                "id": job_id,
                "filename": filename,
                "cbz_filename": cbz_name,
                "cbz_path": cbz_path,
                "log_path": log_path,
                "temp_pdf_path": temp_pdf_path,
                "status": "pending",
                "mode": mode,
                "dpi": dpi,
                "file_size_bytes": bytes_written,
                "total_pages": 0,
                "pages_processed": 0,
                "current_page": 0,
                "progress_percent": 0,
                "slides_extracted": 0,
                "elapsed_ms": 0,
                "cbz_size_bytes": 0,
                "download_url": f"/download/{job_id}",
                "logs": [
                  f"[{time.strftime('%H:%M:%S')}] [UPLOAD] Uploaded "
                  f"'{filename}' ({bytes_written / 1024:.1f} KB). Queued."
                ],
                "error": None,
                "created_at": time.time(),
                "updated_at": time.time(),
            }

            JOBS[job_id] = job
            JOBS_ORDER.insert(0, job_id)

            try:
                with open(log_path, "w", encoding="utf-8") as lf:
                    lf.write(job["logs"][0] + "\n")
            except OSError:
                pass

            asyncio.create_task(
                start_job_background(job_id, temp_pdf_path, cbz_path, log_path, mode, dpi)
            )

            resp.status = falcon.HTTP_200
            resp.media = {
                "status": "ok",
                "job_id": job_id,
                "filename": filename,
                "message": "Job enqueued successfully",
            }
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, RuntimeError) as e:
            resp.status = falcon.HTTP_500
            resp.media = {"status": "error", "error": str(e), "traceback": traceback.format_exc()}


class JobDetailResource:
    """Return details for, or remove, one conversion job."""

    async def on_get(self, _req: falcon.asgi.Request, resp: falcon.asgi.Response, job_id: str) -> None:
        """Return the current status and metadata for a specific job.

        Args:
            _req: Falcon request object, unused by this handler.
            resp: Falcon response populated with job metadata and log entries.
            job_id: Identifier supplied by the ``/jobs/{job_id}`` route.

        Returns:
            ``None``.  The response is ``200 OK`` with the job record fields,
            or ``404 Not Found`` with an error object when the identifier is
            unknown or expired.
        """
        j = JOBS.get(job_id)
        if not j:
            resp.status = falcon.HTTP_404
            resp.media = {"error": "Job not found"}
            return

        resp.status = falcon.HTTP_200
        resp.media = {
            "id": j["id"],
            "filename": j["filename"],
            "cbz_filename": j["cbz_filename"],
            "status": j["status"],
            "mode": j["mode"],
            "dpi": j["dpi"],
            "file_size_bytes": j["file_size_bytes"],
            "total_pages": j["total_pages"],
            "pages_processed": j.get("pages_processed", j["total_pages"]),
            "current_page": j["current_page"],
            "progress_percent": j["progress_percent"],
            "slides_extracted": j["slides_extracted"],
            "elapsed_ms": j["elapsed_ms"],
            "cbz_size_bytes": j["cbz_size_bytes"],
            "download_url": j["download_url"],
            "logs": j["logs"],
            "log_path": j.get("log_path"),
            "error": j["error"],
            "created_at": j["created_at"],
        }

    async def on_delete(self, _req: falcon.asgi.Request, resp: falcon.asgi.Response, job_id: str) -> None:
        """Delete a job and remove its generated and temporary files.

        The job is removed from both in-memory indexes.  When paths are
        present, the generated CBZ archive and uploaded temporary PDF are
        deleted from disk.  Deletion is idempotent: an unknown job still
        receives a successful response.

        Args:
            _req: Falcon request object, unused by this handler.
            resp: Falcon response populated with ``{"status": "ok"}``.
            job_id: Identifier supplied by the ``/jobs/{job_id}`` route.

        Returns:
            ``None``.  The response status is always ``200 OK``.
        """
        j = JOBS.pop(job_id, None)
        if job_id in JOBS_ORDER:
            JOBS_ORDER.remove(job_id)

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

        resp.status = falcon.HTTP_200
        resp.media = {"status": "ok"}


class DownloadResource:
    """Stream completed CBZ archives to clients."""

    async def on_get(self, _req: falcon.asgi.Request, resp: falcon.asgi.Response, job_id: str) -> None:
        """Stream the generated CBZ archive for a completed job.

        The handler only serves jobs whose status is ``completed`` and whose
        output file still exists.  The archive is read in 64 KiB chunks and
        returned with the comic-book ZIP media type and an attachment filename
        derived from the original upload.

        Args:
            _req: Falcon request object, unused by this handler.
            resp: Falcon response configured with the asynchronous file
                stream.
            job_id: Identifier supplied by the ``/download/{job_id}`` route.

        Returns:
            ``None``.  A ready file produces a streamed ``200 OK`` response;
            a missing, expired, incomplete, or deleted output produces
            ``404 Not Found`` with a plain-text error message.
        """
        j = JOBS.get(job_id)
        if not j or j.get("status") != "completed":
            resp.status = falcon.HTTP_404
            resp.text = "File not ready or expired"
            return

        cbz_path = j.get("cbz_path")
        if not cbz_path or not os.path.isfile(cbz_path):
            resp.status = falcon.HTTP_404
            resp.text = "Output file not found on disk"
            return

        resp.content_type = "application/vnd.comicbook+zip"
        resp.append_header(
            "Content-Disposition", f'attachment; filename="{j["cbz_filename"]}"'
        )

        async def file_streamer():
            with open(cbz_path, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk

        resp.stream = file_streamer()

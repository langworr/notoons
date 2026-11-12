"""Authenticated file-management endpoints for logs and conversion outputs."""

import asyncio
import io
import math
import os
import re
import tempfile
import uuid
import zipfile

import falcon
import falcon.asgi
import pymupdf
from PIL import Image, UnidentifiedImageError

from ...config import CONFIG
from ...server_logging import clear_server_logs
from ...state import JOBS

MAX_PDF_SIZE_BYTES = 100 * 1024 * 1024
MAX_CBZ_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_CBZ_PAGES = 500
MAX_CBZ_ENTRIES = 1000
MAX_CBZ_IMAGE_BYTES = 64 * 1024 * 1024
MAX_IMAGE_PIXELS = 12_000_000
PDF_IMAGE_SIZE = (1600, 2200)
PDF_JPEG_QUALITY = 78
IMAGE_EXTENSIONS = {
    ".bmp", ".gif", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp",
}


class InvalidCbzError(ValueError):
    """Raised when an input file is not a usable CBZ archive."""


class PdfTooLargeError(ValueError):
    """Raised when a conversion would exceed the PDF size limit."""


def _natural_sort_key(value):
    return tuple(
        (0, int(part)) if part.isdecimal() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
    )


def _convert_cbz_to_pdf(cbz_path, pdf_path):
    """Convert a bounded CBZ archive to an image-compressed PDF file."""
    if os.path.splitext(cbz_path)[1].casefold() != ".cbz":
        raise InvalidCbzError("PDF downloads are only supported for CBZ files")

    try:
        archive = zipfile.ZipFile(cbz_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise InvalidCbzError(
            "The selected CBZ file is not a valid archive"
        ) from exc

    document = pymupdf.open()
    try:
        with archive:
            entries = archive.infolist()
            if len(entries) > MAX_CBZ_ENTRIES:
                raise PdfTooLargeError(
                    f"The CBZ contains more than {MAX_CBZ_ENTRIES} files"
                )
            images = sorted(
                [
                    entry
                    for entry in entries
                    if not entry.is_dir()
                    and os.path.splitext(entry.filename)[1].casefold()
                    in IMAGE_EXTENSIONS
                ],
                key=lambda entry: _natural_sort_key(entry.filename),
            )
            if not images:
                raise InvalidCbzError(
                    "The CBZ archive contains no supported images"
                )
            if len(images) > MAX_CBZ_PAGES:
                raise PdfTooLargeError(
                    f"The CBZ contains more than {MAX_CBZ_PAGES} pages"
                )
            uncompressed_size = sum(entry.file_size for entry in images)
            if uncompressed_size > MAX_CBZ_UNCOMPRESSED_BYTES:
                raise PdfTooLargeError(
                    "The CBZ images exceed the supported uncompressed size"
                )

            total_image_bytes = 0
            for entry in images:
                if entry.file_size > MAX_CBZ_IMAGE_BYTES:
                    raise PdfTooLargeError(
                        f"Image '{entry.filename}' exceeds the 64 MiB limit"
                    )
                try:
                    image_data = archive.read(entry)
                    with Image.open(io.BytesIO(image_data)) as image:
                        if image.width * image.height > MAX_IMAGE_PIXELS:
                            raise PdfTooLargeError(
                                f"Image '{entry.filename}' exceeds the "
                                "supported pixel dimensions"
                            )
                        image.seek(0)
                        image.thumbnail(
                            PDF_IMAGE_SIZE, Image.Resampling.LANCZOS
                        )
                        if (
                            image.mode in ("RGBA", "LA")
                            or "transparency" in image.info
                        ):
                            rgba_image = image.convert("RGBA")
                            rgb_image = Image.new(
                                "RGB", rgba_image.size, "white"
                            )
                            rgb_image.paste(
                                rgba_image,
                                mask=rgba_image.getchannel("A"),
                            )
                            image = rgb_image
                        elif image.mode != "RGB":
                            image = image.convert("RGB")
                        image_width, image_height = image.size
                        buffer = io.BytesIO()
                        image.save(
                            buffer,
                            format="JPEG",
                            quality=PDF_JPEG_QUALITY,
                            optimize=True,
                        )
                        jpeg_data = buffer.getvalue()
                except PdfTooLargeError:
                    raise
                except (
                    OSError,
                    ValueError,
                    RuntimeError,
                    UnidentifiedImageError,
                    zipfile.BadZipFile,
                ) as exc:
                    raise InvalidCbzError(
                        f"Unable to read CBZ image '{entry.filename}'"
                    ) from exc

                total_image_bytes += len(jpeg_data)
                if total_image_bytes > MAX_PDF_SIZE_BYTES:
                    raise PdfTooLargeError(
                        "The converted PDF would exceed the 100 MiB size limit"
                    )

                page_width = image_width * 72 / 150
                page_height = image_height * 72 / 150
                page = document.new_page(width=page_width, height=page_height)
                page.insert_image(page.rect, stream=jpeg_data)

        document.save(
            pdf_path,
            garbage=4,
            deflate=True,
            deflate_images=True,
            deflate_fonts=True,
        )
    except zipfile.BadZipFile as exc:
        raise InvalidCbzError(
            "The selected CBZ file is not a valid archive"
        ) from exc
    finally:
        document.close()

    if os.path.getsize(pdf_path) > MAX_PDF_SIZE_BYTES:
        raise PdfTooLargeError(
            "The converted PDF would exceed the 100 MiB size limit"
        )


def _is_within(root, path):
    try:
        common_path = os.path.normcase(os.path.commonpath((root, path)))
        return common_path == os.path.normcase(root)
    except ValueError:
        return False


def _configured_default_output():
    return next(
        (
            directory
            for directory in CONFIG["outputs_dir"]
            if directory["nickname"] == "Default"
        ),
        None,
    )


def _direct_files(root):
    """Yield regular files immediately inside a directory without symlinks."""
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_file(follow_symlinks=False):
                yield entry.path


def _walk_files(root):
    """Yield regular files recursively without following directory symlinks."""
    def raise_walk_error(error):
        raise error

    for current_dir, directories, filenames in os.walk(
        root, topdown=True, followlinks=False, onerror=raise_walk_error
    ):
        directories[:] = [
            name for name in directories
            if not os.path.islink(os.path.join(current_dir, name))
        ]
        for filename in filenames:
            path = os.path.join(current_dir, filename)
            if not os.path.islink(path) and os.path.isfile(path):
                yield path


def _safe_relative_path(path):
    if not isinstance(path, str) or "\\" in path or path.startswith("/"):
        return None
    parts = path.split("/") if path else []
    if any(part in ("", ".", "..") for part in parts):
        return None
    return parts


def _resolve_output_directory(directory_index, relative_path):
    if directory_index < 0 or directory_index >= len(CONFIG["outputs_dir"]):
        return None
    parts = _safe_relative_path(relative_path)
    if parts is None:
        return None

    root = os.path.realpath(CONFIG["outputs_dir"][directory_index]["path"])
    path = root
    for part in parts:
        path = os.path.join(path, part)
        if os.path.islink(path):
            return None
    resolved_path = os.path.realpath(path)
    if not _is_within(root, resolved_path) or not os.path.isdir(path):
        return None
    return root, path, parts


def _list_output_directory(directory_index, relative_path, page, per_page):
    resolved = _resolve_output_directory(directory_index, relative_path)
    if not resolved:
        return None
    root, current_path, current_parts = resolved
    configured_roots = [
        os.path.realpath(output["path"])
        for output in CONFIG["outputs_dir"]
    ]
    entries = []
    with os.scandir(current_path) as directory_entries:
        for entry in directory_entries:
            if entry.is_symlink():
                continue
            child_parts = current_parts + [entry.name]
            child_path = "/".join(child_parts)
            if entry.is_dir(follow_symlinks=False):
                child_root = os.path.realpath(entry.path)
                if any(
                    index != directory_index
                    and os.path.normcase(child_root)
                    == os.path.normcase(configured_root)
                    for index, configured_root in enumerate(configured_roots)
                ):
                    continue
                entries.append({
                    "kind": "directory",
                    "name": entry.name,
                    "path": child_path,
                })
            elif entry.is_file(follow_symlinks=False):
                entries.append({
                    "kind": "file",
                    "id": f"{directory_index}:{child_path}",
                    "name": entry.name,
                    "path": child_path,
                })

    entries.sort(
        key=lambda item: (
            item["kind"] != "directory",
            item["name"].casefold(),
        )
    )
    total = len(entries)
    total_pages = max(1, math.ceil(total / per_page))
    page = min(page, total_pages)
    start = (page - 1) * per_page
    breadcrumbs = [{
        "name": CONFIG["outputs_dir"][directory_index]["nickname"],
        "path": "",
    }]
    accumulated_path = []
    for part in current_parts:
        accumulated_path.append(part)
        breadcrumbs.append({
            "name": part,
            "path": "/".join(accumulated_path),
        })
    return {
        "directory": CONFIG["outputs_dir"][directory_index]["nickname"],
        "directory_index": directory_index,
        "path": "/".join(current_parts),
        "breadcrumbs": breadcrumbs,
        "entries": entries[start:start + per_page],
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
    }


def _resolve_output_file(file_id):
    try:
        index_text, relative_path = file_id.split(":", 1)
        if not index_text.isdecimal():
            return None
        directory_index = int(index_text)
    except (AttributeError, ValueError):
        return None
    resolved = _resolve_output_directory(
        directory_index,
        "/".join((_safe_relative_path(relative_path) or [])[:-1]),
    )
    parts = _safe_relative_path(relative_path)
    if not resolved or not parts:
        return None
    root, parent_path, _ = resolved
    path = os.path.join(parent_path, parts[-1])
    real_path = os.path.realpath(path)
    if (
        os.path.islink(path)
        or not _is_within(root, real_path)
        or not os.path.isfile(path)
    ):
        return None
    return directory_index, path, relative_path


def _archive_directory_names():
    names = []
    used_names = set()
    for index, output in enumerate(CONFIG["outputs_dir"], start=1):
        name = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]',
            "_",
            output["nickname"],
        ).strip(" .") or f"output-{index}"
        base_name = name
        suffix = 2
        while name in used_names:
            name = f"{base_name}-{suffix}"
            suffix += 1
        used_names.add(name)
        names.append(name)
    return names


def _all_output_files():
    roots = []
    for output in CONFIG["outputs_dir"]:
        root = os.path.realpath(output["path"])
        if not os.path.isdir(root):
            raise OSError(
                "Configured output directory does not exist: "
                f"{output['nickname']}"
            )
        roots.append(root)

    for index, root in enumerate(roots):
        for path in _walk_files(root):
            resolved_path = os.path.realpath(path)
            owners = [
                candidate_index
                for candidate_index, candidate_root in enumerate(roots)
                if _is_within(candidate_root, resolved_path)
            ]
            if max(owners, key=lambda owner: len(roots[owner])) != index:
                continue
            yield (
                index,
                path,
                os.path.relpath(path, root).replace("\\", "/"),
            )


class ServerLogsResource:
    """Delete configured, date-stamped server log files."""

    async def on_delete(
        self, _req: falcon.asgi.Request, resp: falcon.asgi.Response
    ) -> None:
        try:
            deleted_count = clear_server_logs(CONFIG)
        except OSError as exc:
            resp.status = falcon.HTTP_500
            resp.media = {
                "status": "error",
                "error": f"Unable to delete server logs: {exc}",
            }
            return

        resp.status = falcon.HTTP_200
        resp.media = {"status": "ok", "deleted_count": deleted_count}


class DefaultOutputResource:
    """Delete regular files directly inside the output named ``Default``."""

    async def on_delete(
        self, _req: falcon.asgi.Request, resp: falcon.asgi.Response
    ) -> None:
        output = _configured_default_output()
        if not output:
            resp.status = falcon.HTTP_404
            resp.media = {
                "status": "error",
                "error": "No output directory named Default is configured",
            }
            return

        if any(
            job.get("output_dir") == "Default"
            and job.get("status") in ("pending", "processing")
            for job in JOBS.values()
        ):
            resp.status = falcon.HTTP_409
            resp.media = {
                "status": "error",
                "error": (
                    "Wait for Default directory conversions to finish "
                    "before deleting outputs"
                ),
            }
            return

        root = os.path.realpath(output["path"])
        if not os.path.isdir(root):
            resp.status = falcon.HTTP_404
            resp.media = {
                "status": "error",
                "error": "Default output directory does not exist",
            }
            return

        deleted_paths = set()
        try:
            for path in _direct_files(root):
                os.remove(path)
                deleted_paths.add(os.path.normcase(path))
        except OSError as exc:
            resp.status = falcon.HTTP_500
            resp.media = {
                "status": "error",
                "error": (
                    "Unable to delete Default output files after removing "
                    f"{len(deleted_paths)}: {exc}"
                ),
            }
            return

        resp.status = falcon.HTTP_200
        resp.media = {"status": "ok", "deleted_count": len(deleted_paths)}


class OutputsFilesResource:
    """Browse output folders and paginated file entries."""

    async def on_get(
        self, req: falcon.asgi.Request, resp: falcon.asgi.Response
    ) -> None:
        # Parse pagination parameters safely, falling back to defaults when missing, empty, or invalid
        directory_value = req.get_param("directory")
        # Raw query strings (may be None or empty string)
        page_raw = req.get_param("page")
        per_page_raw = req.get_param("per_page")
        # Convert with defaults; treat None or empty as missing
        try:
            page = int(page_raw) if page_raw not in (None, "") else 1
        except (ValueError, TypeError):
            page = 1
        try:
            per_page = int(per_page_raw) if per_page_raw not in (None, "") else 50
        except (ValueError, TypeError):
            per_page = 50
        relative_path = req.get_param("path") or ""
        # Ensure positive pagination values
        if page < 1 or per_page < 1:
            resp.status = falcon.HTTP_400
            resp.media = {"status": "error", "error": "Page and page size must be positive integers"}
            return

        if page < 1 or per_page < 1 or per_page > 100:
            resp.status = falcon.HTTP_400
            resp.media = {
                "status": "error",
                "error": (
                    "Page must be positive and page size must be between "
                    "1 and 100"
                ),
            }
            return
        directory_index = self._resolve_directory_index(directory_value)
        if directory_index is None:
            resp.status = falcon.HTTP_400
            resp.media = {
                "status": "error",
                "error": "Unknown output directory",
            }
            return

        try:
            listing = _list_output_directory(
                directory_index, relative_path, page, per_page
            )
        except OSError as exc:
            resp.status = falcon.HTTP_500
            resp.media = {
                "status": "error",
                "error": f"Unable to list output directory: {exc}",
            }
            return
        if listing is None:
            resp.status = falcon.HTTP_404
            resp.media = {
                "status": "error",
                "error": "Output directory not found",
            }
            return
        resp.status = falcon.HTTP_200
        resp.media = {
            "directories": [
                {"index": index, "nickname": output["nickname"]}
                for index, output in enumerate(CONFIG["outputs_dir"])
            ],
            **listing,
        }

    @staticmethod
    def _resolve_directory_index(value):
        if value is None or value == "":
            return 0 if CONFIG["outputs_dir"] else None
        for index, output in enumerate(CONFIG["outputs_dir"]):
            if output["nickname"] == value:
                return index
        if value.isdecimal():
            directory_index = int(value)
            if directory_index < len(CONFIG["outputs_dir"]):
                return directory_index
        return None


class OutputsArchiveResource:
    """Download all or selected output files as a ZIP archive."""

    async def on_get(
        self, _req: falcon.asgi.Request, resp: falcon.asgi.Response
    ) -> None:
        try:
            files = _all_output_files()
        except OSError as exc:
            resp.status = falcon.HTTP_500
            resp.media = {
                "status": "error",
                "error": f"Unable to list output files: {exc}",
            }
            return
        await self._send_archive(files, resp)

    async def on_post(
        self, req: falcon.asgi.Request, resp: falcon.asgi.Response
    ) -> None:
        payload = await req.get_media()
        selected_ids = (
            payload.get("files") if isinstance(payload, dict) else None
        )
        if (
            not isinstance(selected_ids, list)
            or not selected_ids
            or any(not isinstance(file_id, str) for file_id in selected_ids)
        ):
            resp.status = falcon.HTTP_400
            resp.media = {
                "status": "error",
                "error": "Select at least one output file to download",
            }
            return

        files = []
        for file_id in dict.fromkeys(selected_ids):
            output_file = _resolve_output_file(file_id)
            if not output_file:
                resp.status = falcon.HTTP_400
                resp.media = {
                    "status": "error",
                    "error": (
                        "One or more selected output files are no longer "
                        "available"
                    ),
                }
                return
            files.append(output_file)
        await self._send_archive(files, resp)

    async def _send_archive(
        self,
        files,
        resp: falcon.asgi.Response,
    ) -> None:
        archive_path = None
        try:
            archive_roots = _archive_directory_names()
            descriptor, archive_path = tempfile.mkstemp(
                prefix=f"outputs_{uuid.uuid4().hex}_",
                suffix=".zip",
                dir=CONFIG["temp_dir"],
            )
            os.close(descriptor)
            with zipfile.ZipFile(
                archive_path, mode="w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                for output_index, path, relative_path in files:
                    archive_name = os.path.join(
                        archive_roots[output_index],
                        relative_path.replace("/", os.sep),
                    ).replace("\\", "/")
                    archive.write(path, archive_name)
            archive_file = open(archive_path, "rb")
        except (OSError, ValueError, zipfile.LargeZipFile) as exc:
            cleanup_error = None
            if archive_path and os.path.exists(archive_path):
                try:
                    os.remove(archive_path)
                except OSError as cleanup_exc:
                    cleanup_error = cleanup_exc
            resp.status = falcon.HTTP_500
            error = f"Unable to create outputs archive: {exc}"
            if cleanup_error:
                error += (
                    "; unable to remove temporary archive: "
                    f"{cleanup_error}"
                )
            resp.media = {
                "status": "error",
                "error": error,
            }
            return

        resp.status = falcon.HTTP_200
        resp.content_type = "application/zip"
        resp.append_header(
            "Content-Disposition",
            'attachment; filename="notoons_outputs.zip"',
        )

        async def file_streamer():
            try:
                while True:
                    chunk = archive_file.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                archive_file.close()
                try:
                    os.remove(archive_path)
                except FileNotFoundError:
                    pass

        resp.stream = file_streamer()


class OutputPdfResource:
    """Convert one selected CBZ output to a temporary PDF for download."""

    async def on_post(
        self, req: falcon.asgi.Request, resp: falcon.asgi.Response
    ) -> None:
        payload = await req.get_media()
        file_id = payload.get("file") if isinstance(payload, dict) else None
        if not isinstance(file_id, str):
            resp.status = falcon.HTTP_400
            resp.media = {
                "status": "error",
                "error": "Select one CBZ file to download",
            }
            return

        output_file = _resolve_output_file(file_id)
        if not output_file:
            resp.status = falcon.HTTP_400
            resp.media = {
                "status": "error",
                "error": "The selected output file is no longer available",
            }
            return
        _, cbz_path, relative_path = output_file
        if os.path.splitext(cbz_path)[1].casefold() != ".cbz":
            resp.status = falcon.HTTP_400
            resp.media = {
                "status": "error",
                "error": "PDF downloads are only supported for CBZ files",
            }
            return

        pdf_path = None
        try:
            descriptor, pdf_path = tempfile.mkstemp(
                prefix=f"output_{uuid.uuid4().hex}_",
                suffix=".pdf",
                dir=CONFIG["temp_dir"],
            )
            os.close(descriptor)
            await asyncio.to_thread(_convert_cbz_to_pdf, cbz_path, pdf_path)
            pdf_file = open(pdf_path, "rb")
        except InvalidCbzError as exc:
            if pdf_path and os.path.exists(pdf_path):
                os.remove(pdf_path)
            resp.status = falcon.HTTP_400
            resp.media = {"status": "error", "error": str(exc)}
            return
        except PdfTooLargeError as exc:
            if pdf_path and os.path.exists(pdf_path):
                os.remove(pdf_path)
            resp.status = falcon.HTTP_413
            resp.media = {"status": "error", "error": str(exc)}
            return
        except (OSError, RuntimeError, ValueError) as exc:
            if pdf_path and os.path.exists(pdf_path):
                os.remove(pdf_path)
            resp.status = falcon.HTTP_500
            resp.media = {
                "status": "error",
                "error": f"Unable to convert selected CBZ to PDF: {exc}",
            }
            return

        base_name = os.path.splitext(os.path.basename(relative_path))[0]
        download_name = re.sub(r"[^A-Za-z0-9._-]+", "_", base_name).strip("._")
        download_name = f"{download_name or 'comic'}.pdf"
        resp.status = falcon.HTTP_200
        resp.content_type = "application/pdf"
        resp.append_header(
            "Content-Disposition",
            f'attachment; filename="{download_name}"',
        )

        async def file_streamer():
            try:
                while True:
                    chunk = pdf_file.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
            finally:
                pdf_file.close()
                try:
                    os.remove(pdf_path)
                except FileNotFoundError:
                    pass

        resp.stream = file_streamer()

"""Convert PDF files to CBZ archives with rendering, embedded-image extraction, and slide detection."""

import argparse
import io
import os
import sys
import time
import traceback
import zipfile
import pymupdf
from PIL import Image, ImageChops, ImageStat


def find_content_bands(brightness_1d, threshold=238, min_size=50):
    """Return the start/end indexes of contiguous content bands below a brightness threshold."""
    in_content = False
    bands = []
    start = 0
    for j, v in enumerate(brightness_1d):
        if v <= threshold and not in_content:
            in_content = True
            start = j
        elif v > threshold and in_content:
            if j - start >= min_size:
                bands.append((start, j))
            in_content = False
    if in_content and len(brightness_1d) - start >= min_size:
        bands.append((start, len(brightness_1d)))
    return bands


def detect_slide_grid(pil_img, threshold=238, min_row_size=50, min_col_size=80):
    """Detect slide regions arranged in a grid and return crop boxes plus grid dimensions."""
    gray = pil_img.convert("L")
    w, h = gray.size

    # Fast 1D projection via Pillow C resize
    row_proj = list(gray.resize((1, h), Image.Resampling.BOX).get_flattened_data())
    col_proj = list(gray.resize((w, 1), Image.Resampling.BOX).get_flattened_data())

    row_bands = find_content_bands(row_proj, threshold=threshold, min_size=min_row_size)
    col_bands = find_content_bands(col_proj, threshold=threshold, min_size=min_col_size)

    if row_bands and col_bands:
        boxes = []
        for (y1, y2) in row_bands:
            for (x1, x2) in col_bands:
                boxes.append((x1, y1, x2, y2))
        return boxes, len(row_bands), len(col_bands)

    return [], 0, 0


def images_match(img_bytes_a, img_bytes_b, resize=(300, 300), threshold=0.90):
    """Return whether two images are visually similar using a normalized difference score."""
    try:
        a = Image.open(io.BytesIO(img_bytes_a)).convert("RGB").resize(resize, Image.Resampling.BILINEAR)
        b = Image.open(io.BytesIO(img_bytes_b)).convert("RGB").resize(resize, Image.Resampling.BILINEAR)
        diff = ImageChops.difference(a, b)
        stat = ImageStat.Stat(diff)
        diff_mean = sum(stat.mean) / 3.0
        similarity = 1.0 - (diff_mean / 255.0)
        return similarity >= threshold, similarity
    except (OSError, ValueError, TypeError):
        return False, 0.0


def extract_or_render_page(page, doc, dpi=200, jpeg_quality=92, prefer_embedded=True):
    """Prefer a matching embedded page image, otherwise render the page as JPEG."""
    if prefer_embedded:
        try:
            images = page.get_images(full=True)
            if images:
                page_rect = page.rect
                page_w_pt, page_h_pt = page_rect.width, page_rect.height

                candidates = []
                for img in images:
                    xref = img[0]
                    try:
                        w, h = img[2], img[3]
                    except (IndexError, TypeError, KeyError):
                        d = doc.extract_image(xref)
                        w, h = d.get("width", 0), d.get("height", 0)

                    if w > 400 and h > 400:
                        aspect_page = page_w_pt / max(1.0, page_h_pt)
                        aspect_img = w / max(1.0, h)
                        if abs(aspect_page - aspect_img) < 0.15:
                            candidates.append((xref, w, h, w * h))

                if candidates:
                    best = max(candidates, key=lambda c: c[3])
                    xref = best[0]
                    img_dict = doc.extract_image(xref)
                    raw_bytes = img_dict["image"]
                    ext = img_dict.get("ext", "jpg")

                    thumb_pix = page.get_pixmap(dpi=72, alpha=False)
                    thumb_bytes = thumb_pix.tobytes("png")

                    matches, _ = images_match(raw_bytes, thumb_bytes, threshold=0.90)
                    if matches:
                        return raw_bytes, ext, "extracted_raw"
        except (OSError, ValueError, TypeError, KeyError, IndexError, RuntimeError):
            pass

    # Direct high-speed PyMuPDF render (alpha=False ensures RGB compatibility)
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    jpg_data = pix.tobytes("jpg", jpg_quality=jpeg_quality)
    return jpg_data, "jpg", "rendered"


def convert_pdf(
    pdf_source,
    mode="auto",
    dpi=200,
    jpeg_quality=92,
    prefer_embedded=True,
    output_cbz_path=None,
    logger=None,
    progress_callback=None,
):
    """Convert a PDF source into a CBZ archive and return summary metadata."""
    logs = []

    def log(msg):
        timestamp = time.strftime("%H:%M:%S")
        entry = f"[{timestamp}] {msg}"
        logs.append(entry)
        if logger:
            try:
                logger(entry)
            except (OSError, ValueError, TypeError, RuntimeError):
                pass
        print(entry, flush=True)

    start_time = time.perf_counter()

    # Open PDF
    try:
        if isinstance(pdf_source, (bytes, bytearray)):
            log(f"[INFO] Opening in-memory PDF ({len(pdf_source) / 1024:.1f} KB)")
            doc = pymupdf.open(stream=pdf_source, filetype="pdf")
        else:
            size_kb = os.path.getsize(pdf_source) / 1024
            log(f"[INFO] Opening PDF from file: '{pdf_source}' ({size_kb:.1f} KB)")
            doc = pymupdf.open(pdf_source)
    except (OSError, ValueError, RuntimeError) as e:
        log(f"[ERROR] Failed to open PDF document: {e}")
        log(traceback.format_exc())
        raise

    if getattr(doc, "is_encrypted", False):
        log("[WARN] Document is encrypted, attempting blank password authentication...")
        if not doc.authenticate(""):
            log("[ERROR] PDF is password-protected and cannot be opened.")
            raise ValueError("PDF is password protected")

    total_pages = len(doc)
    log(f"[INFO] PDF loaded successfully: {total_pages} page(s). Mode='{mode}', DPI={dpi}, Quality={jpeg_quality}")

    if total_pages == 0:
        log("[ERROR] PDF contains 0 pages.")
        raise ValueError("PDF contains no pages")

    slide_records = []  # list of (bytes, ext, label)

    for page_idx in range(total_pages):
        page_num = page_idx + 1
        page = doc[page_idx]
        rect = page.rect
        log(f"[PAGE {page_num}/{total_pages}] Dimensions: {int(rect.width)}x{int(rect.height)} pt")

        # Mode "pages": keep full page without grid checking
        if mode == "pages":
            data, ext, src = extract_or_render_page(
                page, doc, dpi=dpi, jpeg_quality=jpeg_quality, prefer_embedded=prefer_embedded
            )
            slide_records.append((data, ext, f"Page {page_num} ({src})"))
            log(f"[PAGE {page_num}/{total_pages}] Extracted as full page ({src}, {len(data)/1024:.1f} KB)")
            if progress_callback:
                try:
                    progress_callback(page_num, total_pages, len(slide_records))
                except (OSError, TypeError, ValueError, RuntimeError):
                    pass
            continue

        # For "auto" or "slides": render pixmap to check for multi-slide grid
        try:
            pix = page.get_pixmap(dpi=dpi, alpha=False)
            page_img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            boxes, r_count, c_count = detect_slide_grid(page_img)
        except (OSError, ValueError, RuntimeError, TypeError) as e:
            log(f"[PAGE {page_num}/{total_pages}] Grid analysis exception: {e}. Falling back to standard render.")
            boxes = []

        if len(boxes) > 1 or (mode == "slides" and len(boxes) >= 1):
            log(f"[PAGE {page_num}/{total_pages}] Detected {len(boxes)} slides in {r_count}x{c_count} grid.")
            for b_idx, box in enumerate(boxes, start=1):
                slide = page_img.crop(box)
                buf = io.BytesIO()
                slide.save(buf, format="JPEG", quality=jpeg_quality)
                slide_bytes = buf.getvalue()
                slide_records.append(
                    (slide_bytes, "jpg", f"Page {page_num} - Slide {b_idx} ({slide.width}x{slide.height})")
                )
                log(
                    f"  -> Slide {b_idx}/{len(boxes)}: {slide.width}x{slide.height} px "
                    f"({len(slide_bytes)/1024:.1f} KB)"
                )
        else:
            # Single slide page
            if prefer_embedded:
                data, ext, src = extract_or_render_page(
                    page, doc, dpi=dpi, jpeg_quality=jpeg_quality, prefer_embedded=True
                )
                if src == "extracted_raw":
                    slide_records.append((data, ext, f"Page {page_num} (raw embedded)"))
                    log(
                        f"[PAGE {page_num}/{total_pages}] Direct-extracted full slide image "
                        f"(0 ms re-encoding, {len(data)/1024:.1f} KB)"
                    )
                    if progress_callback:
                        try:
                            progress_callback(page_num, total_pages, len(slide_records))
                        except (OSError, TypeError, ValueError, RuntimeError):
                            pass
                    continue

            # Rendered JPEG directly from PyMuPDF
            jpg_data = pix.tobytes("jpg", jpg_quality=jpeg_quality)
            slide_records.append((jpg_data, "jpg", f"Page {page_num} (rendered)"))
            log(
                f"[PAGE {page_num}/{total_pages}] Rendered single slide "
                f"({pix.width}x{pix.height} px, {len(jpg_data)/1024:.1f} KB)"
            )

        if progress_callback:
            try:
                progress_callback(page_num, total_pages, len(slide_records))
            except (OSError, TypeError, ValueError, RuntimeError):
                pass

    doc.close()

    total_slides = len(slide_records)
    digits = max(3, len(str(total_slides)))
    log(f"[PACK] Packing {total_slides} slides into CBZ archive...")

    target_stream = io.BytesIO() if output_cbz_path is None else output_cbz_path
    try:
        with zipfile.ZipFile(target_stream, "w", compression=zipfile.ZIP_STORED) as cbz:
            for idx, (data, ext, _) in enumerate(slide_records, start=1):
                entry_name = f"slide-{idx:0{digits}d}.{ext}"
                cbz.writestr(entry_name, data)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as e:
        log(f"[ERROR] Failed to assemble CBZ archive: {e}")
        log(traceback.format_exc())
        raise

    elapsed_ms = (time.perf_counter() - start_time) * 1000
    log(f"[SUCCESS] CBZ generated in {elapsed_ms:.1f} ms. Total slides: {total_slides}")

    result = {
        "status": "ok",
        "pages_processed": total_pages,
        "slides_extracted": total_slides,
        "mode_used": mode,
        "dpi": dpi,
        "elapsed_ms": round(elapsed_ms, 2),
        "logs": logs,
    }

    if output_cbz_path:
        result["cbz_path"] = output_cbz_path
        result["cbz_size_bytes"] = os.path.getsize(output_cbz_path)
    else:
        cbz_data = target_stream.getvalue()
        result["cbz_bytes"] = cbz_data
        result["cbz_size_bytes"] = len(cbz_data)

    return result


def main():
    """Command-line entry point for converting a PDF to a CBZ archive."""
    parser = argparse.ArgumentParser(description="Convert PDF to CBZ with high-speed rendering & smart grid detection.")
    parser.add_argument("pdf_path", help="Path to input PDF file")
    parser.add_argument("-o", "--output", help="Path to output CBZ file (default: <pdf_name>.cbz)")
    parser.add_argument("--mode", choices=["auto", "slides", "pages"], default="auto",
                        help="Mode: auto (detect grids), slides (force split), pages (1 page = 1 slide)")
    parser.add_argument("--dpi", type=int, default=200, help="Rendering DPI (default: 200)")
    parser.add_argument("--quality", type=int, default=92, help="JPEG quality (default: 92)")
    parser.add_argument("--no-embedded", dest="embedded", action="store_false",
                        help="Disable direct embedded image extraction")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf_path):
        print(f"Error: file not found '{args.pdf_path}'")
        sys.exit(1)

    out_cbz = args.output
    if not out_cbz:
        base = os.path.splitext(args.pdf_path)[0]
        out_cbz = base + ".cbz"

    res = convert_pdf(
        args.pdf_path,
        mode=args.mode,
        dpi=args.dpi,
        jpeg_quality=args.quality,
        prefer_embedded=args.embedded,
        output_cbz_path=out_cbz,
    )

    print(f"\nFinal Result: {out_cbz}")
    print(f"  Pages:   {res['pages_processed']}")
    print(f"  Slides:  {res['slides_extracted']}")
    print(f"  Size:    {res['cbz_size_bytes'] / 1024:.1f} KB")
    print(f"  Time:    {res['elapsed_ms']} ms")


if __name__ == "__main__":
    main()

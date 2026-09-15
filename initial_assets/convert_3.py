"""Convert PDF pages or slide grids into CBZ archives or individual image files."""

import io
import os
import sys
import zipfile

import numpy as np
from pdf2image import convert_from_path


def find_content_bands(brightness_1d, threshold=238, min_size=50):
    """Return contiguous bands where brightness stays below the chosen threshold."""
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


def detect_slide_grid(page_img, threshold=238):
    """Detect slide crop boxes on a page by scanning row and column brightness bands."""
    arr = np.array(page_img.convert("L"))

    row_brightness = arr.mean(axis=1)
    col_brightness = arr.mean(axis=0)

    row_bands = find_content_bands(row_brightness, threshold=threshold, min_size=50)
    col_bands = find_content_bands(col_brightness, threshold=threshold, min_size=80)

    boxes = []
    for (y1, y2) in row_bands:
        for (x1, x2) in col_bands:
            boxes.append((x1, y1, x2, y2))

    return boxes


def pdf_to_cbz(pdf_path, dpi=200, mode="slides"):
    """Convert a PDF to a CBZ archive, either page-by-page or by splitting slide grids."""
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    cbz_name = base_name + "_slide.cbz"

    print(f"Rendering PDF at {dpi} DPI...")
    pages = convert_from_path(pdf_path, dpi=dpi)
    total_pages = len(pages)
    print(f"Total pages: {total_pages}")

    images = []

    if mode == "slides":
        for page_num, page_img in enumerate(pages):
            boxes = detect_slide_grid(page_img)
            print(f"  Page {page_num + 1}: found {len(boxes)} slide(s)")
            for box in boxes:
                slide = page_img.crop(box)
                images.append(slide)
    else:
        images = pages

    total_images = len(images)
    digits = len(str(total_images))
    print(f"Total images to pack: {total_images}")

    with zipfile.ZipFile(cbz_name, "w", compression=zipfile.ZIP_STORED) as cbz:
        for idx, img in enumerate(images):
            img_name = f"slide-{idx:0{digits}d}.jpg"
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=92)
            cbz.writestr(img_name, buf.getvalue())
            print(f"  Packed {img_name} ({img.size[0]}x{img.size[1]})")

    print(f"\nCreato: {cbz_name}")
    return cbz_name


def pdf_to_images(pdf_path, output_dir=None, dpi=200, mode="slides", fmt="jpg"):
    """Export a PDF as individual slide or page images to a target directory."""
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]

    if output_dir is None:
        output_dir = base_name + "_slides"
    os.makedirs(output_dir, exist_ok=True)

    print(f"Rendering PDF at {dpi} DPI...")
    pages = convert_from_path(pdf_path, dpi=dpi)
    total_pages = len(pages)
    print(f"Total pages: {total_pages}")

    images = []

    if mode == "slides":
        for page_num, page_img in enumerate(pages):
            boxes = detect_slide_grid(page_img)
            print(f"  Page {page_num + 1}: found {len(boxes)} slide(s)")
            for box in boxes:
                images.append(page_img.crop(box))
    else:
        images = pages

    digits = len(str(len(images)))
    saved = []
    for idx, img in enumerate(images):
        ext = "jpg" if fmt.lower() in ("jpg", "jpeg") else "png"
        fname = os.path.join(output_dir, f"slide-{idx:0{digits}d}.{ext}")
        save_kw = {"quality": 92} if ext == "jpg" else {}
        img.save(fname, **save_kw)
        saved.append(fname)
        print(f"  Saved {fname} ({img.size[0]}x{img.size[1]})")

    print(f"\nSalvate {len(saved)} immagini in: {output_dir}/")
    return saved


def print_help():
    """Display the CLI usage instructions for the converter script."""
    print("""
Uso: python convert.py [opzioni] <pdf_path>

Opzioni:
  --mode slides     Estrae le singole slide da PDF con griglie di slide (default)
  --mode pages      Tratta ogni pagina PDF come una singola immagine
  --output cbz      Produce un file .cbz (default)
  --output images   Produce cartella di immagini singole
  --dpi N           Risoluzione di rendering (default: 200)
  --fmt jpg|png     Formato immagini (solo con --output images, default: jpg)

Esempi:
  python convert.py lezione.pdf
  python convert.py --mode slides --output images lezione.pdf
  python convert.py --mode pages --dpi 150 lezione.pdf
""")


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or "--help" in args or "-h" in args:
        print_help()
        sys.exit(0)

    # Parse options
    mode_ = "slides"
    output = "cbz"
    dpi_ = 200
    fmt_ = "jpg"
    pdf_path_ = None

    i = 0
    while i < len(args):
        if args[i] == "--mode" and i + 1 < len(args):
            mode_ = args[i + 1]
            i += 2
        elif args[i] == "--output" and i + 1 < len(args):
            output = args[i + 1]
            i += 2
        elif args[i] == "--dpi" and i + 1 < len(args):
            dpi_ = int(args[i + 1])
            i += 2
        elif args[i] == "--fmt" and i + 1 < len(args):
            fmt_ = args[i + 1]
            i += 2
        else:
            pdf_path_ = args[i]
            i += 1

    if pdf_path_ is None:
        print("Errore: specificare il percorso del PDF.")
        print_help()
        sys.exit(1)

    if not os.path.exists(pdf_path_):
        print(f"Errore: file non trovato: {pdf_path_}")
        sys.exit(1)

    if mode_ not in ("slides", "pages"):
        print(f"Errore: --mode deve essere 'slides' o 'pages', non '{mode_}'")
        sys.exit(1)

    if output == "cbz":
        pdf_to_cbz(pdf_path_, dpi=dpi_, mode=mode_)
    elif output == "images":
        pdf_to_images(pdf_path_, dpi=dpi_, mode=mode_, fmt=fmt_)
    else:
        print(f"Errore: --output deve essere 'cbz' o 'images', non '{output}'")
        sys.exit(1)

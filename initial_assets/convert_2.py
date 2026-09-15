"""Convert a PDF into a CBZ archive by rendering pages or extracting embedded images."""

import argparse
import io
import os
import zipfile

import pymupdf
from PIL import Image


def page_pixels_from_points(rect, dpi):
    """Return page dimensions in pixels for a given DPI value."""
    return int(rect.width * dpi / 72), int(rect.height * dpi / 72)


def images_similar(img_bytes_a, img_bytes_b, resize=(400, 400), threshold=0.92):
    """Check whether two images are visually similar after resizing and normalizing the MSE."""
    resample_filter = getattr(Image, "Resampling", Image).LANCZOS
    a = Image.open(io.BytesIO(img_bytes_a)).convert("RGB")
    b = Image.open(io.BytesIO(img_bytes_b)).convert("RGB")
    a = a.resize(resize, resample_filter)
    b = b.resize(resize, resample_filter)
    # calcola MSE
    arr_a = list(a.getdata())
    arr_b = list(b.getdata())
    mse = 0.0
    for p, q in zip(arr_a, arr_b):
        mse += sum((pa - qa) ** 2 for pa, qa in zip(p, q))
    mse /= (len(arr_a) * 3)
    # normalizza: max per canale 255^2 => max_pixel_error = 255^2
    max_mse = 255.0**2
    similarity = 1.0 - (mse / max_mse)
    return similarity >= threshold, similarity


def extract_best_image_or_render(page, doc, dpi_render, prefer_full_image, fallback_render):
    """Try to extract a large embedded page image and fall back to rendering when needed."""
    images = page.get_images(full=True)
    page_w_px, page_h_px = page_pixels_from_points(page.rect, dpi_render)
    # cerca immagini candidate che abbiano dimensioni (width,height) riportate in get_images
    candidates = []
    for img in images:
        xref = img[0]
        try:
            # img tuple: (xref, smask, width, height, bpc, colorspace, alt)
            w = img[2]
            h = img[3]
        except (IndexError, TypeError, KeyError):
            # estrai per ottenere dimensioni
            d = doc.extract_image(xref)
            w = d.get("width", 0)
            h = d.get("height", 0)
        # considera candidate se area significativa rispetto alla pagina
        area_ratio = (w * h) / float(max(1, page_w_px * page_h_px))
        if area_ratio >= 0.6:  # soglia: 60% dell'area pagina (aggiustabile)
            candidates.append((xref, w, h, area_ratio))

    # se prefer_full_image e abbiamo candidate, prova la migliore
    if prefer_full_image and candidates:
        # scegli la più grande per area_ratio
        best = max(candidates, key=lambda t: t[3])
        xref = best[0]
        img_dict = doc.extract_image(xref)
        img_bytes = img_dict["image"]
        # render a bassa risoluzione per confronto (veloce)
        pix = page.get_pixmap(dpi=72)  # 72 dpi per confronto rapido
        rendered_bytes = pix.tobytes("png")
        similar, _ = images_similar(img_bytes, rendered_bytes, resize=(300, 300), threshold=0.90)
        if similar:
            ext = img_dict.get("ext", "png")
            return img_bytes, ext, "extracted_full"
        else:
            if not fallback_render:
                return None, None, "skipped_no_fallback"
            # fallback: render ad alta risoluzione
    # se non prefer_full_image ma ci sono immagini e preferisci estrarre tutte:
    if not prefer_full_image and images:
        # estrai tutte le immagini della pagina e le ritorna come lista (qui ritorniamo la prima per naming semplice)
        # per il tuo caso probabilmente vuoi render comunque la pagina completa, quindi non entriamo qui
        xref = images[0][0]
        img_dict = doc.extract_image(xref)
        return img_dict["image"], img_dict.get("ext", "png"), "extracted_any"

    # fallback: render ad alta risoluzione (slide completa con testo)
    pix = page.get_pixmap(dpi=dpi_render)
    return pix.tobytes("jpg"), "jpg", "rendered"


def pdf_to_cbz(
    pdf_path, dpi=300, extract_images=False, prefer_full_image=False,
    fallback_render=True, compression=zipfile.ZIP_STORED
):
    """Create a CBZ archive from a PDF file, optionally extracting embedded images."""
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    cbz_name = base_name + ".cbz"
    doc = pymupdf.open(pdf_path)
    total_pages = len(doc)
    digits = len(str(total_pages))

    with zipfile.ZipFile(cbz_name, "w", compression=compression) as cbz:
        for page_number in range(total_pages):
            page = doc[page_number]
            page_label = f"page-{page_number:0{digits}d}"

            if extract_images:
                img_bytes, ext, _ = extract_best_image_or_render(
                    page, doc, dpi, prefer_full_image, fallback_render
                )
                if img_bytes is None:
                    # saltata
                    continue
                img_name = f"{page_label}.{ext}"
                cbz.writestr(img_name, img_bytes)
            else:
                # comportamento standard: render ad alta risoluzione per avere slide complete
                pix = page.get_pixmap(dpi=dpi)
                img_name = f"{page_label}.jpg"
                cbz.writestr(img_name, pix.tobytes("jpg"))

    doc.close()
    print(f"Creato: {cbz_name}")


def main():
    """Run the CLI entry point for converting a PDF into a CBZ archive."""
    parser = argparse.ArgumentParser(description="Converti PDF in CBZ estraendo slide complete (testo+grafica).")
    parser.add_argument("pdf_path", help="Percorso al file PDF")
    parser.add_argument(
        "--dpi", type=int, default=300,
        help="DPI per il rendering delle pagine (default 300)"
    )
    parser.add_argument(
        "-e", "--extract-images", action="store_true",
        help="Prova a estrarre immagini incorporate quando possibile"
    )
    parser.add_argument(
        "--prefer-full-image", action="store_true",
        help="Accetta solo immagini che coprono gran parte della pagina"
    )
    parser.add_argument(
        "--no-fallback", dest="fallback", action="store_false",
        help="Se usato con -e e --prefer-full-image, non eseguire il rendering "
        "se non trovi immagini full-slide"
    )
    parser.add_argument("--deflated", action="store_true", help="Usa compressione ZIP_DEFLATED")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf_path):
        print("File non trovato:", args.pdf_path)
        return

    compression = zipfile.ZIP_DEFLATED if args.deflated else zipfile.ZIP_STORED
    pdf_to_cbz(args.pdf_path, dpi=args.dpi, extract_images=args.extract_images,
               prefer_full_image=args.prefer_full_image, fallback_render=args.fallback,
               compression=compression)


if __name__ == "__main__":
    main()

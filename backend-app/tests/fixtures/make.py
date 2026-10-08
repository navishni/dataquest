"""Test-only generators for fixture files (never imported by runtime code)."""
import io
import zipfile

import pymupdf
from PIL import Image, ImageDraw, ImageFont


def text_pdf(pages=("Hello ParseFusion invoice number INV-20491 total due",), title=None) -> bytes:
    doc = pymupdf.open()
    for t in pages:
        p = doc.new_page(width=595, height=842)
        y = 100
        if title:
            p.insert_text((72, 70), title, fontsize=22)
        for line in t.split("\n"):
            p.insert_text((72, y), line, fontsize=11)
            y += 16
    return doc.tobytes()


def table_pdf(rows, col_w=110, row_h=24, x0=72, y0=150) -> bytes:
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=842)
    p.insert_text((72, 100), "Statement of account", fontsize=18)
    for r, row in enumerate(rows):
        for c, val in enumerate(row):
            rect = pymupdf.Rect(x0 + c * col_w, y0 + r * row_h, x0 + (c + 1) * col_w, y0 + (r + 1) * row_h)
            p.draw_rect(rect, color=(0, 0, 0), width=0.8)
            p.insert_text((rect.x0 + 4, rect.y0 + 16), str(val), fontsize=10)
    return doc.tobytes()


def scanned_pdf(text="INVOICE 4821 TOTAL 1250", size=900) -> bytes:
    im = Image.new("RGB", (size, 300), "white")
    d = ImageDraw.Draw(im)
    d.text((30, 100), text, fill="black", font=ImageFont.load_default(size=48))
    doc = pymupdf.open()
    p = doc.new_page(width=595, height=300)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    p.insert_image(p.rect, stream=buf.getvalue())
    return doc.tobytes()


def png(w=64, h=64, color="white") -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (w, h), color).save(b, format="PNG")
    return b.getvalue()


def docx_like(extra: dict | None = None) -> bytes:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", "<w:document/>")
        for k, v in (extra or {}).items():
            z.writestr(k, v)
    return b.getvalue()

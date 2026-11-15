"""Render the list as an A4 image and send it to the printer over IPP (AirPrint)."""
import io
import os
import struct

import requests
from PIL import Image, ImageDraw, ImageFont

W, H = 1654, 2339          # A4 at 200 dpi
MARGIN = 80
COLS = 3
FONT_DIRS = ["/usr/share/fonts/dejavu", "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/TTF"]


def _font(bold, size):
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    for d in FONT_DIRS:
        p = os.path.join(d, name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def _layout(entries, size):
    """Column-major flow. Returns list of (col, y, kind, text) or None if it doesn't fit."""
    line_h = int(size * 1.45)
    head_h = int(size * 1.9)
    top = MARGIN + int(size * 2.6)
    bottom = H - MARGIN
    col, y, out = 0, top, []
    for i, (kind, text) in enumerate(entries):
        h = head_h if kind == "h" else line_h
        need = h + (line_h if kind == "h" else 0)   # keep a heading with its first item
        if y + need > bottom:
            col, y = col + 1, top
            if col >= COLS:
                return None
        if kind == "h" and y != top:
            y += int(size * 0.4)
        out.append((col, y, kind, text))
        y += h
    return out


def render(sections, title):
    """sections: [(section_name, [line text, ...])]. Returns JPEG bytes."""
    entries = []
    for name, lines in sections:
        if lines:
            entries.append(("h", name))
            entries += [("i", t) for t in lines]
    size, placed = 34, None
    while size >= 16:
        placed = _layout(entries, size)
        if placed is not None:
            break
        size -= 2
    if placed is None:
        placed = _layout(entries[: len(entries) * 2 // 3], 16) or []
    img = Image.new("L", (W, H), 255)
    d = ImageDraw.Draw(img)
    d.text((MARGIN, MARGIN), title, font=_font(True, 40), fill=0)
    col_w = (W - 2 * MARGIN) // COLS
    reg, bold = _font(False, size), _font(True, size)
    box = int(size * 0.7)
    for col, y, kind, text in placed:
        x = MARGIN + col * col_w
        if kind == "h":
            d.text((x, y), text.upper(), font=_font(True, int(size * 0.8)), fill=0)
            d.line((x, y + int(size * 1.15), x + col_w - 30, y + int(size * 1.15)), fill=0, width=2)
        else:
            d.rectangle((x, y + int(size * 0.2), x + box, y + int(size * 0.2) + box), outline=0, width=2)
            t = text
            while d.textlength(t, font=reg) > col_w - box - 50 and len(t) > 4:
                t = t[:-2] + "…"
            d.text((x + box + 14, y), t, font=reg, fill=0)
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=90, dpi=(200, 200))
    return out.getvalue()


def _attr(tag, name, value):
    n = name.encode()
    v = value.encode() if isinstance(value, str) else value
    return bytes([tag]) + struct.pack(">H", len(n)) + n + struct.pack(">H", len(v)) + v


def _ipp(uri, op, attrs, data=b""):
    body = b"\x01\x01" + struct.pack(">HI", op, 1) + b"\x01"
    body += _attr(0x47, "attributes-charset", "utf-8")
    body += _attr(0x48, "attributes-natural-language", "en")
    body += _attr(0x45, "printer-uri", uri)
    body += attrs + b"\x03" + data
    url = uri.replace("ipps://", "https://", 1).replace("ipp://", "http://", 1)
    r = requests.post(url, data=body, headers={"Content-Type": "application/ipp"}, timeout=60, verify=False)
    r.raise_for_status()
    status = struct.unpack(">H", r.content[2:4])[0]
    return status, r.content


def send(uri, jpeg):
    attrs = (_attr(0x42, "requesting-user-name", "weekly-shop") + _attr(0x42, "job-name", "Shopping list")
             + _attr(0x49, "document-format", "image/jpeg")
             + b"\x02" + _attr(0x44, "media", "iso_a4_210x297mm") + _attr(0x44, "print-scaling", "fit"))
    status, _ = _ipp(uri, 0x0002, attrs, jpeg)
    if status >= 0x0100:
        raise RuntimeError(f"Printer refused the job (IPP status 0x{status:04x})")


def check(uri):
    """Get-Printer-Attributes: returns readable printer info for the test page."""
    status, raw = _ipp(uri, 0x000B, _attr(0x44, "requested-attributes", "printer-make-and-model")
                       + _attr(0x44, "", "document-format-supported") + _attr(0x44, "", "printer-state"))
    text = raw[8:].decode("latin-1")
    readable = "".join(ch if 32 <= ord(ch) < 127 else " " for ch in text)
    return f"IPP status 0x{status:04x}: " + " ".join(readable.split())

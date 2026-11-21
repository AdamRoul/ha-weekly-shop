"""Receipt reading: the one step that uses AI."""
import base64
import io
import json
import os
import re
import uuid

import requests
from PIL import Image, ImageOps

import ha

MEDIA_DIR = os.environ.get("WS_MEDIA", "/media/weekly_shop")

PROMPT = """You are reading photos of ONE UK Sainsbury's till receipt (SmartShop). The photos are in order from top to bottom and usually overlap: the bottom lines of one photo are often repeated at the top of the next.

Return ONLY a JSON object, no other text:
{{"date": "YYYY-MM-DD",
 "item_count": <the number printed just before BALANCE DUE, or null>,
 "total": <the BALANCE DUE amount as a number, or null>,
 "lines": [{{"text": "<product text exactly as printed>", "price": <number>, "weight_kg": <number or null>}}],
 "suggestions": {{"<product text>": {{"item": "<shopping-list name>", "section": "<section>"}}}}}}

Rules for "lines":
- One entry per purchased product line, in the printed (scan) order. If the same product is printed on two consecutive lines, include it twice.
- Do NOT repeat lines just because they appear in two overlapping photos - line the photos up using the surrounding lines.
- Leave out "Nectar Price Saving" and other saving/discount lines, the totals, payment, Nectar summary and footer.
- "ITEM CANCELLED" followed by a product with a negative price means an earlier line of that product was removed: leave out the cancellation AND one earlier line of that same product.
- For a weighed item ("0.820 kg @ £0.90/kg"), set weight_kg and use the price on that line.
- Drop the leading "*" marker from product names.

Rules for "suggestions": for each distinct product text, the short generic name a family would write on a paper shopping list (e.g. "JS BRTS SM SKM PINT" -> "Milk"). Use one of these existing names whenever it fits: {items}.
"section" must be one of: {sections}.
"""


def prepare_image(raw):
    """Upright, RGB JPEG no larger than 2000px."""
    try:
        img = Image.open(io.BytesIO(raw))
    except Exception as e:
        raise ValueError("Couldn't open that photo. If it's HEIC, share it as JPEG (Most Compatible).") from e
    img = ImageOps.exif_transpose(img).convert("RGB")
    img.thumbnail((2000, 2000))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=85)
    return out.getvalue()


def _parse_json(text):
    text = text.strip()
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise ValueError(f"The AI didn't return JSON: {text[:200]}")
    return json.loads(m.group(0))


def read(images, item_names, section_names, options):
    """images: list of JPEG bytes in top-to-bottom order. Returns the parsed dict."""
    prompt = PROMPT.format(items=", ".join(sorted(item_names)), sections=", ".join(section_names))
    key = (options.get("anthropic_api_key") or "").strip()
    if key:
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                 "data": base64.b64encode(b).decode()}} for b in images]
        content.append({"type": "text", "text": prompt})
        r = requests.post("https://api.anthropic.com/v1/messages", timeout=180, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            # effort "low": reading a receipt needs little reasoning, and at the default effort the model's
            # thinking used most of the token budget, cutting the JSON off part-way through long receipts
            json={"model": options.get("anthropic_model") or "claude-sonnet-5-5", "max_tokens": 16000,
                  "output_config": {"effort": "low"},
                  "messages": [{"role": "user", "content": content}]})
        if r.status_code >= 400:
            raise RuntimeError(f"Anthropic API error {r.status_code}: {r.text[:300]}")
        resp = r.json()
        if resp.get("stop_reason") == "max_tokens":
            raise RuntimeError("The AI's answer was cut off before it finished (receipt too long). Try again, or send the receipt in two halves.")
        if resp.get("stop_reason") == "refusal":
            raise RuntimeError("The AI declined to read this receipt. Try a clearer photo.")
        text = "".join(b.get("text", "") for b in resp["content"] if b.get("type") == "text")
    else:
        entity = options.get("ai_task_entity")
        if not entity:
            raise RuntimeError("No AI configured: set ai_task_entity or anthropic_api_key in the add-on options.")
        batch = uuid.uuid4().hex[:8]
        os.makedirs(MEDIA_DIR, exist_ok=True)
        media_ids = []
        for i, b in enumerate(images):
            name = f"receipt_{batch}_{i + 1}.jpg"
            with open(os.path.join(MEDIA_DIR, name), "wb") as f:
                f.write(b)
            media_ids.append(f"media-source://media_source/local/weekly_shop/{name}")
        text = ha.ai_task(entity, prompt, media_ids)
        if isinstance(text, dict):
            return text
    return _parse_json(text)

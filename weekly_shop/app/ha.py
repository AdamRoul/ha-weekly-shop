"""Talking to Home Assistant through the Supervisor proxy."""
import os

import requests

import json

OPTIONS_PATH = os.environ.get("WS_OPTIONS", "/data/options.json")


def supervisor_token():
    for key in ("SUPERVISOR_TOKEN", "HASSIO_TOKEN"):
        if os.environ.get(key):
            return os.environ[key]
    for path in ("/run/s6/container_environment/SUPERVISOR_TOKEN", "/var/run/s6/container_environment/SUPERVISOR_TOKEN"):
        if os.path.exists(path):
            return open(path).read().strip()
    return ""


def endpoint():
    """(base_url, token, description). A long-lived token in the options overrides the Supervisor proxy."""
    try:
        opts = json.load(open(OPTIONS_PATH))
    except Exception:
        opts = {}
    if (opts.get("ha_token") or "").strip():
        return "http://homeassistant:8123/api", opts["ha_token"].strip(), "long-lived token"
    return os.environ.get("WS_HA_URL", "http://supervisor/core/api"), supervisor_token(), "Supervisor token"


def _post(path, payload, timeout=30):
    base, token, how = endpoint()
    r = requests.post(f"{base}{path}", json=payload, timeout=timeout,
                      headers={"Authorization": f"Bearer {token}"})
    if r.status_code >= 400:
        hint = " (no token found)" if not token else ""
        raise RuntimeError(f"Home Assistant {path} returned {r.status_code} using {how}{hint}: {r.text[:200]}")
    return r.json() if r.content else {}


def status():
    base, token, how = endpoint()
    try:
        r = requests.get(f"{base}/", headers={"Authorization": f"Bearer {token}"}, timeout=10)
        return f"{how}: {'present' if token else 'MISSING'}; GET {base}/ -> {r.status_code} {r.text[:100]}"
    except Exception as e:
        return f"{how}: {'present' if token else 'MISSING'}; error {e}"


def service(domain, name, data, response=False, timeout=30):
    path = f"/services/{domain}/{name}" + ("?return_response" if response else "")
    out = _post(path, data, timeout)
    return out.get("service_response", out) if response else out


def todo_items(entity):
    """Open items on the kitchen to-do list: [{'uid', 'summary'}]."""
    resp = service("todo", "get_items", {"entity_id": entity, "status": "needs_action"}, response=True)
    items = resp.get(entity, {}).get("items", [])
    return [{"uid": i.get("uid"), "summary": i.get("summary", "")} for i in items]


def todo_remove(entity, uids):
    if uids:
        service("todo", "remove_item", {"entity_id": entity, "item": list(uids)})


def ai_task(entity, instructions, media_ids):
    resp = service("ai_task", "generate_data", {
        "entity_id": entity,
        "task_name": "Read Sainsbury's receipt",
        "instructions": instructions,
        "attachments": [{"media_content_id": m, "media_content_type": "image/jpeg"} for m in media_ids],
    }, response=True, timeout=180)
    return resp.get("data", "")

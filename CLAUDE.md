# Weekly Shop – notes for Claude

A Home Assistant add-on that learns the family's weekly Sainsbury's shop from receipt photos and prints a shopping list in walk-round-the-shop order. Used mainly from iPads through the HA app (ingress), Saturday-morning shopping, list built Friday/Saturday.

## Design principles (agreed with Adam)
- **AI only for reading receipts.** Everything else (matching, frequency, walk order, printing) is plain code.
- **Generic list names, learned mappings.** Receipt text (`JS BRTS SM SKM PINT`) → product → item ("Milk"). New products are confirmed once on the review screen; after that matching is automatic.
- **Walk order from scan order.** SmartShop receipts list items in scan order. Item position = recency-weighted *median* of its normalised receipt positions (robust to the odd out-of-order scan; decays so store re-layouts take over in 2–3 shops). The original paper list gives a weak seed position.
- **Frequency from purchase gaps.** Weighted median interval; suggest at ≥80% of interval, stop suggesting at 3× overdue; one-off purchases only suggested the following week; a "no" on a suggested item pushes it back half an interval. Constants are at the top of `learn.py`.
- Sections are shown on screen and printed; section order = median position of its items.

## Layout
```
repository.yaml            HA add-on repository manifest
weekly_shop/config.yaml    add-on manifest — BUMP `version` ON EVERY CHANGE or HA won't offer the update
weekly_shop/Dockerfile     Alpine HA base image; apk-only deps (python3, flask, pillow, requests, dejavu fonts)
weekly_shop/app/
  main.py      Flask app + routes (all URLs relative, single-level — required for ingress)
  db.py        SQLite schema, matching helpers, seed; DB lives in /share/weekly_shop/weekly_shop.db
  learn.py     the learning (walk order + due logic)
  receipt.py   AI receipt reading: HA ai_task.generate_data (images saved to /media/weekly_shop) or Anthropic API
  ha.py        HA REST via Supervisor proxy (SUPERVISOR_TOKEN) or optional long-lived ha_token
  printing.py  renders A4 JPEG (Pillow, 3 columns, auto font size) and sends via raw IPP Print-Job
  templates/   week (tick tiles, find-or-add box, in-page preview), receipt, review, items, products, message
```

## Behaviour worth knowing
- Week page: pre-ticks due items + anything on the HA to-do list; user overrides stored in `overrides`. Print → records `decisions`, clears overrides, removes all open items from the to-do list.
- Unmatched to-do text prints under "Also"; linking it creates an alias.
- Receipt review warns if the line count differs from the receipt's printed item count (e.g. "80 BALANCE DUE").
- Previews must open in-page (the HA app webview has no ingress session in a new window). `/last.jpg` is the last printed list.
- `/status` page checks the HA connection and the to-do list.

## Testing locally
```
WS_DB=./ws.db WS_OPTIONS=./options.json WS_HA_URL=http://127.0.0.1:9000/api python3 weekly_shop/app/main.py
```
Point `WS_HA_URL` at a small mock that answers `/services/todo/get_items?return_response` and `/services/todo/remove_item`.

## Adam's setup
- HA OS 2026.11 at 192.168.0.9 (Samba shares available: config, share, addons, media). Installed from this GitHub repo.
- Options: `todo_entity: todo.sainsburys_list`, `ai_task_entity: ai_task.google_ai_task` (Gemini), printer Canon TS5350i `ipp://192.168.0.169:631/ipp/print` (Supervisor token initially gave a 401; `ha_token` is the fallback).
- Deploy = commit, bump version, push; Adam clicks Update in HA. Local clone: `C:\Users\adam2\Documents\ha-weekly-shop`.
- Adam prefers iterative changes and coded logic over AI wherever practical.

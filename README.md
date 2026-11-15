# Weekly Shop – Home Assistant add-on

Turns photos of your Sainsbury's SmartShop receipt into a weekly shopping list that learns what you buy and prints in the order you walk round the shop.

- **Reads receipts** with AI (a Home Assistant AI Task entity, e.g. Google Gemini, or an Anthropic API key). That is the only AI step.
- **Learns, in plain code**: how often each item is bought (so it pre-ticks what's due) and where it sits in the shop (from scan order on the receipt, using a recency-weighted median so stray scans and store re-layouts are handled).
- **Picks up your kitchen list**: anything added to a Home Assistant to-do list during the week is ticked automatically, and the list is cleared after printing.
- **Prints** an A4 list in sections, in walk order, straight to an AirPrint/IPP printer.

## Install

1. Settings → Add-ons → Add-on store → ⋮ → Repositories → add `https://github.com/AdamRoul/ha-weekly-shop`.
2. Install **Weekly Shop**, set the options (below), start it and turn on *Show in sidebar*.

## Options

| Option | What it is |
|---|---|
| `shop_name` | Shown at the top of the printed list |
| `todo_entity` | The to-do list you add things to during the week, e.g. `todo.sainsburys_list` |
| `ai_task_entity` | AI Task entity used to read receipts, e.g. `ai_task.google_ai_task` |
| `anthropic_api_key` / `anthropic_model` | Optional: read receipts with Claude directly instead |
| `printer_uri` | e.g. `ipp://192.168.0.50:631/ipp/print` |
| `ha_token` | Optional long-lived access token, only if the add-on can't reach the to-do list |

Data is kept in `/share/weekly_shop/` so it survives reinstalls.

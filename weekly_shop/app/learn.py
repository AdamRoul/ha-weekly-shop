"""The learning: how often each item is bought, and where it sits in the shop.

All plain arithmetic on the stored history - no AI.

Walk order: on each receipt, every line gets a position from 0 (first scanned)
to 1 (last scanned). An item's position is the weighted MEDIAN of its positions
across shops, recent shops weighted more (POS_DECAY per shop). The median means
a single out-of-order scan doesn't move it; the decay means a store re-layout
takes over after two or three shops. The current paper list provides a weak
starting position for items that haven't appeared on a receipt.

Frequency: the gaps (in days) between shops where an item was bought give a
weighted median buying interval. An item is suggested when the time since it
was last bought reaches DUE_RATIO of that interval. Saying "no" to a suggested
item counts as "we still have some", pushing it back by half an interval. Something bought only once is suggested
the following week only; after that it waits until it has been bought again.
Something more than three intervals overdue stops being suggested.
"""
from collections import defaultdict
from datetime import date

POS_DECAY = 0.75       # weight multiplier per older shop for walk order
GAP_DECAY = 0.8        # weight multiplier per older gap for buying interval
SEED_WEIGHT = 0.3      # weight of the original paper-list position
DUE_RATIO = 0.8        # suggest when days since last buy >= 80% of usual interval
STALE_RATIO = 3.0      # ...but not once it is 3x overdue (you have probably stopped buying it)
DEFAULT_INTERVAL = 7   # until there are two purchases, assume weekly
QTY_SHOPS = 4          # recent purchases used to estimate quantity


def wmedian(pairs):
    pairs = sorted(pairs)
    total = sum(w for _, w in pairs)
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0] if pairs else None


def compute(conn, today=None):
    today = today or date.today()
    items = {r["id"]: dict(r) for r in conn.execute(
        "SELECT i.*, s.name AS section, s.fallback_order FROM items i JOIN sections s ON s.id=i.section_id")}
    shops = conn.execute("SELECT id, shop_date FROM shops ORDER BY shop_date DESC, id DESC").fetchall()

    pos_obs = defaultdict(list)        # item -> [(pos, weight)]
    buys = defaultdict(dict)           # item -> {date: count}
    for age, shop in enumerate(shops):
        lines = conn.execute(
            "SELECT l.seq, p.item_id FROM shop_lines l JOIN products p ON p.id=l.product_id "
            "WHERE l.shop_id=? ORDER BY l.seq", (shop["id"],)).fetchall()
        n = len(lines)
        seen = set()
        for idx, line in enumerate(lines):
            iid = line["item_id"]
            if iid is None:
                continue
            d = shop["shop_date"]
            buys[iid][d] = buys[iid].get(d, 0) + 1
            if iid not in seen:
                seen.add(iid)
                pos_obs[iid].append((idx / max(n - 1, 1), POS_DECAY ** age))

    declines = defaultdict(list)
    for r in conn.execute("SELECT item_id, decided FROM decisions WHERE suggested=1 AND selected=0"):
        declines[r["item_id"]].append(r["decided"])

    model = {}
    for iid, it in items.items():
        obs = list(pos_obs[iid])
        if it["seed_pos"] is not None:
            obs.append((it["seed_pos"], SEED_WEIGHT))
        pos = wmedian(obs) if obs else None

        dates = sorted(date.fromisoformat(d) for d in buys[iid])
        counts = [buys[iid][d.isoformat()] for d in dates]
        interval = DEFAULT_INTERVAL
        if len(dates) >= 2:
            gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
            interval = max(wmedian([(g, GAP_DECAY ** k) for k, g in enumerate(reversed(gaps))]), 7)
        due, ratio, last = False, None, None
        if dates:
            last = dates[-1]
            effective = last.toordinal()
            for d in declines[iid]:
                dd = date.fromisoformat(d)
                if dd > last:
                    effective = max(effective, dd.toordinal() - interval / 2)
            ratio = (today.toordinal() - effective) / interval
            due = DUE_RATIO <= ratio <= STALE_RATIO
            if len(dates) == 1:
                # Only bought once: suggest it only if that was the latest shop and it
                # hasn't been turned down since. Otherwise wait until it's bought again.
                later_shop = any(date.fromisoformat(s["shop_date"]) > last for s in shops)
                declined = any(date.fromisoformat(d) > last for d in declines[iid])
                due = due and not later_shop and not declined
        recent = counts[-QTY_SHOPS:]
        qty = sorted(recent)[len(recent) // 2] if recent else it["default_qty"]
        model[iid] = {
            "pos": pos, "interval": interval, "last": last, "ratio": ratio, "due": due,
            "times_bought": len(dates), "qty": max(qty, it["default_qty"] if not recent else 1),
        }

    # Section order: median position of the section's items.
    sec_items = defaultdict(list)
    for iid, it in items.items():
        if model[iid]["pos"] is not None:
            sec_items[it["section_id"]].append(model[iid]["pos"])
    sec_pos = {}
    for iid, it in items.items():
        sid = it["section_id"]
        if sid not in sec_pos:
            vals = sorted(sec_items[sid])
            sec_pos[sid] = vals[len(vals) // 2] if vals else 2 + it["fallback_order"]
    for iid, it in items.items():
        if model[iid]["pos"] is None:
            model[iid]["pos"] = sec_pos[it["section_id"]] + 0.0001
    return model, sec_pos


def describe(m, today=None):
    """Short human hint, e.g. 'usually weekly · last 6 days ago'."""
    today = today or date.today()
    if not m["times_bought"]:
        return "not bought yet"
    iv = m["interval"]
    if m["times_bought"] < 2:
        freq = "bought once"
    elif iv <= 9:
        freq = "usually weekly"
    else:
        freq = f"about every {round(iv / 7)} wks"
    ago = (today - m["last"]).days
    return f"{freq} · last {ago} day{'s' if ago != 1 else ''} ago"

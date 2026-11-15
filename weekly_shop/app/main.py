"""Weekly Shop - Home Assistant add-on web app (served through ingress)."""
import json
import os
import re
from datetime import date, timedelta

from flask import Flask, Response, jsonify, redirect, render_template, request

import db
import ha
import learn
import printing
import receipt

OPTIONS_PATH = os.environ.get("WS_OPTIONS", "/data/options.json")
LAST_LIST = os.path.join(os.path.dirname(db.DB_PATH) or ".", "last_list.jpg")
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024


def options():
    try:
        with open(OPTIONS_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def conn():
    return db.connect()


def sections(c):
    return [r["name"] for r in c.execute("SELECT name FROM sections ORDER BY fallback_order")]


QTY_RE = re.compile(r"^\s*(?:(\d+)\s*[x×*]\s*)?(.*?)(?:\s*[x×*]\s*(\d+))?\s*$", re.I)


def parse_todo(text):
    m = QTY_RE.match(text or "")
    qty = int(m.group(1) or m.group(3) or 0) or None
    return (m.group(2) or text).strip(), qty


# ---------------------------------------------------------------- weekly list
def build_week(c):
    opts = options()
    model, sec_pos = learn.compute(c)
    overrides = {r["item_id"]: r for r in c.execute("SELECT * FROM overrides")}
    todo_error, todo_raw, unmatched, todo_hits = None, [], [], {}
    try:
        todo_raw = ha.todo_items(opts.get("todo_entity", "todo.sainsburys_list"))
    except Exception as e:  # HA unreachable shouldn't stop the page
        todo_error = str(e)
    for t in todo_raw:
        name, qty = parse_todo(t["summary"])
        iid = db.find_item(c, name)
        if iid:
            todo_hits[iid] = qty
        else:
            unmatched.append({"text": t["summary"], "name": name, "qty": qty})

    rows = []
    for it in c.execute("SELECT i.*, s.name AS section FROM items i JOIN sections s ON s.id=i.section_id "
                        "WHERE i.active=1"):
        m = model[it["id"]]
        suggested = bool(m["due"] or it["id"] in todo_hits)
        ov = overrides.get(it["id"])
        selected = bool(ov["selected"]) if ov and ov["selected"] is not None else suggested
        qty = (ov["qty"] if ov and ov["qty"] else None) or todo_hits.get(it["id"]) or m["qty"]
        rows.append({
            "id": it["id"], "name": it["name"], "section": it["section"], "section_id": it["section_id"],
            "pos": m["pos"], "sec_pos": sec_pos[it["section_id"]], "suggested": suggested,
            "selected": selected, "qty": qty, "from_todo": it["id"] in todo_hits,
            "hint": ("on the kitchen list · " if it["id"] in todo_hits else "") + learn.describe(m),
        })
    rows.sort(key=lambda r: (r["sec_pos"], r["section"], r["pos"], r["name"]))
    grouped = []
    for r in rows:
        if not grouped or grouped[-1][0] != r["section"]:
            grouped.append((r["section"], []))
        grouped[-1][1].append(r)
    return {"groups": grouped, "unmatched": unmatched, "todo_error": todo_error, "todo_raw": todo_raw}


def shop_title():
    d = date.today()
    sat = d + timedelta(days=(5 - d.weekday()) % 7)
    return f"{options().get('shop_name') or 'Shopping'}  –  {sat.strftime('%A %-d %B')}"


def print_sections(week):
    out = []
    for name, rows in week["groups"]:
        lines = [r["name"] + (f"  ×{r['qty']}" if r["qty"] and r["qty"] > 1 else "") for r in rows if r["selected"]]
        out.append((name, lines))
    if week["unmatched"]:
        out.append(("Also", [u["text"] for u in week["unmatched"]]))
    return out


@app.route("/")
def index():
    c = conn()
    week = build_week(c)
    n_sel = sum(r["selected"] for _, rows in week["groups"] for r in rows) + len(week["unmatched"])
    pending = c.execute("SELECT id, created FROM pending ORDER BY id").fetchall()
    last_shop = c.execute("SELECT MAX(shop_date) FROM shops").fetchone()[0]
    return render_template("week.html", week=week, n_sel=n_sel, sections=sections(c), pending=pending,
                           last_shop=last_shop, printer=bool(options().get("printer_uri")),
                           items=[r["name"] for r in c.execute("SELECT name FROM items WHERE active=1")])


@app.post("/toggle")
def toggle():
    data = request.get_json()
    c = conn()
    c.execute("INSERT INTO overrides(item_id, selected, qty) VALUES (?,?,?) "
              "ON CONFLICT(item_id) DO UPDATE SET selected=excluded.selected, qty=excluded.qty",
              (int(data["id"]), 1 if data["selected"] else 0, int(data["qty"]) if data.get("qty") else None))
    c.commit()
    return jsonify(ok=True)


@app.post("/add")
def add():
    c = conn()
    name = request.form.get("name", "").strip()
    if name:
        nm, qty = parse_todo(name)
        iid = db.get_or_create_item(c, nm, request.form.get("section") or "Other")
        todo_text = request.form.get("todo_text")
        if todo_text:   # remember how the kitchen list spells it
            c.execute("INSERT OR REPLACE INTO aliases(text, item_id) VALUES (?,?)",
                      (db.norm(parse_todo(todo_text)[0]), iid))
        else:
            c.execute("INSERT OR REPLACE INTO overrides(item_id, selected, qty) VALUES (?,?,?)", (iid, 1, qty))
        c.commit()
    return redirect("./")


@app.get("/list.jpg")
def list_image():
    c = conn()
    return Response(printing.render(print_sections(build_week(c)), shop_title()), mimetype="image/jpeg")


def finalise(c, week):
    today = date.today().isoformat()
    n = 0
    for _, rows in week["groups"]:
        for r in rows:
            if r["suggested"] or r["selected"]:
                c.execute("INSERT INTO decisions(decided, item_id, suggested, selected, qty) VALUES (?,?,?,?,?)",
                          (today, r["id"], int(r["suggested"]), int(r["selected"]), r["qty"]))
            n += r["selected"]
    c.execute("DELETE FROM overrides")
    c.execute("INSERT INTO prints(item_count) VALUES (?)", (n + len(week["unmatched"]),))
    c.commit()
    try:
        ha.todo_remove(options().get("todo_entity", "todo.sainsburys_list"),
                       [t["uid"] or t["summary"] for t in week["todo_raw"]])
    except Exception as e:
        return f"List saved, but the kitchen list couldn't be cleared: {e}"
    return None


@app.post("/print")
def do_print():
    c = conn()
    week = build_week(c)
    jpeg = printing.render(print_sections(week), shop_title())
    with open(LAST_LIST, "wb") as f:
        f.write(jpeg)
    uri = options().get("printer_uri")
    try:
        printing.send(uri, jpeg)
    except Exception as e:
        return render_template("message.html", title="Printing didn't work",
                               message=f"{e}. You can open the list image and print it from there, "
                                       "then tap 'I've printed it' to clear the week.", show_image=True)
    warn = finalise(c, week)
    return render_template("message.html", title="Sent to the printer",
                           message=warn or "The kitchen list has been cleared. Happy shopping!", show_image=True,
                           done=True)


@app.get("/last.jpg")
def last_image():
    with open(LAST_LIST, "rb") as f:
        return Response(f.read(), mimetype="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/status")
def status_page():
    lines = [ha.status()]
    try:
        n = len(ha.todo_items(options().get("todo_entity", "todo.sainsburys_list")))
        lines.append(f"Kitchen list OK: {n} open item(s)")
    except Exception as e:
        lines.append(f"Kitchen list error: {e}")
    return render_template("message.html", title="Connection check", message=" | ".join(lines))


@app.post("/finish")
def finish():
    c = conn()
    warn = finalise(c, build_week(c))
    return render_template("message.html", title="Week cleared",
                           message=warn or "Choices saved and the kitchen list has been cleared.", done=True)


@app.get("/printer")
def printer_test():
    uri = options().get("printer_uri")
    try:
        info = printing.check(uri)
    except Exception as e:
        info = f"Couldn't reach {uri}: {e}"
    return render_template("message.html", title="Printer check", message=info)


# ---------------------------------------------------------------- receipts
@app.get("/receipt")
def receipt_form():
    return render_template("receipt.html")


@app.post("/receipt")
def receipt_upload():
    files = [f for f in request.files.getlist("photos") if f and f.filename]
    if not files:
        return redirect("receipt")
    c = conn()
    try:
        images = [receipt.prepare_image(f.read()) for f in files]
        items = [r["name"] for r in c.execute("SELECT name FROM items")]
        data = receipt.read(images, items, sections(c), options())
    except Exception as e:
        return render_template("message.html", title="Couldn't read the receipt", message=str(e))
    for line in data.get("lines", []):
        line["text"] = db.clean_receipt_text(line.get("text"))
    data["lines"] = [l for l in data.get("lines", []) if l["text"]]
    pid = c.execute("INSERT INTO pending(data) VALUES (?)", (json.dumps(data),)).lastrowid
    c.commit()
    return redirect(f"review?id={pid}")


@app.route("/review", methods=["GET", "POST"])
def review():
    c = conn()
    pid = int(request.values["id"])
    row = c.execute("SELECT data FROM pending WHERE id=?", (pid,)).fetchone()
    if not row:
        return redirect("./")
    data = json.loads(row["data"])
    known = {r["receipt_text"]: r["name"] for r in c.execute(
        "SELECT p.receipt_text, i.name FROM products p JOIN items i ON i.id=p.item_id")}
    texts = []
    for l in data["lines"]:
        if l["text"] not in known and l["text"] not in texts:
            texts.append(l["text"])
    if request.method == "POST":
        if request.form.get("action") == "discard":
            c.execute("DELETE FROM pending WHERE id=?", (pid,))
            c.commit()
            return redirect("./")
        for i, t in enumerate(texts):
            name = request.form.get(f"item_{i}", "").strip() or t.title()
            iid = db.get_or_create_item(c, name, request.form.get(f"section_{i}") or "Other")
            db.get_or_create_product(c, t, iid)
        db.save_shop(c, request.form.get("shop_date") or date.today().isoformat(), data["lines"],
                     data.get("item_count"), data.get("total"))
        c.execute("DELETE FROM pending WHERE id=?", (pid,))
        c.commit()
        return render_template("message.html", title="Receipt saved",
                               message=f"{len(data['lines'])} lines recorded, {len(texts)} new products learned.",
                               done=True)
    sugg = data.get("suggestions", {})
    unknown = [{"text": t, "item": (sugg.get(t) or {}).get("item", ""),
                "section": (sugg.get(t) or {}).get("section", "Other")} for t in texts]
    count = data.get("item_count")
    mismatch = count is not None and int(count) != len(data["lines"])
    return render_template("review.html", pid=pid, data=data, known=known, unknown=unknown, mismatch=mismatch,
                           sections=sections(c), items=[r["name"] for r in c.execute("SELECT name FROM items")])


# ---------------------------------------------------------------- editing
@app.route("/items", methods=["GET", "POST"])
def items_page():
    c = conn()
    if request.method == "POST":
        for key in request.form:
            if not key.startswith("name_"):
                continue
            iid = int(key[5:])
            name = request.form[key].strip()
            other = db.find_item(c, name)
            if other and other != iid:   # renamed onto an existing item: merge
                c.execute("UPDATE products SET item_id=? WHERE item_id=?", (other, iid))
                c.execute("UPDATE aliases SET item_id=? WHERE item_id=?", (other, iid))
                c.execute("UPDATE decisions SET item_id=? WHERE item_id=?", (other, iid))
                c.execute("DELETE FROM overrides WHERE item_id=?", (iid,))
                c.execute("UPDATE items SET active=0, name=name||' (merged '||id||')' WHERE id=?", (iid,))
                continue
            c.execute("UPDATE items SET name=?, section_id=?, default_qty=?, active=? WHERE id=?",
                      (name, db.section_id(c, request.form.get(f"section_{iid}")),
                       int(request.form.get(f"qty_{iid}") or 1), 1 if request.form.get(f"active_{iid}") else 0, iid))
        c.commit()
        return redirect("items")
    rows = c.execute("SELECT i.*, s.name AS section, (SELECT COUNT(*) FROM products p WHERE p.item_id=i.id) AS n_prod "
                     "FROM items i JOIN sections s ON s.id=i.section_id WHERE i.name NOT LIKE '%(merged %' "
                     "ORDER BY s.fallback_order, i.name COLLATE NOCASE").fetchall()
    return render_template("items.html", rows=rows, sections=sections(c))


@app.route("/products", methods=["GET", "POST"])
def products_page():
    c = conn()
    if request.method == "POST":
        for key, val in request.form.items():
            if key.startswith("prod_") and val.strip():
                iid = db.get_or_create_item(c, val.strip(), "Other")
                c.execute("UPDATE products SET item_id=? WHERE id=?", (iid, int(key[5:])))
        c.commit()
        return redirect("products")
    rows = c.execute("SELECT p.id, p.receipt_text, i.name AS item FROM products p LEFT JOIN items i ON i.id=p.item_id "
                     "ORDER BY i.name COLLATE NOCASE, p.receipt_text").fetchall()
    return render_template("products.html", rows=rows,
                           items=[r["name"] for r in c.execute("SELECT name FROM items WHERE active=1")])


if __name__ == "__main__":
    db.init()
    print("Weekly Shop starting;", ha.status(), flush=True)
    app.run(host="0.0.0.0", port=int(os.environ.get("WS_PORT", 8099)), threaded=True)

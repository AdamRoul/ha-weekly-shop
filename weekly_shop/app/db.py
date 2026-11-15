"""SQLite storage for Weekly Shop."""
import json
import os
import re
import sqlite3

import shutil

SHARE_DIR = "/share/weekly_shop"
OLD_DB = "/data/weekly_shop.db"


def _resolve_path():
    """Keep the database in /share so it survives reinstalling the add-on (or moving it to a
    repository install). Copies an older /data database across the first time."""
    if os.environ.get("WS_DB"):
        return os.environ["WS_DB"]
    if os.path.isdir("/share"):
        os.makedirs(SHARE_DIR, exist_ok=True)
        path = os.path.join(SHARE_DIR, "weekly_shop.db")
        if not os.path.exists(path) and os.path.exists(OLD_DB):
            shutil.copy2(OLD_DB, path)
        return path
    return OLD_DB


DB_PATH = _resolve_path()
SEED_PATH = os.path.join(os.path.dirname(__file__), "seed.json")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, fallback_order REAL NOT NULL);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL COLLATE NOCASE,
    section_id INTEGER NOT NULL REFERENCES sections(id),
    default_qty INTEGER NOT NULL DEFAULT 1, seed_pos REAL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY, receipt_text TEXT UNIQUE NOT NULL, item_id INTEGER REFERENCES items(id));
CREATE TABLE IF NOT EXISTS aliases (
    text TEXT PRIMARY KEY, item_id INTEGER NOT NULL REFERENCES items(id));
CREATE TABLE IF NOT EXISTS shops (
    id INTEGER PRIMARY KEY, shop_date TEXT NOT NULL, item_count INTEGER, total REAL,
    created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS shop_lines (
    id INTEGER PRIMARY KEY, shop_id INTEGER NOT NULL REFERENCES shops(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL, product_id INTEGER NOT NULL REFERENCES products(id),
    price REAL, weight_kg REAL);
CREATE TABLE IF NOT EXISTS pending (
    id INTEGER PRIMARY KEY, data TEXT NOT NULL, created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS overrides (
    item_id INTEGER PRIMARY KEY REFERENCES items(id), selected INTEGER, qty INTEGER);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY, decided TEXT NOT NULL, item_id INTEGER NOT NULL REFERENCES items(id),
    suggested INTEGER NOT NULL, selected INTEGER NOT NULL, qty INTEGER);
CREATE TABLE IF NOT EXISTS prints (
    id INTEGER PRIMARY KEY, printed TEXT DEFAULT CURRENT_TIMESTAMP, item_count INTEGER);
"""


def connect():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init():
    conn = connect()
    conn.executescript(SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM sections").fetchone()[0] == 0:
        seed(conn)
    conn.commit()
    conn.close()


def clean_receipt_text(text):
    text = (text or "").strip().lstrip("*").strip().upper()
    return re.sub(r"\s+", " ", text)


def norm(text):
    """Loose key for matching typed names: lower case, no punctuation, singular-ish."""
    t = re.sub(r"[^a-z0-9% ]", "", (text or "").lower())
    words = t.split()
    if words and len(words[-1]) > 3:
        w = words[-1]
        if w.endswith("ies"):
            w = w[:-3] + "y"
        elif w.endswith("oes"):
            w = w[:-2]
        elif w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        words[-1] = w
    return " ".join(words)


def section_id(conn, name):
    row = conn.execute("SELECT id FROM sections WHERE name=?", (name,)).fetchone()
    if row:
        return row["id"]
    row = conn.execute("SELECT id FROM sections WHERE name='Other'").fetchone()
    return row["id"]


def find_item(conn, name):
    key = norm(name)
    if not key:
        return None
    row = conn.execute("SELECT item_id FROM aliases WHERE text=?", (key,)).fetchone()
    if row:
        return row["item_id"]
    for r in conn.execute("SELECT id, name FROM items"):
        if norm(r["name"]) == key:
            return r["id"]
    return None


def get_or_create_item(conn, name, section_name, qty=1, seed_pos=None):
    name = name.strip()
    item_id = find_item(conn, name)
    if item_id:
        conn.execute("UPDATE items SET active=1 WHERE id=?", (item_id,))
        return item_id
    cur = conn.execute(
        "INSERT INTO items(name, section_id, default_qty, seed_pos) VALUES (?,?,?,?)",
        (name, section_id(conn, section_name), qty, seed_pos))
    return cur.lastrowid


def get_or_create_product(conn, text, item_id=None):
    text = clean_receipt_text(text)
    row = conn.execute("SELECT id, item_id FROM products WHERE receipt_text=?", (text,)).fetchone()
    if row:
        if item_id and not row["item_id"]:
            conn.execute("UPDATE products SET item_id=? WHERE id=?", (item_id, row["id"]))
        return row["id"]
    return conn.execute("INSERT INTO products(receipt_text, item_id) VALUES (?,?)",
                        (text, item_id)).lastrowid


def save_shop(conn, shop_date, lines, item_count=None, total=None):
    """lines: list of dicts with text, price, weight_kg (product mapping must already exist)."""
    shop_id = conn.execute("INSERT INTO shops(shop_date, item_count, total) VALUES (?,?,?)",
                           (shop_date, item_count, total)).lastrowid
    for seq, line in enumerate(lines):
        pid = get_or_create_product(conn, line["text"])
        conn.execute("INSERT INTO shop_lines(shop_id, seq, product_id, price, weight_kg) VALUES (?,?,?,?,?)",
                     (shop_id, seq, pid, line.get("price"), line.get("weight_kg")))
    return shop_id


def seed(conn):
    with open(SEED_PATH) as f:
        data = json.load(f)
    data.setdefault("list_items", [])
    data.setdefault("extra_items", [])
    data.setdefault("shops", [])
    n_sec = len(data["sections"])
    for i, name in enumerate(data["sections"]):
        conn.execute("INSERT INTO sections(name, fallback_order) VALUES (?,?)", (name, i / max(n_sec - 1, 1)))
    items = data["list_items"]
    for i, it in enumerate(items):
        get_or_create_item(conn, it["name"], it["section"], it.get("qty", 1), seed_pos=i / max(len(items) - 1, 1))
    for it in data["extra_items"]:
        get_or_create_item(conn, it["name"], it["section"])
    for shop in data["shops"]:
        for line in shop["lines"]:
            get_or_create_product(conn, line["text"], find_item(conn, line["item"]))
        save_shop(conn, shop["date"], shop["lines"], shop.get("item_count"), shop.get("total"))

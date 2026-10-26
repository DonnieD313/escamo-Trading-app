#!/usr/bin/env python3
"""Hoodie Babies Trade - real multi-user trading app.

Standard library only: http.server + sqlite3. No paid services, no accounts
beyond a local username/password (passwords are hashed with PBKDF2, never
stored in plain text).

Run:  python3 app.py            (listens on http://127.0.0.1:8090)
Env:  PORT, HOST (default 127.0.0.1; set to 0.0.0.0 only behind a
      reverse proxy with HTTPS), ESCAMO_DB (database path, default
      ./app.db), COOKIE_SECURE (set to 1 when running behind HTTPS so
      cookies get the Secure flag), SESSION_MAX_AGE_DAYS (default 7),
      PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET, PAYPAL_MODE (default
      "sandbox"), PUBLIC_BASE_URL (for PayPal/Stripe return URLs),
      STRIPE_SECRET_KEY, STRIPE_MODE (default "test").
"""
import base64
import datetime
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import html
import json
import os
import random
import re
import secrets
import sqlite3
import string
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.environ.get("ESCAMO_DB", os.path.join(BASE, "app.db"))
PORT = int(os.environ.get("PORT", "8090"))
HOST = os.environ.get("HOST", "127.0.0.1")

# Realistic character photos live in img/ next to app.py. They are
# served at /img/<file>.jpg; the SVG portrait engine stays as the
# fallback for every baby without a photo.
IMG_DIR = os.path.join(BASE, "img")
REALISTIC_IMAGES = {
    "Harbor": "harbor.jpg", "Lagoon": "lagoon.jpg",
    "Reef": "reef.jpg", "Drift": "drift.jpg",
    "Birch": "birch.jpg", "Cedar": "cedar.jpg",
    "Alder": "alder.jpg", "Aspen": "aspen.jpg",
    "Sol": "sol.jpg", "Nova": "nova.jpg",
    "Zephyr": "zephyr.jpg", "Flare": "flare.jpg",
    "Damiana June": "damiana-june.jpg",
    "Aaliyah Bee": "aaliyah-bee.jpg", "Jazmyn": "jazmyn.jpg",
    "Muzle Back": "muzle-back.jpg", "Baraka": "baraka.jpg",
    "Sirus V": "sirus-v.jpg",
    # Starter-roster characters: the weekly pop-up is always a roster
    # character, so photographing the whole roster makes every future
    # week's pop-up card realistic too.
    "Marina": "marina.jpg", "Coral": "coral.jpg",
    "Pearl": "pearl.jpg", "Tide": "tide.jpg", "Kai": "kai.jpg",
    "Willow": "willow.jpg", "Fern": "fern.jpg",
    "Hazel": "hazel.jpg", "Rowan": "rowan.jpg",
    "Ivy": "ivy.jpg", "Maple": "maple.jpg",
    "Juniper": "juniper.jpg", "Sage": "sage.jpg",
    "Ember": "ember.jpg", "Storm": "storm.jpg",
    "Skye": "skye.jpg", "Ash": "ash.jpg", "Blaze": "blaze.jpg",
    "Aurora": "aurora.jpg", "Cinder": "cinder.jpg",
    "Tempest": "tempest.jpg",
}
# Set COOKIE_SECURE=1 when the app runs behind HTTPS so session and
# CSRF cookies are only sent over secure connections. It stays off by
# default so plain localhost testing keeps working.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "") == "1"
SESSION_MAX_AGE_DAYS = int(os.environ.get("SESSION_MAX_AGE_DAYS", "7"))

# ---------------------------------------------------------------- rate limiting
# In-memory, per-process: repeated failed logins/signups from one IP are
# blocked for a while so passwords can't be brute-forced. A single
# process serves this app, so one dict is enough; a multi-process
# deployment would need shared storage instead.
RATE_LIMIT_MAX_FAILS = 5
RATE_LIMIT_WINDOW_SECS = 600  # failures counted over the last 10 minutes
_rate_fails = {}  # key -> list of failure timestamps


def rate_blocked(key):
    now = time.time()
    hits = [t for t in _rate_fails.get(key, [])
            if now - t < RATE_LIMIT_WINDOW_SECS]
    _rate_fails[key] = hits
    return len(hits) >= RATE_LIMIT_MAX_FAILS


def rate_record_fail(key):
    _rate_fails.setdefault(key, []).append(time.time())


def rate_clear(key):
    _rate_fails.pop(key, None)

# ---------------------------------------------------------------- starter data
# Baby names for Blues are sea/ocean names (per Doneil's spec). The Blues
# are exactly five characters, not including their leader Damiana June.
# Browns/Reds starter names below are placeholders he can replace.
STARTERS = {
    "Blues": {
        "names": ["Marina", "Coral", "Pearl", "Tide", "Kai"],
        "patterns": ["Guiding Star", "Three Rivers", "Whirlpool Bloom",
                     "Sunrise Over the Bay", "Deep Diamond", "Wave Crest",
                     "Tidal Swirl", "Moonlit Surf"],
        "stones": "yellow + green rhinestones",
        "face": "fish-like",
        "prefix": "B",
    },
    "Browns": {
        "names": ["Willow", "Fern", "Hazel", "Rowan", "Ivy", "Maple",
                  "Juniper", "Sage"],
        "patterns": ["Forest Canopy", "Moss Ring", "Acorn Trail",
                     "Fern Frond", "Bark Weave", "Meadow Light"],
        "stones": "amber + gold rhinestones",
        "face": "animal-like",
        "prefix": "BR",
    },
    "Reds": {
        "names": ["Ember", "Storm", "Skye", "Ash", "Blaze", "Aurora",
                  "Cinder", "Tempest"],
        "patterns": ["Storm Wing", "Sky Fire", "Cloud Break", "Ember Trail",
                     "Thunder Crest", "Sunset Veil"],
        "stones": "red stones",
        "face": "angel-like",
        "prefix": "R",
    },
}

# Blacks ARE the Miners: no ruler, scavengers, underground-raised. One pops
# up for sale each week. Black names below are placeholders until Doneil
# names them.
BLACKS_ROSTER = [
    {"name": "Onyx", "pattern": "Underground Veil"},
    {"name": "Shadow", "pattern": "Cave Spark"},
    {"name": "Coal", "pattern": "Deep Tunnel"},
    {"name": "Midnight", "pattern": "Night Dig"},
    {"name": "Slate", "pattern": "Stone Path"},
    {"name": "Umbra", "pattern": "Hidden Pocket"},
]
BLACK_STONES = "black stones on all-white hoodie"
BLACK_FACE = "angry creature-like"

# Display names for the tribes (Doneil's renames). Internal keys stay
# Blues/Browns/Reds/Blacks everywhere in the database and code — only
# what users see changes. Colors, faces, and lore stay the same.
TRIBE_DISPLAY = {
    "Blues": "Waterliewaveminans",
    "Browns": "Fortress Rooted",
    "Reds": "Windyworrors",
    "Blacks": "Shadows",
    "Gods": "Gods",
}


def tribe_display(tribe):
    return TRIBE_DISPLAY.get(tribe, tribe)

# Display-only prices. Real-money purchases stay pending a payment
# provider choice — nothing here charges anyone.
PRICE_LEADER = "10.00"
PRICE_INDIVIDUAL = "1.99"
PRICE_GOD = "40.00"
GOD_COUNT = 3
BLACK_TRADE_MIN_POINTS = 1200

# Leaders: the three unique founders. Each can be bought once; once sold,
# that leader is unavailable to other buyers. A bought leader joins the
# buyer's collection and trades like any other baby.
LEADERS = [
    {"cert": "EHB-L001", "name": "Damiana June", "tribe": "Blues",
     "pattern": "Guiding Star", "stones": "yellow + green rhinestones",
     "face": "fish-like, surprised crying look"},
    {"cert": "EHB-L002", "name": "Aaliyah Bee", "tribe": "Browns",
     "pattern": "Forest Canopy",
     "stones": "amber + gold rhinestones, red stone in forehead",
     "face": "animal-like, grouchy"},
    {"cert": "EHB-L003", "name": "Jazmyn", "tribe": "Reds",
     "pattern": "All-black hoodie, diamond head, wand with fire-red tip",
     "stones": "stones of every different color on all-black hoodie",
     "face": "angel-like, slight curious smile"},
]

# Gods: exactly 3, named by Doneil (spellings exactly as he wrote them).
GODS = [
    {"cert": "EHB-G001", "name": "Muzle Back"},
    {"cert": "EHB-G002", "name": "Baraka"},
    {"cert": "EHB-G003", "name": "Sirus V"},
]

# Points packs: (points, price). Bought points add to balance only and
# never count toward the character-creator unlock (trade-earned only).
POINTS_PACKS = [
    (300, "4.99"), (600, "9.99"), (1200, "20.00"),
]
# New signups start with 600 points (balance only — starting points
# never count toward the character-creator unlock, same as bought
# points). Existing accounts are untouched.
STARTING_POINTS = 600
# Invite bonus: +20 points to the inviter (balance AND earned) when an
# invited friend completes signup.
INVITE_BONUS = 20
# Villages: a village is complete at 40 babies owned, not counting
# leaders. Drifters, specials, rares, Shadows, and gods all count.
VILLAGE_SIZE = 40
# Pop-up tiers: the weekly drifter plus limited-edition specials and
# rares, and the weekly Shadow pop-up (a money purchase in the Shop).
PRICE_SPECIAL = "4.99"
SPECIAL_EDITION = 25
PRICE_RARE = "9.99"
RARE_EDITION = 8
PRICE_SHADOW = "10.00"
POINTS_PACK_PRICES = {n: p for n, p in POINTS_PACKS}

# ---------------------------------------------------------------- PayPal
# PayPal checkout. Credentials come ONLY from environment variables —
# never hardcode or store them in the repo. Default mode is sandbox, so
# no real money moves until PAYPAL_MODE=live AND live credentials are set
# AND the app is publicly hosted over HTTPS.
PAYPAL_MODE = os.environ.get("PAYPAL_MODE", "sandbox").lower()
PAYPAL_CLIENT_ID = os.environ.get("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.environ.get("PAYPAL_CLIENT_SECRET", "")
PAYPAL_BASE = ("https://api-m.paypal.com" if PAYPAL_MODE == "live"
               else "https://api-m.sandbox.paypal.com")
# PAYPAL_MOCK=1 is a TEST-ONLY switch: it replaces PayPal HTTP calls with
# canned responses so tests never touch the real PayPal API. Never set it
# for a real deployment.
PAYPAL_MOCK = os.environ.get("PAYPAL_MOCK", "") == "1"
PUBLIC_BASE_URL = (os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
                   or "http://127.0.0.1:%d" % PORT)
_mock_order_counter = [0]


class PayPalError(Exception):
    pass


def paypal_configured():
    return PAYPAL_MOCK or bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET)


def _paypal_http(method, path, token=None, data=None, basic=None):
    """One PayPal REST call (stdlib urllib only). Returns parsed JSON."""
    headers = {"Accept": "application/json"}
    body = None
    if data is not None:
        if isinstance(data, dict):
            body = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        else:
            body = data.encode() if isinstance(data, str) else data
            headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = "Bearer " + token
    if basic:
        headers["Authorization"] = ("Basic " + base64.b64encode(
            basic.encode()).decode())
    req = urllib.request.Request(PAYPAL_BASE + path, data=body,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise PayPalError("PayPal %s %s failed (%s): %s"
                          % (method, path, e.code, e.read().decode()))
    except urllib.error.URLError as e:
        raise PayPalError("PayPal %s %s unreachable: %s"
                          % (method, path, e.reason))


def paypal_access_token():
    """OAuth2 client-credentials token."""
    if PAYPAL_MOCK:
        return "MOCK-TOKEN"
    resp = _paypal_http("POST", "/v1/oauth2/token",
                        data="grant_type=client_credentials",
                        basic="%s:%s" % (PAYPAL_CLIENT_ID,
                                         PAYPAL_CLIENT_SECRET))
    token = resp.get("access_token")
    if not token:
        raise PayPalError("PayPal token response had no access_token")
    return token


def paypal_create_order(amount):
    """Create a CAPTURE order. Returns (order_id, approve_url)."""
    if PAYPAL_MOCK:
        _mock_order_counter[0] += 1
        oid = "MOCK-ORDER-%d" % _mock_order_counter[0]
        return oid, PUBLIC_BASE_URL + "/buy_return?token=" + oid
    token = paypal_access_token()
    resp = _paypal_http("POST", "/v2/checkout/orders", token=token, data={
        "intent": "CAPTURE",
        "purchase_units": [{"amount": {"currency_code": "USD",
                                       "value": amount}}],
        "application_context": {
            "return_url": PUBLIC_BASE_URL + "/buy_return",
            "cancel_url": PUBLIC_BASE_URL + "/buy_cancel",
        },
    })
    oid = resp.get("id")
    approve = None
    for link in resp.get("links", []):
        if link.get("rel") == "approve":
            approve = link.get("href")
    if not oid or not approve:
        raise PayPalError("PayPal order response missing id/approve link")
    return oid, approve


def paypal_capture_order(order_id):
    """Capture an approved order. Returns the PayPal order status."""
    if PAYPAL_MOCK:
        if os.environ.get("PAYPAL_MOCK_CAPTURE") == "fail":
            return "FAILED"
        return "COMPLETED"
    token = paypal_access_token()
    resp = _paypal_http("POST",
                        "/v2/checkout/orders/%s/capture" % order_id,
                        token=token, data={})
    return resp.get("status", "")

# ---------------------------------------------------------------- Stripe
# Stripe Checkout with Cash App Pay. The secret key comes ONLY from the
# STRIPE_SECRET_KEY environment variable — never hardcode or store it in
# the repo. Default mode is test, so no real money moves until
# STRIPE_MODE=live AND a live key is set AND the app is publicly hosted
# over HTTPS.
STRIPE_MODE = os.environ.get("STRIPE_MODE", "test").lower()
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_BASE = "https://api.stripe.com"
# STRIPE_MOCK=1 is a TEST-ONLY switch: it replaces Stripe HTTP calls
# with canned responses so tests never touch the real Stripe API. Never
# set it for a real deployment.
STRIPE_MOCK = os.environ.get("STRIPE_MOCK", "") == "1"
_mock_stripe_counter = [0]


class StripeError(Exception):
    pass


def stripe_configured():
    return STRIPE_MOCK or bool(STRIPE_SECRET_KEY)


def _stripe_http(method, path, data=None):
    """One Stripe REST call (stdlib urllib only, form-encoded).
    Returns parsed JSON."""
    headers = {"Authorization": "Bearer " + STRIPE_SECRET_KEY}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    req = urllib.request.Request(STRIPE_BASE + path, data=body,
                                 headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raise StripeError("Stripe %s %s failed (%s): %s"
                          % (method, path, e.code, e.read().decode()))
    except urllib.error.URLError as e:
        raise StripeError("Stripe %s %s unreachable: %s"
                          % (method, path, e.reason))


def stripe_create_session(amount_cents, name):
    """Create a Checkout Session paid with Cash App Pay.
    Returns (session_id, checkout_url)."""
    if STRIPE_MOCK:
        _mock_stripe_counter[0] += 1
        sid = "MOCK-STRIPE-%d" % _mock_stripe_counter[0]
        return sid, PUBLIC_BASE_URL + "/stripe_return?session_id=" + sid
    resp = _stripe_http("POST", "/v1/checkout/sessions", data={
        "mode": "payment",
        "payment_method_types[0]": "cashapp",
        "line_items[0][price_data][currency]": "usd",
        "line_items[0][price_data][unit_amount]": str(amount_cents),
        "line_items[0][price_data][product_data][name]": name,
        "line_items[0][quantity]": "1",
        "success_url": (PUBLIC_BASE_URL + "/stripe_return"
                        "?session_id={CHECKOUT_SESSION_ID}"),
        "cancel_url": (PUBLIC_BASE_URL + "/stripe_cancel"
                       "?session_id={CHECKOUT_SESSION_ID}"),
    })
    sid = resp.get("id")
    url = resp.get("url")
    if not sid or not url:
        raise StripeError("Stripe session response missing id/url")
    return sid, url


def stripe_session_payment_status(session_id):
    """Retrieve a session and return its payment_status ('paid' on
    success). The session is verified server-side — the redirect landing
    alone never grants anything."""
    if STRIPE_MOCK:
        if os.environ.get("STRIPE_MOCK_PAYMENT") == "unpaid":
            return "unpaid"
        return "paid"
    resp = _stripe_http("GET",
                        "/v1/checkout/sessions/%s" % session_id)
    return resp.get("payment_status", "")

# Shop stock: individual characters for sale at $1.99. Names are distinct
# from the starter pools so a shop baby never duplicates a starter name.
SHOP_INDIVIDUALS = [
    ("Harbor", "Blues"), ("Lagoon", "Blues"), ("Reef", "Blues"),
    ("Drift", "Blues"),
    ("Birch", "Browns"), ("Cedar", "Browns"), ("Alder", "Browns"),
    ("Aspen", "Browns"),
    ("Sol", "Reds"), ("Nova", "Reds"), ("Zephyr", "Reds"),
    ("Flare", "Reds"),
]

# ---------------------------------------------------------------- database
def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_db():
    con = db()
    con.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        points INTEGER NOT NULL DEFAULT 600,
        points_earned INTEGER NOT NULL DEFAULT 0,
        creator_slots_used INTEGER NOT NULL DEFAULT 0,
        invite_code TEXT UNIQUE NOT NULL,
        invited_by INTEGER REFERENCES users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS babies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cert TEXT UNIQUE NOT NULL,
        owner_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL,
        tribe TEXT NOT NULL,
        hoodie_pattern TEXT NOT NULL,
        stones TEXT NOT NULL,
        face TEXT NOT NULL,
        for_trade INTEGER NOT NULL DEFAULT 0,
        is_custom INTEGER NOT NULL DEFAULT 0,
        gender TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS monthly_gifts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        month TEXT NOT NULL,
        baby_cert TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(user_id, month)
    );
    CREATE TABLE IF NOT EXISTS choice_claims (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        baby_cert TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS popup_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cert TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        tribe TEXT NOT NULL,
        hoodie_pattern TEXT NOT NULL,
        stones TEXT NOT NULL,
        face TEXT NOT NULL,
        price TEXT NOT NULL DEFAULT '1.99',
        sold INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS offers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        from_user INTEGER NOT NULL REFERENCES users(id),
        to_user INTEGER NOT NULL REFERENCES users(id),
        offered_baby INTEGER REFERENCES babies(id),
        offered_points INTEGER NOT NULL DEFAULT 0,
        wanted_baby INTEGER NOT NULL REFERENCES babies(id),
        status TEXT NOT NULL DEFAULT 'pending',
        counter_of INTEGER REFERENCES offers(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS sessions (
        token TEXT PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS pending_signups (
        token TEXT PRIMARY KEY,
        username TEXT NOT NULL,
        password_hash TEXT NOT NULL,
        salt TEXT NOT NULL,
        tribe TEXT NOT NULL,
        invite_code TEXT,
        invited_by INTEGER REFERENCES users(id),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS notices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        text TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS purchases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        item_type TEXT NOT NULL,
        item_ref TEXT NOT NULL,
        amount TEXT NOT NULL,
        paypal_order_id TEXT,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS shop_items (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cert TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        tribe TEXT NOT NULL,
        hoodie_pattern TEXT NOT NULL,
        stones TEXT NOT NULL,
        face TEXT NOT NULL,
        price TEXT NOT NULL DEFAULT '1.99',
        sold INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS leaders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cert TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        tribe TEXT NOT NULL,
        hoodie_pattern TEXT NOT NULL,
        stones TEXT NOT NULL,
        face TEXT NOT NULL,
        price TEXT NOT NULL DEFAULT '10.00',
        sold INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS gods (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cert TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        price TEXT NOT NULL DEFAULT '40.00',
        sold INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    # Migration for databases created before Stripe: purchases gain a
    # stripe_session_id column (PayPal purchases keep paypal_order_id).
    cols = [r[1] for r in
            con.execute("PRAGMA table_info(purchases)").fetchall()]
    if "stripe_session_id" not in cols:
        con.execute("ALTER TABLE purchases ADD COLUMN stripe_session_id"
                    " TEXT")
    # Migration for databases created before gender: babies gain a
    # gender column. Every row is (re)computed under the current rule
    # (leaders female, Blacks and gods male, others unmarked), so rows
    # stored under the old dark/light color rule get corrected.
    baby_cols = [r[1] for r in
                 con.execute("PRAGMA table_info(babies)").fetchall()]
    if "gender" not in baby_cols:
        con.execute("ALTER TABLE babies ADD COLUMN gender TEXT")
    for row in con.execute(
            "SELECT id, cert, name, tribe, gender FROM babies").fetchall():
        new_gender = baby_gender(row["tribe"], "", "",
                                 name=row["name"], cert=row["cert"])
        if row["gender"] != new_gender:
            con.execute("UPDATE babies SET gender=? WHERE id=?",
                        (new_gender, row["id"]))
    # Notices board: seeded once with the current characters and where
    # they were found; new additions append below.
    if con.execute("SELECT COUNT(*) FROM notices").fetchone()[0] == 0:
        for text in (
            "The First Beginnings collection is live — the first hoodie "
            "babies have arrived.",
            "Damiana June, leader of the Waterliewaveminans, is "
            "available — found in the sea.",
            "Aaliyah Bee, leader of the Fortress Rooted, is available — "
            "found in the woods.",
            "Jazmyn, leader of the Windyworrors, is available — found "
            "in the sky.",
            "The Waterliewaveminans have arrived — Marina, Coral, Pearl, "
            "Tide, and Kai, found by the sea.",
        ):
            con.execute("INSERT INTO notices (text) VALUES (?)", (text,))
    # System account that owns the weekly Black sale listing.
    if not con.execute("SELECT 1 FROM users WHERE username='underground'"
                       ).fetchone():
        salt = secrets.token_hex(16)
        con.execute(
            "INSERT INTO users (username, password_hash, salt, invite_code)"
            " VALUES (?,?,?,?)",
            ("underground", hash_password(secrets.token_hex(32), salt),
             salt, new_invite_code(con)))
    # Shop stock: individual characters for sale at $1.99, seeded once.
    if con.execute("SELECT COUNT(*) FROM shop_items").fetchone()[0] == 0:
        for i, (name, tribe) in enumerate(SHOP_INDIVIDUALS):
            pool = STARTERS[tribe]
            con.execute(
                "INSERT INTO shop_items (cert, name, tribe, hoodie_pattern,"
                " stones, face, price) VALUES (?,?,?,?,?,?,?)",
                ("EHB-S%03d" % (i + 1), name, tribe,
                 pool["patterns"][i % len(pool["patterns"])],
                 pool["stones"], pool["face"], PRICE_INDIVIDUAL))
    # Leaders: the 3 unique founders, seeded once.
    if con.execute("SELECT COUNT(*) FROM leaders").fetchone()[0] == 0:
        for ld in LEADERS:
            con.execute(
                "INSERT INTO leaders (cert, name, tribe, hoodie_pattern,"
                " stones, face, price) VALUES (?,?,?,?,?,?,?)",
                (ld["cert"], ld["name"], ld["tribe"], ld["pattern"],
                 ld["stones"], ld["face"], PRICE_LEADER))
    # Gods: exactly 3, seeded once with Doneil's names.
    if con.execute("SELECT COUNT(*) FROM gods").fetchone()[0] == 0:
        for g in GODS:
            con.execute(
                "INSERT INTO gods (cert, name, price) VALUES (?,?,?)",
                (g["cert"], g["name"], PRICE_GOD))
    # Migration for databases seeded before the gods were named: rename
    # the generic slots to Doneil's names, matched by certificate.
    for g in GODS:
        con.execute("UPDATE gods SET name=? WHERE cert=?",
                    (g["name"], g["cert"]))
    # Migration for pop-up tiers: popup_items gains a tier (drifter /
    # special / rare / shadow) and a stock count (copies left) plus the
    # edition total. Existing rows are all weekly drifters (one copy).
    popup_cols = [r[1] for r in
                  con.execute("PRAGMA table_info(popup_items)").fetchall()]
    for col, ddl in (("tier", "TEXT DEFAULT 'drifter'"),
                     ("stock", "INTEGER DEFAULT 1"),
                     ("total", "INTEGER DEFAULT 1")):
        if col not in popup_cols:
            con.execute("ALTER TABLE popup_items ADD COLUMN %s %s"
                        % (col, ddl))
    con.execute("UPDATE popup_items SET stock=0 WHERE sold=1")
    # Migration for the tribe renames: rewrite old tribe names in the
    # Notices board so no user-facing page still shows them.
    for old, new in (("Blacks", "Shadows"),
                     ("Blues", "Waterliewaveminans"),
                     ("Browns", "Fortress Rooted"),
                     ("Reds", "Windyworrors")):
        con.execute("UPDATE notices SET text=REPLACE(text, ?, ?)",
                    (old, new))
    con.execute("UPDATE notices SET text=REPLACE(text, "
                "'A Black has popped up', 'A Shadow has popped up')")
    con.execute("UPDATE notices SET text=REPLACE(text, "
                "'A Black was caught', 'A Shadow was caught')")
    con.commit()
    con.close()


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(),
                               salt.encode(), 200_000).hex()


def new_invite_code(con):
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = "".join(secrets.choice(alphabet) for _ in range(8))
        if not con.execute("SELECT 1 FROM users WHERE invite_code=?",
                           (code,)).fetchone():
            return code


def mint_baby(con, owner_id, tribe=None, name=None, pattern=None,
              stones=None, face=None, is_custom=0):
    """Create one unique baby (1-of-1 certificate) for an owner."""
    if tribe is None:
        tribe = random.choice(list(STARTERS))
    pool = STARTERS[tribe]
    name = name or random.choice(pool["names"])
    pattern = pattern or random.choice(pool["patterns"])
    stones = stones or pool["stones"]
    face = face or pool["face"]
    prefix = pool["prefix"] if tribe in STARTERS else "C"
    n = con.execute("SELECT COUNT(*) FROM babies WHERE tribe=?",
                    (tribe,)).fetchone()[0] + 1
    while True:
        cert = "EHB-%s%03d" % (prefix, n)
        if not con.execute("SELECT 1 FROM babies WHERE cert=?",
                           (cert,)).fetchone():
            break
        n += 1
    con.execute(
        "INSERT INTO babies (cert, owner_id, name, tribe, hoodie_pattern,"
        " stones, face, is_custom, gender) VALUES (?,?,?,?,?,?,?,?,?)",
        (cert, owner_id, name, tribe, pattern, stones, face, is_custom,
         baby_gender(tribe, pattern, stones, name=name, cert=cert)))
    return cert


def creator_slots_available(user):
    return user["points_earned"] // 300 - user["creator_slots_used"]


def trade_cap(con, user_id):
    """Max pending outgoing offers at once: regular 1, leader owner 2,
    god owner 5 (highest applicable wins). Perks follow current
    ownership — trade away your leader or god and the cap drops."""
    certs = {r["cert"] for r in con.execute(
        "SELECT cert FROM babies WHERE owner_id=?",
        (user_id,)).fetchall()}
    god_certs = {r["cert"] for r in con.execute(
        "SELECT cert FROM gods").fetchall()}
    if certs & god_certs:
        return 5
    leader_certs = {r["cert"] for r in con.execute(
        "SELECT cert FROM leaders").fetchall()}
    if certs & leader_certs:
        return 2
    return 1


def cap_error(cap, pending):
    """Message shown when an offer would exceed the trade cap."""
    return ("You can have at most %d pending offer(s) at once — regular "
            "traders 1, leader owners 2, god owners 5. You already have "
            "%d pending, so wait for one to resolve before sending "
            "another." % (cap, pending))


def grant_purchase(con, pur, user_id):
    """Grant a verified purchase. Returns (ok, kind, name, cert, points).

    kind is one of 'individual', 'leader', 'god', 'points'. Callers must
    have verified the payment already and checked the purchase is still
    pending; this function only checks the item is still available and
    performs the grant. It does not commit — the caller does that after
    marking the purchase completed.
    """
    item_type, ref = pur["item_type"], pur["item_ref"]
    if item_type == "individual":
        item = con.execute(
            "SELECT * FROM shop_items WHERE cert=? AND sold=0",
            (ref,)).fetchone()
        if not item:
            return (False, "individual", "", "",
                    "That character was already sold — nothing was "
                    "granted.")
        con.execute(
            "INSERT INTO babies (cert, owner_id, name, tribe,"
            " hoodie_pattern, stones, face, gender) VALUES (?,?,?,?,?,?,?,?)",
            (item["cert"], user_id, item["name"], item["tribe"],
             item["hoodie_pattern"], item["stones"], item["face"],
             baby_gender(item["tribe"], item["hoodie_pattern"],
                         item["stones"], name=item["name"],
                         cert=item["cert"])))
        con.execute("UPDATE shop_items SET sold=1 WHERE id=?",
                    (item["id"],))
        return (True, "individual", item["name"], item["cert"], 0)
    if item_type == "popup":
        # Edition stock: drifters and Shadows are one copy (the baby
        # keeps the listing cert); specials and rares have many copies,
        # each baby getting the listing cert plus a copy number. The
        # listing is gone once stock hits 0.
        item = con.execute(
            "SELECT * FROM popup_items WHERE cert=? AND stock>0",
            (ref,)).fetchone()
        if not item:
            return (False, "popup", "", "",
                    "That pop-up was already sold — nothing was "
                    "granted.")
        total = item["total"] or 1
        stock = item["stock"] or 0
        copy_no = total - stock + 1
        baby_cert = (item["cert"] if total <= 1
                     else "%s-%02d" % (item["cert"], copy_no))
        con.execute(
            "INSERT INTO babies (cert, owner_id, name, tribe,"
            " hoodie_pattern, stones, face, gender) VALUES (?,?,?,?,?,?,?,?)",
            (baby_cert, user_id, item["name"], item["tribe"],
             item["hoodie_pattern"], item["stones"], item["face"],
             baby_gender(item["tribe"], item["hoodie_pattern"],
                         item["stones"], name=item["name"],
                         cert=baby_cert)))
        new_stock = stock - 1
        con.execute("UPDATE popup_items SET stock=?, sold=? WHERE id=?",
                    (new_stock, 1 if new_stock <= 0 else 0, item["id"]))
        return (True, "popup", item["name"], baby_cert, 0)
    if item_type == "leader":
        ld = con.execute(
            "SELECT * FROM leaders WHERE cert=? AND sold=0",
            (ref,)).fetchone()
        if not ld:
            return (False, "leader", "", "",
                    "That leader was already sold — nothing was granted.")
        con.execute(
            "INSERT INTO babies (cert, owner_id, name, tribe,"
            " hoodie_pattern, stones, face, gender) VALUES (?,?,?,?,?,?,?,?)",
            (ld["cert"], user_id, ld["name"], ld["tribe"],
             ld["hoodie_pattern"], ld["stones"], ld["face"],
             baby_gender(ld["tribe"], ld["hoodie_pattern"],
                         ld["stones"], name=ld["name"],
                         cert=ld["cert"])))
        con.execute("UPDATE leaders SET sold=1 WHERE id=?", (ld["id"],))
        add_notice(con, "Leader %s was purchased — now in a new "
                   "collection." % ld["name"])
        return (True, "leader", ld["name"], ld["cert"], 0)
    if item_type == "god":
        g = con.execute(
            "SELECT * FROM gods WHERE cert=? AND sold=0",
            (ref,)).fetchone()
        if not g:
            return (False, "god", "", "",
                    "That god was already taken — nothing was granted.")
        con.execute(
            "INSERT INTO babies (cert, owner_id, name, tribe,"
            " hoodie_pattern, stones, face, gender) VALUES (?,?,?,?,?,?,?,?)",
            (g["cert"], user_id, g["name"], "Gods", "God hoodie",
             "god rhinestones", "god-like",
             baby_gender("Gods", "God hoodie", "god rhinestones")))
        con.execute("UPDATE gods SET sold=1 WHERE id=?", (g["id"],))
        add_notice(con, "A god was purchased — one of only 3 gods, now "
                   "in a new collection.")
        return (True, "god", g["name"], g["cert"], 0)
    if item_type == "points":
        try:
            n = int(ref.split("-", 1)[1])
        except (IndexError, ValueError):
            n = 0
        if n not in POINTS_PACK_PRICES:
            return (False, "points", "", "",
                    "That points pack is not for sale — nothing was "
                    "granted.")
        # Balance only: bought points never count toward the
        # character-creator unlock (trade-earned only).
        con.execute("UPDATE users SET points=points+? WHERE id=?",
                    (n, user_id))
        return (True, "points", "", "", n)
    return (False, item_type, "", "",
            "Unknown purchase type — nothing was granted.")


def add_notice(con, text):
    con.execute("INSERT INTO notices (text) VALUES (?)", (text,))


def weekly_shadow_cert():
    iso_year, iso_week = current_iso_week()
    return "EHB-SHD-%d-W%02d" % (iso_year, iso_week)


def ensure_weekly_shadow(con):
    """One Shadow pops up in the Shop each week as a $10.00 money
    purchase (PayPal or Cash App Pay, like any shop buy). This replaces
    the old Underground points-offer listing on the Trade Board: player
    to player, Shadows still trade under the 1,200-point gate."""
    cert = weekly_shadow_cert()
    if con.execute("SELECT 1 FROM popup_items WHERE cert=?",
                   (cert,)).fetchone():
        return
    _, iso_week = current_iso_week()
    pick = BLACKS_ROSTER[iso_week % len(BLACKS_ROSTER)]
    con.execute(
        "INSERT INTO popup_items (cert, name, tribe, hoodie_pattern,"
        " stones, face, price, tier, stock, total)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cert, pick["name"], "Blacks", pick["pattern"], BLACK_STONES,
         BLACK_FACE, PRICE_SHADOW, "shadow", 1, 1))
    add_notice(con, "A Shadow has popped up in the Shop — %s with %s, "
               "found underground. $%s." % (pick["name"], pick["pattern"],
                                            PRICE_SHADOW))
    con.commit()

# ---------------------------------------------------------------- deals, gifts, pop-ups, ownership, gender
# Defaults in this section are Doneil's to correct (see README):
# deals pick 4 individual characters per ISO week (2 at 25% off, 2 at
# half off); the monthly gift needs 1 completed purchase in the month;
# a choice baby is earned every 5 completed shop purchases; pop-ups
# are $1.99 like other individuals; gender is leaders female, Blacks
# and gods male, every other baby unmarked (no color inference).

# Pop-up color variants: NEW pattern + rhinestone combinations on the
# existing tribes only. No new tribe names are invented here — a new
# tribe joins the pop-up rotation automatically once it is added to
# STARTERS as data (see ensure_weekly_popup).
POPUP_VARIANTS = {
    "Blues": [("Aurora Tide", "teal + silver rhinestones"),
              ("Coral Glow", "pink + gold rhinestones"),
              ("Seafoam Dream", "teal + white rhinestones")],
    "Browns": [("Amber Dusk", "copper + amber rhinestones"),
               ("Moss Glow", "green + gold rhinestones"),
               ("Timber Night", "brown + black rhinestones")],
    "Reds": [("Crimson Veil", "crimson + gold rhinestones"),
             ("Sky Blush", "pink + silver rhinestones"),
             ("Ember Dusk", "orange + red rhinestones")],
}
POPUP_FOUND = {"Blues": "by the sea", "Browns": "in the woods",
               "Reds": "in the sky"}

# Gender rule (Doneil's correction): the three leaders are female,
# Blacks and gods are male, every other baby has no gender marker.
# No inference from hoodie colors. Leaders are matched by certificate
# or name, the same way portraits and ownership already match them.
_LEADER_CERT_SET = {ld["cert"] for ld in LEADERS}
_LEADER_NAME_SET = {ld["name"] for ld in LEADERS}


def baby_gender(tribe, pattern, stones, name=None, cert=None):
    """Gender for a baby: leaders female, Blacks and gods male, all
    other babies unmarked (None). Pattern/stones are accepted for
    call-site compatibility and ignored."""
    if tribe in ("Blacks", "Gods"):
        return "male"
    if cert in _LEADER_CERT_SET or name in _LEADER_NAME_SET:
        return "female"
    return None


def gender_marker(gender):
    if gender == "male":
        return "♂"
    if gender == "female":
        return "♀"
    return ""


def stone_count(tribe, cert=None, name=None):
    """Rhinestones on a hoodie: gods carry 15, leaders 10, every
    villager 5 — drifters, specials, rares, and Shadows count as
    villagers. Colors are unchanged; only the counts are fixed."""
    if tribe == "Gods":
        return 15
    if cert in _LEADER_CERT_SET or name in _LEADER_NAME_SET:
        return 10
    return 5


def current_iso_week():
    today = datetime.date.today()
    iso_year, iso_week, _ = today.isocalendar()
    return iso_year, iso_week


def deal_price(price_str, fraction_off):
    """Sale price for a discount fraction, rounded half-up to cents:
    $1.99 at 25% off is $1.49, at half off is $1.00."""
    price = Decimal(price_str) * (Decimal(1) - Decimal(str(fraction_off)))
    return format(price.quantize(Decimal("0.01"),
                                 rounding=ROUND_HALF_UP), "f")


def weekly_deal_picks(con):
    """This week's Deals shelf: a deterministic rotating set of
    individual shop characters — up to 4, alternating 25% off and half
    off. The pick is seeded by the ISO week, so it is stable all week
    and changes next week; sold items simply drop off the shelf."""
    certs = [r["cert"] for r in con.execute(
        "SELECT cert FROM shop_items ORDER BY id").fetchall()]
    if not certs:
        return {}
    iso_year, iso_week = current_iso_week()
    rng = random.Random("deals-%d-W%02d" % (iso_year, iso_week))
    order = certs[:]
    rng.shuffle(order)
    picks = {}
    for i, cert in enumerate(order[:4]):
        picks[cert] = 0.25 if i % 2 == 0 else 0.50
    return picks


def weekly_popup_cert():
    iso_year, iso_week = current_iso_week()
    return "EHB-P-%d-W%02d" % (iso_year, iso_week)


def ensure_weekly_popup(con):
    """One new color variant pops up in the Shop each week — the
    regular pop-up, now called a drifter ($1.99) — plus a notice saying
    what popped up and where it was found. Only the current week's
    pop-up is listed for sale. The tribe rotation reads STARTERS, so a
    tribe added there later joins automatically."""
    cert = weekly_popup_cert()
    if con.execute("SELECT 1 FROM popup_items WHERE cert=?",
                   (cert,)).fetchone():
        return
    tribes = list(STARTERS)
    _, iso_week = current_iso_week()
    tribe = tribes[iso_week % len(tribes)]
    variants = POPUP_VARIANTS.get(tribe, [])
    if variants:
        pattern, stones = variants[iso_week % len(variants)]
    else:
        pattern = STARTERS[tribe]["patterns"][0]
        stones = STARTERS[tribe]["stones"]
    name = STARTERS[tribe]["names"][iso_week % len(STARTERS[tribe]["names"])]
    con.execute(
        "INSERT INTO popup_items (cert, name, tribe, hoodie_pattern,"
        " stones, face, price, tier, stock, total)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cert, name, tribe, pattern, stones, STARTERS[tribe]["face"],
         PRICE_INDIVIDUAL, "drifter", 1, 1))
    add_notice(con, "A new %s drifter popped up in the Shop — %s with "
               "%s, found %s." % (tribe_display(tribe), pattern, stones,
                                  POPUP_FOUND.get(tribe, "somewhere new")))
    con.commit()


def weekly_special_cert():
    iso_year, iso_week = current_iso_week()
    return "EHB-SP-%d-W%02d" % (iso_year, iso_week)


def weekly_rare_cert():
    iso_year, iso_week = current_iso_week()
    return "EHB-R-%d-W%02d" % (iso_year, iso_week)


def _ensure_weekly_tier_popup(con, cert, tier, price, edition, offset,
                              kind_word):
    """One limited-edition pop-up release per week: a special ($4.99,
    25 copies) or a rare ($9.99, 8 copies). Same rotation as drifters,
    offset so the tier's tribe and variant differ from the drifter's.
    Only the current week's release is listed; unsold copies stop being
    listed next week, like drifters."""
    if con.execute("SELECT 1 FROM popup_items WHERE cert=?",
                   (cert,)).fetchone():
        return
    tribes = list(STARTERS)
    _, iso_week = current_iso_week()
    idx = iso_week + offset
    tribe = tribes[idx % len(tribes)]
    variants = POPUP_VARIANTS.get(tribe, [])
    if variants:
        pattern, stones = variants[idx % len(variants)]
    else:
        pattern = STARTERS[tribe]["patterns"][0]
        stones = STARTERS[tribe]["stones"]
    name = STARTERS[tribe]["names"][idx % len(STARTERS[tribe]["names"])]
    con.execute(
        "INSERT INTO popup_items (cert, name, tribe, hoodie_pattern,"
        " stones, face, price, tier, stock, total)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cert, name, tribe, pattern, stones, STARTERS[tribe]["face"],
         price, tier, edition, edition))
    add_notice(con, "A %s pop-up appeared in the Shop — %s (%s) with "
               "%s, found %s. Only %d in this edition, $%s."
               % (kind_word, pattern, tribe_display(tribe), stones,
                  POPUP_FOUND.get(tribe, "somewhere new"), edition,
                  price))
    con.commit()


def ensure_weekly_special(con):
    _ensure_weekly_tier_popup(con, weekly_special_cert(), "special",
                              PRICE_SPECIAL, SPECIAL_EDITION, 1,
                              "special")


def ensure_weekly_rare(con):
    _ensure_weekly_tier_popup(con, weekly_rare_cert(), "rare",
                              PRICE_RARE, RARE_EDITION, 2, "rare")


def ensure_weekly_popups(con):
    """Make sure all four weekly pop-up listings exist: the drifter,
    the special, the rare, and the Shadow."""
    ensure_weekly_popup(con)
    ensure_weekly_special(con)
    ensure_weekly_rare(con)
    ensure_weekly_shadow(con)


def tribe_leader_cert(con, tribe):
    row = con.execute("SELECT cert FROM leaders WHERE tribe=?",
                      (tribe,)).fetchone()
    return row["cert"] if row else None


def tribe_owner(con, tribe):
    """Owner of a tribe: whoever currently holds that tribe's leader
    baby. Ownership passes automatically when the leader is traded,
    because it follows the baby, not the buyer."""
    cert = tribe_leader_cert(con, tribe)
    if not cert:
        return None
    row = con.execute(
        "SELECT users.username FROM babies JOIN users "
        "ON users.id = babies.owner_id WHERE babies.cert=?",
        (cert,)).fetchone()
    return row["username"] if row else None


def tribe_standings(con, tribe):
    """(owner, second, third) for a tribe. Second and Third are the two
    users other than the owner holding the most babies of that tribe
    (ties broken by username, so the ranking is deterministic). Titles
    only — no perks for 2nd/3rd."""
    owner = tribe_owner(con, tribe)
    rows = con.execute(
        "SELECT users.username, COUNT(*) AS c FROM babies JOIN users "
        "ON users.id = babies.owner_id WHERE babies.tribe=? "
        "GROUP BY users.id ORDER BY c DESC, users.username ASC",
        (tribe,)).fetchall()
    others = [r["username"] for r in rows if r["username"] != owner]
    second = others[0] if len(others) > 0 else None
    third = others[1] if len(others) > 1 else None
    return owner, second, third


def village_progress(con, user_id):
    """(baby count, leader names) for a player's village: every baby
    owned except leaders counts, and the village is complete at 40."""
    count = con.execute(
        "SELECT COUNT(*) FROM babies WHERE owner_id=? AND cert NOT IN "
        "(SELECT cert FROM leaders)", (user_id,)).fetchone()[0]
    leaders = con.execute(
        "SELECT name FROM babies WHERE owner_id=? AND cert IN "
        "(SELECT cert FROM leaders)", (user_id,)).fetchall()
    return count, [r["name"] for r in leaders]


def user_owned_tribes(con, user_id):
    """Tribes whose leader baby this user currently holds."""
    owned = []
    for tribe in ("Blues", "Browns", "Reds"):
        cert = tribe_leader_cert(con, tribe)
        if not cert:
            continue
        row = con.execute(
            "SELECT 1 FROM babies WHERE cert=? AND owner_id=?",
            (cert, user_id)).fetchone()
        if row:
            owned.append(tribe)
    return owned


def monthly_gift_state(con, user_id):
    """(month, bought_this_month, already_claimed) for the monthly
    gift: one free baby per user per calendar month, only if the user
    made at least one completed purchase that month."""
    month = datetime.date.today().strftime("%Y-%m")
    bought = con.execute(
        "SELECT COUNT(*) FROM purchases WHERE user_id=? AND status="
        "'completed' AND strftime('%Y-%m', created_at)=?",
        (user_id, month)).fetchone()[0]
    claimed = con.execute(
        "SELECT 1 FROM monthly_gifts WHERE user_id=? AND month=?",
        (user_id, month)).fetchone()
    return month, bought > 0, claimed is not None


def choice_progress(con, user_id):
    """(available, completed, earned, claimed) for choice babies: one
    choice baby per 5 cumulative completed shop purchases, at 5, 10,
    15, ... Unclaimed milestones stay available until claimed."""
    done = con.execute(
        "SELECT COUNT(*) FROM purchases WHERE user_id=? AND status="
        "'completed'", (user_id,)).fetchone()[0]
    earned = done // 5
    claimed = con.execute(
        "SELECT COUNT(*) FROM choice_claims WHERE user_id=?",
        (user_id,)).fetchone()[0]
    return max(0, earned - claimed), done, earned, claimed

# ---------------------------------------------------------------- html helpers
def esc(s):
    return html.escape(str(s))


def page(title, body, user=None):
    nav = ""
    share_btn = ("<button type='button' id='share-btn' "
                 "onclick='sharePage()'>Share</button>")
    # Empty <p> with zero margin: takes no space until the copy
    # fallback writes text into it. (Deliberately NOT display:none:
    # the footer-disclaimer checks assert no display:none on the page.)
    share_notice = ("<p id='share-notice' "
                    "style='margin:0;color:green'></p>")
    if user:
        nav = ("<nav><a href='/me'>My Collection</a> | "
               "<a href='/browse'>Trade Board</a> | "
               "<a href='/offers'>Offers</a> | "
               "<a href='/shop'>Shop</a> | "
               "<a href='/purchases'>Purchases</a> | "
               "<a href='/world'>World</a> | "
               "<a href='/notices'>Notices</a> | "
               "<a href='/logout'>Log out (%s)</a> | "
               "%s</nav>%s" % (esc(user["username"]), share_btn,
                               share_notice))
    else:
        nav = ("<nav><a href='/'>Home</a> | <a href='/login'>Log in</a> | "
               "<a href='/signup'>Sign up</a> | "
               "<a href='/shop'>Shop</a> | "
               "<a href='/world'>World</a> | "
               "<a href='/notices'>Notices</a> | "
               "%s</nav>%s" % (share_btn, share_notice))
    return ("<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>%s</title><style>"
            "body{font-family:system-ui,sans-serif;max-width:640px;margin:0 auto;"
            "padding:16px;line-height:1.5} nav{margin-bottom:16px}"
            "input,select,button{font-size:16px;padding:8px;margin:4px 0}"
            ".card{border:1px solid #ccc;border-radius:10px;padding:12px;"
            "margin:10px 0} .tag{display:inline-block;background:#eef;"
            "border-radius:6px;padding:2px 8px;margin-right:6px}"
            ".ok{color:green} .err{color:red}"
            "</style></head><body>%s<h1>%s</h1>%s"
            "<footer style='margin-top:24px;padding-top:12px;"
            "border-top:1px solid #ccc;font-size:13px;color:#444'>"
            "Note: the original Hoodie Babies figure will not be released "
            "until further notice — but actual, physical Hoodie Babies "
            "are coming. Nothing is set in stone yet.</footer>"
            % (esc(title), nav, esc(title), body)
            + share_script + "</body></html>")


# Share button script: phone share sheet when available, clipboard
# fallback with an inline notice otherwise. Shares the CURRENT page
# URL on purpose — Home shares the join link, a baby detail page
# shares that baby. A failed or closed share sheet now falls back to
# copying the link too, so a tap never silently does nothing.
# No new dependencies.
share_script = (
    "<script>function sharePage(){"
    "var n=document.getElementById('share-notice');"
    "function msg(t){n.textContent=t;}"
    "function copyFallback(){"
    "if(navigator.clipboard&&navigator.clipboard.writeText){"
    "navigator.clipboard.writeText(window.location.href).then("
    "function(){msg('Link copied — paste it anywhere!');},"
    "function(){msg('Copy this link: '+window.location.href);});}"
    "else{var t=document.createElement('textarea');"
    "t.value=window.location.href;document.body.appendChild(t);"
    "t.select();try{document.execCommand('copy');"
    "msg('Link copied — paste it anywhere!');}"
    "catch(e){msg('Copy this link: '+window.location.href);}"
    "document.body.removeChild(t);}}"
    "var d={title:'Hoodie Babies Trade',"
    "text:'Join Hoodie Babies Trade — pick your tribe, "
    "get your starter baby and 600 points, and start trading!',"
    "url:window.location.href};"
    "if(navigator.share){navigator.share(d).catch(function(){"
    "copyFallback();});}"
    "else{copyFallback();}}"
    "</script>")


# ---------------------------------------------------------------- portraits
# Inline-SVG portraits, rendered server-side from each baby's stored
# attributes (tribe, pattern, stones, cert). No image files and no new
# dependencies, so the Render deploy needs no static-file handling. The
# certificate seeds a small deterministic variation, so no two portraits
# are identical even for babies sharing a pattern and stones. All text
# that can come from a user (name, cert) is escaped; every color comes
# from a fixed palette, never from user input.
_STONE_COLORS = {
    "yellow": "#FFD93B", "green": "#3DBA6B", "amber": "#E8A020",
    "gold": "#FFC93B", "red": "#E53935", "black": "#222222",
    "diamond": "#BFEFFF", "white": "#FFFFFF", "blue": "#2196F3",
    "purple": "#9C27B0", "pink": "#FF7BAC", "orange": "#FF9800",
    "teal": "#0E7C6B", "silver": "#C0C0C0",
}
_RAINBOW = ["#E53935", "#FF9800", "#FFD93B", "#3DBA6B", "#2196F3",
            "#9C27B0"]
_TRIBE_STYLE = {
    "Blues": {"bg": "#DCEFFF", "hoodie": "#3B82C4", "face": "#A8E6D9",
              "default_stones": ["#FFD93B", "#3DBA6B"]},
    "Browns": {"bg": "#EFE6C8", "hoodie": "#7A5230", "face": "#EFC493",
               "default_stones": ["#E8A020", "#FFC93B"]},
    "Reds": {"bg": "#FFE3E0", "hoodie": "#D94F4F", "face": "#FFD9B8",
             "default_stones": ["#E53935"]},
    "Blacks": {"bg": "#3F3F46", "hoodie": "#FFFFFF", "face": "#A9BCA0",
               "default_stones": ["#222222"]},
    "Gods": {"bg": "#F3E8FF", "hoodie": "#5B2D8E", "face": "#FFE9B8",
             "default_stones": ["#FFC93B"]},
}
_GOD_ACCENTS = {
    "Muzle Back": "#7B2D8E", "Baraka": "#0E7C6B", "Sirus V": "#8E1F2F",
}
_LEADER_CERTS = {"EHB-L001": "Damiana June", "EHB-L002": "Aaliyah Bee",
                 "EHB-L003": "Jazmyn"}


def _bget(b, key, default=""):
    try:
        v = b[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if v is None else v


def _stone_colors(stones_text, tribe):
    """Rhinestone colors for a baby: parsed from its stored stones text,
    falling back to the tribe default. Shadows always get black stones
    on their white hoodies; Jazmyn-style 'every different color' gets
    the rainbow."""
    if tribe == "Blacks":
        return ["#222222"]
    text = (stones_text or "").lower()
    if "every different color" in text or "every color" in text:
        return list(_RAINBOW)
    found = []
    for word, hexv in _STONE_COLORS.items():
        if word in text and hexv not in found:
            found.append(hexv)
    if found:
        return found
    style = _TRIBE_STYLE.get(tribe, _TRIBE_STYLE["Blues"])
    return list(style["default_stones"])


def _motif(pattern, color, hoodie):
    """Small chest emblem picked from the stored hoodie pattern, so
    each 1-of-1 hoodie carries its own back design."""
    import hashlib
    kinds = ["star", "wave", "swirl", "diamond", "leaf", "flame", "moon",
             "sun"]
    kind = kinds[int(hashlib.md5((pattern or "").encode()).hexdigest()[:2],
                     16) % len(kinds)]
    if kind == "star":
        return ("<polygon points='60,96 63,103 70,103 64,107 66,114 "
                "60,110 54,114 56,107 50,103 57,103' fill='%s'/>" % color)
    if kind == "wave":
        return ("<path d='M48,107 q6,-7 12,0 t12,0' fill='none' "
                "stroke='%s' stroke-width='2.5'/>" % color)
    if kind == "swirl":
        return ("<circle cx='60' cy='106' r='7' fill='none' stroke='%s' "
                "stroke-width='2'/><circle cx='60' cy='106' r='2.5' "
                "fill='%s'/>" % (color, color))
    if kind == "diamond":
        return ("<polygon points='60,97 68,106 60,115 52,106' "
                "fill='%s'/>" % color)
    if kind == "leaf":
        return ("<path d='M60,96 Q70,106 60,116 Q50,106 60,96' "
                "fill='%s'/>" % color)
    if kind == "flame":
        return ("<path d='M60,96 Q66,104 64,110 Q60,115 56,110 Q54,104 "
                "60,96' fill='%s'/>" % color)
    if kind == "moon":
        return ("<circle cx='60' cy='106' r='8' fill='%s'/>"
                "<circle cx='63' cy='103' r='7' fill='%s'/>"
                % (color, hoodie))
    return ("<circle cx='60' cy='106' r='5' fill='%s'/>"
            "<path d='M60,94 v-4 M60,118 v-4 M48,106 h-4 M76,106 h-4' "
            "stroke='%s' stroke-width='2'/>" % (color, color))


def portrait_svg(b, size=120):
    """Inline-SVG portrait for one baby (or shop item / leader / god).

    b needs name, cert, tribe, hoodie_pattern, stones, face. Faces
    follow Doneil's final specs: Blues fish-like, Browns animal-like,
    Reds angel-like, Shadows angry creature-like, with the three
    leaders' signature looks and grander crowned portraits for gods.
    """
    name = _bget(b, "name")
    cert = _bget(b, "cert")
    tribe = _bget(b, "tribe") or "Blues"
    pattern = _bget(b, "hoodie_pattern")
    stones_text = _bget(b, "stones")
    style = _TRIBE_STYLE.get(tribe, _TRIBE_STYLE["Blues"])
    bg, hoodie, face = style["bg"], style["hoodie"], style["face"]
    is_damiana = cert == "EHB-L001" or name == "Damiana June"
    is_aaliyah = cert == "EHB-L002" or name == "Aaliyah Bee"
    is_jazmyn = cert == "EHB-L003" or name == "Jazmyn"
    is_god = tribe == "Gods"
    if is_jazmyn:
        hoodie = "#111111"
    accent = _GOD_ACCENTS.get(name, "#FFC93B") if is_god else None
    if is_god:
        hoodie = accent
    stones = _stone_colors(stones_text, tribe)
    if is_jazmyn:
        stones = list(_RAINBOW)
    # Deterministic per-baby variation, seeded by the unique certificate.
    rnd = random.Random(cert or "X")
    parts = []
    parts.append("<svg class='portrait' width='%d' height='%d' "
                 "viewBox='0 0 120 150' xmlns='http://www.w3.org/2000/svg' "
                 "role='img' aria-label='%s'>"
                 % (size, size * 150 // 120, esc(name)))
    parts.append("<rect x='2' y='2' width='116' height='146' rx='12' "
                 "fill='%s'/>" % bg)
    # Faint background sparkles, placed per certificate.
    for sx, sy in ((18, 22), (102, 30), (16, 128), (104, 126)):
        if rnd.random() < 0.6:
            parts.append("<circle cx='%d' cy='%d' r='2' fill='#FFFFFF' "
                         "opacity='0.7'/>" % (sx + rnd.randint(-3, 3),
                                              sy + rnd.randint(-3, 3)))
    # Wings behind the body for angel-like Reds.
    if tribe == "Reds" and not is_jazmyn:
        parts.append("<ellipse cx='24' cy='98' rx='11' ry='20' fill='#FFFFFF' "
                     "stroke='#DDDDDD' transform='rotate(-18 24 98)'/>")
        parts.append("<ellipse cx='96' cy='98' rx='11' ry='20' fill='#FFFFFF' "
                     "stroke='#DDDDDD' transform='rotate(18 96 98)'/>")
    # Hoodie torso, then the hood ring behind the head.
    parts.append("<rect x='30' y='72' width='60' height='60' rx='12' "
                 "fill='%s' stroke='#00000022'/>" % hoodie)
    parts.append("<circle cx='60' cy='56' r='30' fill='%s' "
                 "stroke='#00000022'/>" % hoodie)
    # Ears: fins for fish-like Blues, round ears for animal-like Browns.
    if tribe == "Blues":
        parts.append("<polygon points='37,48 25,41 30,57' fill='%s'/>" % hoodie)
        parts.append("<polygon points='83,48 95,41 90,57' fill='%s'/>" % hoodie)
    elif tribe == "Browns":
        parts.append("<circle cx='40' cy='30' r='8' fill='%s'/>" % face)
        parts.append("<circle cx='80' cy='30' r='8' fill='%s'/>" % face)
        parts.append("<circle cx='40' cy='30' r='4' fill='#F2C9A0'/>")
        parts.append("<circle cx='80' cy='30' r='4' fill='#F2C9A0'/>")
    # God glow ring behind the head.
    if is_god:
        parts.append("<circle cx='60' cy='52' r='31' fill='none' "
                     "stroke='%s' stroke-width='2' opacity='0.7'/>" % accent)
    # Head: diamond-shaped and glowing for Jazmyn, round otherwise.
    if is_jazmyn:
        parts.append("<polygon points='60,28 82,52 60,76 38,52' "
                     "fill='#EAF7FF' stroke='#7CC4FF' stroke-width='2'/>")
        parts.append("<path d='M38,52 L60,28 L82,52' fill='none' "
                     "stroke='#FFFFFF' stroke-width='1.5' opacity='0.8'/>")
        # Hoodie over her face: only the head and eyes show.
        parts.append("<path d='M30,56 Q30,26 60,26 Q90,26 90,56 L82,56 "
                     "Q82,34 60,34 Q38,34 38,56 Z' fill='#111111'/>")
    elif tribe == "Blues":
        parts.append("<ellipse cx='60' cy='52' rx='25' ry='22' fill='%s'/>" % face)
    else:
        parts.append("<circle cx='60' cy='52' r='23' fill='%s'/>" % face)
    # Face features.
    if tribe == "Blacks":
        # Angry creature-like: heavy angled brows, diamond eyes, grimace.
        parts.append("<line x1='43' y1='40' x2='57' y2='45' stroke='#222222' "
                     "stroke-width='3'/>")
        parts.append("<line x1='77' y1='40' x2='63' y2='45' stroke='#222222' "
                     "stroke-width='3'/>")
        parts.append("<polygon points='51,44 55,40 59,44 55,50' fill='#DFF6FF' "
                     "stroke='#333333'/>")
        parts.append("<polygon points='69,44 73,40 77,44 73,50' fill='#DFF6FF' "
                     "stroke='#333333'/>")
        parts.append("<path d='M50,66 l4,-3 4,3 4,-3 4,3' fill='none' "
                     "stroke='#333333' stroke-width='2'/>")
    elif is_jazmyn:
        # Black eyes only, on the glowing diamond head.
        parts.append("<ellipse cx='51' cy='50' rx='4' ry='5' fill='#111111'/>")
        parts.append("<ellipse cx='69' cy='50' rx='4' ry='5' fill='#111111'/>")
        parts.append("<circle cx='52.5' cy='48.5' r='1.2' fill='#FFFFFF'/>")
        parts.append("<circle cx='70.5' cy='48.5' r='1.2' fill='#FFFFFF'/>")
    else:
        if is_damiana:
            # Surprised crying look, blue slanted eyes.
            parts.append("<ellipse cx='51' cy='48' rx='6' ry='3.5' "
                         "fill='#FFFFFF' transform='rotate(-20 51 48)'/>")
            parts.append("<ellipse cx='69' cy='48' rx='6' ry='3.5' "
                         "fill='#FFFFFF' transform='rotate(20 69 48)'/>")
            parts.append("<circle cx='51' cy='48' r='2' fill='#1F4E79'/>")
            parts.append("<circle cx='69' cy='48' r='2' fill='#1F4E79'/>")
            parts.append("<path d='M45,56 q3,7 1,10 q-4,-2 -2,-8 Z' "
                         "fill='#7CC4FF'/>")
            parts.append("<path d='M75,56 q-3,7 -1,10 q4,-2 2,-8 Z' "
                         "fill='#7CC4FF'/>")
            parts.append("<ellipse cx='60' cy='64' rx='5' ry='6' "
                         "fill='#8A4B3A'/>")
        else:
            parts.append("<circle cx='51' cy='48' r='5' fill='#FFFFFF'/>")
            parts.append("<circle cx='69' cy='48' r='5' fill='#FFFFFF'/>")
            parts.append("<circle cx='51' cy='48' r='2.5' fill='#222222'/>")
            parts.append("<circle cx='69' cy='48' r='2.5' fill='#222222'/>")
            if tribe == "Blues":
                # Puckered fish lips.
                parts.append("<ellipse cx='60' cy='64' rx='4' ry='5' "
                             "fill='#D96A6A'/>")
            elif tribe == "Browns":
                # Grouchy: angled brows and a frown.
                parts.append("<line x1='44' y1='41' x2='56' y2='45' "
                             "stroke='#333333' stroke-width='2.5'/>")
                parts.append("<line x1='76' y1='41' x2='64' y2='45' "
                             "stroke='#333333' stroke-width='2.5'/>")
                parts.append("<path d='M52,66 Q60,60 68,66' fill='none' "
                             "stroke='#333333' stroke-width='2'/>")
            elif tribe == "Reds":
                # Slight curious smile, one brow raised.
                parts.append("<path d='M54,64 Q61,68 67,63' fill='none' "
                             "stroke='#333333' stroke-width='2'/>")
                parts.append("<line x1='45' y1='39' x2='55' y2='37' "
                             "stroke='#333333' stroke-width='2'/>")
        if rnd.random() < 0.5:
            parts.append("<circle cx='42' cy='60' r='3.5' fill='#FF9AA2' "
                         "opacity='0.6'/>")
            parts.append("<circle cx='78' cy='60' r='3.5' fill='#FF9AA2' "
                         "opacity='0.6'/>")
    # Aaliyah Bee: red stone in the middle of her forehead.
    if is_aaliyah:
        parts.append("<polygon points='60,26 64,31 60,36 56,31' "
                     "fill='#E53935' stroke='#8E1F2F'/>")
    # Halo for angel-like Reds (Jazmyn's hood covers hers).
    if tribe == "Reds" and not is_jazmyn:
        parts.append("<ellipse cx='60' cy='20' rx='15' ry='4.5' fill='none' "
                     "stroke='#FFC93B' stroke-width='3'/>")
    # Crown for gods; each god's accent makes it distinct.
    if is_god:
        parts.append("<polygon points='42,30 48,19 54,28 60,17 66,28 72,19 "
                     "78,30 78,34 42,34' fill='%s' stroke='#8A6D00'/>"
                     % accent)
        parts.append("<circle cx='60' cy='27' r='2' fill='#FFFFFF'/>")
    # Jazmyn's wand with a fire-red tip.
    if is_jazmyn:
        parts.append("<line x1='88' y1='114' x2='98' y2='72' stroke='#6B4423' "
                     "stroke-width='3'/>")
        parts.append("<circle cx='98' cy='69' r='6' fill='#FF3B1F'/>")
        parts.append("<circle cx='98' cy='69' r='2.5' fill='#FF9800'/>")
    # The hoodie's own back design, from its stored pattern.
    parts.append(_motif(pattern, "#FFFFFF" if hoodie != "#FFFFFF" else "#888888",
                        hoodie))
    # Rhinestones on the hoodie: gods 15, leaders 10, villagers 5.
    # Villagers keep their per-certificate shuffled slots; leaders and
    # gods get full rows so the count is exact.
    is_leader = is_damiana or is_aaliyah or is_jazmyn
    if is_god:
        rows = [(38, 88), (50, 88), (62, 88), (74, 88), (86, 88),
                (38, 106), (50, 106), (62, 106), (74, 106), (86, 106),
                (38, 124), (50, 124), (62, 124), (74, 124), (86, 124)]
    elif is_leader:
        rows = [(38, 88), (50, 88), (62, 88), (74, 88), (86, 88),
                (38, 106), (50, 106), (62, 106), (74, 106), (86, 106)]
    else:
        rows = [(42, 94), (60, 90), (78, 94), (48, 114), (72, 114)]
        rnd.shuffle(rows)
    for i, (sx, sy) in enumerate(rows):
        col = stones[i % len(stones)]
        parts.append("<rect x='%d' y='%d' width='7' height='7' fill='%s' "
                     "stroke='#00000033' transform='rotate(45 %d %d)'/>"
                     % (sx - 3, sy - 3, col, sx, sy))
    # Certificate number under the portrait.
    parts.append("<text x='60' y='145' text-anchor='middle' font-size='8.5' "
                 "font-family='sans-serif' fill='#333333'>%s</text>"
                 % esc(cert))
    parts.append("</svg>")
    return "".join(parts)


def realistic_img_file(b):
    """Realistic photo filename for this baby, or None.

    The 40 photographed characters match by name; every Black baby
    (any name) uses the Black Miner photo. Anything else returns
    None and keeps its SVG portrait.
    """
    if _bget(b, "tribe") == "Blacks":
        return "black-miner.jpg"
    return REALISTIC_IMAGES.get(_bget(b, "name"))


def baby_img_html(b, size=120):
    """Photo <img> when a realistic photo exists, else SVG portrait.

    The file must actually be on disk; a missing file falls back to
    the SVG portrait so a card never renders broken.
    """
    fname = realistic_img_file(b)
    if fname:
        full = os.path.join(IMG_DIR, fname)
        if os.path.isfile(full):
            return ("<img src='/img/%s' alt='%s' width='%d' "
                    "style='max-width:100%%;border-radius:12px'>"
                    % (fname, esc(_bget(b, "name")), size))
    return portrait_svg(b, size)


def baby_card(b, owner_name=None, show_owner=False):
    out = ("<div class='card'><div style='text-align:center'>%s</div>"
           "<b>%s</b> %s <span class='tag'>%s</span> " \
           "<span class='tag'>%s</span><br>" % (
               baby_img_html(b), esc(b["name"]),
               gender_marker(_bget(b, "gender", "")),
               esc(b["cert"]), esc(tribe_display(b["tribe"]))))
    out += ("Hoodie: %s<br>Rhinestones: %s<br>Stones: %d<br>Face: %s<br>"
            % (esc(b["hoodie_pattern"]), esc(b["stones"]),
               stone_count(b["tribe"], _bget(b, "cert"),
                           _bget(b, "name")), esc(b["face"])))
    if b["tribe"] == "Blacks":
        out += "Eyes: Diamond<br>"
    if b["is_custom"]:
        out += "<span class='tag'>custom character</span> "
    if b["for_trade"]:
        out += "<span class='tag'>for trade</span> "
    if show_owner and owner_name:
        out += "<br>Owner: %s" % esc(owner_name)
    out += ("<br><a href='/baby/%s'>View baby — zoom &amp; spin</a>"
            % esc(b["cert"]))
    return out + "</div>"


# ---------------------------------------------------------------- baby detail viewer
# One baby, blown up: a fullscreen-zoomable front view plus a
# drag-to-spin viewer. HONEST LIMIT: each baby has only one photo
# today, so a true photo spin is not possible yet. The spin therefore
# has two views — the front photo (or SVG portrait) and a "Back /
# details" card with the exact back-design specs (pattern, stone
# colors and count, tribe, certificate). The back view is labeled as
# a details card, never presented as a photo. Views are a list, so
# real side / back photos can be appended later without rework. No
# AI-generated side/back angles: they would not match the front
# exactly, which would misrepresent a 1-of-1 baby.
MAKE_IT_REAL_NOTE = (
    "1-of-1 — when physical figures launch, this exact character "
    "(pattern, stones, colors, certificate) can be sent in to be made "
    "real, exactly as it appears online.")


def resolve_baby(con, cert):
    """Find one character by certificate: an owned baby first, then a
    shop listing (individual, pop-up, leader, god). Returns a plain
    dict of the baby's attributes, or None."""
    row = con.execute(
        "SELECT * FROM babies WHERE cert=?", (cert,)).fetchone()
    if row:
        return dict(row)
    for table in ("shop_items", "popup_items", "leaders"):
        row = con.execute(
            "SELECT * FROM %s WHERE cert=?" % table, (cert,)).fetchone()
        if row:
            return dict(row)
    row = con.execute(
        "SELECT * FROM gods WHERE cert=?", (cert,)).fetchone()
    if row:
        return {"name": row["name"], "cert": row["cert"],
                "tribe": "Gods", "hoodie_pattern": "God Crown",
                "stones": "gold rhinestones", "face": "grand",
                "gender": "male", "is_custom": 0, "for_trade": 0}
    return None


def baby_detail_views(b):
    """Spin-viewer views for one baby, in order. Each view is
    (view_id, label, html). Appending a real side/back photo later is
    one more tuple — no viewer rework needed."""
    front = baby_img_html(b, 320)
    stones = _stone_colors(_bget(b, "stones"), _bget(b, "tribe"))
    dots = "".join(
        "<span style='display:inline-block;width:18px;height:18px;"
        "border-radius:50%%;background:%s;border:1px solid #999;"
        "margin-right:4px'></span>" % c for c in stones)
    back = (
        "<div class='card'><h2>Back view — details card</h2>"
        "<p>This is not a photo of the back yet — each baby has one "
        "photo today. This card records the exact back-design specs, "
        "so the 1-of-1 design is on record until side and back photos "
        "are added here.</p>"
        "<p>Hoodie pattern: <b>%s</b><br>"
        "Rhinestones: %s<br>Stone colors: %s<br>"
        "Stones on hoodie: <b>%d</b><br>"
        "Tribe: <b>%s</b><br>Face: %s<br>"
        "Certificate (1-of-1): <b>%s</b></p></div>" % (
            esc(_bget(b, "hoodie_pattern")), esc(_bget(b, "stones")),
            dots,
            stone_count(_bget(b, "tribe"), _bget(b, "cert"),
                        _bget(b, "name")),
            esc(tribe_display(_bget(b, "tribe"))),
            esc(_bget(b, "face")), esc(_bget(b, "cert"))))
    return [("front", "Front view", front),
            ("back", "Back view — details", back)]


def popup_shop_section(con, cert, heading, user):
    """One pop-up listing card for the Shop (drifter, special, rare,
    or Shadow): photo/SVG, tribe, hoodie, stones, price, edition stock
    when there is more than one copy, and both buy buttons. Empty when
    the listing is sold out."""
    item = con.execute(
        "SELECT * FROM popup_items WHERE cert=? AND stock>0",
        (cert,)).fetchone()
    if not item:
        return ""
    stock_line = ""
    if (item["total"] or 1) > 1:
        stock_line = ("<br>Only %d of %d left in this edition"
                      % (item["stock"], item["total"]))
    out = ("<h2>%s</h2><div class='card'><div style='text-align:center'>"
           "%s</div><b>%s</b> %s "
           "<span class='tag'>%s</span> "
           "<span class='tag'>%s</span><br>"
           "Hoodie: %s<br>Rhinestones: %s<br>Stones: %d<br>Face: %s<br>"
           "Price: $%s%s<br><a href='/baby/%s'>View baby — zoom "
           "&amp; spin</a></div>" % (
               heading, baby_img_html(item),
               esc(item["name"]),
               gender_marker(baby_gender(item["tribe"],
                                         item["hoodie_pattern"],
                                         item["stones"],
                                         name=item["name"],
                                         cert=item["cert"])),
               esc(item["cert"]), esc(tribe_display(item["tribe"])),
               esc(item["hoodie_pattern"]), esc(item["stones"]),
               stone_count(item["tribe"], item["cert"], item["name"]),
               esc(item["face"]), esc(item["price"]), stock_line,
               esc(item["cert"])))
    if user:
        out += ("<form method='post' action='/buy_popup'>"
                "<input type='hidden' name='popup_id' "
                "value='%d'><button>Buy for $%s with "
                "PayPal</button></form>"
                "<form method='post' action='/buy_popup_stripe'>"
                "<input type='hidden' name='popup_id' "
                "value='%d'><button>Buy for $%s with "
                "Cash App Pay</button></form>"
                % (item["id"], item["price"],
                   item["id"], item["price"]))
    else:
        out += "<p><a href='/login'>Log in</a> to buy.</p>"
    return out

# ---------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep test output clean
        pass

    # -- plumbing --
    def _cookies(self):
        out = {}
        for part in self.headers.get("Cookie", "").split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out

    def _cookie_flags(self):
        flags = "; HttpOnly; Path=/; SameSite=Lax"
        if COOKIE_SECURE:
            flags += "; Secure"
        return flags

    def _ensure_csrf(self):
        """Double-submit CSRF token: one random token per browser, kept
        in a cookie and echoed into every POST form. The token is new
        for a first-time visitor (so _send_csrf_cookie issues it) and
        stable afterwards."""
        cookies = self._cookies()
        token = cookies.get("csrf_token")
        self._csrf_new = False
        if not token:
            token = secrets.token_hex(32)
            self._csrf_new = True
        self._csrf_token = token
        return token

    def _send_csrf_cookie(self):
        if getattr(self, "_csrf_new", False):
            self.send_header(
                "Set-Cookie",
                "csrf_token=%s%s" % (self._csrf_token,
                                     self._cookie_flags()))

    def send_html(self, body, status=200):
        # Inject the CSRF token into every POST form in one place, so
        # no form can forget it.
        token = getattr(self, "_csrf_token", None)
        if token:
            body = re.sub(
                r"(<form[^>]*method=['\"]post['\"][^>]*>)",
                r"\1<input type='hidden' name='csrf_token' value='%s'>"
                % token, body)
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self._send_csrf_cookie()
        self.end_headers()
        self.wfile.write(data)

    def serve_img(self, name):
        # Realistic character photos live in img/ next to app.py.
        # Only plain lowercase filenames are served: no slashes, no
        # dot segments, so no path can escape the folder.
        if not re.fullmatch(r"[a-z0-9-]+\.jpg", name or ""):
            self.send_html(page("Not found", "<p>Unknown image.</p>",
                                None), 404)
            return
        full = os.path.join(IMG_DIR, name)
        if not os.path.isfile(full):
            self.send_html(page("Not found", "<p>Unknown image.</p>",
                                None), 404)
            return
        with open(full, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, path):
        self.send_response(302)
        self.send_header("Location", path)
        self._send_csrf_cookie()
        self.end_headers()

    def set_session(self, token):
        self.send_response(302)
        self.send_header("Location", "/me")
        self.send_header("Set-Cookie",
                         "session=%s%s" % (token, self._cookie_flags()))
        self._send_csrf_cookie()
        self.end_headers()

    def current_user(self, con):
        cookies = self._cookies()
        token = cookies.get("session")
        if not token:
            return None
        row = con.execute(
            "SELECT * FROM sessions WHERE token=?",
            (token,)).fetchone()
        if not row:
            return None
        # Sessions expire: a session older than SESSION_MAX_AGE_DAYS
        # (default 7 days) is deleted and no longer logs anyone in.
        try:
            created = datetime.datetime.strptime(
                row["created_at"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            created = None
        if created is not None:
            age = datetime.datetime.utcnow() - created
            if age.days >= SESSION_MAX_AGE_DAYS:
                con.execute("DELETE FROM sessions WHERE token=?",
                            (token,))
                con.commit()
                return None
        return con.execute("SELECT * FROM users WHERE id=?",
                           (row["user_id"],)).fetchone()

    def form(self):
        # Parsed once per POST request (do_POST parses first so it can
        # check the CSRF token); actions reuse the cached dict.
        if hasattr(self, "_form"):
            return self._form
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length).decode()
        parsed = urllib.parse.parse_qs(raw)
        self._form = {k: v[0] for k, v in parsed.items()}
        return self._form

    # -- routing --
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        con = db()
        try:
            self._ensure_csrf()
            user = self.current_user(con)
            if path == "/":
                self.home(con, user)
            elif path == "/signup":
                self.signup_form(query)
            elif path == "/login":
                self.login_form()
            elif path == "/logout":
                self.do_logout(con)
            elif path == "/me":
                self.me(con, user, query)
            elif path == "/browse":
                self.browse(con, user)
            elif path == "/offers":
                self.offers(con, user)
            elif path == "/world":
                self.world(con, user)
            elif path == "/notices":
                self.notices(con, user)
            elif path == "/shop":
                self.shop(con, user)
            elif path == "/purchases":
                self.purchases_page(con, user)
            elif path == "/buy_return":
                self.buy_return(con, user, query)
            elif path == "/buy_cancel":
                self.buy_cancel(con, user, query)
            elif path == "/stripe_return":
                self.stripe_return(con, user, query)
            elif path == "/stripe_cancel":
                self.stripe_cancel(con, user, query)
            elif path == "/create":
                self.create_form(con, user)
            elif path == "/choice_baby":
                self.choice_baby_form(con, user)
            elif path.startswith("/baby/"):
                self.baby_detail(con, user, path[6:])
            elif path.startswith("/img/"):
                self.serve_img(path[5:])
            else:
                self.send_html(page("Not found", "<p>Unknown page.</p>",
                                    user), 404)
        finally:
            con.close()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        con = db()
        try:
            self._ensure_csrf()
            self._form = self.form()
            # CSRF check: the submitted token must match the cookie
            # token. A missing or wrong token is rejected before any
            # action runs, so a forged cross-site POST can do nothing.
            cookie_token = self._cookies().get("csrf_token")
            if not cookie_token or \
                    self._form.get("csrf_token") != cookie_token:
                self.send_html(page(
                    "Forbidden",
                    "<p class='err'>Security check failed — please reload "
                    "the page and try again.</p>", None), 403)
                return
            user = self.current_user(con)
            if path == "/signup":
                self.do_signup(con)
            elif path == "/signup_step2":
                self.do_signup_step2(con)
            elif path == "/login":
                self.do_login(con)
            elif path == "/toggle_trade":
                self.do_toggle(con, user)
            elif path == "/buy":
                self.do_buy(con, user)
            elif path == "/buy_stripe":
                self.do_buy_stripe(con, user)
            elif path == "/buy_leader":
                self.do_buy_leader(con, user)
            elif path == "/buy_leader_stripe":
                self.do_buy_leader_stripe(con, user)
            elif path == "/buy_god":
                self.do_buy_god(con, user)
            elif path == "/buy_god_stripe":
                self.do_buy_god_stripe(con, user)
            elif path == "/buy_points":
                self.do_buy_points(con, user)
            elif path == "/buy_points_stripe":
                self.do_buy_points_stripe(con, user)
            elif path == "/buy_popup":
                self.do_buy_popup(con, user)
            elif path == "/buy_popup_stripe":
                self.do_buy_popup_stripe(con, user)
            elif path == "/claim_gift":
                self.do_claim_gift(con, user)
            elif path == "/choice_baby":
                self.do_choice_baby(con, user)
            elif path == "/offer":
                self.do_offer(con, user)
            elif path == "/offer_action":
                self.do_offer_action(con, user)
            elif path == "/create_character":
                self.do_create(con, user)
            else:
                self.send_html(page("Not found", "<p>Unknown action.</p>",
                                    user), 404)
        finally:
            con.close()

    def need_login(self, user):
        if not user:
            self.redirect("/login")
            return True
        return False

    # -- pages --
    def home(self, con, user):
        body = """<p>Welcome to <b>Hoodie Babies Trade</b> — a trading
        platform for small 3-inch hoodie babies. Every hoodie is 1-of-1:
        no two are the same, and that's what makes the trade better.</p>
        <p>Join and you get <b>one free hoodie baby</b> and
        <b>600 points</b>. You pick the tribe you join
        (Waterliewaveminans, Fortress Rooted, or Windyworrors), then
        choose your free character from that tribe and
        name it yourself. Every completed trade is worth 20 points, and
        every 300 points earned unlocks adding your own character.</p>"""
        if user:
            body += "<p><a href='/me'>Go to my collection</a></p>"
            month, bought, claimed = monthly_gift_state(con, user["id"])
            if not claimed and bought:
                body += ("<div class='card'><b>Monthly gift — "
                         "available!</b> You bought something this month, "
                         "so you can claim one free hoodie baby."
                         "<form method='post' action='/claim_gift'>"
                         "<button>Claim my monthly gift</button></form>"
                         "</div>")
            elif not claimed:
                body += ("<div class='card'>Monthly gift: buy something "
                         "in the <a href='/shop'>Shop</a> this month and "
                         "you can claim one free hoodie baby.</div>")
            else:
                body += ("<div class='card'>Monthly gift claimed for "
                         "%s — your next one comes next month.</div>"
                         % esc(month))
            avail, done, earned, nclaimed = choice_progress(con,
                                                            user["id"])
            if avail > 0:
                body += ("<div class='card'><b>You have %d choice "
                         "hoodie %s to claim!</b> Every 5 shop purchases "
                         "earns one — pick yours "
                         "<a href='/choice_baby'>here</a>.</div>"
                         % (avail, "baby" if avail == 1 else "babies"))
            else:
                body += ("<div class='card'>Choice babies: %d shop "
                         "purchases so far — %d more until your next "
                         "choice baby.</div>"
                         % (done, 5 - (done % 5)))
        else:
            body += ("<p><a href='/signup'>Sign up</a> or "
                     "<a href='/login'>log in</a> to start trading.</p>")
        body += ("<p>Prices: leaders $%s (buy in the "
                 "<a href='/shop'>Shop</a> — choose 1 of the 3 leaders, "
                 "each unique), individual characters $%s, gods $%s "
                 "(only %d gods, and their trades are almost impossible). "
                 "Everyone is free to purchase a leader. Points packs: "
                 "300 for $4.99, 600 for $9.99, 1,200 for "
                 "$20.00 — also in the Shop. Checkout runs in test mode "
                 "with PayPal or Cash App Pay — no real money moves.</p>"
                 "<p>Trade slots: at most 1 pending offer at once for a "
                 "regular trader, 2 for a leader owner, 5 for a god "
                 "owner. Perks follow current ownership.</p>"
                 "<p>Collections: <b>First Beginnings</b> (current). Summer, "
                 "Fall, and Winter collections are coming.</p>"
                 "<p><a href='/notices'>Notices</a> — see what's new and "
                 "where it was found.</p>" % (
                     PRICE_LEADER, PRICE_INDIVIDUAL, PRICE_GOD, GOD_COUNT))
        self.send_html(page("Hoodie Babies Trade", body, user))

    def world(self, con, user):
        ensure_weekly_popups(con)
        standings = ""
        for tribe, leader in (("Blues", "Damiana June"),
                              ("Browns", "Aaliyah Bee"),
                              ("Reds", "Jazmyn")):
            owner, second, third = tribe_standings(con, tribe)
            standings += ("<div class='card'><b>%s tribe standings</b>"
                          "<br>Owner: %s<br>Second: %s<br>Third: %s</div>"
                          % (esc(tribe_display(tribe)),
                             esc(owner) if owner else
                             "none yet — buy %s in the Shop" % leader,
                             esc(second) if second else "—",
                             esc(third) if third else "—"))
        standings += ("<div class='card'>Whoever buys a tribe's leader "
                      "owns that tribe — Damiana June owns the "
                      "Waterliewaveminans, Aaliyah Bee owns the Fortress "
                      "Rooted, Jazmyn owns the Windyworrors. Trade the "
                      "leader away and ownership passes to the new "
                      "holder. Second and Third are the two other "
                      "holders with the most babies of that tribe — "
                      "titles only, no extra perks.</div>")
        body = """
        <div class='card'><b>Waterliewaveminans — rulers over water</b><br>
        Led by <b>Damiana June</b>, Controller of Water. Blue slanted eyes,
        fish-like face with a surprised crying look. The rare one of her
        tribe — purchasable when the platform goes live. The
        Waterliewaveminans run the sea and have only five characters, not
        including her: Marina, Coral, Pearl, Tide, and Kai. They carry sea
        and ocean names, and their faces are fish-like.</div>
        <div class='card'><b>Fortress Rooted — rulers of the forest</b><br>
        Led by <b>Aaliyah Bee</b>, ruler of the woods, keeper of the woods
        and animals, with a red stone in the middle of her forehead.
        Fortress Rooted have a more animal-like face, and Aaliyah Bee's is
        animal-looking and grouchy.</div>
        <div class='card'><b>Windyworrors — rulers of the sky</b><br>
        Led by <b>Jazmyn</b>, ruler of the sky. Although she leads the
        Windyworrors, she is more powerful than the other controllers.
        Glowing diamond head, black eyes, all-black hoodie with stones of
        every different color, hoodie over her face, wand with a fire-red
        tip. Windyworrors have more angel-like faces, and Jazmyn's is
        angel-looking with a slight curious smile. Jazmyn is the elder of
        them all, the first hoodie baby to arrive.</div>
        <div class='card'><b>Shadows — the protectors</b><br>
        The Shadows have no leader: they are protectors, underground-raised,
        and they just pop up out of anywhere. They mean no harm, but they
        are out to get what they can to go back underground. All Shadows
        carry Diamond eyes and all-white hoodies with black stones, and
        their faces are angry creature-like. Once a week, one Shadow pops
        up in the Shop for $10.00. Trading for a Shadow between players
        starts at 1,200 points.</div>
        <div class='card'><b>The 3 Gods — over everyone</b><br>
        There are only 3 gods, and they are over everyone. Purchase:
        $40.00 each — buy one in the <a href='/shop'>Shop</a> with
        PayPal or Cash App Pay (test mode, no real money moves).
        Their trades are almost impossible — but don't get discouraged:
        more gods will arrive over new tribes.</div>
        <div class='card'><b>Stones on every hoodie</b><br>
        Gods' hoodies carry 15 rhinestones, leaders' hoodies carry 10,
        and every villager's hoodie carries 5 — drifters, specials,
        rares, and Shadows included. The colors stay the same; only the
        counts are fixed.</div>
        <div class='card'><b>Prices</b><br>
        Leaders: $10.00 to purchase, and everyone is free to purchase a
        leader — buy one in the <a href='/shop'>Shop</a>, choosing 1 of
        the 3 leaders (Damiana June, Aaliyah Bee, Jazmyn); each leader
        is unique, so once one is sold it is gone. Individual
        characters: $1.99 to purchase — buy one in the
        <a href='/shop'>Shop</a> with PayPal or Cash App Pay (test
        mode, no real money moves). Gods: $40.00 to purchase — buy one
        of the 3 god slots in the <a href='/shop'>Shop</a>.</div>
        <div class='card'><b>Trade slots</b><br>
        A regular trader can have at most 1 pending offer at once.
        Owning a leader raises that to 2 trades at once; owning a god
        raises it to 5 trades at once. The perk follows current
        ownership — trade away your leader or god and the cap drops
        back.</div>
        <div class='card'><b>Collections</b><br>
        The current babies are the First Beginnings. Summer, Fall, and
        Winter collections are coming. As characters are added, everyone
        gets a notice that they are available and where they were found —
        see the <a href='/notices'>Notices</a> board.</div>
        <p>These leaders are the founders of a new world.</p>""" + standings
        self.send_html(page("The World of Hoodie Babies", body, user))

    def notices(self, con, user):
        rows = con.execute(
            "SELECT * FROM notices ORDER BY id DESC").fetchall()
        body = ("<p>Whenever a character is added, everyone gets a notice "
                "that it is available and where it was found.</p>")
        for n in rows:
            body += "<div class='card'>%s</div>" % esc(n["text"])
        self.send_html(page("Notices", body, user))

    def signup_form(self, query, msg=""):
        invite = esc(query.get("invite", [""])[0])
        body = msg + """<form method='post' action='/signup'>
        Username: <input name='username' required><br>
        Password: <input type='password' name='password' required><br>
        Pick your tribe:<br>
        <label><input type='radio' name='tribe' value='Blues' required>
        Waterliewaveminans — rulers over water</label><br>
        <label><input type='radio' name='tribe' value='Browns' required>
        Fortress Rooted — rulers of the forest</label><br>
        <label><input type='radio' name='tribe' value='Reds' required>
        Windyworrors — rulers of the sky</label><br>
        Invite code (optional): <input name='invite_code' value='%s'><br>
        <button>Continue</button></form>
        <p>Every new account gets one free hoodie baby and 600 points.
        On the next step you choose your free character from your
        tribe and name it yourself. Shadows are not a starter tribe —
        they are the protectors, found through pop-ups in the Shop.</p>
        """ % invite
        self.send_html(page("Sign up", body))

    def signup_step2_form(self, pending):
        """Step 2 of signup: pick one starter character from the chosen
        tribe's roster and name it. The name field starts prefilled
        with the roster name and can be overwritten; leaving it blank
        keeps the roster name."""
        tribe = pending["tribe"]
        names = STARTERS[tribe]["names"]
        opts = "".join("<option value='%s'>%s</option>"
                       % (esc(n), esc(n)) for n in names)
        body = """<p>You joined the <b>%s</b>. Now choose your free
        hoodie baby and name it.</p>
        <form method='post' action='/signup_step2'>
        <input type='hidden' name='signup_token' value='%s'>
        Character: <select name='roster_name' id='roster'>%s</select><br>
        Name: <input name='baby_name' id='baby_name'
        value='%s' maxlength='40'><br>
        <button>Create my account</button></form>
        <script>document.getElementById('roster').addEventListener(
        'change', function () { document.getElementById('baby_name')
        .value = this.value; });</script>
        <p>Your free baby comes with your tribe's hoodie pattern,
        rhinestones, and face — and your account starts with
        600 points.</p>
        """ % (esc(tribe_display(tribe)), esc(pending["token"]), opts,
               esc(names[0]))
        self.send_html(page("Choose your hoodie baby", body))

    def login_form(self, msg=""):
        body = msg + """<form method='post' action='/login'>
        Username: <input name='username' required><br>
        Password: <input type='password' name='password' required><br>
        <button>Log in</button></form>"""
        self.send_html(page("Log in", body))

    def do_logout(self, con):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("session="):
                con.execute("DELETE FROM sessions WHERE token=?",
                            (part[len("session="):],))
                con.commit()
        self.send_response(302)
        self.send_header("Location", "/")
        self.send_header("Set-Cookie",
                         "session=%s; Max-Age=0" % self._cookie_flags())
        self._send_csrf_cookie()
        self.end_headers()

    # -- auth actions --
    # Signup is two steps. Step 1 collects username, password, tribe,
    # and invite code and stores them as a pending signup — no account
    # exists yet. Step 2 picks the free character and its name, and
    # only then is the account created. Abandoning step 2 creates
    # nothing; pending signups expire after 30 minutes.
    PENDING_SIGNUP_TTL = "30 minutes"

    def do_signup(self, con):
        f = self.form()
        key = "signup:" + self.client_address[0]
        if rate_blocked(key):
            self.send_html(page(
                "Too many attempts",
                "<p class='err'>Too many signup attempts from this "
                "address — please wait a few minutes and try again.</p>"),
                429)
            return
        username = (f.get("username") or "").strip()
        password = f.get("password") or ""
        tribe = (f.get("tribe") or "").strip()
        invite = (f.get("invite_code") or "").strip()
        if not username or not password:
            rate_record_fail(key)
            self.signup_form(
                {}, "<p class='err'>Username and password are "
                "required.</p>")
            return
        if tribe not in STARTERS:
            # STARTERS holds exactly the joinable tribes (Blues, Browns,
            # Reds). Shadows are the protectors and are not joinable.
            rate_record_fail(key)
            self.signup_form(
                {}, "<p class='err'>Please pick a tribe to join: "
                "Waterliewaveminans, Fortress Rooted, or Windyworrors."
                "</p>")
            return
        if con.execute("SELECT 1 FROM users WHERE username=?",
                       (username,)).fetchone():
            rate_record_fail(key)
            self.signup_form(
                {}, "<p class='err'>That username is taken.</p>")
            return
        invited_by = None
        if invite:
            row = con.execute("SELECT id FROM users WHERE invite_code=?",
                              (invite,)).fetchone()
            if row:
                invited_by = row["id"]
        salt = secrets.token_hex(16)
        token = secrets.token_hex(32)
        con.execute(
            "INSERT INTO pending_signups (token, username, password_hash,"
            " salt, tribe, invite_code, invited_by) VALUES (?,?,?,?,?,?,?)",
            (token, username, hash_password(password, salt), salt, tribe,
             invite or None, invited_by))
        con.commit()
        pending = con.execute(
            "SELECT * FROM pending_signups WHERE token=?",
            (token,)).fetchone()
        self.signup_step2_form(pending)

    def do_signup_step2(self, con):
        f = self.form()
        key = "signup:" + self.client_address[0]
        if rate_blocked(key):
            self.send_html(page(
                "Too many attempts",
                "<p class='err'>Too many signup attempts from this "
                "address — please wait a few minutes and try again.</p>"),
                429)
            return
        # Drop expired pending signups first (30 minutes, UTC).
        con.execute("DELETE FROM pending_signups WHERE created_at < "
                    "datetime('now', '-" + self.PENDING_SIGNUP_TTL + "')")
        con.commit()
        token = f.get("signup_token") or ""
        pending = con.execute(
            "SELECT * FROM pending_signups WHERE token=?",
            (token,)).fetchone()
        if not pending:
            rate_record_fail(key)
            self.send_html(page(
                "Sign up",
                "<p class='err'>This signup step expired or is invalid — "
                "please <a href='/signup'>sign up again</a>.</p>"))
            return
        tribe = pending["tribe"]
        roster_name = (f.get("roster_name") or "").strip()
        if roster_name not in STARTERS[tribe]["names"]:
            rate_record_fail(key)
            self.send_html(page(
                "Sign up",
                "<p class='err'>Please pick one of your tribe's starter "
                "characters.</p>"))
            return
        # Blank keeps the roster name; anything else is the user's own
        # name for their free baby, capped at 40 characters.
        baby_name = (f.get("baby_name") or "").strip()[:40] or roster_name
        username = pending["username"]
        if con.execute("SELECT 1 FROM users WHERE username=?",
                       (username,)).fetchone():
            rate_record_fail(key)
            con.execute("DELETE FROM pending_signups WHERE token=?",
                        (token,))
            con.commit()
            self.signup_form(
                {}, "<p class='err'>That username is taken.</p>")
            return
        code = new_invite_code(con)
        # Points are set explicitly (not left to the column default) so
        # new signups get 600 even on databases created before the
        # starting-points change. Starting points never count toward
        # the character-creator unlock.
        cur = con.execute(
            "INSERT INTO users (username, password_hash, salt, invite_code,"
            " invited_by, points) VALUES (?,?,?,?,?,?)",
            (username, pending["password_hash"], pending["salt"], code,
             pending["invited_by"], STARTING_POINTS))
        user_id = cur.lastrowid
        # The free baby is the chosen character with the chosen name,
        # using the tribe's default pattern, rhinestones, and face.
        mint_baby(con, user_id, tribe=tribe, name=baby_name)
        # Invite bonus: the inviter gets +20 points, balance AND earned
        # (invite bonuses are earned, not bought).
        if pending["invited_by"]:
            con.execute(
                "UPDATE users SET points=points+?, points_earned="
                "points_earned+? WHERE id=?",
                (INVITE_BONUS, INVITE_BONUS, pending["invited_by"]))
        con.execute("DELETE FROM pending_signups WHERE token=?",
                    (token,))
        con.commit()
        token = secrets.token_hex(32)
        con.execute("INSERT INTO sessions (token, user_id) VALUES (?,?)",
                    (token, user_id))
        con.commit()
        rate_clear(key)
        self.set_session(token)

    def do_login(self, con):
        f = self.form()
        key = "login:" + self.client_address[0]
        if rate_blocked(key):
            self.send_html(page(
                "Too many attempts",
                "<p class='err'>Too many login attempts from this "
                "address — please wait a few minutes and try again.</p>"),
                429)
            return
        row = con.execute("SELECT * FROM users WHERE username=?",
                          (f.get("username", ""),)).fetchone()
        if not row or row["password_hash"] != hash_password(
                f.get("password", ""), row["salt"]):
            rate_record_fail(key)
            self.login_form("<p class='err'>Wrong username or password.</p>")
            return
        rate_clear(key)
        token = secrets.token_hex(32)
        con.execute("INSERT INTO sessions (token, user_id) VALUES (?,?)",
                    (token, row["id"]))
        con.commit()
        self.set_session(token)

    # -- collection / trade board --
    def baby_detail(self, con, user, cert):
        """One baby, blown up: a fullscreen-zoomable front view, a
        drag-to-spin viewer (front photo + back-details card until
        real side/back photos exist), specs, and the Make-it-real
        note. Public — anyone with the certificate link can view the
        character; no owner info is shown."""
        if not re.fullmatch(r"[A-Za-z0-9-]+", cert or ""):
            self.send_html(page("Not found", "<p>Unknown baby.</p>",
                                user), 404)
            return
        b = resolve_baby(con, cert)
        if not b:
            self.send_html(page("Not found", "<p>Unknown baby.</p>",
                                user), 404)
            return
        views = baby_detail_views(b)
        view_divs = "".join(
            "<div class='spin-view' data-label='%s'>%s</div>"
            % (esc(label), html) for _vid, label, html in views)
        dots = "".join(
            "<span class='spin-dot' data-i='%d' "
            "style='cursor:pointer'>●</span> " % i
            for i in range(len(views)))
        body = (
            "<p><a href='/me'>My Collection</a> | "
            "<a href='/shop'>Shop</a> | "
            "<a href='/browse'>Trade Board</a></p>"
            "<div id='spin-stage' style='touch-action:pan-y;"
            "text-align:center'>%s</div>"
            "<p style='text-align:center'>"
            "<button id='spin-prev' type='button'>◀ Turn</button> "
            "<span id='spin-label'>%s</span> "
            "<button id='spin-next' type='button'>Turn ▶</button>"
            "<br>%s</p>"
            "<p style='text-align:center'>"
            "<button id='fs-open' type='button'>Blow it up "
            "fullscreen</button></p>"
            "<p>Drag left or right on the picture to spin it around. "
            "Tap the front picture (or the button above) to blow it "
            "up fullscreen — pinch to zoom on your phone or tablet, "
            "or use the + / − buttons.</p>"
            "<div id='fs-overlay' style='display:none;position:fixed;"
            "inset:0;background:#000;z-index:9999;overflow:auto;"
            "text-align:center'>"
            "<div id='fs-wrap' style='display:inline-block'>%s</div>"
            "<p><button id='fs-zoom-in' type='button'>+</button> "
            "<button id='fs-zoom-out' type='button'>−</button> "
            "<button id='fs-close' type='button'>Close</button></p>"
            "</div>"
            "<p>Hoodie: %s<br>Rhinestones: %s<br>Stones: %d<br>"
            "Tribe: %s<br>Face: %s<br>Certificate: %s</p>"
            "<div class='card'><b>Make it real</b><br>%s<br>"
            "<small>This is just a note for now — there is nothing "
            "to order or pay for yet; figures are not released "
            "yet.</small></div>"
            % (view_divs, esc(views[0][1]), dots,
               baby_img_html(b, 640),
               esc(_bget(b, "hoodie_pattern")), esc(_bget(b, "stones")),
               stone_count(_bget(b, "tribe"), _bget(b, "cert"),
                           _bget(b, "name")),
               esc(tribe_display(_bget(b, "tribe"))),
               esc(_bget(b, "face")), esc(_bget(b, "cert")),
               esc(MAKE_IT_REAL_NOTE)))
        body += (
            "<script>(function(){"
            "var stage=document.getElementById('spin-stage');"
            "if(!stage) return;"
            "var views=stage.querySelectorAll('.spin-view');"
            "var label=document.getElementById('spin-label');"
            "var dots=document.querySelectorAll('.spin-dot');"
            "var n=views.length, angle=0, startX=null, dragged=false;"
            "function show(){"
            "var idx=((Math.floor(angle/(360/n))%n)+n)%n;"
            "for(var i=0;i<n;i++){"
            "views[i].style.display=(i===idx)?'block':'none';}"
            "if(label) label.textContent="
            "views[idx].getAttribute('data-label');"
            "for(var j=0;j<dots.length;j++){"
            "dots[j].style.opacity=(j===idx)?'1':'0.4';}}"
            "function step(d){ angle+=d*(360/n); show(); }"
            "document.getElementById('spin-prev').onclick="
            "function(){ step(-1); };"
            "document.getElementById('spin-next').onclick="
            "function(){ step(1); };"
            "for(var k=0;k<dots.length;k++){"
            "(function(k){ dots[k].onclick=function(){"
            "angle=k*(360/n); show(); }; })(k);}"
            "stage.addEventListener('pointerdown',"
            "function(e){ startX=e.clientX; dragged=false; });"
            "stage.addEventListener('pointermove',"
            "function(e){ if(startX===null) return;"
            "var dx=e.clientX-startX;"
            "if(Math.abs(dx)>8){ dragged=true; angle+=dx*0.5;"
            "startX=e.clientX; show(); } });"
            "addEventListener('pointerup',"
            "function(){ startX=null; });"
            "stage.style.cursor='grab';"
            "var ov=document.getElementById('fs-overlay');"
            "var wrap=document.getElementById('fs-wrap');"
            "var scale=1;"
            "function openFs(){ ov.style.display='block'; scale=1;"
            "wrap.style.transform='scale(1)'; }"
            "var front=views[0];"
            "if(front) front.addEventListener('click',"
            "function(){ if(dragged){ dragged=false; return; }"
            "openFs(); });"
            "document.getElementById('fs-open').onclick=openFs;"
            "document.getElementById('fs-close').onclick="
            "function(){ ov.style.display='none'; };"
            "ov.addEventListener('click',function(e){"
            "if(e.target===ov) ov.style.display='none'; });"
            "document.getElementById('fs-zoom-in').onclick="
            "function(e){ e.stopPropagation();"
            "scale=Math.min(4,scale+0.5);"
            "wrap.style.transform='scale('+scale+')'; };"
            "document.getElementById('fs-zoom-out').onclick="
            "function(e){ e.stopPropagation();"
            "scale=Math.max(1,scale-0.5);"
            "wrap.style.transform='scale('+scale+')'; };"
            "show();"
            "})();</script>")
        self.send_html(page("%s — hoodie baby" % _bget(b, "name"),
                             body, user))

    def me(self, con, user, query):
        if self.need_login(user):
            return
        user = con.execute("SELECT * FROM users WHERE id=?",
                           (user["id"],)).fetchone()
        babies = con.execute("SELECT * FROM babies WHERE owner_id=? "
                             "ORDER BY id", (user["id"],)).fetchall()
        slots = creator_slots_available(user)
        body = ("<p>Points: <b>%d</b> | Points earned: <b>%d</b> | "
                "Character creator: %s</p>" % (
                    user["points"], user["points_earned"],
                    ("<b>UNLOCKED — <a href='/create'>add your own "
                     "character</a></b>" if slots >= 1
                     else "%d / 300 points earned" % user["points_earned"])))
        # My Village: every baby owned except leaders counts, and the
        # village is complete at 40. An owned leader is shown with it.
        vcount, vleader_names = village_progress(con, user["id"])
        body += ("<p><b>My Village: %d / %d</b>%s</p>" % (
            vcount, VILLAGE_SIZE,
            " — <b>Village complete!</b>" if vcount >= VILLAGE_SIZE
            else ""))
        if vleader_names:
            body += ("<p>Village leader: %s</p>"
                     % esc(", ".join(vleader_names)))
        invites = con.execute(
            "SELECT COUNT(*) FROM users WHERE invited_by=?",
            (user["id"],)).fetchone()[0]
        if invites:
            body += ("<p>Invite bonus: +20 points × %d invite%s — "
                     "friends who joined with your code.</p>"
                     % (invites, "s" if invites != 1 else ""))
        body += ("<p>Your invite code: <b>%s</b><br>Share this link so "
                 "someone can join and trade with you:<br>"
                 "<code>/signup?invite=%s</code></p>" % (
                     esc(user["invite_code"]), esc(user["invite_code"])))
        if query.get("msg"):
            body += "<p class='ok'>%s</p>" % esc(query["msg"][0])
        owned = user_owned_tribes(con, user["id"])
        for tribe in owned:
            body += ("<p><b>You are the Owner of the %s tribe!</b> "
                     "Buying that tribe's leader made you its owner — "
                     "if you trade the leader away, ownership passes "
                     "to the new holder.</p>" % esc(tribe_display(tribe)))
        body += "<h2>My hoodie babies (%d)</h2>" % len(babies)
        for b in babies:
            body += baby_card(b)
            body += ("<form method='post' action='/toggle_trade'>"
                     "<input type='hidden' name='baby_id' value='%d'>"
                     "<button>%s</button></form>"
                     % (b["id"], "Remove from trade board"
                        if b["for_trade"] else "Put up for trade"))
        self.send_html(page("My Collection", body, user))

    def do_toggle(self, con, user):
        if self.need_login(user):
            return
        f = self.form()
        b = con.execute("SELECT * FROM babies WHERE id=? AND owner_id=?",
                        (f.get("baby_id"), user["id"])).fetchone()
        if b:
            con.execute("UPDATE babies SET for_trade=? WHERE id=?",
                        (0 if b["for_trade"] else 1, b["id"]))
            con.commit()
        self.redirect("/me")

    def browse(self, con, user):
        if self.need_login(user):
            return
        mine = con.execute("SELECT * FROM babies WHERE owner_id=?",
                           (user["id"],)).fetchall()
        rows = con.execute(
            "SELECT babies.*, users.username AS owner FROM babies "
            "JOIN users ON users.id = babies.owner_id "
            "WHERE babies.for_trade=1 AND babies.owner_id != ? ORDER BY id",
            (user["id"],)).fetchall()
        cap = trade_cap(con, user["id"])
        body = ("<p>Tap a baby to make an offer for it. Your trade "
                "slots: at most <b>%d</b> pending offer(s) at once "
                "(regular 1, leader owner 2, god owner 5).</p>" % cap)
        if not rows:
            body += "<p>No babies are up for trade right now.</p>"
        for b in rows:
            body += baby_card(b, b["owner"], show_owner=True)
            if b["tribe"] == "Blacks":
                body += ("<p><b>Trading for a Shadow starts at 1,200 "
                         "points.</b></p>")
            opts = "".join(
                "<option value='%d'>%s (%s)</option>"
                % (m["id"], esc(m["name"]), esc(m["cert"])) for m in mine)
            body += ("""<form method='post' action='/offer'>
            <input type='hidden' name='wanted_baby_id' value='%d'>
            Your baby (optional): <select name='offered_baby_id'>
            <option value=''>points only</option>%s</select><br>
            Points to offer: <input type='number' name='offered_points'
            value='0' min='0'><br><button>Send offer</button></form>"""
                     % (b["id"], opts))
        self.send_html(page("Trade Board", body, user))

    def do_offer(self, con, user):
        if self.need_login(user):
            return
        f = self.form()
        wanted = con.execute(
            "SELECT * FROM babies WHERE id=? AND for_trade=1 "
            "AND owner_id != ?", (f.get("wanted_baby_id"), user["id"])).fetchone()
        if not wanted:
            self.send_html(page("Trade Board",
                                 "<p class='err'>That baby is not up for "
                                 "trade.</p>", user))
            return
        if wanted["tribe"] == "Blacks":
            fresh = con.execute("SELECT points FROM users WHERE id=?",
                                (user["id"],)).fetchone()
            if fresh["points"] < BLACK_TRADE_MIN_POINTS:
                self.send_html(page("Trade Board",
                                     "<p class='err'>Trading for a Shadow "
                                     "starts at 1,200 points — you have %d."
                                     "</p>" % fresh["points"], user))
                return
        offered_baby = None
        if f.get("offered_baby_id"):
            offered_baby = con.execute(
                "SELECT * FROM babies WHERE id=? AND owner_id=?",
                (f.get("offered_baby_id"), user["id"])).fetchone()
            if not offered_baby:
                self.send_html(page("Trade Board",
                                     "<p class='err'>That baby is not "
                                     "yours.</p>", user))
                return
        try:
            pts = int(f.get("offered_points") or 0)
        except ValueError:
            pts = 0
        if pts < 0 or pts > user["points"]:
            self.send_html(page("Trade Board",
                                 "<p class='err'>You don't have that many "
                                 "points.</p>", user))
            return
        # Trade slots: at most 1 pending outgoing offer for a regular
        # trader, 2 for a leader owner, 5 for a god owner.
        cap = trade_cap(con, user["id"])
        pending = con.execute(
            "SELECT COUNT(*) FROM offers WHERE from_user=? AND status="
            "'pending'", (user["id"],)).fetchone()[0]
        if pending >= cap:
            self.send_html(page("Trade Board",
                                 "<p class='err'>%s</p>"
                                 % cap_error(cap, pending), user))
            return
        con.execute(
            "INSERT INTO offers (from_user, to_user, offered_baby,"
            " offered_points, wanted_baby) VALUES (?,?,?,?,?)",
            (user["id"], wanted["owner_id"],
             offered_baby["id"] if offered_baby else None, pts, wanted["id"]))
        con.commit()
        # Shadow trades between players go through the normal flow
        # (accept / decline / counter) — the 1,200-point gate above is
        # the only special rule, and acceptance still awards 60 points
        # to the buyer instead of the usual 20.
        self.redirect("/offers")

    def offers(self, con, user):
        if self.need_login(user):
            return
        mine = {b["id"]: b for b in
                con.execute("SELECT * FROM babies").fetchall()}
        users = {u["id"]: u["username"] for u in
                 con.execute("SELECT id, username FROM users").fetchall()}

        def offer_block(o, incoming):
            ob = mine.get(o["offered_baby"]) if o["offered_baby"] else None
            wb = mine.get(o["wanted_baby"])
            portraits = ""
            if ob is not None or wb is not None:
                portraits = ("<div style='text-align:center'>%s"
                             "<span style='font-size:20px'> &#8594; </span>"
                             "%s</div>" % (
                                 baby_img_html(ob, 72) if ob else "",
                                 baby_img_html(wb, 72) if wb else ""))
            text = ("<div class='card'>%sFrom <b>%s</b> to <b>%s</b> — "
                    "offers %s + %d points for %s "
                    "(status: %s)</div>" % (
                        portraits,
                        esc(users.get(o["from_user"], "?")),
                        esc(users.get(o["to_user"], "?")),
                        ("%s %s (%s)" % (esc(ob["name"]),
                                          gender_marker(_bget(ob, "gender", "")),
                                          esc(ob["cert"])))
                        if ob else "no baby",
                        o["offered_points"],
                        ("%s %s (%s)" % (esc(wb["name"]),
                                          gender_marker(_bget(wb, "gender", "")),
                                          esc(wb["cert"])))
                        if wb else "?",
                        esc(o["status"])))
            if o["status"] == "pending" and incoming:
                text += ("""<form method='post' action='/offer_action'
                style='display:inline'>
                <input type='hidden' name='offer_id' value='%d'>
                <input type='hidden' name='action' value='accept'>
                <button>Accept</button></form>
                <form method='post' action='/offer_action'
                style='display:inline'>
                <input type='hidden' name='offer_id' value='%d'>
                <input type='hidden' name='action' value='decline'>
                <button>Decline</button></form>""" % (o["id"], o["id"]))
                if ob is None:
                    # counter needs a baby coming back, so offer one of mine
                    pass
                else:
                    text += ("""<form method='post' action='/offer_action'>
                    <input type='hidden' name='offer_id' value='%d'>
                    <input type='hidden' name='action' value='counter'>
                    Counter with my baby:
                    <select name='counter_baby_id'>%s</select>
                    + points: <input type='number' name='counter_points'
                    value='0' min='0'>
                    <button>Send counter</button></form>""" % (
                        o["id"], "".join(
                            "<option value='%d'>%s (%s)</option>" % (
                                m["id"], esc(m["name"]), esc(m["cert"]))
                            for m in con.execute(
                                "SELECT * FROM babies WHERE owner_id=?",
                                (user["id"],)).fetchall())))
            return text

        incoming = con.execute(
            "SELECT * FROM offers WHERE to_user=? ORDER BY id DESC",
            (user["id"],)).fetchall()
        outgoing = con.execute(
            "SELECT * FROM offers WHERE from_user=? ORDER BY id DESC",
            (user["id"],)).fetchall()
        body = "<h2>Incoming offers</h2>"
        body += "".join(offer_block(o, True) for o in incoming) or "<p>None.</p>"
        body += "<h2>Outgoing offers</h2>"
        body += "".join(offer_block(o, False) for o in outgoing) or "<p>None.</p>"
        self.send_html(page("Offers", body, user))

    def do_offer_action(self, con, user):
        if self.need_login(user):
            return
        f = self.form()
        o = con.execute("SELECT * FROM offers WHERE id=? AND to_user=? "
                        "AND status='pending'",
                        (f.get("offer_id"), user["id"])).fetchone()
        if not o:
            self.redirect("/offers")
            return
        action = f.get("action")
        if action == "decline":
            con.execute("UPDATE offers SET status='declined' WHERE id=?",
                        (o["id"],))
            con.commit()
        elif action == "accept":
            self.accept_offer(con, o)
        elif action == "counter":
            cb = con.execute("SELECT * FROM babies WHERE id=? AND owner_id=?",
                             (f.get("counter_baby_id"), user["id"])).fetchone()
            if cb and o["offered_baby"]:
                try:
                    cpts = int(f.get("counter_points") or 0)
                except ValueError:
                    cpts = 0
                if 0 <= cpts <= user["points"]:
                    # A counter is also a new outgoing offer, so the
                    # trade-slot cap applies to it too.
                    cap = trade_cap(con, user["id"])
                    pending = con.execute(
                        "SELECT COUNT(*) FROM offers WHERE from_user=? "
                        "AND status='pending'",
                        (user["id"],)).fetchone()[0]
                    if pending >= cap:
                        self.send_html(page(
                            "Offers",
                            "<p class='err'>%s</p>"
                            % cap_error(cap, pending), user))
                        return
                    con.execute(
                        "UPDATE offers SET status='countered' WHERE id=?",
                        (o["id"],))
                    con.execute(
                        "INSERT INTO offers (from_user, to_user,"
                        " offered_baby, offered_points, wanted_baby,"
                        " counter_of) VALUES (?,?,?,?,?,?)",
                        (user["id"], o["from_user"], cb["id"], cpts,
                         o["offered_baby"], o["id"]))
                    con.commit()
        self.redirect("/offers")

    def accept_offer(self, con, o):
        """Swap the babies, move points, award 20 points to each trader
        (60 points to the buyer when the trade is for a Shadow)."""
        sender = con.execute("SELECT * FROM users WHERE id=?",
                             (o["from_user"],)).fetchone()
        receiver = con.execute("SELECT * FROM users WHERE id=?",
                               (o["to_user"],)).fetchone()
        wanted = con.execute("SELECT * FROM babies WHERE id=?",
                             (o["wanted_baby"],)).fetchone()
        offered = None
        if o["offered_baby"]:
            offered = con.execute("SELECT * FROM babies WHERE id=?",
                                  (o["offered_baby"],)).fetchone()
        if not wanted or wanted["owner_id"] != o["to_user"]:
            return
        if offered and offered["owner_id"] != o["from_user"]:
            return
        if wanted["tribe"] == "Blacks" and \
                sender["points"] < BLACK_TRADE_MIN_POINTS:
            return
        if sender["points"] < o["offered_points"]:
            return
        # swap ownership; traded babies leave the trade board
        con.execute("UPDATE babies SET owner_id=?, for_trade=0 WHERE id=?",
                    (o["from_user"], wanted["id"]))
        if offered:
            con.execute("UPDATE babies SET owner_id=?, for_trade=0 "
                        "WHERE id=?", (o["to_user"], offered["id"]))
        # points: offered points move sender -> receiver, plus 20 each.
        # A trade for a Shadow is worth 60 points to the buyer instead
        # of 20; the Underground system account earns nothing.
        is_black_trade = wanted["tribe"] == "Blacks"
        underground = con.execute(
            "SELECT id FROM users WHERE username='underground'").fetchone()
        is_underground = underground and receiver["id"] == underground["id"]
        buyer_bonus = 60 if is_black_trade else 20
        receiver_bonus = 0 if is_underground else 20
        con.execute(
            "UPDATE users SET points=points-?+%d, points_earned="
            "points_earned+%d WHERE id=?"
            % (buyer_bonus, buyer_bonus),
            (o["offered_points"], sender["id"]))
        con.execute(
            "UPDATE users SET points=points+?+%d, points_earned="
            "points_earned+%d WHERE id=?"
            % (receiver_bonus, receiver_bonus),
            (o["offered_points"], receiver["id"]))
        con.execute("UPDATE offers SET status='accepted' WHERE id=?",
                    (o["id"],))
        # withdraw other pending offers touching the traded babies
        ids = [wanted["id"]] + ([offered["id"]] if offered else [])
        q = ",".join("?" * len(ids))
        con.execute(
            "UPDATE offers SET status='withdrawn' WHERE status='pending' "
            "AND id != ? AND (wanted_baby IN (%s) OR offered_baby IN (%s))"
            % (q, q), (o["id"], *ids, *ids))
        con.commit()

    # -- character creator --
    def create_form(self, con, user):
        if self.need_login(user):
            return
        user = con.execute("SELECT * FROM users WHERE id=?",
                           (user["id"],)).fetchone()
        slots = creator_slots_available(user)
        if slots < 1:
            self.send_html(page("Add your own character",
                                 "<p>Locked. Earn 300 points per character: "
                                 "you have %d points earned.</p>"
                                 % user["points_earned"], user))
            return
        body = ("""<p>You have %d creator slot(s) open.</p>
        <form method='post' action='/create_character'>
        Name: <input name='name' required><br>
        Tribe: <select name='tribe'>
        <option value='Blues'>Waterliewaveminans</option>
        <option value='Browns'>Fortress Rooted</option>
        <option value='Reds'>Windyworrors</option>
        </select><br>
        Hoodie pattern: <input name='pattern' required><br>
        Rhinestone colors: <input name='stones' required><br>
        <button>Create my character</button></form>"""
                % slots)
        self.send_html(page("Add your own character", body, user))

    def do_create(self, con, user):
        if self.need_login(user):
            return
        user = con.execute("SELECT * FROM users WHERE id=?",
                           (user["id"],)).fetchone()
        if creator_slots_available(user) < 1:
            self.redirect("/create")
            return
        f = self.form()
        tribe = f.get("tribe") if f.get("tribe") in STARTERS else "Blues"
        face = STARTERS[tribe]["face"]
        mint_baby(con, user["id"], tribe=tribe, name=(f.get("name") or "Custom")[:40],
                  pattern=(f.get("pattern") or "One of one")[:60],
                  stones=(f.get("stones") or "mixed rhinestones")[:60],
                  face=face, is_custom=1)
        domain = {"Blues": "the sea", "Browns": "the woods",
                  "Reds": "the sky"}[tribe]
        add_notice(con, "New character %s (%s) is available — found in %s."
                   % ((f.get("name") or "Custom")[:40],
                      tribe_display(tribe), domain))
        con.execute("UPDATE users SET creator_slots_used="
                    "creator_slots_used+1 WHERE id=?", (user["id"],))
        con.commit()
        self.redirect("/me?msg=Character+created!")

    # -- shop / PayPal purchases --
    def shop(self, con, user):
        ensure_weekly_popups(con)
        items = con.execute(
            "SELECT * FROM shop_items WHERE sold=0 ORDER BY id").fetchall()
        body = ("<p>Individual characters are <b>$%s</b> each, paid "
                "through PayPal or Cash App Pay (via Stripe)."
                % PRICE_INDIVIDUAL)
        if PAYPAL_MODE != "live" and STRIPE_MODE != "live":
            body += (" Checkout runs in <b>test mode</b>"
                     " — no real money moves.")
        body += "</p>"
        # Monthly gift + choice-baby banners for logged-in shoppers.
        if user:
            month, bought, claimed = monthly_gift_state(con, user["id"])
            if not claimed and bought:
                body += ("<div class='card'><b>Monthly gift — "
                         "available!</b> You bought something this month, "
                         "so you can claim one free hoodie baby."
                         "<form method='post' action='/claim_gift'>"
                         "<button>Claim my monthly gift</button></form>"
                         "</div>")
            avail, done, earned, nclaimed = choice_progress(con,
                                                            user["id"])
            if avail > 0:
                body += ("<div class='card'><b>You have %d choice "
                         "hoodie %s to claim!</b> Every 5 shop purchases "
                         "earns one — pick yours "
                         "<a href='/choice_baby'>here</a>.</div>"
                         % (avail, "baby" if avail == 1 else "babies"))
        # Deals of the week: a rotating set of individual characters at
        # 25% off or half off. Deal items are sold only from this shelf
        # (at the sale price); the regular list below skips them.
        picks = weekly_deal_picks(con)
        deal_items = [it for it in items if it["cert"] in picks]
        if deal_items:
            body += ("<h2>Deals of the week</h2><p>These individual "
                     "characters are on sale this week only — some at "
                     "25% off, some at half off.</p>")
            for it in deal_items:
                frac = picks[it["cert"]]
                sale = deal_price(it["price"], frac)
                pct = "25% off" if frac == 0.25 else "half off"
                body += ("<div class='card'><div style='text-align:center'>"
                         "%s</div><b>%s</b> %s "
                         "<span class='tag'>%s</span> "
                         "<span class='tag'>%s</span><br>"
                         "Hoodie: %s<br>Rhinestones: %s<br>Stones: %d<br>"
                         "Face: %s<br>"
                         "Price: <s>$%s</s> <b>$%s</b> (%s)<br>"
                         "<a href='/baby/%s'>View baby — zoom &amp; "
                         "spin</a></div>" % (
                             baby_img_html(it),
                             esc(it["name"]),
                             gender_marker(baby_gender(it["tribe"],
                                                       it["hoodie_pattern"],
                                                       it["stones"],
                                                       name=it["name"],
                                                       cert=it["cert"])),
                             esc(it["cert"]),
                             esc(tribe_display(it["tribe"])),
                             esc(it["hoodie_pattern"]), esc(it["stones"]),
                             stone_count(it["tribe"], it["cert"],
                                         it["name"]),
                             esc(it["face"]), esc(it["price"]), sale, pct,
                             esc(it["cert"])))
                if user:
                    body += ("<form method='post' action='/buy'>"
                             "<input type='hidden' name='shop_item_id' "
                             "value='%d'><input type='hidden' name='deal' "
                             "value='1'><button>Buy for $%s with "
                             "PayPal</button></form>"
                             "<form method='post' action='/buy_stripe'>"
                             "<input type='hidden' name='shop_item_id' "
                             "value='%d'><input type='hidden' name='deal' "
                             "value='1'><button>Buy for $%s with "
                             "Cash App Pay</button></form>"
                             % (it["id"], sale, it["id"], sale))
                else:
                    body += "<p><a href='/login'>Log in</a> to buy.</p>"
        # This week's pop-ups: the drifter, the limited-edition
        # special and rare, and the Shadow — each sold out listing
        # simply disappears.
        body += popup_shop_section(con, weekly_popup_cert(),
                                   "Drifter pop-up — this week only",
                                   user)
        body += popup_shop_section(con, weekly_special_cert(),
                                   "Special pop-up — limited edition",
                                   user)
        body += popup_shop_section(con, weekly_rare_cert(),
                                   "Rare pop-up — limited edition", user)
        body += popup_shop_section(con, weekly_shadow_cert(),
                                   "Shadow pop-up — this week only", user)
        if not items:
            body += "<p>No individual characters are for sale right now.</p>"
        for it in items:
            if it["cert"] in picks:
                continue
            body += ("<div class='card'><div style='text-align:center'>"
                     "%s</div><b>%s</b> %s "
                     "<span class='tag'>%s</span> "
                     "<span class='tag'>%s</span><br>"
                     "Hoodie: %s<br>Rhinestones: %s<br>Stones: %d<br>"
                     "Face: %s<br>"
                     "Price: $%s<br><a href='/baby/%s'>View baby — "
                     "zoom &amp; spin</a></div>" % (
                         baby_img_html(it),
                         esc(it["name"]),
                         gender_marker(baby_gender(it["tribe"],
                                                   it["hoodie_pattern"],
                                                   it["stones"],
                                                   name=it["name"],
                                                   cert=it["cert"])),
                         esc(it["cert"]),
                         esc(tribe_display(it["tribe"])),
                         esc(it["hoodie_pattern"]), esc(it["stones"]),
                         stone_count(it["tribe"], it["cert"], it["name"]),
                         esc(it["face"]), esc(it["price"]),
                         esc(it["cert"])))
            if user:
                body += ("<form method='post' action='/buy'>"
                         "<input type='hidden' name='shop_item_id' "
                         "value='%d'><button>Buy for $%s with "
                         "PayPal</button></form>"
                         "<form method='post' action='/buy_stripe'>"
                         "<input type='hidden' name='shop_item_id' "
                         "value='%d'><button>Buy for $%s with "
                         "Cash App Pay</button></form>"
                         % (it["id"], PRICE_INDIVIDUAL,
                            it["id"], PRICE_INDIVIDUAL))
            else:
                body += "<p><a href='/login'>Log in</a> to buy.</p>"
        body += ("<h2>Leaders — $%s each</h2>"
                 "<p>Everyone is free to purchase a leader. Choose 1 of "
                 "the 3 leaders — each is unique, so once one is sold "
                 "it is gone. A bought leader joins your collection and "
                 "trades like any other baby, and owning a leader lets "
                 "you make 2 trades at once.</p>" % PRICE_LEADER)
        leaders = con.execute(
            "SELECT * FROM leaders ORDER BY id").fetchall()
        for ld in leaders:
            body += ("<div class='card'><div style='text-align:center'>"
                     "%s</div><b>%s</b> %s "
                     "<span class='tag'>%s</span> "
                     "<span class='tag'>%s</span><br>"
                     "Hoodie: %s<br>Rhinestones: %s<br>Stones: %d<br>"
                     "Face: %s<br>"
                     "Price: $%s%s<br><a href='/baby/%s'>View baby — "
                     "zoom &amp; spin</a></div>" % (
                         baby_img_html(ld),
                         esc(ld["name"]),
                         gender_marker(baby_gender(ld["tribe"],
                                                   ld["hoodie_pattern"],
                                                   ld["stones"],
                                                   name=ld["name"],
                                                   cert=ld["cert"])),
                         esc(ld["cert"]),
                         esc(tribe_display(ld["tribe"])),
                         esc(ld["hoodie_pattern"]),
                         esc(ld["stones"]),
                         stone_count(ld["tribe"], ld["cert"], ld["name"]),
                         esc(ld["face"]),
                         esc(ld["price"]),
                         " — <b>SOLD</b>" if ld["sold"] else "",
                         esc(ld["cert"])))
            if user and not ld["sold"]:
                body += ("<form method='post' action='/buy_leader'>"
                         "<input type='hidden' name='leader_id' "
                         "value='%d'><button>Buy for $%s with "
                         "PayPal</button></form>"
                         "<form method='post' action='/buy_leader_stripe'>"
                         "<input type='hidden' name='leader_id' "
                         "value='%d'><button>Buy for $%s with "
                         "Cash App Pay</button></form>"
                         % (ld["id"], PRICE_LEADER,
                            ld["id"], PRICE_LEADER))
            elif not user:
                body += "<p><a href='/login'>Log in</a> to buy.</p>"
        body += ("<h2>Gods — $%s each (only %d)</h2>"
                 "<p>Over everyone; their trades are almost impossible. "
                 "Owning a god lets you make up to 5 trades at "
                 "once.</p>" % (PRICE_GOD, GOD_COUNT))
        gods = con.execute("SELECT * FROM gods ORDER BY id").fetchall()
        for g in gods:
            body += ("<div class='card'><div style='text-align:center'>"
                     "%s</div><b>%s</b> %s "
                     "<span class='tag'>%s</span><br>"
                     "Stones: 15<br>"
                     "Price: $%s%s<br><a href='/baby/%s'>View baby — "
                     "zoom &amp; spin</a></div>" % (
                         baby_img_html({"name": g["name"], "cert": g["cert"],
                                       "tribe": "Gods",
                                       "hoodie_pattern": "God Crown",
                                       "stones": "gold rhinestones",
                                       "face": "grand"}),
                         esc(g["name"]),
                         gender_marker(baby_gender(
                             "Gods", "God Crown", "gold rhinestones")),
                         esc(g["cert"]),
                         esc(g["price"]),
                         " — <b>SOLD</b>" if g["sold"] else "",
                         esc(g["cert"])))
            if user and not g["sold"]:
                body += ("<form method='post' action='/buy_god'>"
                         "<input type='hidden' name='god_id' "
                         "value='%d'><button>Buy for $%s with "
                         "PayPal</button></form>"
                         "<form method='post' action='/buy_god_stripe'>"
                         "<input type='hidden' name='god_id' "
                         "value='%d'><button>Buy for $%s with "
                         "Cash App Pay</button></form>"
                         % (g["id"], PRICE_GOD,
                            g["id"], PRICE_GOD))
            elif not user:
                body += "<p><a href='/login'>Log in</a> to buy.</p>"
        body += ("<h2>Points packs</h2>"
                 "<p>Buy points with real money (or earn them by "
                 "trading). The 600 points at sign-up are free. Bought "
                 "points add to your balance only — they do not count "
                 "toward the character-creator unlock, which stays "
                 "trade-earned only.</p>")
        for n, price in POINTS_PACKS:
            body += ("<div class='card'><b>%d points</b><br>"
                     "Price: $%s</div>" % (n, price))
            if user:
                body += ("<form method='post' action='/buy_points'>"
                         "<input type='hidden' name='pack' value='%d'>"
                         "<button>Buy %d points for $%s with "
                         "PayPal</button></form>"
                         "<form method='post' action='/buy_points_stripe'>"
                         "<input type='hidden' name='pack' value='%d'>"
                         "<button>Buy %d points for $%s with "
                         "Cash App Pay</button></form>"
                         % (n, n, price, n, n, price))
            else:
                body += "<p><a href='/login'>Log in</a> to buy.</p>"
        self.send_html(page("Shop", body, user))

    def purchases_page(self, con, user):
        if self.need_login(user):
            return
        rows = con.execute(
            "SELECT * FROM purchases WHERE user_id=? ORDER BY id DESC",
            (user["id"],)).fetchall()
        body = "<p>Your purchases (PayPal or Cash App Pay).</p>"
        if not rows:
            body += "<p>None yet. Visit the <a href='/shop'>Shop</a>.</p>"
        for p in rows:
            provider = ("Cash App Pay" if p["stripe_session_id"]
                        else "PayPal")
            body += ("<div class='card'>%s — %s — $%s — %s — status: %s"
                     "</div>" % (esc(p["item_type"]), esc(p["item_ref"]),
                                  esc(p["amount"]), provider,
                                  esc(p["status"])))
        self.send_html(page("My Purchases", body, user))

    def deal_amount(self, con, f, item):
        """Price for an individual buy: the Deals-shelf sale price when
        the request came from the shelf (deal=1) and the item is one of
        this week's deal picks, otherwise the regular price."""
        if f.get("deal") == "1":
            frac = weekly_deal_picks(con).get(item["cert"])
            if frac is not None:
                return deal_price(item["price"], frac)
        return item["price"]

    def do_buy(self, con, user):
        """Start an individual-character purchase via PayPal (sale
        price when bought from the Deals shelf)."""
        if self.need_login(user):
            return
        f = self.form()
        item = con.execute(
            "SELECT * FROM shop_items WHERE id=? AND sold=0",
            (f.get("shop_item_id"),)).fetchone()
        if not item:
            self.send_html(page("Shop",
                                 "<p class='err'>That character is no "
                                 "longer for sale.</p>", user))
            return
        amount = self.deal_amount(con, f, item)
        if not paypal_configured():
            self.send_html(page(
                "Shop",
                "<p class='err'>PayPal checkout is not set up yet — the "
                "shop owner needs to add PayPal credentials (see the "
                "README for setup steps).</p>", user))
            return
        try:
            order_id, approve_url = paypal_create_order(amount)
        except PayPalError as e:
            self.send_html(page("Shop",
                                 "<p class='err'>Could not start PayPal "
                                 "checkout: %s</p>" % esc(str(e)), user))
            return
        con.execute(
            "INSERT INTO purchases (user_id, item_type, item_ref, amount,"
            " paypal_order_id, status) VALUES (?,?,?,?,?,'pending')",
            (user["id"], "individual", item["cert"], amount,
             order_id))
        con.commit()
        self.redirect(approve_url)

    def buy_return(self, con, user, query):
        """PayPal sent the buyer back: capture, and grant ONLY on success."""
        if self.need_login(user):
            return
        token = (query.get("token") or [""])[0]
        pur = con.execute(
            "SELECT * FROM purchases WHERE paypal_order_id=? AND user_id=?",
            (token, user["id"])).fetchone()
        if not pur:
            self.send_html(page("Shop",
                                 "<p class='err'>No matching purchase "
                                 "found.</p>", user))
            return
        if pur["status"] == "completed":
            if pur["item_type"] == "points":
                msg = ("That purchase already completed — the points "
                       "are in your balance.")
            else:
                msg = ("That purchase already completed — your new "
                       "hoodie baby is in <a href='/me'>My "
                       "Collection</a>.")
            self.send_html(page("Shop", "<p class='ok'>%s</p>" % msg,
                                 user))
            return
        if pur["status"] in ("failed", "cancelled"):
            self.send_html(page(
                "Shop",
                "<p class='err'>That purchase did not complete — nothing "
                "was granted. You can try again from the "
                "<a href='/shop'>Shop</a>.</p>", user))
            return
        try:
            status = paypal_capture_order(token)
        except PayPalError as e:
            con.execute("UPDATE purchases SET status='failed' WHERE id=?",
                        (pur["id"],))
            con.commit()
            self.send_html(page(
                "Shop",
                "<p class='err'>PayPal capture failed: %s — nothing was "
                "granted.</p>" % esc(str(e)), user))
            return
        if status != "COMPLETED":
            con.execute("UPDATE purchases SET status='failed' WHERE id=?",
                        (pur["id"],))
            con.commit()
            self.send_html(page(
                "Shop",
                "<p class='err'>Payment did not complete (PayPal status: "
                "%s) — nothing was granted.</p>" % esc(status), user))
            return
        # Capture succeeded: grant via the shared helper — it re-checks
        # the item is still available, so a sold item grants nothing.
        ok, kind, name, cert, extra = grant_purchase(con, pur,
                                                     user["id"])
        if not ok:
            con.execute("UPDATE purchases SET status='failed' WHERE id=?",
                        (pur["id"],))
            con.commit()
            self.send_html(page("Shop",
                                 "<p class='err'>%s</p>" % esc(extra),
                                 user))
            return
        con.execute("UPDATE purchases SET status='completed' WHERE id=?",
                    (pur["id"],))
        con.commit()
        self.send_html(page(
            "Shop",
            "<p class='ok'>%s</p>"
            % self.grant_success_message(kind, name, cert, extra), user))

    def buy_cancel(self, con, user, query):
        if self.need_login(user):
            return
        token = (query.get("token") or [""])[0]
        pur = con.execute(
            "SELECT * FROM purchases WHERE paypal_order_id=? AND user_id=?"
            " AND status='pending'",
            (token, user["id"])).fetchone()
        if pur:
            con.execute("UPDATE purchases SET status='cancelled' "
                        "WHERE id=?", (pur["id"],))
            con.commit()
        self.send_html(page(
            "Shop",
            "<p>Purchase cancelled — nothing was charged. You can try "
            "again from the <a href='/shop'>Shop</a>.</p>", user))

    # -- shop / Stripe (Cash App Pay) purchases --
    def do_buy_stripe(self, con, user):
        """Start an individual-character purchase via Stripe Checkout
        with Cash App Pay (sale price when bought from the Deals
        shelf)."""
        if self.need_login(user):
            return
        f = self.form()
        item = con.execute(
            "SELECT * FROM shop_items WHERE id=? AND sold=0",
            (f.get("shop_item_id"),)).fetchone()
        if not item:
            self.send_html(page("Shop",
                                 "<p class='err'>That character is no "
                                 "longer for sale.</p>", user))
            return
        amount = self.deal_amount(con, f, item)
        if not stripe_configured():
            self.send_html(page(
                "Shop",
                "<p class='err'>Cash App Pay checkout is not set up yet "
                 "— the shop owner needs to add Stripe credentials (see "
                 "the README for setup steps).</p>", user))
            return
        try:
            session_id, checkout_url = stripe_create_session(
                int(round(float(amount) * 100)),
                "%s (%s)" % (item["name"], item["cert"]))
        except StripeError as e:
            self.send_html(page("Shop",
                                 "<p class='err'>Could not start Cash App "
                                 "Pay checkout: %s</p>" % esc(str(e)), user))
            return
        con.execute(
            "INSERT INTO purchases (user_id, item_type, item_ref, amount,"
            " stripe_session_id, status) VALUES (?,?,?,?,?,'pending')",
            (user["id"], "individual", item["cert"], amount,
             session_id))
        con.commit()
        self.redirect(checkout_url)

    # -- shop / leader, god, points purchases (both providers) --
    def begin_paypal_purchase(self, con, user, item_type, item_ref,
                              amount):
        """Create a PayPal order + pending purchase. Returns the
        approval URL, or None after sending an error page."""
        if not paypal_configured():
            self.send_html(page(
                "Shop",
                "<p class='err'>PayPal checkout is not set up yet — the "
                "shop owner needs to add PayPal credentials (see the "
                "README for setup steps).</p>", user))
            return None
        try:
            order_id, approve_url = paypal_create_order(amount)
        except PayPalError as e:
            self.send_html(page("Shop",
                                 "<p class='err'>Could not start PayPal "
                                 "checkout: %s</p>" % esc(str(e)), user))
            return None
        con.execute(
            "INSERT INTO purchases (user_id, item_type, item_ref, amount,"
            " paypal_order_id, status) VALUES (?,?,?,?,?,'pending')",
            (user["id"], item_type, item_ref, amount, order_id))
        con.commit()
        return approve_url

    def begin_stripe_purchase(self, con, user, item_type, item_ref,
                              amount_cents, amount, name):
        """Create a Stripe Checkout Session + pending purchase. Returns
        the checkout URL, or None after sending an error page."""
        if not stripe_configured():
            self.send_html(page(
                "Shop",
                "<p class='err'>Cash App Pay checkout is not set up yet "
                 "— the shop owner needs to add Stripe credentials (see "
                 "the README for setup steps).</p>", user))
            return None
        try:
            session_id, checkout_url = stripe_create_session(
                amount_cents, name)
        except StripeError as e:
            self.send_html(page("Shop",
                                 "<p class='err'>Could not start Cash App "
                                 "Pay checkout: %s</p>" % esc(str(e)), user))
            return None
        con.execute(
            "INSERT INTO purchases (user_id, item_type, item_ref, amount,"
            " stripe_session_id, status) VALUES (?,?,?,?,?,'pending')",
            (user["id"], item_type, item_ref, amount, session_id))
        con.commit()
        return checkout_url

    def do_buy_leader(self, con, user):
        """Start a $10.00 leader purchase via PayPal (buyer chooses 1
        of the 3 leaders; each is unique)."""
        if self.need_login(user):
            return
        f = self.form()
        ld = con.execute(
            "SELECT * FROM leaders WHERE id=? AND sold=0",
            (f.get("leader_id"),)).fetchone()
        if not ld:
            self.send_html(page("Shop",
                                 "<p class='err'>That leader is no "
                                 "longer for sale.</p>", user))
            return
        url = self.begin_paypal_purchase(con, user, "leader", ld["cert"],
                                         PRICE_LEADER)
        if url:
            self.redirect(url)

    def do_buy_leader_stripe(self, con, user):
        """Start a $10.00 leader purchase via Cash App Pay."""
        if self.need_login(user):
            return
        f = self.form()
        ld = con.execute(
            "SELECT * FROM leaders WHERE id=? AND sold=0",
            (f.get("leader_id"),)).fetchone()
        if not ld:
            self.send_html(page("Shop",
                                 "<p class='err'>That leader is no "
                                 "longer for sale.</p>", user))
            return
        url = self.begin_stripe_purchase(
            con, user, "leader", ld["cert"], 1000, PRICE_LEADER,
            "Leader %s (%s)" % (ld["name"], ld["cert"]))
        if url:
            self.redirect(url)

    def do_buy_god(self, con, user):
        """Start a $40.00 god-slot purchase via PayPal."""
        if self.need_login(user):
            return
        f = self.form()
        g = con.execute(
            "SELECT * FROM gods WHERE id=? AND sold=0",
            (f.get("god_id"),)).fetchone()
        if not g:
            self.send_html(page("Shop",
                                 "<p class='err'>That god is no longer "
                                 "for sale.</p>", user))
            return
        url = self.begin_paypal_purchase(con, user, "god", g["cert"],
                                         PRICE_GOD)
        if url:
            self.redirect(url)

    def do_buy_god_stripe(self, con, user):
        """Start a $40.00 god-slot purchase via Cash App Pay."""
        if self.need_login(user):
            return
        f = self.form()
        g = con.execute(
            "SELECT * FROM gods WHERE id=? AND sold=0",
            (f.get("god_id"),)).fetchone()
        if not g:
            self.send_html(page("Shop",
                                 "<p class='err'>That god is no longer "
                                 "for sale.</p>", user))
            return
        url = self.begin_stripe_purchase(
            con, user, "god", g["cert"], 4000, PRICE_GOD,
            "God slot %s" % g["cert"])
        if url:
            self.redirect(url)

    def do_buy_points(self, con, user):
        """Start a points-pack purchase via PayPal."""
        if self.need_login(user):
            return
        f = self.form()
        try:
            n = int(f.get("pack") or 0)
        except ValueError:
            n = 0
        if n not in POINTS_PACK_PRICES:
            self.send_html(page("Shop",
                                 "<p class='err'>That points pack is not "
                                 "for sale.</p>", user))
            return
        url = self.begin_paypal_purchase(con, user, "points",
                                         "points-%d" % n,
                                         POINTS_PACK_PRICES[n])
        if url:
            self.redirect(url)

    def do_buy_points_stripe(self, con, user):
        """Start a points-pack purchase via Cash App Pay."""
        if self.need_login(user):
            return
        f = self.form()
        try:
            n = int(f.get("pack") or 0)
        except ValueError:
            n = 0
        if n not in POINTS_PACK_PRICES:
            self.send_html(page("Shop",
                                 "<p class='err'>That points pack is not "
                                 "for sale.</p>", user))
            return
        cents = int(round(float(POINTS_PACK_PRICES[n]) * 100))
        url = self.begin_stripe_purchase(
            con, user, "points", "points-%d" % n, cents,
            POINTS_PACK_PRICES[n], "%d points pack" % n)
        if url:
            self.redirect(url)

    def do_buy_popup(self, con, user):
        """Start a pop-up purchase via PayPal (drifter, special, rare,
        or Shadow, at the listing's own price)."""
        if self.need_login(user):
            return
        f = self.form()
        item = con.execute(
            "SELECT * FROM popup_items WHERE id=? AND sold=0",
            (f.get("popup_id"),)).fetchone()
        if not item:
            self.send_html(page("Shop",
                                 "<p class='err'>That pop-up color is no "
                                 "longer for sale.</p>", user))
            return
        url = self.begin_paypal_purchase(con, user, "popup", item["cert"],
                                         item["price"])
        if url:
            self.redirect(url)

    def do_buy_popup_stripe(self, con, user):
        """Start a pop-up color purchase via Cash App Pay."""
        if self.need_login(user):
            return
        f = self.form()
        item = con.execute(
            "SELECT * FROM popup_items WHERE id=? AND sold=0",
            (f.get("popup_id"),)).fetchone()
        if not item:
            self.send_html(page("Shop",
                                 "<p class='err'>That pop-up color is no "
                                 "longer for sale.</p>", user))
            return
        url = self.begin_stripe_purchase(
            con, user, "popup", item["cert"],
            int(round(float(item["price"]) * 100)), item["price"],
            "Pop-up %s (%s)" % (item["name"], item["cert"]))
        if url:
            self.redirect(url)

    def do_claim_gift(self, con, user):
        """Claim the monthly gift: one free baby per user per calendar
        month, only after at least one completed purchase that month.
        The baby is a random starter-pool baby (any tribe but Blacks)."""
        if self.need_login(user):
            return
        month, bought, claimed = monthly_gift_state(con, user["id"])
        if claimed:
            self.send_html(page(
                "Monthly gift",
                "<p class='err'>You already claimed your monthly gift "
                "for %s.</p>" % esc(month), user))
            return
        if not bought:
            self.send_html(page(
                "Monthly gift",
                "<p class='err'>The monthly gift needs at least one "
                "completed purchase this month — buy something in the "
                "<a href='/shop'>Shop</a> first.</p>", user))
            return
        tribe = random.choice(list(STARTERS))
        cert = mint_baby(con, user["id"], tribe=tribe)
        con.execute(
            "INSERT INTO monthly_gifts (user_id, month, baby_cert)"
            " VALUES (?,?,?)", (user["id"], month, cert))
        con.commit()
        self.redirect("/me?msg=Monthly+gift+claimed!")

    def choice_baby_form(self, con, user):
        """Pick-and-name form for an earned choice baby (same pattern
        as signup step 2: pick a starter-roster character, name it)."""
        if self.need_login(user):
            return
        avail, done, earned, claimed = choice_progress(con, user["id"])
        if avail <= 0:
            body = ("<p>You have no choice baby to claim right now.</p>"
                    "<p>Shop purchases so far: %d — %d more until your "
                    "next choice baby (one per 5 purchases).</p>"
                    "<p><a href='/shop'>Visit the Shop</a>.</p>"
                    % (done, 5 - (done % 5)))
        else:
            opts = ""
            for tribe in STARTERS:
                opts += "<optgroup label='%s'>" % esc(tribe_display(tribe))
                for name in STARTERS[tribe]["names"]:
                    opts += "<option value='%s'>%s</option>" % (
                        esc(name), esc(name))
                opts += "</optgroup>"
            body = ("<p>You have <b>%d choice hoodie %s</b> to claim "
                    "(earned: %d, claimed: %d).</p>"
                    "<form method='post' action='/choice_baby'>"
                    "Tribe: <select name='tribe'>"
                    "<option value='Blues'>Waterliewaveminans</option>"
                    "<option value='Browns'>Fortress Rooted</option>"
                    "<option value='Reds'>Windyworrors</option></select><br>"
                    "Character: <select name='roster_name'>%s</select><br>"
                    "Name it (blank keeps the roster name): "
                    "<input name='baby_name' maxlength='40'><br>"
                    "<button>Claim my choice baby</button></form>"
                    % (avail, "baby" if avail == 1 else "babies",
                       earned, claimed, opts))
        self.send_html(page("Choice hoodie baby", body, user))

    def do_choice_baby(self, con, user):
        """Grant one earned choice baby, picked and named by the user."""
        if self.need_login(user):
            return
        avail, _, _, _ = choice_progress(con, user["id"])
        if avail <= 0:
            self.send_html(page(
                "Choice hoodie baby",
                "<p class='err'>You have no choice baby to claim right "
                "now — one is earned per 5 shop purchases.</p>", user))
            return
        f = self.form()
        tribe = f.get("tribe") or ""
        roster = (f.get("roster_name") or "").strip()
        if tribe not in STARTERS or \
                roster not in STARTERS[tribe]["names"]:
            self.send_html(page(
                "Choice hoodie baby",
                "<p class='err'>Please pick one of the starter "
                "characters for your chosen tribe.</p>", user))
            return
        baby_name = (f.get("baby_name") or "").strip()[:40] or roster
        cert = mint_baby(con, user["id"], tribe=tribe, name=baby_name)
        con.execute(
            "INSERT INTO choice_claims (user_id, baby_cert)"
            " VALUES (?,?)", (user["id"], cert))
        con.commit()
        self.redirect("/me?msg=Choice+baby+claimed!")

    def grant_success_message(self, kind, name, cert, extra):
        """Success line for a granted purchase."""
        if kind == "points":
            return ("Payment complete! <b>%d points</b> added to your "
                    "balance." % extra)
        if kind == "leader":
            return ("Payment complete! Your new leader <b>%s</b> (%s) "
                    "is in <a href='/me'>My Collection</a>. "
                    "<a href='/baby/%s'>View your baby</a>."
                    % (esc(name), esc(cert), esc(cert)))
        if kind == "god":
            return ("Payment complete! Your new god <b>%s</b> (%s) is "
                    "in <a href='/me'>My Collection</a>. "
                    "<a href='/baby/%s'>View your baby</a>."
                    % (esc(name), esc(cert), esc(cert)))
        return ("Payment complete! Your new hoodie baby <b>%s</b> (%s) "
                "is in <a href='/me'>My Collection</a>. "
                "<a href='/baby/%s'>View your baby</a>."
                % (esc(name), esc(cert), esc(cert)))

    def stripe_return(self, con, user, query):
        """Stripe sent the buyer back: verify the session server-side,
        and grant ONLY on a paid session."""
        if self.need_login(user):
            return
        session_id = (query.get("session_id") or [""])[0]
        pur = con.execute(
            "SELECT * FROM purchases WHERE stripe_session_id=? AND "
            "user_id=?", (session_id, user["id"])).fetchone()
        if not pur:
            self.send_html(page("Shop",
                                 "<p class='err'>No matching purchase "
                                 "found.</p>", user))
            return
        if pur["status"] == "completed":
            if pur["item_type"] == "points":
                msg = ("That purchase already completed — the points "
                       "are in your balance.")
            else:
                msg = ("That purchase already completed — your new "
                       "hoodie baby is in <a href='/me'>My "
                       "Collection</a>.")
            self.send_html(page("Shop", "<p class='ok'>%s</p>" % msg,
                                 user))
            return
        if pur["status"] in ("failed", "cancelled"):
            self.send_html(page(
                "Shop",
                "<p class='err'>That purchase did not complete — nothing "
                "was granted. You can try again from the "
                "<a href='/shop'>Shop</a>.</p>", user))
            return
        try:
            status = stripe_session_payment_status(session_id)
        except StripeError as e:
            con.execute("UPDATE purchases SET status='failed' WHERE id=?",
                        (pur["id"],))
            con.commit()
            self.send_html(page(
                "Shop",
                "<p class='err'>Cash App Pay verification failed: %s — "
                "nothing was granted.</p>" % esc(str(e)), user))
            return
        if status != "paid":
            con.execute("UPDATE purchases SET status='failed' WHERE id=?",
                        (pur["id"],))
            con.commit()
            self.send_html(page(
                "Shop",
                "<p class='err'>Payment did not complete (Stripe status: "
                "%s) — nothing was granted.</p>" % esc(status), user))
            return
        # Session paid: grant via the shared helper — it re-checks the
        # item is still available, so a sold item grants nothing.
        ok, kind, name, cert, extra = grant_purchase(con, pur,
                                                     user["id"])
        if not ok:
            con.execute("UPDATE purchases SET status='failed' WHERE id=?",
                        (pur["id"],))
            con.commit()
            self.send_html(page("Shop",
                                 "<p class='err'>%s</p>" % esc(extra),
                                 user))
            return
        con.execute("UPDATE purchases SET status='completed' WHERE id=?",
                    (pur["id"],))
        con.commit()
        self.send_html(page(
            "Shop",
            "<p class='ok'>%s</p>"
            % self.grant_success_message(kind, name, cert, extra), user))

    def stripe_cancel(self, con, user, query):
        if self.need_login(user):
            return
        session_id = (query.get("session_id") or [""])[0]
        pur = con.execute(
            "SELECT * FROM purchases WHERE stripe_session_id=? AND "
            "user_id=? AND status='pending'",
            (session_id, user["id"])).fetchone()
        if pur:
            con.execute("UPDATE purchases SET status='cancelled' "
                        "WHERE id=?", (pur["id"],))
            con.commit()
        self.send_html(page(
            "Shop",
            "<p>Purchase cancelled — nothing was charged. You can try "
            "again from the <a href='/shop'>Shop</a>.</p>", user))


if __name__ == "__main__":
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print("Hoodie Babies Trade running on http://%s:%d"
          % (HOST, PORT))
    server.serve_forever()

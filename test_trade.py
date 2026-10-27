#!/usr/bin/env python3
"""End-to-end test: two users sign up, get free babies, trade, verify.

Runs the app on a throwaway database and port, drives it over HTTP with
cookie jars (one per user), then checks the database state directly.
Exits non-zero on any failed check.
"""
import http.client
import http.cookiejar
import os
import re
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request

PORT = 8093
DB = "/tmp/escamo_test.db"
BASE = "http://127.0.0.1:%d" % PORT
APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")

fails = []


def check(label, cond, detail=""):
    print(("PASS" if cond else "FAIL"), "-", label, detail)
    if not cond:
        fails.append(label)


def csrf_token_for(jar, base=BASE):
    """Fetch a page so the jar holds a CSRF cookie, then return it."""
    get(jar, "/", base=base)
    for cookie in jar:
        if cookie.name == "csrf_token":
            return cookie.value
    return ""


def post(jar, path, data, base=BASE):
    # Every POST carries the jar's CSRF token, like a browser form does.
    data = dict(data)
    data.setdefault("csrf_token", csrf_token_for(jar, base))
    req = urllib.request.Request(
        base + path,
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    try:
        with opener.open(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def post_raw(jar, path, data, base=BASE):
    """POST exactly as given, with no CSRF token added — for testing
    that token-less or wrong-token requests are rejected."""
    req = urllib.request.Request(
        base + path,
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    try:
        with opener.open(req) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def get(jar, path, base=BASE):
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    with opener.open(base + path) as r:
        return r.status, r.read().decode()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def post_noredirect(jar, path, data, base=BASE):
    """POST without following redirects: returns (status, headers, body)."""
    data = dict(data)
    data.setdefault("csrf_token", csrf_token_for(jar, base))
    req = urllib.request.Request(
        base + path,
        data=urllib.parse.urlencode(data).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    opener = urllib.request.build_opener(
        NoRedirect, urllib.request.HTTPCookieProcessor(jar))
    try:
        with opener.open(req) as r:
            return r.status, dict(r.headers), r.read().decode()
    except urllib.error.HTTPError as e:
        # NoRedirect makes urllib raise the redirect as an error; the
        # 302 response (with its Location header) is what we want.
        return e.code, dict(e.headers), e.read().decode()


ROSTERS = {
    "Blues": ["Marina", "Coral", "Pearl", "Tide", "Kai"],
    "Browns": ["Willow", "Fern", "Hazel", "Rowan", "Ivy", "Maple",
               "Juniper", "Sage"],
    "Reds": ["Ember", "Storm", "Skye", "Ash", "Blaze", "Aurora",
             "Cinder", "Tempest"],
}


def signup_full(jar, username, password, tribe="Blues", roster=None,
                name=None, invite=None, base=BASE):
    """Two-step signup: step 1 (username/password/tribe/invite), then
    step 2 (pick a roster character, optionally rename it). Returns the
    final (status, body); if step 1 fails, returns step 1's result."""
    data = {"username": username, "password": password, "tribe": tribe}
    if invite:
        data["invite_code"] = invite
    s, body = post(jar, "/signup", data, base=base)
    if s not in (200, 302):
        return s, body
    m = re.search(r"name='signup_token' value='([^']+)'", body)
    if not m:
        return s, body
    step2 = {"signup_token": m.group(1),
             "roster_name": roster or ROSTERS[tribe][0]}
    if name is not None:
        step2["baby_name"] = name
    return post(jar, "/signup_step2", step2, base=base)


def main():
    # Portrait unit checks (no server needed): the SVG engine renders
    # from stored attributes only, with no image files or dependencies.
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import app as escamo_app
    blue = {"name": "Marina", "cert": "EHB-B001", "tribe": "Blues",
            "hoodie_pattern": "Wave Crest",
            "stones": "yellow + green rhinestones", "face": "fish-like"}
    blue2 = dict(blue, cert="EHB-B002")
    snoot = {"name": "Snoot-Duie", "cert": "EHB-L001", "tribe": "Blues",
               "hoodie_pattern": "Guiding Star",
               "stones": "yellow + green rhinestones",
               "face": "fish-like, surprised crying look"}
    bea = {"name": "Bea Bee", "cert": "EHB-L002", "tribe": "Browns",
               "hoodie_pattern": "Forest Canopy",
               "stones": "amber + gold rhinestones, red stone in forehead",
               "face": "animal-like, grouchy"}
    memegal = {"name": "Me,Me Gal", "cert": "EHB-L003", "tribe": "Reds",
              "hoodie_pattern": "All-black hoodie, diamond head, wand "
              "with fire-red tip",
              "stones": "stones of every different color on all-black "
              "hoodie", "face": "angel-like, slight curious smile"}
    black = {"name": "Onyx", "cert": "EHB-BLK-2026-W44", "tribe": "Blacks",
             "hoodie_pattern": "Underground Veil",
             "stones": "black stones on all-white hoodie",
             "face": "angry creature-like"}
    god1 = {"name": "Muzle Back", "cert": "EHB-G001", "tribe": "Gods",
            "hoodie_pattern": "God Crown", "stones": "gold rhinestones",
            "face": "grand"}
    god2 = dict(god1, name="Baraka", cert="EHB-G002")
    evil = dict(blue, name="<script>alert(1)</script>")
    svg_blue = escamo_app.portrait_svg(blue)
    check("portrait renders inline svg with cert",
          "<svg" in svg_blue and "EHB-B001" in svg_blue)
    check("portrait is deterministic",
          escamo_app.portrait_svg(blue) == svg_blue)
    check("same pattern, different cert gives different portrait",
          escamo_app.portrait_svg(blue2) != svg_blue)
    check("Snoot-Duie portrait has crying tears",
          "#7CC4FF" in escamo_app.portrait_svg(snoot))
    check("Bea Bee portrait has forehead stone",
          "60,26 64,31 60,36 56,31" in escamo_app.portrait_svg(bea))
    svg_memegal = escamo_app.portrait_svg(memegal)
    check("Me,Me Gal portrait has black hoodie and fire-red wand tip",
          "#111111" in svg_memegal and "#FF3B1F" in svg_memegal)
    check("Black portrait has diamond eyes",
          "#DFF6FF" in escamo_app.portrait_svg(black))
    svg_god1 = escamo_app.portrait_svg(god1)
    svg_god2 = escamo_app.portrait_svg(god2)
    check("god portraits have crowns and differ per god",
          "42,30 48,19" in svg_god1 and svg_god1 != svg_god2)
    # Stone counts in SVG portraits: gods 15, leaders 10, villagers 5.
    # Stones are the only 7x7 rects in a portrait, so counting them is
    # exact.
    def stone_dots(svg):
        return svg.count("width='7' height='7'")
    check("villager portrait has exactly 5 stones",
          stone_dots(svg_blue) == 5, "got %d" % stone_dots(svg_blue))
    check("leader portrait has exactly 10 stones",
          stone_dots(escamo_app.portrait_svg(snoot)) == 10,
          "got %d" % stone_dots(escamo_app.portrait_svg(snoot)))
    check("god portrait has exactly 15 stones",
          stone_dots(svg_god1) == 15, "got %d" % stone_dots(svg_god1))
    check("Shadow portrait has exactly 5 stones",
          stone_dots(escamo_app.portrait_svg(black)) == 5,
          "got %d" % stone_dots(escamo_app.portrait_svg(black)))
    # Stone counts in card text, and tribe display names on cards.
    full_blue = dict(blue, is_custom=0, for_trade=0, gender=None)
    full_snoot = dict(snoot, is_custom=0, for_trade=0,
                        gender="female")
    full_god = dict(god1, is_custom=0, for_trade=0, gender="male")
    check("villager card shows Stones: 5",
          "Stones: 5" in escamo_app.baby_card(full_blue))
    check("leader card shows Stones: 10",
          "Stones: 10" in escamo_app.baby_card(full_snoot))
    check("god card shows Stones: 15",
          "Stones: 15" in escamo_app.baby_card(full_god))
    check("card shows Waterliewaveminans, not Blues",
          "Waterliewaveminans" in escamo_app.baby_card(full_blue)
          and ">Blues<" not in escamo_app.baby_card(full_blue))
    check("card shows Shadows, not Blacks",
          "Shadows" in escamo_app.baby_card(
              dict(black, is_custom=0, for_trade=0, gender="male"))
          and "Blacks" not in escamo_app.baby_card(
              dict(black, is_custom=0, for_trade=0, gender="male")))
    check("tribe display map uses Doneil's spellings",
          escamo_app.tribe_display("Blues") == "Waterliewaveminans"
          and escamo_app.tribe_display("Browns") == "Fortress Rooted"
          and escamo_app.tribe_display("Reds") == "Windyworrors"
          and escamo_app.tribe_display("Blacks") == "Shadows")
    # Leaders (Oct 27, 2026 renames): Snoot-Duie/Blues, Bea Bee/
    # Browns, Me,Me Gal/Reds, $10 each, each controlling only their
    # own tribe; gods unchanged.
    leaders = {ld["tribe"]: ld["name"] for ld in escamo_app.LEADERS}
    check("three leaders with new names and tribes",
          leaders == {"Blues": "Snoot-Duie", "Browns": "Bea Bee",
                      "Reds": "Me,Me Gal"})
    check("leader prices unchanged at $10.00",
          escamo_app.PRICE_LEADER == "10.00"
          and escamo_app.PRICE_INDIVIDUAL == "1.99"
          and escamo_app.PRICE_GOD == "40.00")
    check("leader photos renamed, old files gone from mapping",
          escamo_app.REALISTIC_IMAGES.get("Snoot-Duie") == "snoot-duie.jpg"
          and escamo_app.REALISTIC_IMAGES.get("Bea Bee") == "bea-bee.jpg"
          and escamo_app.REALISTIC_IMAGES.get("Me,Me Gal") == "me-me-gal.jpg"
          and "Damiana June" not in escamo_app.REALISTIC_IMAGES
          and "Aaliyah Bee" not in escamo_app.REALISTIC_IMAGES
          and "Jazmyn" not in escamo_app.REALISTIC_IMAGES)
    # Faces: unique within each tribe, inside the tribe's expression
    # family, for every roster + shop character (+ Shadows roster).
    _fam = {"Blues": ("surprised", "happy", "scared"),
            "Browns": ("grouchy", "sleepy", "slight smile", "happy"),
            "Reds": ("curious", "suspicious", "happy"),
            "Blacks": ("mean", "sneaky")}
    _groups = {"Blues": list(escamo_app.STARTERS["Blues"]["names"]),
               "Browns": list(escamo_app.STARTERS["Browns"]["names"]),
               "Reds": list(escamo_app.STARTERS["Reds"]["names"]),
               "Blacks": [r["name"] for r in escamo_app.BLACKS_ROSTER]}
    for _nm, _tr in escamo_app.SHOP_INDIVIDUALS:
        _groups[_tr].append(_nm)
    for _tr, _names in _groups.items():
        _faces = [escamo_app.face_for(_n, _tr, "?") for _n in _names]
        check("faces unique within %s" % _tr,
              len(set(_faces)) == len(_faces)
              and all(_f != "?" for _f in _faces),
              "faces=%s" % _faces)
        check("faces inside %s expression family" % _tr,
              all(any(_w in _f for _w in _fam[_tr]) for _f in _faces))
    # Pop-up faces rotate curious / scared / aware.
    check("pop-up faces rotate curious, scared, aware",
          "curious" in escamo_app.popup_face("Blues", 0)
          and "scared" in escamo_app.popup_face("Blues", 1)
          and "aware" in escamo_app.popup_face("Blues", 2))
    # Themed cards: water / woods / sky / underground.
    check("card themes match tribes",
          escamo_app.theme_class("Blues") == "theme-water"
          and escamo_app.theme_class("Browns") == "theme-woods"
          and escamo_app.theme_class("Reds") == "theme-sky"
          and escamo_app.theme_class("Blacks") == "theme-underground")
    check("baby card carries its tribe theme class",
          "theme-water" in escamo_app.baby_card(full_blue))
    # Old leader names survive only in the DB-migration rename tuples.
    _src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             "app.py")).read()
    _bad = [ln for ln in _src.splitlines()
            if any(_o in ln for _o in ("Damiana June", "Aaliyah Bee",
                                       "Jazmyn", "Jazmyne"))
            and "Snoot-Duie" not in ln and "Bea Bee" not in ln
            and "Me,Me Gal" not in ln]
    check("old leader names only in migration renames", not _bad,
          "bad=%s" % _bad[:3])
    svg_evil = escamo_app.portrait_svg(evil)
    check("portrait escapes baby names",
          "<script>" not in svg_evil and "&lt;script&gt;" in svg_evil)
    check("small portrait size honored",
          "width='72'" in escamo_app.portrait_svg(blue, 72))
    # Realistic photos: name-matched characters resolve to a photo
    # file, Blacks babies always use the Black Miner photo, and
    # anything else keeps the SVG portrait.
    check("realistic photo file for Harbor",
          escamo_app.realistic_img_file({"name": "Harbor",
                                         "tribe": "Blues"})
          == "harbor.jpg")
    check("realistic photo file for Blacks babies",
          escamo_app.realistic_img_file({"name": "Onyx",
                                         "tribe": "Blacks"})
          == "black-miner.jpg")
    check("realistic photo file for gods",
          escamo_app.realistic_img_file({"name": "Muzle Back",
                                         "tribe": "Gods"})
          == "muzle-back.jpg")
    check("no realistic photo for unknown babies",
          escamo_app.realistic_img_file({"name": "Zorp",
                                         "tribe": "Blues"}) is None)
    check("photo html uses img tag when file exists",
          "<img src='/img/harbor.jpg'" in
          escamo_app.baby_img_html({"name": "Harbor",
                                    "tribe": "Blues"}))
    check("photo html falls back to SVG without a photo",
          "<svg" in escamo_app.baby_img_html({"name": "Zorp",
                                              "tribe": "Blues"}))
    # Starter-roster photos: every roster name resolves to its photo,
    # so the weekly pop-up (always a roster character) renders a
    # realistic photo instead of the cartoon SVG.
    roster_expected = {
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
    roster_tribes = {}
    for _t, _d in escamo_app.STARTERS.items():
        for _n in _d["names"]:
            roster_tribes[_n] = _t
    roster_ok = all(
        escamo_app.realistic_img_file(
            {"name": _n, "tribe": roster_tribes[_n]}) == _f
        for _n, _f in roster_expected.items())
    check("realistic photo file for all 21 roster characters", roster_ok)
    check("pop-up card uses a realistic photo for its character",
          "<img src='/img/blaze.jpg'" in
          escamo_app.baby_img_html({"name": "Blaze", "tribe": "Reds",
                                    "hoodie_pattern": "Ember Dusk",
                                    "stones": "orange + red rhinestones",
                                    "face": "angel-like",
                                    "cert": "EHB-P-2026-W44"}))
    check("pop-up card falls back to SVG for unknown characters",
          "<svg" in escamo_app.baby_img_html({"name": "Zorp",
                                              "tribe": "Reds",
                                              "hoodie_pattern": "New Pattern",
                                              "stones": "pink stones",
                                              "face": "angel-like",
                                              "cert": "EHB-P-2026-W99"}))
    if os.path.exists(DB):
        os.remove(DB)
    env = dict(os.environ, PORT=str(PORT), ESCAMO_DB=DB)
    proc = subprocess.Popen([sys.executable, APP], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        for _ in range(50):
            try:
                get(http.cookiejar.CookieJar(), "/")
                break
            except Exception:
                time.sleep(0.2)
        else:
            print("FAIL - server did not start")
            return 1

        alice, bob = http.cookiejar.CookieJar(), http.cookiejar.CookieJar()
        # 1. Alice signs up (no invite)
        s, _ = signup_full(alice, "alice", "secret1",
                           tribe="Blues", roster="Marina")
        check("alice signs up", s in (200, 302), "status=%s" % s)
        # 2. Bob signs up with Alice's invite code
        con = sqlite3.connect(DB)
        con.row_factory = sqlite3.Row
        alice_row = con.execute(
            "SELECT * FROM users WHERE username='alice'").fetchone()
        s, _ = signup_full(bob, "bob", "secret2",
                           tribe="Browns", roster="Willow",
                           invite=alice_row["invite_code"])
        check("bob signs up with alice's invite", s in (200, 302),
              "status=%s" % s)
        bob_row = con.execute(
            "SELECT * FROM users WHERE username='bob'").fetchone()
        check("bob's invited_by is alice",
              bob_row["invited_by"] == alice_row["id"])
        # 3. Both got one free baby + 600 points; Alice also has the
        # +20 invite bonus from Bob signing up with her code (balance
        # and earned). Starting points never count toward the creator.
        for name, row in (("alice", alice_row), ("bob", bob_row)):
            babies = con.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (row["id"],)).fetchall()
            check("%s starts with 1 free baby" % name, len(babies) == 1,
                  "got %d" % len(babies))
        a_start = con.execute(
            "SELECT * FROM users WHERE username='alice'").fetchone()
        b_start = con.execute(
            "SELECT * FROM users WHERE username='bob'").fetchone()
        check("alice starts with 620 points (600 + 20 invite bonus)",
              a_start["points"] == 620, "got %d" % a_start["points"])
        check("alice invite bonus counts as earned",
              a_start["points_earned"] == 20,
              "got %d" % a_start["points_earned"])
        check("bob starts with 600 points", b_start["points"] == 600,
              "got %d" % b_start["points"])
        check("bob starts with 0 earned", b_start["points_earned"] == 0)
        s, alice_me = get(alice, "/me")
        check("alice collection shows invite bonus line",
              "Invite bonus: +20 points" in alice_me)
        check("alice collection shows village 1 / 40",
              "My Village: 1 / 40" in alice_me)
        s, me_html = get(alice, "/me")
        check("collection page shows pictures",
              ("<svg" in me_html or "/img/" in me_html)
              and alice_row["username"] in me_html)
        alice_baby = con.execute(
            "SELECT * FROM babies WHERE owner_id=?",
            (alice_row["id"],)).fetchone()
        bob_baby = con.execute(
            "SELECT * FROM babies WHERE owner_id=?",
            (bob_row["id"],)).fetchone()
        # 4. Bob puts his baby up for trade
        s, _ = post(bob, "/toggle_trade", {"baby_id": bob_baby["id"]})
        check("bob marks baby for trade", s in (200, 302))
        # 5. Alice sends an offer: her baby + 10 points for Bob's baby
        s, _ = post(alice, "/offer",
                     {"wanted_baby_id": bob_baby["id"],
                      "offered_baby_id": alice_baby["id"],
                      "offered_points": "10"})
        check("alice sends offer", s in (200, 302), "status=%s" % s)
        offer = con.execute(
            "SELECT * FROM offers WHERE from_user=? AND to_user=?",
            (alice_row["id"], bob_row["id"])).fetchone()
        check("offer recorded as pending",
              offer is not None and offer["status"] == "pending")
        # 6. Bob accepts
        s, _ = post(bob, "/offer_action",
                     {"offer_id": offer["id"], "action": "accept"})
        check("bob accepts", s in (200, 302), "status=%s" % s)
        # 7. Verify final state
        con2 = sqlite3.connect(DB)
        con2.row_factory = sqlite3.Row
        a = con2.execute(
            "SELECT * FROM users WHERE username='alice'").fetchone()
        b = con2.execute(
            "SELECT * FROM users WHERE username='bob'").fetchone()
        ab = con2.execute("SELECT * FROM babies WHERE id=?",
                          (alice_baby["id"],)).fetchone()
        bb = con2.execute("SELECT * FROM babies WHERE id=?",
                          (bob_baby["id"],)).fetchone()
        check("alice now owns bob's old baby", ab["owner_id"] == b["id"],
              "owner=%s" % ab["owner_id"])
        check("bob now owns alice's old baby", bb["owner_id"] == a["id"],
              "owner=%s" % bb["owner_id"])
        check("traded babies leave the trade board",
              ab["for_trade"] == 0 and bb["for_trade"] == 0)
        # alice: 620 - 10 offered + 20 earned = 630
        check("alice points = 630", a["points"] == 630,
              "got %d" % a["points"])
        # bob: 600 + 20 award only — the 10 offered points burn and
        # are received by nobody
        check("bob points = 620 (offered points burn)",
              b["points"] == 620, "got %d" % b["points"])
        check("alice earned 40 (20 invite + 20 trade)",
              a["points_earned"] == 40)
        check("bob earned 20", b["points_earned"] == 20)
        check("creator still locked at 40/300",
              a["points_earned"] // 300 - a["creator_slots_used"] == 0)
        o = con2.execute("SELECT * FROM offers WHERE id=?",
                         (offer["id"],)).fetchone()
        check("offer status accepted", o["status"] == "accepted")
        # 8. World page: pricing, gods, five Blues, new faces
        s, world_html = get(alice, "/world")
        check("world shows leader price $10.00", "10.00" in world_html)
        check("world shows individual price $1.99", "1.99" in world_html)
        check("world shows god price $40.00", "40.00" in world_html)
        check("world shows the 3 gods", "3 Gods" in world_html)
        check("world shows five Blues characters",
              "five characters" in world_html)
        check("world shows fish-like Blues", "fish-like" in world_html)
        check("world shows animal-like Browns", "animal-like" in world_html)
        check("world shows angel-like Reds", "angel-like" in world_html)
        check("world shows Shadows as the protectors",
              "Shadows — the protectors" in world_html)
        check("world shows Diamond eyes for Shadows",
              "Diamond eyes" in world_html)
        check("world shows no Blacks tribe name",
              "Blacks" not in world_html)
        # 9. Player-listed Shadow + 1,200-point gate (the weekly Shadow
        # is now a $10.00 Shop pop-up, not a Trade Board listing).
        seller = http.cookiejar.CookieJar()
        s, _ = signup_full(seller, "seller", "secret2",
                           tribe="Reds", roster="Storm")
        check("seller signs up", s in (200, 302))
        con3 = sqlite3.connect(DB)
        con3.row_factory = sqlite3.Row
        seller_row = con3.execute(
            "SELECT * FROM users WHERE username='seller'").fetchone()
        con3.execute(
            "INSERT INTO babies (cert, owner_id, name, tribe,"
            " hoodie_pattern, stones, face, for_trade, gender)"
            " VALUES (?,?,?,?,?,?,?,1,'male')",
            ("EHB-TEST-SHD", seller_row["id"], "Onyx", "Blacks",
             "Underground Veil", "black stones on all-white hoodie",
             "angry creature-like"))
        con3.commit()
        shadow = con3.execute(
            "SELECT * FROM babies WHERE cert='EHB-TEST-SHD'").fetchone()
        s, browse_html = get(alice, "/browse")
        check("trade board shows a picture (photo or portrait)",
              "<svg" in browse_html or "/img/" in browse_html)
        check("trade board shows a Shadow for trade",
              "Shadows" in browse_html)
        check("trade board shows 1,200-point gate", "1,200" in browse_html)
        check("trade board has no Underground auto-accept",
              "auto-accept" not in browse_html)
        # alice has 630 points: below the gate, so the offer is refused
        s, resp = post(alice, "/offer",
                       {"wanted_baby_id": shadow["id"],
                        "offered_points": "0"})
        check("Shadow offer below 1,200 refused", "1,200" in resp)
        no_offer = con3.execute(
            "SELECT * FROM offers WHERE wanted_baby=? AND from_user=? "
            "AND status='pending'",
            (shadow["id"], alice_row["id"])).fetchone()
        check("no pending offer created for refused Shadow trade",
              no_offer is None)
        # boost alice to 1,500 points: the gate now passes and the
        # offer goes pending (no auto-accept any more). Alice offers
        # the baby she got from Bob + 50 points.
        con3.execute("UPDATE users SET points=1500 WHERE username='alice'")
        con3.commit()
        s, _ = post(alice, "/offer",
                    {"wanted_baby_id": shadow["id"],
                     "offered_baby_id": bob_baby["id"],
                     "offered_points": "50"})
        con4 = sqlite3.connect(DB)
        con4.row_factory = sqlite3.Row
        sale = con4.execute(
            "SELECT * FROM offers WHERE wanted_baby=? AND from_user=?",
            (shadow["id"], alice_row["id"])).fetchone()
        check("Shadow offer at 1,500 points goes pending",
              sale is not None and sale["status"] == "pending")
        # The seller accepts: buyer gets the Shadow + 60 points.
        s, _ = post(seller, "/offer_action",
                    {"offer_id": sale["id"], "action": "accept"})
        a2 = con4.execute(
            "SELECT * FROM users WHERE username='alice'").fetchone()
        sh = con4.execute("SELECT * FROM babies WHERE id=?",
                          (shadow["id"],)).fetchone()
        sold_baby = con4.execute("SELECT * FROM babies WHERE id=?",
                                 (bob_baby["id"],)).fetchone()
        check("buyer now owns the Shadow", sh["owner_id"] == a2["id"])
        check("Shadow leaves the trade board", sh["for_trade"] == 0)
        check("offered baby goes to the seller",
              sold_baby["owner_id"] == seller_row["id"])
        # alice: 1500 - 50 offered + 60 earned = 1510;
        # earned 40 (invite + earlier trade) + 60 (Shadow) = 100
        check("buyer points = 1510 after Shadow trade",
              a2["points"] == 1510, "got %d" % a2["points"])
        check("buyer earned 100 after Shadow trade (60 for the Shadow)",
              a2["points_earned"] == 100, "got %d" % a2["points_earned"])
        # Second offer against the same Shadow must fail cleanly: boost
        # Bob past the gate too, so the failure is about the Shadow
        # being gone, not the points gate.
        con4.execute("UPDATE users SET points=1500 WHERE username='bob'")
        con4.commit()
        s, resp2 = post(bob, "/offer",
                        {"wanted_baby_id": shadow["id"],
                         "offered_points": "0"})
        check("second offer against traded Shadow fails",
              "not up for trade" in resp2)
        no_second = con4.execute(
            "SELECT * FROM offers WHERE wanted_baby=? AND from_user=? "
            "AND status IN ('pending', 'accepted')",
            (shadow["id"], bob_row["id"])).fetchone()
        check("no second offer recorded for traded Shadow",
              no_second is None)
        con4.close()
        con3.close()
        # 10. Notices board
        s, notices_html = get(alice, "/notices")
        check("notices board lists arrivals",
              "found in the sea" in notices_html
              and "First Beginnings" in notices_html)
        con.close()
        con2.close()

        def start_server(port, db_path, extra_env):
            if os.path.exists(db_path):
                os.remove(db_path)
            env = dict(os.environ, PORT=str(port), ESCAMO_DB=db_path,
                       **extra_env)
            p = subprocess.Popen([sys.executable, APP], env=env,
                                 stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT)
            base = "http://127.0.0.1:%d" % port
            for _ in range(50):
                try:
                    get(http.cookiejar.CookieJar(), "/", base=base)
                    break
                except Exception:
                    time.sleep(0.2)
            else:
                raise RuntimeError("server on %d did not start" % port)
            return p, base

        # 11. PayPal shop (mocked sandbox): successful capture grants the
        # listed baby and records the purchase; leaders/gods stay
        # coming-soon with no buy path.
        mock_port, mock_db = 8094, "/tmp/escamo_paypal_test.db"
        mproc, mock_base = start_server(mock_port, mock_db,
                                        {"PAYPAL_MOCK": "1"})
        try:
            carol = http.cookiejar.CookieJar()
            s, _ = signup_full(carol, "carol", "secret3",
                               base=mock_base)
            check("carol signs up on mock shop server", s in (200, 302))
            s, shop_html = get(carol, "/shop", base=mock_base)
            check("shop lists individual characters at $1.99",
                  "1.99" in shop_html)
            check("shop shows pictures",
                  "<svg" in shop_html or "/img/" in shop_html)
            check("shop shows leader price $10.00", "10.00" in shop_html)
            check("shop shows god price $40.00", "40.00" in shop_html)
            check("shop shows leader choices for purchase",
                  "Snoot-Duie" in shop_html
                  and "Bea Bee" in shop_html
                  and "Me,Me Gal" in shop_html)
            check("shop shows named gods for purchase",
                  "Muzle Back" in shop_html
                  and "Baraka" in shop_html
                  and "Sirus V" in shop_html)
            check("shop cards carry tribe themes and pop-up underworld line",
                  "theme-water" in shop_html
                  and "theme-woods" in shop_html
                  and "theme-sky" in shop_html
                  and "From the underworld, kept by the gods." in shop_html)
            check("shop shows unique shop faces",
                  all(_f in shop_html for _f in
                      ("fish-like, surprised with a dropped jaw and round eyes",
                       "animal-like, grouchy with a scowl and flattened ears",
                       "angel-like, happy with a beaming grin and starry eyes")))
            s, world_html = get(carol, "/world", base=mock_base)
            check("world page: new leaders, own-tribe rule, gods underworld",
                  "Snoot-Duie" in world_html
                  and "Bea Bee" in world_html
                  and "Me,Me Gal" in world_html
                  and "Damiana June" not in world_html
                  and "Aaliyah Bee" not in world_html
                  and "Jazmyn" not in world_html
                  and "over the other leaders" in world_html
                  and "run the" in world_html
                  and "underworld" in world_html
                  and "Pop-ups are underworld babies" in world_html
                  and "theme-water" in world_html
                  and "theme-woods" in world_html
                  and "theme-sky" in world_html
                  and "theme-underground" in world_html)
            check("shop shows points packs",
                  "300 points" in shop_html
                  and "1200 points" in shop_html)
            s, hdrs, _ = post_noredirect(
                http.cookiejar.CookieJar(), "/buy", {"shop_item_id": "1"},
                base=mock_base)
            check("buy without login redirects to login",
                  s == 302 and hdrs.get("Location") == "/login",
                  "status=%s loc=%s" % (s, hdrs.get("Location")))
            mcon = sqlite3.connect(mock_db)
            mcon.row_factory = sqlite3.Row
            item = mcon.execute(
                "SELECT * FROM shop_items WHERE sold=0 ORDER BY id"
                ).fetchone()
            carol_row = mcon.execute(
                "SELECT * FROM users WHERE username='carol'").fetchone()
            s, hdrs, _ = post_noredirect(
                carol, "/buy", {"shop_item_id": item["id"]},
                base=mock_base)
            loc = hdrs.get("Location", "")
            check("buy redirects to PayPal approval",
                  s == 302 and "buy_return" in loc,
                  "status=%s loc=%s" % (s, loc))
            parsed = urllib.parse.urlparse(loc)
            ret_path = parsed.path + ("?" + parsed.query if parsed.query
                                      else "")
            s, ret_html = get(carol, ret_path, base=mock_base)
            check("successful capture page confirms new baby",
                  "Payment complete" in ret_html)
            mcon2 = sqlite3.connect(mock_db)
            mcon2.row_factory = sqlite3.Row
            pur = mcon2.execute(
                "SELECT * FROM purchases WHERE user_id=?",
                (carol_row["id"],)).fetchone()
            check("purchase recorded as completed at $1.99",
                  pur is not None and pur["status"] == "completed"
                  and pur["amount"] == "1.99"
                  and pur["item_type"] == "individual")
            baby = mcon2.execute(
                "SELECT * FROM babies WHERE cert=?",
                (item["cert"],)).fetchone()
            check("captured purchase grants the listed baby",
                  baby is not None and baby["owner_id"] == carol_row["id"])
            sold = mcon2.execute(
                "SELECT * FROM shop_items WHERE id=?",
                (item["id"],)).fetchone()
            check("bought shop item marked sold", sold["sold"] == 1)
            babies = mcon2.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (carol_row["id"],)).fetchall()
            check("carol now has 2 babies", len(babies) == 2,
                  "got %d" % len(babies))
            s, purch_html = get(carol, "/purchases", base=mock_base)
            check("purchases page shows completed purchase",
                  "completed" in purch_html)
            mcon.close()
            mcon2.close()
        finally:
            mproc.terminate()
            try:
                mproc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                mproc.kill()

        # 12. PayPal shop (mocked sandbox): failed capture grants nothing.
        fail_port, fail_db = 8095, "/tmp/escamo_paypal_fail_test.db"
        fproc, fail_base = start_server(
            fail_port, fail_db,
            {"PAYPAL_MOCK": "1", "PAYPAL_MOCK_CAPTURE": "fail"})
        try:
            dave = http.cookiejar.CookieJar()
            s, _ = signup_full(dave, "dave", "secret4",
                               base=fail_base)
            check("dave signs up on mock fail server", s in (200, 302))
            fcon = sqlite3.connect(fail_db)
            fcon.row_factory = sqlite3.Row
            item = fcon.execute(
                "SELECT * FROM shop_items WHERE sold=0 ORDER BY id"
                ).fetchone()
            dave_row = fcon.execute(
                "SELECT * FROM users WHERE username='dave'").fetchone()
            s, hdrs, _ = post_noredirect(
                dave, "/buy", {"shop_item_id": item["id"]}, base=fail_base)
            parsed = urllib.parse.urlparse(hdrs.get("Location", ""))
            ret_path = parsed.path + ("?" + parsed.query if parsed.query
                                      else "")
            s, ret_html = get(dave, ret_path, base=fail_base)
            check("failed capture page says nothing was granted",
                  "did not complete" in ret_html)
            fcon2 = sqlite3.connect(fail_db)
            fcon2.row_factory = sqlite3.Row
            pur = fcon2.execute(
                "SELECT * FROM purchases WHERE user_id=?",
                (dave_row["id"],)).fetchone()
            check("failed purchase recorded as failed",
                  pur is not None and pur["status"] == "failed")
            babies = fcon2.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (dave_row["id"],)).fetchall()
            check("failed capture grants no baby", len(babies) == 1,
                  "got %d" % len(babies))
            sold = fcon2.execute(
                "SELECT * FROM shop_items WHERE id=?",
                (item["id"],)).fetchone()
            check("failed purchase leaves shop item unsold",
                  sold["sold"] == 0)
            # Failed leader purchase grants nothing, leader unsold.
            ld = fcon2.execute(
                "SELECT * FROM leaders WHERE sold=0 ORDER BY id"
                ).fetchone()
            s, hdrs, _ = post_noredirect(
                dave, "/buy_leader", {"leader_id": ld["id"]},
                base=fail_base)
            parsed = urllib.parse.urlparse(hdrs.get("Location", ""))
            ret_path = parsed.path + ("?" + parsed.query if parsed.query
                                      else "")
            s, ret_html = get(dave, ret_path, base=fail_base)
            check("failed leader capture grants nothing",
                  "did not complete" in ret_html)
            fcon3 = sqlite3.connect(fail_db)
            fcon3.row_factory = sqlite3.Row
            lpur = fcon3.execute(
                "SELECT * FROM purchases WHERE user_id=? AND item_type="
                "'leader'", (dave_row["id"],)).fetchone()
            check("failed leader purchase recorded as failed",
                  lpur is not None and lpur["status"] == "failed"
                  and lpur["amount"] == "10.00")
            lsold = fcon3.execute(
                "SELECT * FROM leaders WHERE id=?",
                (ld["id"],)).fetchone()
            check("failed leader purchase leaves leader unsold",
                  lsold["sold"] == 0)
            lbaby = fcon3.execute(
                "SELECT * FROM babies WHERE cert=?",
                (ld["cert"],)).fetchone()
            check("failed leader purchase grants no leader baby",
                  lbaby is None)
            # Failed points pack grants no points.
            s, hdrs, _ = post_noredirect(
                dave, "/buy_points", {"pack": "600"}, base=fail_base)
            parsed = urllib.parse.urlparse(hdrs.get("Location", ""))
            ret_path = parsed.path + ("?" + parsed.query if parsed.query
                                      else "")
            s, ret_html = get(dave, ret_path, base=fail_base)
            check("failed points pack grants nothing",
                  "did not complete" in ret_html)
            fcon4 = sqlite3.connect(fail_db)
            fcon4.row_factory = sqlite3.Row
            ppur = fcon4.execute(
                "SELECT * FROM purchases WHERE user_id=? AND item_type="
                "'points'", (dave_row["id"],)).fetchone()
            check("failed points purchase recorded as failed",
                  ppur is not None and ppur["status"] == "failed"
                  and ppur["amount"] == "9.99")
            drow = fcon4.execute(
                "SELECT * FROM users WHERE username='dave'").fetchone()
            check("failed points pack adds no points",
                  drow["points"] == 600 and drow["points_earned"] == 0,
                  "points=%d earned=%d" % (drow["points"],
                                           drow["points_earned"]))
            fcon.close()
            fcon2.close()
            fcon3.close()
            fcon4.close()
        finally:
            fproc.terminate()
            try:
                fproc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                fproc.kill()

        # 13. Stripe shop (mocked test mode, Cash App Pay): successful
        # session grants the listed baby and records the purchase.
        stripe_port, stripe_db = 8096, "/tmp/escamo_stripe_test.db"
        sproc, stripe_base = start_server(stripe_port, stripe_db,
                                          {"STRIPE_MOCK": "1"})
        try:
            erin = http.cookiejar.CookieJar()
            s, _ = signup_full(erin, "erin", "secret5",
                               base=stripe_base)
            check("erin signs up on mock stripe server", s in (200, 302))
            s, shop_html = get(erin, "/shop", base=stripe_base)
            check("shop shows Cash App Pay option",
                  "Cash App Pay" in shop_html)
            s, hdrs, _ = post_noredirect(
                http.cookiejar.CookieJar(), "/buy_stripe",
                {"shop_item_id": "1"}, base=stripe_base)
            check("buy_stripe without login redirects to login",
                  s == 302 and hdrs.get("Location") == "/login",
                  "status=%s loc=%s" % (s, hdrs.get("Location")))
            scon = sqlite3.connect(stripe_db)
            scon.row_factory = sqlite3.Row
            item = scon.execute(
                "SELECT * FROM shop_items WHERE sold=0 ORDER BY id"
                ).fetchone()
            erin_row = scon.execute(
                "SELECT * FROM users WHERE username='erin'").fetchone()
            s, hdrs, _ = post_noredirect(
                erin, "/buy_stripe", {"shop_item_id": item["id"]},
                base=stripe_base)
            loc = hdrs.get("Location", "")
            check("buy_stripe redirects to stripe return",
                  s == 302 and "stripe_return" in loc,
                  "status=%s loc=%s" % (s, loc))
            parsed = urllib.parse.urlparse(loc)
            ret_path = parsed.path + ("?" + parsed.query if parsed.query
                                      else "")
            s, ret_html = get(erin, ret_path, base=stripe_base)
            check("paid stripe session page confirms new baby",
                  "Payment complete" in ret_html)
            scon2 = sqlite3.connect(stripe_db)
            scon2.row_factory = sqlite3.Row
            pur = scon2.execute(
                "SELECT * FROM purchases WHERE user_id=?",
                (erin_row["id"],)).fetchone()
            check("stripe purchase recorded as completed at $1.99",
                  pur is not None and pur["status"] == "completed"
                  and pur["amount"] == "1.99"
                  and pur["item_type"] == "individual"
                  and pur["stripe_session_id"] is not None)
            baby = scon2.execute(
                "SELECT * FROM babies WHERE cert=?",
                (item["cert"],)).fetchone()
            check("paid stripe session grants the listed baby",
                  baby is not None and baby["owner_id"] == erin_row["id"])
            sold = scon2.execute(
                "SELECT * FROM shop_items WHERE id=?",
                (item["id"],)).fetchone()
            check("bought shop item marked sold", sold["sold"] == 1)
            babies = scon2.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (erin_row["id"],)).fetchall()
            check("erin now has 2 babies", len(babies) == 2,
                  "got %d" % len(babies))
            s, purch_html = get(erin, "/purchases", base=stripe_base)
            check("purchases page shows Cash App Pay purchase",
                  "Cash App Pay" in purch_html and "completed" in purch_html)
            scon.close()
            scon2.close()
        finally:
            sproc.terminate()
            try:
                sproc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sproc.kill()

        # 14. Stripe shop (mocked): unpaid session grants nothing, and a
        # cancelled session grants nothing.
        unpaid_port, unpaid_db = 8097, "/tmp/escamo_stripe_unpaid.db"
        uproc, unpaid_base = start_server(
            unpaid_port, unpaid_db,
            {"STRIPE_MOCK": "1", "STRIPE_MOCK_PAYMENT": "unpaid"})
        try:
            frank = http.cookiejar.CookieJar()
            s, _ = signup_full(frank, "frank", "secret6",
                               base=unpaid_base)
            check("frank signs up on mock unpaid server", s in (200, 302))
            ucon = sqlite3.connect(unpaid_db)
            ucon.row_factory = sqlite3.Row
            item = ucon.execute(
                "SELECT * FROM shop_items WHERE sold=0 ORDER BY id"
                ).fetchone()
            frank_row = ucon.execute(
                "SELECT * FROM users WHERE username='frank'").fetchone()
            s, hdrs, _ = post_noredirect(
                frank, "/buy_stripe", {"shop_item_id": item["id"]},
                base=unpaid_base)
            parsed = urllib.parse.urlparse(hdrs.get("Location", ""))
            ret_path = parsed.path + ("?" + parsed.query if parsed.query
                                      else "")
            s, ret_html = get(frank, ret_path, base=unpaid_base)
            check("unpaid session page says nothing was granted",
                  "did not complete" in ret_html)
            ucon2 = sqlite3.connect(unpaid_db)
            ucon2.row_factory = sqlite3.Row
            pur = ucon2.execute(
                "SELECT * FROM purchases WHERE user_id=?",
                (frank_row["id"],)).fetchone()
            check("unpaid purchase recorded as failed",
                  pur is not None and pur["status"] == "failed")
            babies = ucon2.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (frank_row["id"],)).fetchall()
            check("unpaid session grants no baby", len(babies) == 1,
                  "got %d" % len(babies))
            sold = ucon2.execute(
                "SELECT * FROM shop_items WHERE id=?",
                (item["id"],)).fetchone()
            check("unpaid purchase leaves shop item unsold",
                  sold["sold"] == 0)
            # Cancel path: start a second purchase, then cancel it.
            item2 = ucon2.execute(
                "SELECT * FROM shop_items WHERE sold=0 ORDER BY id"
                ).fetchone()
            s, hdrs, _ = post_noredirect(
                frank, "/buy_stripe", {"shop_item_id": item2["id"]},
                base=unpaid_base)
            parsed = urllib.parse.urlparse(hdrs.get("Location", ""))
            sid = urllib.parse.parse_qs(parsed.query).get(
                "session_id", [""])[0]
            s, cancel_html = get(
                frank, "/stripe_cancel?session_id=" + sid,
                base=unpaid_base)
            check("cancel page says nothing was charged",
                  "nothing was charged" in cancel_html)
            ucon3 = sqlite3.connect(unpaid_db)
            ucon3.row_factory = sqlite3.Row
            pur2 = ucon3.execute(
                "SELECT * FROM purchases WHERE stripe_session_id=?",
                (sid,)).fetchone()
            check("cancelled purchase recorded as cancelled",
                  pur2 is not None and pur2["status"] == "cancelled")
            babies = ucon3.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (frank_row["id"],)).fetchall()
            check("cancelled purchase grants no baby", len(babies) == 1,
                  "got %d" % len(babies))
            sold2 = ucon3.execute(
                "SELECT * FROM shop_items WHERE id=?",
                (item2["id"],)).fetchone()
            check("cancelled purchase leaves shop item unsold",
                  sold2["sold"] == 0)
            # Unpaid points pack grants no points (300 pack, $4.99).
            s, hdrs, _ = post_noredirect(
                frank, "/buy_points_stripe", {"pack": "300"},
                base=unpaid_base)
            parsed = urllib.parse.urlparse(hdrs.get("Location", ""))
            ret_path = parsed.path + ("?" + parsed.query if parsed.query
                                      else "")
            s, ret_html = get(frank, ret_path, base=unpaid_base)
            check("unpaid points pack grants nothing",
                  "did not complete" in ret_html)
            ucon4 = sqlite3.connect(unpaid_db)
            ucon4.row_factory = sqlite3.Row
            ppur = ucon4.execute(
                "SELECT * FROM purchases WHERE user_id=? AND item_type="
                "'points'", (frank_row["id"],)).fetchone()
            check("unpaid points purchase recorded as failed",
                  ppur is not None and ppur["status"] == "failed"
                  and ppur["amount"] == "4.99")
            frow = ucon4.execute(
                "SELECT * FROM users WHERE username='frank'").fetchone()
            check("unpaid points pack adds no points",
                  frow["points"] == 600 and frow["points_earned"] == 0)
            # The removed 900 pack is refused outright on both
            # providers, even with a valid session.
            s, resp = post(frank, "/buy_points", {"pack": "900"},
                           base=unpaid_base)
            check("900 pack refused via PayPal", "not for sale" in resp)
            s, resp = post(frank, "/buy_points_stripe", {"pack": "900"},
                           base=unpaid_base)
            check("900 pack refused via Cash App Pay",
                  "not for sale" in resp)
            ucon.close()
            ucon2.close()
            ucon3.close()
            ucon4.close()
        finally:
            uproc.terminate()
            try:
                uproc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                uproc.kill()

        # 15. Trade slots + leader/god ownership (mocked PayPal +
        # Stripe): regular 1, leader owner 2, god owner 5 pending
        # outgoing offers; perks follow current ownership.
        cap_port, cap_db = 8098, "/tmp/escamo_cap_test.db"
        cproc, cap_base = start_server(
            cap_port, cap_db,
            {"PAYPAL_MOCK": "1", "STRIPE_MOCK": "1"})
        try:
            u1 = http.cookiejar.CookieJar()
            u2 = http.cookiejar.CookieJar()
            s, _ = signup_full(u1, "u1", "secret7", base=cap_base)
            check("u1 signs up on cap server", s in (200, 302))
            s, _ = signup_full(u2, "u2", "secret8", base=cap_base)
            check("u2 signs up on cap server", s in (200, 302))

            def mock_buy(jar, path, data):
                s, hdrs, _ = post_noredirect(jar, path, data,
                                             base=cap_base)
                parsed = urllib.parse.urlparse(hdrs.get("Location", ""))
                ret = parsed.path + ("?" + parsed.query if parsed.query
                                     else "")
                return get(jar, ret, base=cap_base)

            def pending_count(username):
                c = sqlite3.connect(cap_db)
                c.row_factory = sqlite3.Row
                n = c.execute(
                    "SELECT COUNT(*) FROM offers WHERE from_user="
                    "(SELECT id FROM users WHERE username=?) AND status="
                    "'pending'", (username,)).fetchone()[0]
                c.close()
                return n

            # u2 buys 5 shop individuals and lists them + free baby.
            for _ in range(5):
                ccon = sqlite3.connect(cap_db)
                ccon.row_factory = sqlite3.Row
                it = ccon.execute(
                    "SELECT * FROM shop_items WHERE sold=0 ORDER BY id"
                    ).fetchone()
                ccon.close()
                s, html = mock_buy(u2, "/buy",
                                   {"shop_item_id": it["id"]})
                check("u2 mock-buys a shop baby",
                      "Payment complete" in html)
            ccon = sqlite3.connect(cap_db)
            ccon.row_factory = sqlite3.Row
            u2_row = ccon.execute(
                "SELECT * FROM users WHERE username='u2'").fetchone()
            u2_babies = ccon.execute(
                "SELECT * FROM babies WHERE owner_id=? ORDER BY id",
                (u2_row["id"],)).fetchall()
            check("u2 has 6 babies to list", len(u2_babies) == 6,
                  "got %d" % len(u2_babies))
            ccon.close()
            for b in u2_babies:
                post(u2, "/toggle_trade", {"baby_id": b["id"]},
                     base=cap_base)
            # u1 (regular, cap 1): first offer ok, second refused.
            post(u1, "/offer",
                 {"wanted_baby_id": u2_babies[0]["id"],
                  "offered_points": "0"}, base=cap_base)
            check("regular trader gets 1 pending offer",
                  pending_count("u1") == 1)
            s, resp = post(u1, "/offer",
                           {"wanted_baby_id": u2_babies[1]["id"],
                            "offered_points": "0"}, base=cap_base)
            check("regular trader second offer refused by cap",
                  "at most 1" in resp)
            # u1 buys leader Snoot-Duie via PayPal: cap becomes 2.
            ccon = sqlite3.connect(cap_db)
            ccon.row_factory = sqlite3.Row
            ld = ccon.execute(
                "SELECT * FROM leaders WHERE sold=0 ORDER BY id"
                ).fetchone()
            ccon.close()
            s, html = mock_buy(u1, "/buy_leader",
                               {"leader_id": ld["id"]})
            check("leader purchase page confirms new leader",
                  "Payment complete" in html and "Snoot-Duie" in html)
            ccon = sqlite3.connect(cap_db)
            ccon.row_factory = sqlite3.Row
            lsold = ccon.execute(
                "SELECT * FROM leaders WHERE id=?", (ld["id"],)).fetchone()
            check("bought leader marked sold", lsold["sold"] == 1)
            lpur = ccon.execute(
                "SELECT * FROM purchases WHERE item_type='leader' AND "
                "user_id=(SELECT id FROM users WHERE username='u1')"
                ).fetchone()
            check("leader purchase recorded at $10.00",
                  lpur is not None and lpur["status"] == "completed"
                  and lpur["amount"] == "10.00")
            u1_row = ccon.execute(
                "SELECT * FROM users WHERE username='u1'").fetchone()
            lbaby = ccon.execute(
                "SELECT * FROM babies WHERE cert=?",
                (ld["cert"],)).fetchone()
            check("leader baby joins buyer collection",
                  lbaby is not None and lbaby["owner_id"] == u1_row["id"])
            ccon.close()
            # Sold-out leader cannot be bought by someone else.
            s, resp = post(u2, "/buy_leader", {"leader_id": ld["id"]},
                           base=cap_base)
            check("sold-out leader unavailable to others",
                  "no longer for sale" in resp)
            # Leader owner: second offer now succeeds, third refused.
            post(u1, "/offer",
                 {"wanted_baby_id": u2_babies[1]["id"],
                  "offered_points": "0"}, base=cap_base)
            check("leader owner gets 2 pending offers",
                  pending_count("u1") == 2)
            s, resp = post(u1, "/offer",
                           {"wanted_baby_id": u2_babies[2]["id"],
                            "offered_points": "0"}, base=cap_base)
            check("leader owner third offer refused by cap",
                  "at most 2" in resp)
            # u1 lists the leader; u2 offers for it; u1 accepts — the
            # perk follows ownership, so u1's cap drops back to 1.
            post(u1, "/toggle_trade", {"baby_id": lbaby["id"]},
                 base=cap_base)
            post(u2, "/offer",
                 {"wanted_baby_id": lbaby["id"],
                  "offered_points": "0"}, base=cap_base)
            ccon = sqlite3.connect(cap_db)
            ccon.row_factory = sqlite3.Row
            loffer = ccon.execute(
                "SELECT * FROM offers WHERE wanted_baby=? AND status="
                "'pending'", (lbaby["id"],)).fetchone()
            ccon.close()
            post(u1, "/offer_action",
                 {"offer_id": loffer["id"], "action": "accept"},
                 base=cap_base)
            ccon = sqlite3.connect(cap_db)
            ccon.row_factory = sqlite3.Row
            lbaby2 = ccon.execute(
                "SELECT * FROM babies WHERE id=?",
                (lbaby["id"],)).fetchone()
            u2_row2 = ccon.execute(
                "SELECT * FROM users WHERE username='u2'").fetchone()
            check("traded leader now belongs to u2",
                  lbaby2["owner_id"] == u2_row2["id"])
            ccon.close()
            s, resp = post(u1, "/offer",
                           {"wanted_baby_id": u2_babies[2]["id"],
                            "offered_points": "0"}, base=cap_base)
            check("cap drops after trading leader away",
                  "at most 1" in resp)
            # u1 buys a god via Cash App Pay: cap becomes 5.
            ccon = sqlite3.connect(cap_db)
            ccon.row_factory = sqlite3.Row
            g = ccon.execute(
                "SELECT * FROM gods WHERE sold=0 ORDER BY id"
                ).fetchone()
            ccon.close()
            s, html = mock_buy(u1, "/buy_god_stripe",
                               {"god_id": g["id"]})
            check("god purchase page confirms new god",
                  "Payment complete" in html and "Muzle Back" in html)
            ccon = sqlite3.connect(cap_db)
            ccon.row_factory = sqlite3.Row
            gsold = ccon.execute(
                "SELECT * FROM gods WHERE id=?", (g["id"],)).fetchone()
            check("bought god slot marked sold", gsold["sold"] == 1)
            gpur = ccon.execute(
                "SELECT * FROM purchases WHERE item_type='god' AND "
                "user_id=(SELECT id FROM users WHERE username='u1')"
                ).fetchone()
            check("god purchase recorded at $40.00",
                  gpur is not None and gpur["status"] == "completed"
                  and gpur["amount"] == "40.00")
            u1_row = ccon.execute(
                "SELECT * FROM users WHERE username='u1'").fetchone()
            gbaby = ccon.execute(
                "SELECT * FROM babies WHERE cert=?",
                (g["cert"],)).fetchone()
            check("god baby joins buyer collection",
                  gbaby is not None and gbaby["owner_id"] == u1_row["id"])
            ccon.close()
            # God owner: offers 3, 4, 5 succeed; 6th refused.
            for i in (2, 3, 4):
                post(u1, "/offer",
                     {"wanted_baby_id": u2_babies[i]["id"],
                      "offered_points": "0"}, base=cap_base)
            check("god owner gets 5 pending offers",
                  pending_count("u1") == 5,
                  "got %d" % pending_count("u1"))
            s, resp = post(u1, "/offer",
                           {"wanted_baby_id": u2_babies[5]["id"],
                            "offered_points": "0"}, base=cap_base)
            check("god owner sixth offer refused by cap",
                  "at most 5" in resp)
        finally:
            cproc.terminate()
            try:
                cproc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cproc.kill()

        # 16. Points packs (mocked): balance only, never points_earned.
        pts_port, pts_db = 8099, "/tmp/escamo_points_test.db"
        pproc, pts_base = start_server(
            pts_port, pts_db,
            {"PAYPAL_MOCK": "1", "STRIPE_MOCK": "1"})
        try:
            w1 = http.cookiejar.CookieJar()
            s, _ = signup_full(w1, "w1", "secret9", base=pts_base)
            check("w1 signs up on points server", s in (200, 302))
            # 600 points via PayPal.
            s, hdrs, _ = post_noredirect(
                w1, "/buy_points", {"pack": "600"}, base=pts_base)
            parsed = urllib.parse.urlparse(hdrs.get("Location", ""))
            ret = parsed.path + ("?" + parsed.query if parsed.query
                                 else "")
            s, html = get(w1, ret, base=pts_base)
            check("points pack page confirms points added",
                  "600 points" in html)
            pcon = sqlite3.connect(pts_db)
            pcon.row_factory = sqlite3.Row
            w1_row = pcon.execute(
                "SELECT * FROM users WHERE username='w1'").fetchone()
            check("600-point pack raises balance to 1200",
                  w1_row["points"] == 1200, "got %d" % w1_row["points"])
            check("bought points do not raise points_earned",
                  w1_row["points_earned"] == 0)
            ppur = pcon.execute(
                "SELECT * FROM purchases WHERE user_id=? AND item_type="
                "'points'", (w1_row["id"],)).fetchone()
            check("points purchase recorded at $9.99",
                  ppur is not None and ppur["status"] == "completed"
                  and ppur["amount"] == "9.99")
            pcon.close()
            # 1,200 points via Cash App Pay.
            s, hdrs, _ = post_noredirect(
                w1, "/buy_points_stripe", {"pack": "1200"},
                base=pts_base)
            parsed = urllib.parse.urlparse(hdrs.get("Location", ""))
            ret = parsed.path + ("?" + parsed.query if parsed.query
                                 else "")
            s, html = get(w1, ret, base=pts_base)
            check("stripe points pack page confirms points added",
                  "1200 points" in html)
            pcon2 = sqlite3.connect(pts_db)
            pcon2.row_factory = sqlite3.Row
            w1_row = pcon2.execute(
                "SELECT * FROM users WHERE username='w1'").fetchone()
            check("1200-point pack raises balance to 2400",
                  w1_row["points"] == 2400, "got %d" % w1_row["points"])
            check("stripe bought points still do not raise earned",
                  w1_row["points_earned"] == 0)
            s, me_html = get(w1, "/me", base=pts_base)
            check("creator still locked despite bought points",
                  "0 / 300 points earned" in me_html)
            # Invalid pack refused.
            s, resp = post(w1, "/buy_points", {"pack": "500"},
                           base=pts_base)
            check("invalid points pack refused",
                  "not for sale" in resp)
            pcon2.close()
        finally:
            pproc.terminate()
            try:
                pproc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pproc.kill()

        # 17. Two-step signup: tribe choice, character choice, naming.
        tcon = sqlite3.connect(DB)
        tcon.row_factory = sqlite3.Row
        # Blues + Coral renamed to a custom name.
        chooser = http.cookiejar.CookieJar()
        s, _ = signup_full(chooser, "chooser", "secret10",
                           tribe="Blues", roster="Coral", name="Bubbles")
        check("two-step signup completes", s in (200, 302),
              "status=%s" % s)
        ch_row = tcon.execute(
            "SELECT * FROM users WHERE username='chooser'").fetchone()
        check("chooser account exists", ch_row is not None)
        ch_baby = tcon.execute(
            "SELECT * FROM babies WHERE owner_id=?",
            (ch_row["id"],)).fetchone()
        check("chooser's free baby is Blues, renamed",
              ch_baby["tribe"] == "Blues" and ch_baby["name"] == "Bubbles",
              "got %s %s" % (ch_baby["tribe"], ch_baby["name"]))
        check("chooser starts with 600 points", ch_row["points"] == 600)
        # Reds + roster name kept as-is (blank name).
        keeper = http.cookiejar.CookieJar()
        s, _ = signup_full(keeper, "keeper", "secret10",
                           tribe="Reds", roster="Ember", name="")
        k_row = tcon.execute(
            "SELECT * FROM users WHERE username='keeper'").fetchone()
        k_baby = tcon.execute(
            "SELECT * FROM babies WHERE owner_id=?",
            (k_row["id"],)).fetchone()
        check("blank name keeps the roster name",
              k_baby["tribe"] == "Reds" and k_baby["name"] == "Ember",
              "got %s %s" % (k_baby["tribe"], k_baby["name"]))
        # Invite code passed at step 1 is recorded after step 2.
        inv = http.cookiejar.CookieJar()
        s, _ = signup_full(inv, "invited1", "secret10",
                           tribe="Browns", roster="Fern",
                           invite=alice_row["invite_code"])
        i_row = tcon.execute(
            "SELECT * FROM users WHERE username='invited1'").fetchone()
        check("invite code survives both signup steps",
              i_row is not None
              and i_row["invited_by"] == alice_row["id"])
        # Step 1 without a tribe is refused and creates nothing.
        s, body = post(http.cookiejar.CookieJar(), "/signup",
                       {"username": "notribe", "password": "secret10"})
        check("signup without a tribe is refused",
              s == 200 and "pick a tribe" in body.lower(),
              "status=%s" % s)
        check("tribe-less signup creates no user",
              tcon.execute("SELECT * FROM users WHERE username="
                           "'notribe'").fetchone() is None)
        # Blacks are not a joinable starter tribe.
        s, body = post(http.cookiejar.CookieJar(), "/signup",
                       {"username": "blackfan", "password": "secret10",
                        "tribe": "Blacks"})
        check("Blacks signup is refused",
              s == 200 and "pick a tribe" in body.lower())
        check("Blacks signup creates no user",
              tcon.execute("SELECT * FROM users WHERE username="
                           "'blackfan'").fetchone() is None)
        # Duplicate username is rejected at step 1.
        s, body = post(http.cookiejar.CookieJar(), "/signup",
                       {"username": "alice", "password": "secret10",
                        "tribe": "Blues"})
        check("duplicate username rejected at signup",
              s == 200 and "taken" in body.lower())
        # Abandoning step 2 creates no account.
        ab = http.cookiejar.CookieJar()
        s, body = post(ab, "/signup",
                       {"username": "abandoner", "password": "secret10",
                        "tribe": "Reds"})
        check("step 1 alone returns the character picker",
              s == 200 and "signup_token" in body)
        check("abandoned step 2 creates no account",
              tcon.execute("SELECT * FROM users WHERE username="
                           "'abandoner'").fetchone() is None)
        # Step 2 with a bogus token creates nothing.
        s, body = post(http.cookiejar.CookieJar(), "/signup_step2",
                       {"signup_token": "bogus",
                        "roster_name": "Marina", "baby_name": "X"})
        check("bogus step-2 token refused",
              s == 200 and "expired or is invalid" in body)
        tcon.close()

        # 18. Hardening: CSRF, session expiry, rate limiting, Secure
        # cookies — on their own server instances, because rate-limit
        # state is per-process and IP-keyed and must not leak into the
        # main server's tests. NOTE: the Secure-flag instance is used
        # for a header check only — a Secure cookie is (correctly)
        # never sent back over plain HTTP, so POST-based tests run on
        # a second instance without COOKIE_SECURE.
        sec_port, sec_db = 8101, "/tmp/escamo_secure_test.db"
        sproc, sec_base = start_server(
            sec_port, sec_db,
            {"COOKIE_SECURE": "1", "HOST": "127.0.0.1"})
        try:
            # Secure cookies: with COOKIE_SECURE=1 the CSRF cookie (and
            # the session cookie, which shares the same flags) carries
            # the Secure flag.
            conn = http.client.HTTPConnection("127.0.0.1", sec_port)
            conn.request("GET", "/")
            resp = conn.getresponse()
            resp.read()
            set_cookies = [v for k, v in resp.getheaders()
                           if k.lower() == "set-cookie"]
            check("csrf cookie carries Secure flag behind HTTPS",
                  any("Secure" in c for c in set_cookies),
                  "cookies=%s" % set_cookies)
            conn.close()
        finally:
            sproc.terminate()
            try:
                sproc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                sproc.kill()
        hard_port, hard_db = 8102, "/tmp/escamo_hardening_test.db"
        hproc, hard_base = start_server(
            hard_port, hard_db, {"HOST": "127.0.0.1"})
        try:
            # CSRF: a POST with no token is rejected and creates nothing.
            hjar = http.cookiejar.CookieJar()
            s, body = post_raw(hjar, "/signup",
                               {"username": "csrfvictim",
                                "password": "secret0"}, base=hard_base)
            check("signup without CSRF token rejected",
                  s == 403 and "Security check" in body,
                  "status=%s" % s)
            hcon = sqlite3.connect(hard_db)
            hcon.row_factory = sqlite3.Row
            check("rejected signup creates no user",
                  hcon.execute("SELECT * FROM users WHERE username="
                               "'csrfvictim'").fetchone() is None)
            # CSRF: a wrong token is rejected too.
            get(hjar, "/", base=hard_base)
            s, body = post_raw(hjar, "/signup",
                               {"username": "csrfvictim",
                                "password": "secret0",
                                "csrf_token": "bogus"},
                               base=hard_base)
            check("signup with wrong CSRF token rejected", s == 403,
                  "status=%s" % s)
            hcon.close()
            # Session expiry: backdate a session 8 days; /me must stop
            # working, while a fresh session still works.
            exp = http.cookiejar.CookieJar()
            s, _ = signup_full(exp, "exp", "secret8", base=hard_base)
            check("expiry-test user signs up", s in (200, 302))
            hcon = sqlite3.connect(hard_db)
            hcon.row_factory = sqlite3.Row
            exp_row = hcon.execute(
                "SELECT * FROM users WHERE username='exp'").fetchone()
            hcon.execute(
                "UPDATE sessions SET created_at="
                "datetime('now', '-8 days') WHERE user_id=?",
                (exp_row["id"],))
            hcon.commit()
            hcon.close()
            s, me_html = get(exp, "/me", base=hard_base)
            check("8-day-old session no longer logs in",
                  "Log in" in me_html and "My Collection" not in me_html)
            fresh = http.cookiejar.CookieJar()
            s, _ = signup_full(fresh, "fresh", "secret8", base=hard_base)
            s, me_html = get(fresh, "/me", base=hard_base)
            check("fresh session still logs in",
                  "My Collection" in me_html)
            # Login rate limiting: 5 wrong passwords, then even the
            # right password is refused with 429.
            vic = http.cookiejar.CookieJar()
            s, _ = signup_full(vic, "victim", "secret8", base=hard_base)
            check("rate-test user signs up", s in (200, 302))
            for _ in range(5):
                s, body = post(vic, "/login",
                               {"username": "victim",
                                "password": "wrongpass"}, base=hard_base)
                check("wrong password refused",
                      s == 200 and "Wrong username" in body,
                      "status=%s" % s)
            s, body = post(vic, "/login",
                           {"username": "victim",
                            "password": "secret8"}, base=hard_base)
            check("login rate-limited after 5 failures",
                  s == 429 and "Too many" in body, "status=%s" % s)
            # Signup rate limiting (last: it blocks the IP): 5 failed
            # signups, then a valid signup is refused with 429.
            for _ in range(5):
                s, body = post(hjar, "/signup",
                               {"username": "spammer",
                                "password": ""}, base=hard_base)
            s, body = post(hjar, "/signup",
                           {"username": "legit", "password": "secret8",
                            "tribe": "Blues"},
                           base=hard_base)
            check("signup rate-limited after 5 failures",
                  s == 429 and "Too many" in body, "status=%s" % s)
            hcon = sqlite3.connect(hard_db)
            hcon.row_factory = sqlite3.Row
            check("rate-limited signup creates no user",
                  hcon.execute("SELECT * FROM users WHERE username="
                               "'legit'").fetchone() is None)
            hcon.close()
        finally:
            hproc.terminate()
            try:
                hproc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                hproc.kill()

        # 16. Deals, monthly gift, choice babies, pop-ups, ownership,
        # gender (mocked PayPal server).
        check("deal price 25% off $1.99 is $1.49",
              escamo_app.deal_price("1.99", 0.25) == "1.49")
        check("deal price half off $1.99 is $1.00",
              escamo_app.deal_price("1.99", 0.50) == "1.00")
        mem = sqlite3.connect(":memory:")
        mem.row_factory = sqlite3.Row
        mem.execute("CREATE TABLE shop_items (id INTEGER PRIMARY KEY,"
                    " cert TEXT)")
        for i in range(12):
            mem.execute("INSERT INTO shop_items (id, cert) VALUES (?,?)",
                        (i + 1, "EHB-S%03d" % (i + 1)))
        picks_a = escamo_app.weekly_deal_picks(mem)
        picks_b = escamo_app.weekly_deal_picks(mem)
        check("weekly deal picks are deterministic",
              picks_a == picks_b)
        check("weekly deal picks are 25% or 50% off, at most 4",
              len(picks_a) <= 4
              and set(picks_a.values()) <= {0.25, 0.50}
              and len(picks_a) >= 1)
        mem.close()
        check("Blacks are always male",
              escamo_app.baby_gender("Blacks", "Underground Veil",
                                     "black stones") == "male")
        check("leaders are female by name",
              escamo_app.baby_gender("Blues", "Sea Crown",
                                     "blue rhinestones",
                                     name="Snoot-Duie") == "female"
              and escamo_app.baby_gender("Browns", "Forest Canopy",
                                         "green rhinestones",
                                         name="Bea Bee") == "female"
              and escamo_app.baby_gender("Reds", "Sky Veil",
                                         "red rhinestones",
                                         name="Me,Me Gal") == "female")
        check("leaders are female by certificate",
              escamo_app.baby_gender("Blues", "Sea Crown",
                                     "blue rhinestones",
                                     cert="EHB-L001") == "female")
        check("gods are male",
              escamo_app.baby_gender("Gods", "God Crown",
                                     "gold rhinestones") == "male")
        check("regular babies have no gender marker",
              escamo_app.baby_gender("Blues", "Deep Diamond",
                                     "yellow + green rhinestones") is None
              and escamo_app.baby_gender("Blues", "Wave Crest",
                                         "yellow + green rhinestones")
              is None)
        bport, bdb = 8101, "/tmp/escamo_bundle_test.db"
        bproc, bbase = start_server(bport, bdb, {"PAYPAL_MOCK": "1"})
        try:
            dana = http.cookiejar.CookieJar()
            s, _ = signup_full(dana, "dana", "secret9",
                               tribe="Blues", roster="Marina",
                               base=bbase)
            check("dana signs up on bundle server", s in (200, 302))
            bcon = sqlite3.connect(bdb)
            bcon.row_factory = sqlite3.Row
            dana_row = bcon.execute(
                "SELECT * FROM users WHERE username='dana'").fetchone()

            def buy_and_capture(jar, path, data):
                s, hdrs, _ = post_noredirect(jar, path, data,
                                             base=bbase)
                loc = hdrs.get("Location", "")
                if "buy_return" not in loc:
                    return s, ""
                parsed = urllib.parse.urlparse(loc)
                ret = parsed.path + ("?" + parsed.query
                                     if parsed.query else "")
                return get(jar, ret, base=bbase)

            s, shop_html = get(dana, "/shop", base=bbase)
            check("shop shows deals shelf",
                  "Deals of the week" in shop_html)
            check("shop shows struck-through deal price",
                  "<s>$1.99</s>" in shop_html)
            check("shop shows pop-up color section",
                  "Drifter pop-up" in shop_html)
            popup_idx = shop_html.find("Drifter pop-up")
            check("shop pop-up card uses a realistic photo",
                  popup_idx >= 0
                  and "<img src='/img/" in
                  shop_html[popup_idx:popup_idx + 800])
            s, notices_html = get(dana, "/notices", base=bbase)
            check("notices board announces the pop-up",
                  "popped up" in notices_html)
            # Monthly gift needs a purchase first.
            nb = bcon.execute(
                "SELECT COUNT(*) FROM babies WHERE owner_id=?",
                (dana_row["id"],)).fetchone()[0]
            s, gift_html = post(dana, "/claim_gift", {}, base=bbase)
            check("monthly gift refused without a purchase",
                  "at least one" in gift_html)
            nb2 = bcon.execute(
                "SELECT COUNT(*) FROM babies WHERE owner_id=?",
                (dana_row["id"],)).fetchone()[0]
            check("refused gift grants no baby", nb2 == nb)
            # Deal buy: current deal pick at its sale price.
            picks = escamo_app.weekly_deal_picks(bcon)
            deal_cert = sorted(picks)[0]
            deal_item = bcon.execute(
                "SELECT * FROM shop_items WHERE cert=?",
                (deal_cert,)).fetchone()
            sale = escamo_app.deal_price(deal_item["price"],
                                         picks[deal_cert])
            s, ret_html = buy_and_capture(
                dana, "/buy", {"shop_item_id": deal_item["id"],
                               "deal": "1"})
            check("deal purchase completes",
                  "Payment complete" in ret_html, "status=%s" % s)
            pur = bcon.execute(
                "SELECT * FROM purchases WHERE user_id=? ORDER BY id "
                "DESC", (dana_row["id"],)).fetchone()
            check("deal purchase recorded at sale price",
                  pur is not None and pur["status"] == "completed"
                  and pur["amount"] == sale,
                  "amount=%s sale=%s" % (
                      pur["amount"] if pur else None, sale))
            baby = bcon.execute(
                "SELECT * FROM babies WHERE cert=?",
                (deal_cert,)).fetchone()
            check("deal purchase grants the baby",
                  baby is not None
                  and baby["owner_id"] == dana_row["id"])
            # deal=1 on a non-deal item still charges full price.
            plain = bcon.execute(
                "SELECT * FROM shop_items WHERE sold=0 AND cert NOT IN "
                "(%s) ORDER BY id"
                % ",".join("?" * len(picks)),
                list(picks)).fetchone()
            s, ret_html = buy_and_capture(
                dana, "/buy", {"shop_item_id": plain["id"],
                               "deal": "1"})
            pur = bcon.execute(
                "SELECT * FROM purchases WHERE user_id=? ORDER BY id "
                "DESC", (dana_row["id"],)).fetchone()
            check("deal flag on non-deal item charges full price",
                  pur is not None and pur["amount"] == "1.99",
                  "amount=%s" % (pur["amount"] if pur else None))
            # Monthly gift now available, once only.
            s, _ = post(dana, "/claim_gift", {}, base=bbase)
            nb3 = bcon.execute(
                "SELECT COUNT(*) FROM babies WHERE owner_id=?",
                (dana_row["id"],)).fetchone()[0]
            check("monthly gift grants one baby", nb3 == nb2 + 3,
                  "was %d now %d" % (nb2, nb3))
            grow = bcon.execute(
                "SELECT * FROM monthly_gifts WHERE user_id=?",
                (dana_row["id"],)).fetchone()
            check("monthly gift recorded", grow is not None)
            s, _ = post(dana, "/claim_gift", {}, base=bbase)
            nb4 = bcon.execute(
                "SELECT COUNT(*) FROM babies WHERE owner_id=?",
                (dana_row["id"],)).fetchone()[0]
            check("second monthly gift in same month grants nothing",
                  nb4 == nb3)
            s, home_html = get(dana, "/", base=bbase)
            check("home shows monthly gift claimed",
                  "Monthly gift claimed" in home_html)
            # Choice baby: 3 more purchases reach 5 completed.
            for _ in range(3):
                nxt = bcon.execute(
                    "SELECT * FROM shop_items WHERE sold=0 ORDER BY id"
                    ).fetchone()
                buy_and_capture(dana, "/buy", {"shop_item_id": nxt["id"]})
            done = bcon.execute(
                "SELECT COUNT(*) FROM purchases WHERE user_id=? AND "
                "status='completed'", (dana_row["id"],)).fetchone()[0]
            check("dana reaches 5 completed purchases", done == 5,
                  "got %d" % done)
            s, choice_html = get(dana, "/choice_baby", base=bbase)
            check("choice baby form shows when milestone reached",
                  "Claim my choice baby" in choice_html)
            nb5 = bcon.execute(
                "SELECT COUNT(*) FROM babies WHERE owner_id=?",
                (dana_row["id"],)).fetchone()[0]
            s, _ = post(dana, "/choice_baby",
                        {"tribe": "Blues", "roster_name": "Ember"},
                        base=bbase)
            nb6 = bcon.execute(
                "SELECT COUNT(*) FROM babies WHERE owner_id=?",
                (dana_row["id"],)).fetchone()[0]
            check("mismatched tribe/roster choice grants nothing",
                  nb6 == nb5)
            s, _ = post(dana, "/choice_baby",
                        {"tribe": "Reds", "roster_name": "Ember",
                         "baby_name": "MyChoice"}, base=bbase)
            nb7 = bcon.execute(
                "SELECT COUNT(*) FROM babies WHERE owner_id=?",
                (dana_row["id"],)).fetchone()[0]
            check("choice baby grants picked and named baby",
                  nb7 == nb5 + 1)
            chosen = bcon.execute(
                "SELECT * FROM babies WHERE owner_id=? AND name=?",
                (dana_row["id"], "MyChoice")).fetchone()
            check("choice baby is a Reds baby",
                  chosen is not None and chosen["tribe"] == "Reds")
            crow = bcon.execute(
                "SELECT * FROM choice_claims WHERE user_id=?",
                (dana_row["id"],)).fetchone()
            check("choice claim recorded", crow is not None)
            s, choice_html = get(dana, "/choice_baby", base=bbase)
            check("choice milestone spent after claim",
                  "no choice baby to claim" in choice_html)
            # Pop-up buy.
            popup = bcon.execute(
                "SELECT * FROM popup_items WHERE sold=0 ORDER BY id"
                ).fetchone()
            check("weekly pop-up item exists", popup is not None)
            s, ret_html = buy_and_capture(
                dana, "/buy_popup", {"popup_id": popup["id"]})
            check("pop-up purchase completes",
                  "Payment complete" in ret_html)
            ppur = bcon.execute(
                "SELECT * FROM purchases WHERE user_id=? AND "
                "item_type='popup' ORDER BY id DESC",
                (dana_row["id"],)).fetchone()
            check("pop-up purchase recorded at $1.99",
                  ppur is not None and ppur["status"] == "completed"
                  and ppur["amount"] == "1.99")
            pbaby = bcon.execute(
                "SELECT * FROM babies WHERE cert=?",
                (popup["cert"],)).fetchone()
            check("pop-up purchase grants the baby",
                  pbaby is not None
                  and pbaby["owner_id"] == dana_row["id"])
            # Ownership: dana buys Snoot-Duie, then trades her away.
            ld = bcon.execute(
                "SELECT * FROM leaders WHERE name='Snoot-Duie'"
                ).fetchone()
            s, ret_html = buy_and_capture(
                dana, "/buy_leader", {"leader_id": ld["id"]})
            check("leader purchase completes",
                  "Payment complete" in ret_html)
            s, world_html = get(dana, "/world", base=bbase)
            check("world shows dana as Waterliewaveminans owner",
                  "Owner: dana" in world_html)
            s, me_html = get(dana, "/me", base=bbase)
            check("owner home shows Waterliewaveminans ownership",
                  "Owner of the Waterliewaveminans tribe" in me_html)
            erin = http.cookiejar.CookieJar()
            s, _ = signup_full(erin, "erin", "secret9",
                               tribe="Blues", roster="Coral",
                               base=bbase)
            fred = http.cookiejar.CookieJar()
            s, _ = signup_full(fred, "fred", "secret9",
                               tribe="Blues", roster="Pearl",
                               base=bbase)
            s, world_html = get(dana, "/world", base=bbase)
            check("world shows second and third of the tribe",
                  "Second: erin" in world_html
                  and "Third: fred" in world_html)
            leader_baby = bcon.execute(
                "SELECT * FROM babies WHERE cert='EHB-L001'").fetchone()
            s, _ = post(dana, "/toggle_trade",
                        {"baby_id": leader_baby["id"]}, base=bbase)
            s, _ = post(erin, "/offer",
                        {"wanted_baby_id": leader_baby["id"],
                         "offered_points": "0"}, base=bbase)
            off = bcon.execute(
                "SELECT * FROM offers WHERE from_user=(SELECT id FROM "
                "users WHERE username='erin') AND status='pending'"
                ).fetchone()
            check("erin offers for the leader", off is not None)
            s, _ = post(dana, "/offer_action",
                        {"offer_id": off["id"], "action": "accept"},
                        base=bbase)
            s, world_html = get(dana, "/world", base=bbase)
            check("ownership passes when leader is traded",
                  "Owner: erin" in world_html)
            # Dana buys the Shadow pop-up, so a Shadows-tribe baby
            # exists for the gender checks below (the old weekly board
            # listing is gone — Shadows are now a Shop pop-up).
            sh_item = bcon.execute(
                "SELECT * FROM popup_items WHERE tier='shadow' AND "
                "stock>0 ORDER BY id").fetchone()
            check("Shadow pop-up listing exists on bundle server",
                  sh_item is not None)
            s, ret_html = buy_and_capture(
                dana, "/buy_popup", {"popup_id": sh_item["id"]})
            check("Shadow pop-up purchase completes",
                  "Payment complete" in ret_html)
            sh_baby = bcon.execute(
                "SELECT * FROM babies WHERE cert=?",
                (sh_item["cert"],)).fetchone()
            check("Shadow pop-up grants the baby",
                  sh_baby is not None
                  and sh_baby["owner_id"] == dana_row["id"])
            # Gender markers under the corrected rule: leaders female,
            # Shadows and gods male, regular babies unmarked.
            leader_row = bcon.execute(
                "SELECT gender FROM babies WHERE cert='EHB-L001'"
                ).fetchone()
            check("leader baby row is female",
                  leader_row is not None
                  and leader_row["gender"] == "female")
            black_row = bcon.execute(
                "SELECT gender FROM babies WHERE tribe='Blacks'"
                ).fetchone()
            check("Shadow baby row is male",
                  black_row is not None
                  and black_row["gender"] == "male")
            marked_regular = bcon.execute(
                "SELECT COUNT(*) FROM babies WHERE gender IS NOT NULL "
                "AND cert NOT LIKE 'EHB-L%' "
                "AND tribe NOT IN ('Blacks', 'Gods')").fetchone()[0]
            check("regular babies have no stored gender",
                  marked_regular == 0, "marked=%d" % marked_regular)
            s, erin_me = get(erin, "/me", base=bbase)
            check("leader card shows the female marker",
                  "♀" in erin_me)
            # Dana lists her Shadow for trade; erin's board shows it
            # with the male marker.
            s, _ = post(dana, "/toggle_trade",
                        {"baby_id": sh_baby["id"]}, base=bbase)
            s, browse_html = get(erin, "/browse", base=bbase)
            check("Shadow card shows the male marker",
                  "♂" in browse_html)
            s, shop2_html = get(dana, "/shop", base=bbase)
            check("god card shows the male marker",
                  "♂" in shop2_html)
            # Realistic photos over HTTP: the /img route serves the
            # 40 photos, rejects traversal, and cards use them with
            # SVG fallback for babies without a photo.
            def get_raw(path):
                conn = http.client.HTTPConnection(
                    "127.0.0.1", PORT, timeout=10)
                conn.request("GET", path)
                resp = conn.getresponse()
                body = resp.read()
                headers = dict(resp.getheaders())
                conn.close()
                return resp.status, headers, body

            photo_files = [
                "harbor.jpg", "lagoon.jpg", "reef.jpg", "drift.jpg",
                "birch.jpg", "cedar.jpg", "alder.jpg", "aspen.jpg",
                "sol.jpg", "nova.jpg", "zephyr.jpg", "flare.jpg",
                "snoot-duie.jpg", "bea-bee.jpg", "me-me-gal.jpg",
                "muzle-back.jpg", "baraka.jpg", "sirus-v.jpg",
                "black-miner.jpg",
                "marina.jpg", "coral.jpg", "pearl.jpg", "tide.jpg",
                "kai.jpg", "willow.jpg", "fern.jpg", "hazel.jpg",
                "rowan.jpg", "ivy.jpg", "maple.jpg", "juniper.jpg",
                "sage.jpg", "ember.jpg", "storm.jpg", "skye.jpg",
                "ash.jpg", "blaze.jpg", "aurora.jpg", "cinder.jpg",
                "tempest.jpg",
            ]
            photos_ok = True
            for fname in photo_files:
                st, hdrs, body = get_raw("/img/" + fname)
                if not (st == 200
                        and hdrs.get("Content-Type") == "image/jpeg"
                        and len(body) > 5000):
                    photos_ok = False
            check("image route serves all 40 photos", photos_ok)
            st, _, _ = get_raw("/img/../app.py")
            check("image route rejects dot-dot traversal", st == 404)
            st, _, _ = get_raw("/img/%2e%2e%2fapp.py")
            check("image route rejects encoded traversal", st == 404)
            st, _, _ = get_raw("/img/nope.jpg")
            check("image route 404s unknown files", st == 404)
            st, _, _ = get_raw("/img/HARBOR.JPG")
            check("image route is case-sensitive", st == 404)
            s, shop_html = get(http.cookiejar.CookieJar(), "/shop")
            check("shop cards reference realistic photos",
                  "/img/harbor.jpg" in shop_html
                  and "/img/snoot-duie.jpg" in shop_html
                  and "/img/muzle-back.jpg" in shop_html)
            photo_jar = http.cookiejar.CookieJar()
            s, _ = signup_full(photo_jar, "photo", "secret9",
                               tribe="Blues", roster="Marina")
            mcon = sqlite3.connect(DB)
            mcon.row_factory = sqlite3.Row
            photo_row = mcon.execute(
                "SELECT * FROM users WHERE username='photo'").fetchone()
            # A listed Black baby (the weekly sale may already have
            # been bought by this point in the suite, so list one
            # directly for the board check).
            mcon.execute(
                "INSERT INTO babies (cert, owner_id, name, tribe,"
                " hoodie_pattern, stones, face, for_trade)"
                " VALUES (?,?,?,?,?,?,?,1)",
                ("EHB-TEST-BLK", photo_row["id"], "Onyx", "Blacks",
                 "Underground Veil", "black stones on all-white hoodie",
                 "angry creature-like"))
            mcon.commit()
            mcon.close()
            # The board hides the viewer's own babies, so check it
            # with a second logged-in user.
            board_jar = http.cookiejar.CookieJar()
            s, _ = signup_full(board_jar, "board", "secret9",
                               tribe="Reds", roster="Ember")
            s, browse2_html = get(board_jar, "/browse")
            check("board Shadow listing uses the Black photo",
                  "/img/black-miner.jpg" in browse2_html)
            mcon = sqlite3.connect(DB)
            mcon.row_factory = sqlite3.Row
            photo_row = mcon.execute(
                "SELECT * FROM users WHERE username='photo'").fetchone()
            mcon.execute(
                "INSERT INTO babies (cert, owner_id, name, tribe,"
                " hoodie_pattern, stones, face)"
                " VALUES (?,?,?,?,?,?,?)",
                ("EHB-TEST-HARBOR", photo_row["id"], "Harbor", "Blues",
                 "Guiding Star", "yellow + green rhinestones",
                 "fish-like"))
            mcon.commit()
            mcon.close()
            s, me_html = get(photo_jar, "/me")
            check("collection Harbor baby shows its photo",
                  "/img/harbor.jpg" in me_html)
            # A baby with no photo (unknown name) must still render
            # via the SVG portrait engine.
            mcon = sqlite3.connect(DB)
            mcon.execute(
                "INSERT INTO babies (cert, owner_id, name, tribe,"
                " hoodie_pattern, stones, face)"
                " VALUES (?,?,?,?,?,?,?)",
                ("EHB-TEST-ZORP", photo_row["id"], "Zorp", "Blues",
                 "Odd Pattern", "purple stones",
                 "fish-like"))
            mcon.commit()
            mcon.close()
            s, me_html = get(photo_jar, "/me")
            check("collection baby without a photo keeps SVG fallback",
                  "<svg" in me_html and "Zorp" in me_html)
            bcon.close()
        finally:
            bproc.terminate()
            try:
                bproc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                bproc.kill()

        # 19. Renames, 600 start, new packs, pop-up tiers, villages,
        # invite bonus, Shadow $10 shop buy, no "Blacks" tribe name.
        nport, ndb = 8103, "/tmp/escamo_new_test.db"
        nproc, nbase = start_server(
            nport, ndb, {"PAYPAL_MOCK": "1", "STRIPE_MOCK": "1"})
        try:
            def n_buy(jar, path, data):
                s, hdrs, _ = post_noredirect(jar, path, data,
                                             base=nbase)
                loc = hdrs.get("Location", "")
                if "buy_return" not in loc and "stripe_return" not in loc:
                    return s, ""
                parsed = urllib.parse.urlparse(loc)
                ret = parsed.path + ("?" + parsed.query
                                     if parsed.query else "")
                return get(jar, ret, base=nbase)

            nova = http.cookiejar.CookieJar()
            s, _ = signup_full(nova, "nova", "secret9", base=nbase)
            check("nova signs up on new-features server", s in (200, 302))
            ncon = sqlite3.connect(ndb)
            ncon.row_factory = sqlite3.Row
            nova_row = ncon.execute(
                "SELECT * FROM users WHERE username='nova'").fetchone()
            check("new signup grants 600 points, 0 earned",
                  nova_row["points"] == 600
                  and nova_row["points_earned"] == 0,
                  "points=%d earned=%d" % (nova_row["points"],
                                           nova_row["points_earned"]))
            s, home_html = get(nova, "/", base=nbase)
            check("home shows 600 starting points",
                  "600 points" in home_html)
            # Physical-figure disclaimer: exact wording, small print
            # but legible, on Home and Shop (it is site-wide via the
            # page footer, so it appears everywhere).
            notice = ("Note: the original Hoodie Babies figure will not "
                      "be released until further notice — but actual, "
                      "physical Hoodie Babies are coming. Nothing is set "
                      "in stone yet.")
            check("home shows the physical-figure disclaimer",
                  notice in home_html)
            check("home disclaimer is small print, not hidden",
                  "font-size:13px" in home_html
                  and "display:none" not in home_html
                  and "visibility:hidden" not in home_html)
            s, world2 = get(nova, "/world", base=nbase)
            check("world shows renamed tribes",
                  "Waterliewaveminans" in world2
                  and "Fortress Rooted" in world2
                  and "Windyworrors" in world2
                  and "Shadows" in world2)
            check("world shows no Blacks tribe name",
                  "Blacks" not in world2)
            s, signup_html = get(nova, "/signup", base=nbase)
            check("signup shows renamed tribes, no Blacks",
                  "Waterliewaveminans" in signup_html
                  and "Fortress Rooted" in signup_html
                  and "Windyworrors" in signup_html
                  and "Blacks" not in signup_html)
            s, shop2 = get(nova, "/shop", base=nbase)
            check("shop shows the physical-figure disclaimer",
                  notice in shop2)
            check("shop disclaimer is small print, not hidden",
                  "font-size:13px" in shop2
                  and "display:none" not in shop2)
            check("shop shows new points packs",
                  "300 points" in shop2 and "4.99" in shop2
                  and "600 points" in shop2 and "9.99" in shop2
                  and "1200 points" in shop2 and "20.00" in shop2)
            check("shop shows no 900 pack",
                  "900 points" not in shop2)
            check("shop shows drifter $1.99",
                  "Drifter pop-up" in shop2)
            check("shop shows special $4.99, 25 of 25 left",
                  "Special pop-up" in shop2
                  and "Only 25 of 25 left" in shop2)
            check("shop shows rare $9.99, 8 of 8 left",
                  "Rare pop-up" in shop2
                  and "Only 8 of 8 left" in shop2)
            check("shop shows Shadow pop-up $10.00",
                  "Shadow pop-up" in shop2 and "$10.00" in shop2)
            check("shop shows no Blacks tribe name",
                  "Blacks" not in shop2)
            # Drifter buy (PayPal) at $1.99.
            d = ncon.execute(
                "SELECT * FROM popup_items WHERE tier='drifter' AND "
                "stock>0 ORDER BY id").fetchone()
            check("drifter listing exists", d is not None)
            s, ret_html = n_buy(nova, "/buy_popup", {"popup_id": d["id"]})
            check("drifter purchase completes",
                  "Payment complete" in ret_html, "status=%s" % s)
            dpur = ncon.execute(
                "SELECT * FROM purchases WHERE user_id=? AND item_type="
                "'popup' ORDER BY id DESC",
                (nova_row["id"],)).fetchone()
            check("drifter purchase recorded at $1.99",
                  dpur is not None and dpur["amount"] == "1.99"
                  and dpur["status"] == "completed")
            # Special buys: two copies, certs suffixed, stock drops.
            sp = ncon.execute(
                "SELECT * FROM popup_items WHERE tier='special' AND "
                "stock>0 ORDER BY id").fetchone()
            check("special listing exists", sp is not None)
            s, ret_html = n_buy(nova, "/buy_popup", {"popup_id": sp["id"]})
            check("special purchase completes",
                  "Payment complete" in ret_html)
            sp1 = ncon.execute(
                "SELECT * FROM babies WHERE cert=?",
                (sp["cert"] + "-01",)).fetchone()
            check("first special copy gets -01 cert",
                  sp1 is not None and sp1["owner_id"] == nova_row["id"])
            sp_row = ncon.execute(
                "SELECT * FROM popup_items WHERE id=?",
                (sp["id"],)).fetchone()
            check("special stock drops to 24", sp_row["stock"] == 24,
                  "got %s" % sp_row["stock"])
            s, ret_html = n_buy(nova, "/buy_popup_stripe",
                                {"popup_id": sp["id"]})
            check("second special purchase completes via Cash App Pay",
                  "Payment complete" in ret_html)
            sp2 = ncon.execute(
                "SELECT * FROM babies WHERE cert=?",
                (sp["cert"] + "-02",)).fetchone()
            check("second special copy gets -02 cert", sp2 is not None)
            sp_row = ncon.execute(
                "SELECT * FROM popup_items WHERE id=?",
                (sp["id"],)).fetchone()
            check("special stock drops to 23", sp_row["stock"] == 23,
                  "got %s" % sp_row["stock"])
            # Rare buy (Stripe) at $9.99, stock 8 -> 7.
            ra = ncon.execute(
                "SELECT * FROM popup_items WHERE tier='rare' AND "
                "stock>0 ORDER BY id").fetchone()
            check("rare listing exists", ra is not None)
            s, ret_html = n_buy(nova, "/buy_popup_stripe",
                                {"popup_id": ra["id"]})
            check("rare purchase completes",
                  "Payment complete" in ret_html)
            ra_row = ncon.execute(
                "SELECT * FROM popup_items WHERE id=?",
                (ra["id"],)).fetchone()
            check("rare stock drops to 7", ra_row["stock"] == 7,
                  "got %s" % ra_row["stock"])
            # Shadow buy (PayPal) at $10.00.
            sh = ncon.execute(
                "SELECT * FROM popup_items WHERE tier='shadow' AND "
                "stock>0 ORDER BY id").fetchone()
            check("Shadow listing exists", sh is not None)
            s, ret_html = n_buy(nova, "/buy_popup", {"popup_id": sh["id"]})
            check("Shadow purchase completes",
                  "Payment complete" in ret_html)
            shpur = ncon.execute(
                "SELECT * FROM purchases WHERE user_id=? AND item_type="
                "'popup' AND amount='10.00' ORDER BY id DESC",
                (nova_row["id"],)).fetchone()
            check("Shadow purchase recorded at $10.00",
                  shpur is not None and shpur["status"] == "completed")
            shbaby = ncon.execute(
                "SELECT * FROM babies WHERE cert=?",
                (sh["cert"],)).fetchone()
            check("Shadow purchase grants a Shadows-tribe baby",
                  shbaby is not None
                  and shbaby["owner_id"] == nova_row["id"]
                  and shbaby["tribe"] == "Blacks")
            # Sold-out listing disappears and cannot be bought.
            ncon.execute("UPDATE popup_items SET stock=0, sold=1 "
                         "WHERE id=?", (sp["id"],))
            ncon.commit()
            s, shop3 = get(nova, "/shop", base=nbase)
            check("sold-out special disappears from shop",
                  "Special pop-up" not in shop3)
            s, resp = post(nova, "/buy_popup", {"popup_id": sp["id"]},
                           base=nbase)
            check("sold-out special cannot be bought",
                  "no longer for sale" in resp)
            # Village: nova now has free baby + drifter + 2 specials +
            # rare + Shadow = 6 non-leader babies.
            vcount, _ = escamo_app.village_progress(ncon, nova_row["id"])
            check("village counts 6 non-leader babies", vcount == 6,
                  "got %d" % vcount)
            # Fill to 39 (no complete), then 40 (complete).
            for i in range(33):
                ncon.execute(
                    "INSERT INTO babies (cert, owner_id, name, tribe,"
                    " hoodie_pattern, stones, face)"
                    " VALUES (?,?,?,?,?,?,?)",
                    ("EHB-V%03d" % i, nova_row["id"], "Villager%d" % i,
                     "Blues", "Village Weave", "yellow rhinestones",
                     "fish-like"))
            ncon.commit()
            vcount, _ = escamo_app.village_progress(ncon, nova_row["id"])
            check("village counts 39 before completion", vcount == 39,
                  "got %d" % vcount)
            s, me2 = get(nova, "/me", base=nbase)
            check("collection shows village 39 / 40, not complete",
                  "My Village: 39 / 40" in me2
                  and "Village complete!" not in me2)
            ncon.execute(
                "INSERT INTO babies (cert, owner_id, name, tribe,"
                " hoodie_pattern, stones, face)"
                " VALUES (?,?,?,?,?,?,?)",
                ("EHB-V999", nova_row["id"], "VillagerX",
                 "Reds", "Village Weave", "red rhinestones",
                 "angel-like"))
            ncon.commit()
            s, me3 = get(nova, "/me", base=nbase)
            check("collection shows village complete at 40",
                  "My Village: 40 / 40" in me3
                  and "Village complete!" in me3)
            # A leader purchase does not raise the village count.
            ld = ncon.execute(
                "SELECT * FROM leaders WHERE name='Snoot-Duie'"
                ).fetchone()
            s, ret_html = n_buy(nova, "/buy_leader",
                                {"leader_id": ld["id"]})
            check("leader purchase completes on new server",
                  "Payment complete" in ret_html)
            vcount, vnames = escamo_app.village_progress(
                ncon, nova_row["id"])
            check("leader excluded from village count", vcount == 40,
                  "got %d" % vcount)
            check("village progress names the owned leader",
                  "Snoot-Duie" in vnames)
            s, me4 = get(nova, "/me", base=nbase)
            check("collection shows village leader alongside",
                  "Village leader: Snoot-Duie" in me4)
            # Invite bonus: a friend joining with nova's code pays +20.
            friend = http.cookiejar.CookieJar()
            s, _ = signup_full(friend, "friend1", "secret9",
                               tribe="Browns", roster="Fern",
                               invite=nova_row["invite_code"],
                               base=nbase)
            check("invited friend signs up", s in (200, 302))
            ncon2 = sqlite3.connect(ndb)
            ncon2.row_factory = sqlite3.Row
            nova2 = ncon2.execute(
                "SELECT * FROM users WHERE username='nova'").fetchone()
            check("inviter gains +20 balance and +20 earned",
                  nova2["points"] == 620
                  and nova2["points_earned"] == 20,
                  "points=%d earned=%d" % (nova2["points"],
                                           nova2["points_earned"]))
            s, me5 = get(nova, "/me", base=nbase)
            check("collection shows invite bonus line",
                  "Invite bonus: +20 points" in me5)
            # 20. Baby detail viewer: fullscreen zoom + drag-to-spin
            # 360 (front photo + back-details card) + Make-it-real
            # note, linked from collection, shop, board, and the
            # purchase confirmation.
            nbaby = ncon.execute(
                "SELECT * FROM babies WHERE owner_id=? ORDER BY id",
                (nova_row["id"],)).fetchone()
            check("nova has a baby to view", nbaby is not None)
            ncert = nbaby["cert"]
            s, detail = get(nova, "/baby/" + ncert, base=nbase)
            check("detail page renders", s == 200
                  and nbaby["name"] in detail)
            check("detail page has the spin viewer",
                  "spin-stage" in detail
                  and "spin-prev" in detail
                  and "spin-next" in detail
                  and "Front view" in detail
                  and "Back view" in detail)
            check("detail back view is honestly a details card, "
                  "not a photo",
                  "not a photo of the back yet" in detail)
            check("detail back view shows pattern, stones, tribe, "
                  "cert",
                  nbaby["hoodie_pattern"] in detail
                  and nbaby["stones"] in detail
                  and ("Stones: %d" % escamo_app.stone_count(
                      nbaby["tribe"], nbaby["cert"],
                      nbaby["name"])) in detail
                  and escamo_app.tribe_display(
                      nbaby["tribe"]) in detail
                  and ncert in detail)
            check("detail page has fullscreen zoom elements",
                  "fs-overlay" in detail
                  and "fs-zoom-in" in detail
                  and "fs-zoom-out" in detail
                  and "Blow it up fullscreen" in detail)
            check("detail page has the Make-it-real note",
                  "Make it real" in detail
                  and "made real, exactly as it appears online"
                  in detail)
            check("detail page promises no order or payment",
                  "nothing to order or pay for yet" in detail)
            # Unknown certificate 404s.
            try:
                s, _ = get(nova, "/baby/NOPE-123", base=nbase)
                notfound = s
            except urllib.error.HTTPError as e:
                notfound = e.code
            check("unknown baby detail 404s", notfound == 404)
            # A shop item (not yet a baby) has a detail page too.
            sitem = ncon.execute(
                "SELECT * FROM shop_items WHERE sold=0 ORDER BY id"
                ).fetchone()
            check("unsold shop item exists", sitem is not None)
            s, sdetail = get(nova, "/baby/" + sitem["cert"],
                             base=nbase)
            check("shop item detail page renders",
                  s == 200 and sitem["name"] in sdetail
                  and "spin-stage" in sdetail)
            # Links into the viewer from collection, shop, board.
            s, me6 = get(nova, "/me", base=nbase)
            check("collection links to the detail viewer",
                  ("/baby/" + ncert) in me6)
            s, shop4 = get(nova, "/shop", base=nbase)
            check("shop links to the detail viewer",
                  "/baby/" in shop4
                  and ("/baby/" + sitem["cert"]) in shop4)
            # A second user's baby on the board links to its viewer.
            fr_row = ncon2.execute(
                "SELECT * FROM users WHERE username='friend1'"
                ).fetchone()
            fr_baby = ncon2.execute(
                "SELECT * FROM babies WHERE owner_id=? ORDER BY id",
                (fr_row["id"],)).fetchone()
            ncon2.execute("UPDATE babies SET for_trade=1 WHERE id=?",
                          (fr_baby["id"],))
            ncon2.commit()
            s, brow = get(nova, "/browse", base=nbase)
            check("trade board links to the detail viewer",
                  ("/baby/" + fr_baby["cert"]) in brow)
            # Purchase confirmation links to the new baby's viewer.
            it = ncon.execute(
                "SELECT * FROM shop_items WHERE sold=0 ORDER BY id"
                ).fetchone()
            s, ret_html = n_buy(nova, "/buy",
                                {"shop_item_id": it["id"]})
            check("individual purchase completes",
                  "Payment complete" in ret_html)
            check("purchase confirmation links to detail viewer",
                  "View your baby" in ret_html
                  and ("/baby/" + it["cert"]) in ret_html)
            # No "Blacks" tribe name on any user-facing page.
            for path in ("/", "/world", "/shop", "/signup", "/me",
                         "/browse", "/notices"):
                s, html = get(nova, path, base=nbase)
                check("no Blacks tribe name on %s" % path,
                      "Blacks" not in html)
            # Share button in the nav on every page: phone share
            # sheet when available, clipboard fallback with an inline
            # notice otherwise. Shares the CURRENT page URL.
            anon = http.cookiejar.CookieJar()
            s, home = get(anon, "/", base=nbase)
            check("share button on logged-out home page",
                  "id='share-btn'" in home and ">Share</button>" in home)
            check("share script has phone share-sheet branch",
                  "navigator.share" in home)
            check("share script has clipboard fallback branch",
                  "navigator.clipboard" in home
                  and "clipboard.writeText" in home)
            check("share fallback shows Link copied notice",
                  "Link copied" in home
                  and "paste it anywhere!" in home
                  and "id='share-notice'" in home)
            check("share text names the platform and 600 points",
                  "Hoodie Babies Trade" in home
                  and "600 points" in home)
            check("share uses the current page URL, not a hardcoded one",
                  "window.location.href" in home)
            s, shop5 = get(nova, "/shop", base=nbase)
            check("share button on logged-in shop page",
                  "id='share-btn'" in shop5)
            s, me7 = get(nova, "/me", base=nbase)
            check("share button on logged-in collection page",
                  "id='share-btn'" in me7)
            ncon.close()
            ncon2.close()
        finally:
            nproc.terminate()
            try:
                nproc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                nproc.kill()

        # 20. Points are NEVER a purchase currency: shop and pop-up
        # buys are money only (PayPal/Stripe). Points move only inside
        # trade offers, to the other trader on accept.
        pport, pdb = 8104, "/tmp/escamo_points_currency_test.db"
        pproc, pbase = start_server(
            pport, pdb,
            {"PAYPAL_MOCK": "", "STRIPE_MOCK": "",
             "PAYPAL_CLIENT_ID": "", "PAYPAL_CLIENT_SECRET": "",
             "STRIPE_SECRET_KEY": ""})
        try:
            rich = http.cookiejar.CookieJar()
            s, _ = signup_full(rich, "rich", "secret9", base=pbase)
            check("points-currency user signs up", s in (200, 302))
            pcon = sqlite3.connect(pdb)
            pcon.row_factory = sqlite3.Row
            rich_row = pcon.execute(
                "SELECT * FROM users WHERE username='rich'").fetchone()
            # Make the user rich in points: even 99,999 points must
            # not buy anything without money going through a provider.
            pcon.execute("UPDATE users SET points=99999 WHERE id=?",
                         (rich_row["id"],))
            pcon.commit()
            # A shop visit ensures the weekly pop-up listings exist.
            s, shop4 = get(rich, "/shop", base=pbase)
            pcon = sqlite3.connect(pdb)
            pcon.row_factory = sqlite3.Row
            item = pcon.execute(
                "SELECT * FROM shop_items WHERE sold=0 ORDER BY id"
                ).fetchone()
            ld = pcon.execute(
                "SELECT * FROM leaders WHERE sold=0 ORDER BY id"
                ).fetchone()
            gd = pcon.execute(
                "SELECT * FROM gods WHERE sold=0 ORDER BY id").fetchone()
            pop = pcon.execute(
                "SELECT * FROM popup_items WHERE stock>0 ORDER BY id"
                ).fetchone()
            for path, data in (
                    ("/buy", {"shop_item_id": item["id"]}),
                    ("/buy_stripe", {"shop_item_id": item["id"]}),
                    ("/buy_leader", {"leader_id": ld["id"]}),
                    ("/buy_leader_stripe", {"leader_id": ld["id"]}),
                    ("/buy_god", {"god_id": gd["id"]}),
                    ("/buy_god_stripe", {"god_id": gd["id"]}),
                    ("/buy_popup", {"popup_id": pop["id"]}),
                    ("/buy_popup_stripe", {"popup_id": pop["id"]}),
                    ("/buy_points", {"pack": "300"}),
                    ("/buy_points_stripe", {"pack": "300"})):
                s, resp = post(rich, path, data, base=pbase)
                check("no-provider %s refuses the buy" % path,
                      "not set up yet" in resp, "status=%s" % s)
            babies = pcon.execute(
                "SELECT COUNT(*) FROM babies WHERE owner_id=?",
                (rich_row["id"],)).fetchone()[0]
            check("no-provider buys grant no babies", babies == 1,
                  "got %d" % babies)
            purs = pcon.execute(
                "SELECT COUNT(*) FROM purchases WHERE user_id=?",
                (rich_row["id"],)).fetchone()[0]
            check("no-provider buys record no purchases", purs == 0,
                  "got %d" % purs)
            # Shop buy forms all point at money-provider routes (or
            # the free monthly-gift claim, which is not a purchase);
            # none offers to pay with points.
            actions = set(re.findall(r"action='([^']+)'", shop4))
            allowed = {"/buy", "/buy_stripe", "/buy_leader",
                       "/buy_leader_stripe", "/buy_god",
                       "/buy_god_stripe", "/buy_points",
                       "/buy_points_stripe", "/buy_popup",
                       "/buy_popup_stripe", "/buy_popup_points",
                       "/claim_gift"}
            check("shop forms are money routes, the free gift, or "
                  "the points freebie only",
                  actions <= allowed, "actions=%s" % sorted(actions))
            check("shop offers pay-with-points only on the freebie",
                  "Buy with 120 points" in shop4
                  and shop4.count("Buy with") == 1)
            # (b) Trade points BURN: offered points leave the giver
            # and reach nobody; each trader still gets the +20 award.
            giver = http.cookiejar.CookieJar()
            taker = http.cookiejar.CookieJar()
            s, _ = signup_full(giver, "giver", "secret9",
                               tribe="Blues", roster="Marina", base=pbase)
            check("giver signs up", s in (200, 302))
            s, _ = signup_full(taker, "taker", "secret9",
                               tribe="Browns", roster="Willow", base=pbase)
            check("taker signs up", s in (200, 302))
            pcon2 = sqlite3.connect(pdb)
            pcon2.row_factory = sqlite3.Row
            giver_row = pcon2.execute(
                "SELECT * FROM users WHERE username='giver'").fetchone()
            taker_row = pcon2.execute(
                "SELECT * FROM users WHERE username='taker'").fetchone()
            giver_baby = pcon2.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (giver_row["id"],)).fetchone()
            taker_baby = pcon2.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (taker_row["id"],)).fetchone()
            s, _ = post(taker, "/toggle_trade",
                        {"baby_id": taker_baby["id"]}, base=pbase)
            # Offer: giver's baby + exactly 75 points for taker's baby.
            s, _ = post(giver, "/offer",
                        {"wanted_baby_id": taker_baby["id"],
                         "offered_baby_id": giver_baby["id"],
                         "offered_points": "75"}, base=pbase)
            off = pcon2.execute(
                "SELECT * FROM offers WHERE from_user=? AND to_user=? "
                "AND status='pending'",
                (giver_row["id"], taker_row["id"])).fetchone()
            check("points offer recorded as pending", off is not None)
            s, _ = post(taker, "/offer_action",
                        {"offer_id": off["id"], "action": "accept"},
                        base=pbase)
            pcon3 = sqlite3.connect(pdb)
            pcon3.row_factory = sqlite3.Row
            g2 = pcon3.execute(
                "SELECT * FROM users WHERE username='giver'").fetchone()
            t2 = pcon3.execute(
                "SELECT * FROM users WHERE username='taker'").fetchone()
            # giver: 600 - 75 offered + 20 award = 545; taker: 600 +
            # 20 award only — the 75 offered points burn, nobody
            # receives them; each earned +20 (award kept).
            check("offered points leave the giver exactly",
                  g2["points"] == 545, "got %d" % g2["points"])
            check("offered points do NOT reach the taker (burned)",
                  t2["points"] == 620, "got %d" % t2["points"])
            check("trade award still +20 earned each",
                  g2["points_earned"] == 20
                  and t2["points_earned"] == 20)
            # Points-only offer (no baby): 40 points move on accept.
            giver2 = http.cookiejar.CookieJar()
            taker2 = http.cookiejar.CookieJar()
            s, _ = signup_full(giver2, "giver2", "secret9",
                               tribe="Reds", roster="Ember", base=pbase)
            s, _ = signup_full(taker2, "taker2", "secret9",
                               tribe="Blues", roster="Coral", base=pbase)
            pcon4 = sqlite3.connect(pdb)
            pcon4.row_factory = sqlite3.Row
            g2r = pcon4.execute(
                "SELECT * FROM users WHERE username='giver2'").fetchone()
            t2r = pcon4.execute(
                "SELECT * FROM users WHERE username='taker2'").fetchone()
            t2baby = pcon4.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (t2r["id"],)).fetchone()
            s, _ = post(taker2, "/toggle_trade",
                        {"baby_id": t2baby["id"]}, base=pbase)
            s, _ = post(giver2, "/offer",
                        {"wanted_baby_id": t2baby["id"],
                         "offered_points": "40"}, base=pbase)
            off2 = pcon4.execute(
                "SELECT * FROM offers WHERE from_user=? AND to_user=? "
                "AND status='pending'",
                (g2r["id"], t2r["id"])).fetchone()
            check("points-only offer recorded", off2 is not None)
            s, _ = post(taker2, "/offer_action",
                        {"offer_id": off2["id"], "action": "accept"},
                        base=pbase)
            pcon5 = sqlite3.connect(pdb)
            pcon5.row_factory = sqlite3.Row
            ga = pcon5.execute(
                "SELECT * FROM users WHERE username='giver2'").fetchone()
            ta = pcon5.execute(
                "SELECT * FROM users WHERE username='taker2'").fetchone()
            check("points-only offer burns the 40 points",
                  ga["points"] == 580 and ta["points"] == 620,
                  "giver=%d taker=%d" % (ga["points"], ta["points"]))
            # (b2) Counter-offer with points: the counter's points
            # burn too — the counter offerer loses them, the receiver
            # gains only the +20 award.
            giver3 = http.cookiejar.CookieJar()
            taker3 = http.cookiejar.CookieJar()
            s, _ = signup_full(giver3, "giver3", "secret9",
                               tribe="Browns", roster="Fern", base=pbase)
            s, _ = signup_full(taker3, "taker3", "secret9",
                               tribe="Reds", roster="Storm", base=pbase)
            pcon6 = sqlite3.connect(pdb)
            pcon6.row_factory = sqlite3.Row
            g3r = pcon6.execute(
                "SELECT * FROM users WHERE username='giver3'").fetchone()
            t3r = pcon6.execute(
                "SELECT * FROM users WHERE username='taker3'").fetchone()
            g3baby = pcon6.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (g3r["id"],)).fetchone()
            t3baby = pcon6.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (t3r["id"],)).fetchone()
            s, _ = post(taker3, "/toggle_trade",
                        {"baby_id": t3baby["id"]}, base=pbase)
            s, _ = post(giver3, "/offer",
                        {"wanted_baby_id": t3baby["id"],
                         "offered_baby_id": g3baby["id"],
                         "offered_points": "30"}, base=pbase)
            off3 = pcon6.execute(
                "SELECT * FROM offers WHERE from_user=? AND to_user=? "
                "AND status='pending'",
                (g3r["id"], t3r["id"])).fetchone()
            check("counter test offer recorded", off3 is not None)
            s, _ = post(taker3, "/offer_action",
                        {"offer_id": off3["id"], "action": "counter",
                         "counter_baby_id": t3baby["id"],
                         "counter_points": "25"}, base=pbase)
            pcon7 = sqlite3.connect(pdb)
            pcon7.row_factory = sqlite3.Row
            coff = pcon7.execute(
                "SELECT * FROM offers WHERE from_user=? AND to_user=? "
                "AND status='pending'",
                (t3r["id"], g3r["id"])).fetchone()
            check("counter-offer recorded with points",
                  coff is not None and coff["offered_points"] == 25)
            s, _ = post(giver3, "/offer_action",
                        {"offer_id": coff["id"], "action": "accept"},
                        base=pbase)
            pcon8 = sqlite3.connect(pdb)
            pcon8.row_factory = sqlite3.Row
            g3 = pcon8.execute(
                "SELECT * FROM users WHERE username='giver3'").fetchone()
            t3 = pcon8.execute(
                "SELECT * FROM users WHERE username='taker3'").fetchone()
            # counter offerer (taker3): 600 - 25 + 20 = 595;
            # counter receiver (giver3): 600 + 20 only; earned 20 each.
            check("counter points burn for the counter offerer",
                  t3["points"] == 595, "got %d" % t3["points"])
            check("counter points do NOT reach the receiver",
                  g3["points"] == 620, "got %d" % g3["points"])
            check("counter trade awards +20 earned each",
                  g3["points_earned"] == 20
                  and t3["points_earned"] == 20)
            pcon6.close()
            pcon7.close()
            pcon8.close()
            # (c) A declined offer moves no points at all.
            giver4 = http.cookiejar.CookieJar()
            taker4 = http.cookiejar.CookieJar()
            s, _ = signup_full(giver4, "giver4", "secret9",
                               tribe="Browns", roster="Hazel", base=pbase)
            s, _ = signup_full(taker4, "taker4", "secret9",
                               tribe="Reds", roster="Skye", base=pbase)
            pcon9 = sqlite3.connect(pdb)
            pcon9.row_factory = sqlite3.Row
            g4r = pcon9.execute(
                "SELECT * FROM users WHERE username='giver4'").fetchone()
            t4r = pcon9.execute(
                "SELECT * FROM users WHERE username='taker4'").fetchone()
            t4baby = pcon9.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (t4r["id"],)).fetchone()
            g4baby = pcon9.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (g4r["id"],)).fetchone()
            s, _ = post(taker4, "/toggle_trade",
                        {"baby_id": t4baby["id"]}, base=pbase)
            s, _ = post(giver4, "/offer",
                        {"wanted_baby_id": t4baby["id"],
                         "offered_baby_id": g4baby["id"],
                         "offered_points": "50"}, base=pbase)
            off4 = pcon9.execute(
                "SELECT * FROM offers WHERE from_user=? AND to_user=? "
                "AND status='pending'",
                (g4r["id"], t4r["id"])).fetchone()
            check("decline test offer recorded", off4 is not None)
            s, _ = post(taker4, "/offer_action",
                        {"offer_id": off4["id"], "action": "decline"},
                        base=pbase)
            pcon10 = sqlite3.connect(pdb)
            pcon10.row_factory = sqlite3.Row
            g4 = pcon10.execute(
                "SELECT * FROM users WHERE username='giver4'").fetchone()
            t4 = pcon10.execute(
                "SELECT * FROM users WHERE username='taker4'").fetchone()
            off4b = pcon10.execute(
                "SELECT * FROM offers WHERE id=?",
                (off4["id"],)).fetchone()
            check("declined offer moves no points",
                  g4["points"] == 600 and t4["points"] == 600
                  and g4["points_earned"] == 0
                  and t4["points_earned"] == 0
                  and off4b["status"] == "declined",
                  "giver=%d taker=%d" % (g4["points"], t4["points"]))
            pcon9.close()
            pcon10.close()
            # (d-f) Points gifts: the only player-to-player points move.
            s, gifts_html = get(giver4, "/gifts", base=pbase)
            check("gifts page has a send form and explanation",
                  "Send a points gift" in gifts_html
                  and "only" in gifts_html
                  and "does not receive" in gifts_html)
            check("nav links to Gifts",
                  "/gifts" in gifts_html)
            gift1 = http.cookiejar.CookieJar()
            gift2 = http.cookiejar.CookieJar()
            s, _ = signup_full(gift1, "gift1", "secret9",
                               tribe="Blues", roster="Pearl", base=pbase)
            s, _ = signup_full(gift2, "gift2", "secret9",
                               tribe="Browns", roster="Rowan", base=pbase)
            pcong = sqlite3.connect(pdb)
            pcong.row_factory = sqlite3.Row
            gg1 = pcong.execute(
                "SELECT * FROM users WHERE username='gift1'").fetchone()
            gg2 = pcong.execute(
                "SELECT * FROM users WHERE username='gift2'").fetchone()
            s, _ = post(gift1, "/gift_send",
                        {"to_username": "gift2", "points": "100"},
                        base=pbase)
            pg = pcong.execute(
                "SELECT * FROM point_gifts WHERE from_user=? AND "
                "to_user=? AND status='pending'",
                (gg1["id"], gg2["id"])).fetchone()
            check("gift sits pending after send", pg is not None
                  and pg["points"] == 100)
            s, me2_html = get(gift2, "/me", base=pbase)
            check("recipient sees the pending gift on My Collection",
                  "sent you" in me2_html and "100 points" in me2_html)
            s, _ = post(gift2, "/gift_action",
                        {"gift_id": pg["id"], "action": "accept"},
                        base=pbase)
            pcong2 = sqlite3.connect(pdb)
            pcong2.row_factory = sqlite3.Row
            a1 = pcong2.execute(
                "SELECT * FROM users WHERE username='gift1'").fetchone()
            a2 = pcong2.execute(
                "SELECT * FROM users WHERE username='gift2'").fetchone()
            pg2 = pcong2.execute(
                "SELECT * FROM point_gifts WHERE id=?",
                (pg["id"],)).fetchone()
            # balance only: 500 / 700, earned unchanged at 0 both sides.
            check("gift accept moves balance sender to receiver",
                  a1["points"] == 500 and a2["points"] == 700
                  and pg2["status"] == "accepted",
                  "gift1=%d gift2=%d" % (a1["points"], a2["points"]))
            check("gift accept never changes points earned",
                  a1["points_earned"] == 0 and a2["points_earned"] == 0)
            # (e) Decline and cancel move nothing.
            s, _ = post(gift1, "/gift_send",
                        {"to_username": "gift2", "points": "50"},
                        base=pbase)
            pgd = pcong2.execute(
                "SELECT * FROM point_gifts WHERE from_user=? AND "
                "to_user=? AND status='pending'",
                (a1["id"], a2["id"])).fetchone()
            check("second gift pending", pgd is not None)
            s, _ = post(gift2, "/gift_action",
                        {"gift_id": pgd["id"], "action": "decline"},
                        base=pbase)
            s, _ = post(gift1, "/gift_send",
                        {"to_username": "gift2", "points": "30"},
                        base=pbase)
            pgc = pcong2.execute(
                "SELECT * FROM point_gifts WHERE from_user=? AND "
                "to_user=? AND status='pending'",
                (a1["id"], a2["id"])).fetchone()
            check("third gift pending", pgc is not None)
            s, _ = post(gift1, "/gift_action",
                        {"gift_id": pgc["id"], "action": "cancel"},
                        base=pbase)
            pcong3 = sqlite3.connect(pdb)
            pcong3.row_factory = sqlite3.Row
            b1 = pcong3.execute(
                "SELECT * FROM users WHERE username='gift1'").fetchone()
            b2 = pcong3.execute(
                "SELECT * FROM users WHERE username='gift2'").fetchone()
            pgd2 = pcong3.execute(
                "SELECT * FROM point_gifts WHERE id=?",
                (pgd["id"],)).fetchone()
            pgc2 = pcong3.execute(
                "SELECT * FROM point_gifts WHERE id=?",
                (pgc["id"],)).fetchone()
            check("gift decline and cancel move nothing",
                  b1["points"] == 500 and b2["points"] == 700
                  and pgd2["status"] == "declined"
                  and pgc2["status"] == "cancelled",
                  "gift1=%d gift2=%d" % (b1["points"], b2["points"]))
            # (f) Gifts to self and over-balance gifts are refused.
            def gift_count():
                c = sqlite3.connect(pdb)
                c.row_factory = sqlite3.Row
                n = c.execute(
                    "SELECT COUNT(*) FROM point_gifts WHERE from_user=?",
                    (b1["id"],)).fetchone()[0]
                c.close()
                return n
            before = gift_count()
            s, resp_self = post(gift1, "/gift_send",
                                {"to_username": "gift1", "points": "10"},
                                base=pbase)
            check("gift to self refused",
                  gift_count() == before and "yourself" in resp_self)
            s, resp_big = post(gift1, "/gift_send",
                               {"to_username": "gift2", "points": "9999"},
                               base=pbase)
            check("gift over balance refused",
                  gift_count() == before and "only have" in resp_big)
            # Accept when the sender's balance has since dropped:
            # fails, leaves the gift pending, moves nothing.
            s, _ = post(gift1, "/gift_send",
                        {"to_username": "gift2", "points": "100"},
                        base=pbase)
            pgl = sqlite3.connect(pdb)
            pgl.row_factory = sqlite3.Row
            pgl.execute("UPDATE users SET points=50 WHERE username="
                        "'gift1'")
            pgl.commit()
            pglate = pgl.execute(
                "SELECT * FROM point_gifts WHERE from_user=? AND "
                "to_user=? AND status='pending'",
                (b1["id"], b2["id"])).fetchone()
            check("late gift pending before accept", pglate is not None)
            s, resp_late = post(gift2, "/gift_action",
                                {"gift_id": pglate["id"],
                                 "action": "accept"}, base=pbase)
            pgl2 = sqlite3.connect(pdb)
            pgl2.row_factory = sqlite3.Row
            c1 = pgl2.execute(
                "SELECT * FROM users WHERE username='gift1'").fetchone()
            c2 = pgl2.execute(
                "SELECT * FROM users WHERE username='gift2'").fetchone()
            pglate2 = pgl2.execute(
                "SELECT * FROM point_gifts WHERE id=?",
                (pglate["id"],)).fetchone()
            check("gift accept with dropped balance fails, stays pending",
                  "no longer holds" in resp_late
                  and pglate2["status"] == "pending"
                  and c1["points"] == 50 and c2["points"] == 700,
                  "gift1=%d gift2=%d" % (c1["points"], c2["points"]))
            # (g) Points freebies, even swaps, and the points market.
            fb = http.cookiejar.CookieJar()
            ms = http.cookiejar.CookieJar()
            mb = http.cookiejar.CookieJar()
            s, _ = signup_full(fb, "freebie", "secret9",
                               tribe="Blues", roster="Coral", base=pbase)
            s, _ = signup_full(ms, "mseller", "secret9",
                               tribe="Browns", roster="Fern", base=pbase)
            s, _ = signup_full(mb, "mbuyer", "secret9",
                               tribe="Reds", roster="Ember", base=pbase)
            pcong4 = sqlite3.connect(pdb)
            pcong4.row_factory = sqlite3.Row
            fb_row = pcong4.execute(
                "SELECT * FROM users WHERE username='freebie'").fetchone()
            ms_row = pcong4.execute(
                "SELECT * FROM users WHERE username='mseller'").fetchone()
            mb_row = pcong4.execute(
                "SELECT * FROM users WHERE username='mbuyer'").fetchone()
            # The weekly drifter is the points freebie (120 points).
            s, shopg = get(fb, "/shop", base=pbase)
            drift = pcong4.execute(
                "SELECT * FROM popup_items WHERE tier='drifter' AND "
                "stock>0 ORDER BY id DESC").fetchone()
            check("weekly drifter carries a 120-point freebie price",
                  drift is not None and drift["points_price"] == 120
                  and "Buy with 120 points" in shopg)
            s, _ = post(fb, "/buy_popup_points",
                        {"popup_id": drift["id"]}, base=pbase)
            pcong5 = sqlite3.connect(pdb)
            pcong5.row_factory = sqlite3.Row
            fb2 = pcong5.execute(
                "SELECT * FROM users WHERE username='freebie'").fetchone()
            fbb = pcong5.execute(
                "SELECT COUNT(*) FROM babies WHERE owner_id=?",
                (fb_row["id"],)).fetchone()[0]
            drift2 = pcong5.execute(
                "SELECT * FROM popup_items WHERE id=?",
                (drift["id"],)).fetchone()
            check("freebie buy spends 120 points, grants the baby",
                  fb2["points"] == 480 and fb2["points_earned"] == 0
                  and fbb == 2 and drift2["stock"] == 0,
                  "points=%d babies=%d" % (fb2["points"], fbb))
            s, resp_gone = post(ms, "/buy_popup_points",
                                {"popup_id": drift["id"]}, base=pbase)
            check("sold-out freebie refuses a second points buy",
                  "not available for points" in resp_gone)
            # Even swap: seller puts a baby up, buyer offers a baby
            # and 0 points, seller accepts — babies swap, free.
            ms_baby = pcong5.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (ms_row["id"],)).fetchone()
            mb_baby = pcong5.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (mb_row["id"],)).fetchone()
            s, _ = post(ms, "/toggle_trade",
                        {"baby_id": ms_baby["id"]}, base=pbase)
            s, _ = post(mb, "/offer",
                        {"wanted_baby_id": ms_baby["id"],
                         "offered_baby_id": mb_baby["id"],
                         "offered_points": "0"}, base=pbase)
            ev = pcong5.execute(
                "SELECT * FROM offers WHERE from_user=? AND to_user=? "
                "AND status='pending'",
                (mb_row["id"], ms_row["id"])).fetchone()
            check("even-swap offer recorded", ev is not None)
            s, off_html = get(ms, "/offers", base=pbase)
            check("offers page tags a 0-point baby offer as an even "
                  "swap", "Even swap" in off_html)
            s, _ = post(ms, "/offer_action",
                        {"offer_id": ev["id"], "action": "accept"},
                        base=pbase)
            pcong6 = sqlite3.connect(pdb)
            pcong6.row_factory = sqlite3.Row
            ms_baby2 = pcong6.execute(
                "SELECT * FROM babies WHERE id=?",
                (ms_baby["id"],)).fetchone()
            mb_baby2 = pcong6.execute(
                "SELECT * FROM babies WHERE id=?",
                (mb_baby["id"],)).fetchone()
            ms2 = pcong6.execute(
                "SELECT * FROM users WHERE username='mseller'").fetchone()
            mb2 = pcong6.execute(
                "SELECT * FROM users WHERE username='mbuyer'").fetchone()
            check("even swap swaps owners and awards +20 each",
                  ms_baby2["owner_id"] == mb_row["id"]
                  and mb_baby2["owner_id"] == ms_row["id"]
                  and ms2["points"] == 620 and mb2["points"] == 620
                  and ms2["points_earned"] == 20
                  and mb2["points_earned"] == 20,
                  "ms=%d mb=%d" % (ms2["points"], mb2["points"]))
            # Points market: buyer (mb) lists the baby just received
            # in the swap for 200 points; freebie buyer (fb, 480
            # points) buys it.
            s, _ = post(mb, "/points_list",
                        {"baby_id": ms_baby2["id"],
                         "points_price": "200"}, base=pbase)
            lst = pcong6.execute(
                "SELECT * FROM points_listings WHERE seller_id=? AND "
                "status='pending'",
                (mb_row["id"],)).fetchone()
            check("points listing recorded", lst is not None
                  and lst["points"] == 200)
            s, market_html = get(fb, "/points_market", base=pbase)
            check("points market shows the listing",
                  "200 points" in market_html)
            s, _ = post(fb, "/points_buy",
                        {"listing_id": lst["id"]}, base=pbase)
            pcong7 = sqlite3.connect(pdb)
            pcong7.row_factory = sqlite3.Row
            fb3 = pcong7.execute(
                "SELECT * FROM users WHERE username='freebie'").fetchone()
            mb3 = pcong7.execute(
                "SELECT * FROM users WHERE username='mbuyer'").fetchone()
            sold_baby = pcong7.execute(
                "SELECT * FROM babies WHERE id=?",
                (ms_baby2["id"],)).fetchone()
            lst2 = pcong7.execute(
                "SELECT * FROM points_listings WHERE id=?",
                (lst["id"],)).fetchone()
            check("points sale moves points buyer to seller, baby "
                  "changes owner",
                  fb3["points"] == 280 and mb3["points"] == 820
                  and mb3["points_earned"] == 20
                  and sold_baby["owner_id"] == fb_row["id"]
                  and lst2["status"] == "sold",
                  "fb=%d mb=%d" % (fb3["points"], mb3["points"]))
            # Refusals: own listing, and cancel keeps the baby.
            # (ms still holds the Ember baby received in the swap.)
            s, _ = post(ms, "/points_list",
                        {"baby_id": mb_baby2["id"],
                         "points_price": "50"}, base=pbase)
            own = pcong7.execute(
                "SELECT * FROM points_listings WHERE seller_id=? AND "
                "status='pending'",
                (ms_row["id"],)).fetchone()
            s, resp_own = post(ms, "/points_buy",
                               {"listing_id": own["id"]}, base=pbase)
            check("buying your own listing refused",
                  "your own" in resp_own)
            s, _ = post(ms, "/points_list",
                        {"baby_id": mb_baby2["id"],
                         "points_price": "9999"}, base=pbase)
            check("second listing for same baby refused",
                  pcong7.execute(
                      "SELECT COUNT(*) FROM points_listings WHERE "
                      "baby_id=? AND status='pending'",
                      (mb_baby2["id"],)).fetchone()[0] == 1)
            s, _ = post(ms, "/points_cancel",
                        {"listing_id": own["id"]}, base=pbase)
            pcong8 = sqlite3.connect(pdb)
            pcong8.row_factory = sqlite3.Row
            own2 = pcong8.execute(
                "SELECT * FROM points_listings WHERE id=?",
                (own["id"],)).fetchone()
            keep = pcong8.execute(
                "SELECT * FROM babies WHERE id=?",
                (mb_baby2["id"],)).fetchone()
            check("cancelling a listing keeps the baby with the seller",
                  own2["status"] == "cancelled"
                  and keep["owner_id"] == ms_row["id"])
            # (h) Display shelves: add, style, reorder, public
            # view, remove, and a traded baby leaves the shelf.
            sh = http.cookiejar.CookieJar()
            sh2 = http.cookiejar.CookieJar()
            s, _ = signup_full(sh, "shelfy", "secret9",
                               tribe="Blues", roster="Marina", base=pbase)
            s, _ = signup_full(sh2, "shelfy2", "secret9",
                               tribe="Browns", roster="Willow",
                               base=pbase)
            pcongs = sqlite3.connect(pdb)
            pcongs.row_factory = sqlite3.Row
            sh_row = pcongs.execute(
                "SELECT * FROM users WHERE username='shelfy'").fetchone()
            sh_baby = pcongs.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (sh_row["id"],)).fetchone()
            s, shelf_html = get(sh, "/shelf", base=pbase)
            check("shelf page loads with a style picker",
                  "Shelf style" in shelf_html and "My Shelf" in shelf_html)
            s, _ = post(sh, "/shelf_add",
                        {"baby_id": sh_baby["id"]}, base=pbase)
            s, shelf2 = get(sh, "/shelf", base=pbase)
            check("added baby shows on own shelf",
                  sh_baby["name"] in shelf2)
            s, pub = get(sh2, "/shelf/shelfy", base=pbase)
            check("shelf is publicly viewable by username",
                  sh_baby["name"] in pub and "Take off shelf" not in pub)
            s, _ = post(sh, "/shelf_style",
                        {"style": "sky"}, base=pbase)
            s, shelf3 = get(sh, "/shelf", base=pbase)
            check("sky shelf style applies",
                  "Sky style" in shelf3 and "theme-sky" in shelf3)
            s, _ = post(sh2, "/shelf_add",
                        {"baby_id": sh_baby["id"]}, base=pbase)
            check("another player cannot shelve your baby",
                  pcongs.execute(
                      "SELECT COUNT(*) FROM shelf_items WHERE "
                      "baby_id=?", (sh_baby["id"],)).fetchone()[0] == 1)
            s, _ = post(sh, "/shelf_remove",
                        {"shelf_id": pcongs.execute(
                            "SELECT id FROM shelf_items WHERE "
                            "baby_id=?", (sh_baby["id"],)).fetchone()[0]},
                        base=pbase)
            s, shelf4 = get(sh, "/shelf", base=pbase)
            check("removed baby leaves the shelf",
                  "Take off shelf" not in shelf4
                  and pcongs.execute(
                      "SELECT COUNT(*) FROM shelf_items WHERE "
                      "baby_id=?", (sh_baby["id"],)).fetchone()[0] == 0)
            # (i) Post-trade color choice: display only, tribe stays.
            sh2_baby = pcongs.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (pcongs.execute(
                    "SELECT id FROM users WHERE username='shelfy2'"
                    ).fetchone()[0],)).fetchone()
            s, _ = post(sh, "/toggle_trade",
                        {"baby_id": sh_baby["id"]}, base=pbase)
            s, _ = post(sh2, "/offer",
                        {"wanted_baby_id": sh_baby["id"],
                         "offered_baby_id": sh2_baby["id"],
                         "offered_points": "0"}, base=pbase)
            trade_offer = pcongs.execute(
                "SELECT * FROM offers WHERE from_user=? ORDER BY id "
                "DESC LIMIT 1",
                (pcongs.execute(
                    "SELECT id FROM users WHERE username='shelfy2'"
                    ).fetchone()[0],)).fetchone()
            s, _ = post(sh, "/offer_action",
                        {"offer_id": trade_offer["id"],
                         "action": "accept"}, base=pbase)
            choices = pcongs.execute(
                "SELECT * FROM recolor_choices WHERE offer_id=?",
                (trade_offer["id"],)).fetchall()
            check("a color choice is created for each trader",
                  len(choices) == 2)
            s, offers_html = get(sh2, "/offers", base=pbase)
            check("offers page shows the color choice",
                  "Choose colors for babies you traded for"
                  in offers_html
                  and "Match my Fortress Rooted colors" in offers_html)
            ch2 = [c for c in choices
                   if c["baby_id"] == sh_baby["id"]][0]
            s, _ = post(sh2, "/recolor",
                        {"choice_id": ch2["id"],
                         "decision": "recolor"}, base=pbase)
            got = pcongs.execute(
                "SELECT * FROM babies WHERE id=?",
                (sh_baby["id"],)).fetchone()
            check("recolor restyles the card but keeps the tribe",
                  got["color_theme"] == "Browns"
                  and got["tribe"] == "Blues")
            s, me2 = get(sh2, "/me", base=pbase)
            check("collection shows the recolored tag",
                  "recolored to Fortress Rooted colors" in me2)
            ch1 = [c for c in choices
                   if c["baby_id"] == sh2_baby["id"]][0]
            s, _ = post(sh, "/recolor",
                        {"choice_id": ch1["id"],
                         "decision": "keep"}, base=pbase)
            kept = pcongs.execute(
                "SELECT * FROM babies WHERE id=?",
                (sh2_baby["id"],)).fetchone()
            check("keep leaves colors untouched",
                  kept["color_theme"] is None)
            pcongs.close()
            pcong4.close()
            pcong5.close()
            pcong6.close()
            pcong7.close()
            pcong8.close()
            pcong.close()
            pcong2.close()
            pcong3.close()
            pgl.close()
            pgl2.close()
            pcon.close()
            pcon2.close()
            pcon3.close()
            pcon4.close()
            pcon5.close()
        finally:
            pproc.terminate()
            try:
                pproc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pproc.kill()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    print()
    if fails:
        print("FAILED:", len(fails), "check(s)")
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())

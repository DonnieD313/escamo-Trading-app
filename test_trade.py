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
    damiana = {"name": "Damiana June", "cert": "EHB-L001", "tribe": "Blues",
               "hoodie_pattern": "Guiding Star",
               "stones": "yellow + green rhinestones",
               "face": "fish-like, surprised crying look"}
    aaliyah = {"name": "Aaliyah Bee", "cert": "EHB-L002", "tribe": "Browns",
               "hoodie_pattern": "Forest Canopy",
               "stones": "amber + gold rhinestones, red stone in forehead",
               "face": "animal-like, grouchy"}
    jazmyn = {"name": "Jazmyn", "cert": "EHB-L003", "tribe": "Reds",
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
    check("Damiana portrait has crying tears",
          "#7CC4FF" in escamo_app.portrait_svg(damiana))
    check("Aaliyah portrait has forehead stone",
          "60,26 64,31 60,36 56,31" in escamo_app.portrait_svg(aaliyah))
    svg_jazmyn = escamo_app.portrait_svg(jazmyn)
    check("Jazmyn portrait has black hoodie and fire-red wand tip",
          "#111111" in svg_jazmyn and "#FF3B1F" in svg_jazmyn)
    check("Black portrait has diamond eyes",
          "#DFF6FF" in escamo_app.portrait_svg(black))
    svg_god1 = escamo_app.portrait_svg(god1)
    svg_god2 = escamo_app.portrait_svg(god2)
    check("god portraits have crowns and differ per god",
          "42,30 48,19" in svg_god1 and svg_god1 != svg_god2)
    svg_evil = escamo_app.portrait_svg(evil)
    check("portrait escapes baby names",
          "<script>" not in svg_evil and "&lt;script&gt;" in svg_evil)
    check("small portrait size honored",
          "width='72'" in escamo_app.portrait_svg(blue, 72))
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
        # 3. Both got one free baby + 300 points
        for name, row in (("alice", alice_row), ("bob", bob_row)):
            babies = con.execute(
                "SELECT * FROM babies WHERE owner_id=?",
                (row["id"],)).fetchall()
            check("%s starts with 1 free baby" % name, len(babies) == 1,
                  "got %d" % len(babies))
            check("%s starts with 300 points" % name, row["points"] == 300)
        s, me_html = get(alice, "/me")
        check("collection page shows portraits",
              "<svg" in me_html and alice_row["username"] in me_html)
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
        # alice: 300 - 10 offered + 20 earned = 310
        check("alice points = 310", a["points"] == 310,
              "got %d" % a["points"])
        # bob: 300 + 10 received + 20 earned = 330
        check("bob points = 330", b["points"] == 330, "got %d" % b["points"])
        check("alice earned 20", a["points_earned"] == 20)
        check("bob earned 20", b["points_earned"] == 20)
        check("creator still locked at 20/300",
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
        check("world shows Blacks as the Miners",
              "Blacks — the Miners" in world_html)
        check("world shows Diamond eyes for Blacks",
              "Diamond eyes" in world_html)
        # 9. Weekly Black listing + 1,200-point gate
        s, browse_html = get(alice, "/browse")
        check("trade board shows portraits", "<svg" in browse_html)
        check("trade board shows weekly Black", "Blacks" in browse_html)
        check("trade board shows 1,200-point gate", "1,200" in browse_html)
        check("trade board says Underground auto-accepts",
              "auto-accept" in browse_html)
        con3 = sqlite3.connect(DB)
        con3.row_factory = sqlite3.Row
        black = con3.execute(
            "SELECT * FROM babies WHERE tribe='Blacks' AND for_trade=1"
            ).fetchone()
        check("weekly Black is listed", black is not None)
        # alice has 310 points: below the gate, so the offer is refused
        s, resp = post(alice, "/offer",
                       {"wanted_baby_id": black["id"],
                        "offered_points": "0"})
        check("Black offer below 1,200 refused", "1,200" in resp)
        no_offer = con3.execute(
            "SELECT * FROM offers WHERE wanted_baby=? AND from_user=? "
            "AND status='pending'",
            (black["id"], alice_row["id"])).fetchone()
        check("no pending offer created for refused Black trade",
              no_offer is None)
        # boost alice to 1,500 points: the gate now passes, and the
        # Underground auto-accepts the qualifying offer immediately.
        # Alice offers the baby she got from Bob + 50 points.
        con3.execute("UPDATE users SET points=1500 WHERE username='alice'")
        con3.commit()
        s, _ = post(alice, "/offer",
                    {"wanted_baby_id": black["id"],
                     "offered_baby_id": bob_baby["id"],
                     "offered_points": "50"})
        con4 = sqlite3.connect(DB)
        con4.row_factory = sqlite3.Row
        a2 = con4.execute(
            "SELECT * FROM users WHERE username='alice'").fetchone()
        shop = con4.execute(
            "SELECT * FROM users WHERE username='underground'").fetchone()
        blk = con4.execute("SELECT * FROM babies WHERE id=?",
                           (black["id"],)).fetchone()
        sold_baby = con4.execute("SELECT * FROM babies WHERE id=?",
                                 (bob_baby["id"],)).fetchone()
        sale = con4.execute(
            "SELECT * FROM offers WHERE wanted_baby=? AND from_user=?",
            (black["id"], alice_row["id"])).fetchone()
        check("Black offer at 1,500 points immediately accepted",
              sale is not None and sale["status"] == "accepted")
        check("buyer now owns the Black", blk["owner_id"] == a2["id"])
        check("Black leaves the trade board", blk["for_trade"] == 0)
        check("offered baby goes to the Underground",
              sold_baby["owner_id"] == shop["id"])
        # alice: 1500 - 50 offered + 60 earned = 1510;
        # earned 20 (earlier trade) + 60 (Black catch) = 80
        check("buyer points = 1510 after Black sale",
              a2["points"] == 1510, "got %d" % a2["points"])
        check("buyer earned 80 after Black sale (60 for the Black)",
              a2["points_earned"] == 80, "got %d" % a2["points_earned"])
        notice = con4.execute(
            "SELECT * FROM notices WHERE text LIKE '%caught by alice%'"
            ).fetchone()
        check("notices board records the Black catch", notice is not None)
        # Second offer against the same Black must fail cleanly: boost
        # Bob past the gate too, so the failure is about the Black being
        # gone, not the points gate.
        con4.execute("UPDATE users SET points=1500 WHERE username='bob'")
        con4.commit()
        s, resp2 = post(bob, "/offer",
                        {"wanted_baby_id": black["id"],
                         "offered_points": "0"})
        check("second offer against caught Black fails",
              "not up for trade" in resp2)
        no_second = con4.execute(
            "SELECT * FROM offers WHERE wanted_baby=? AND from_user=? "
            "AND status IN ('pending', 'accepted')",
            (black["id"], bob_row["id"])).fetchone()
        check("no second offer recorded for caught Black",
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
            check("shop shows portraits", "<svg" in shop_html)
            check("shop shows leader price $10.00", "10.00" in shop_html)
            check("shop shows god price $40.00", "40.00" in shop_html)
            check("shop shows leader choices for purchase",
                  "Damiana June" in shop_html
                  and "Aaliyah Bee" in shop_html
                  and "Jazmyn" in shop_html)
            check("shop shows named gods for purchase",
                  "Muzle Back" in shop_html
                  and "Baraka" in shop_html
                  and "Sirus V" in shop_html)
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
                  and ppur["amount"] == "3.99")
            drow = fcon4.execute(
                "SELECT * FROM users WHERE username='dave'").fetchone()
            check("failed points pack adds no points",
                  drow["points"] == 300 and drow["points_earned"] == 0,
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
            # Unpaid points pack grants no points.
            s, hdrs, _ = post_noredirect(
                frank, "/buy_points_stripe", {"pack": "900"},
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
                  and ppur["amount"] == "9.99")
            frow = ucon4.execute(
                "SELECT * FROM users WHERE username='frank'").fetchone()
            check("unpaid points pack adds no points",
                  frow["points"] == 300 and frow["points_earned"] == 0)
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
            # u1 buys leader Damiana June via PayPal: cap becomes 2.
            ccon = sqlite3.connect(cap_db)
            ccon.row_factory = sqlite3.Row
            ld = ccon.execute(
                "SELECT * FROM leaders WHERE sold=0 ORDER BY id"
                ).fetchone()
            ccon.close()
            s, html = mock_buy(u1, "/buy_leader",
                               {"leader_id": ld["id"]})
            check("leader purchase page confirms new leader",
                  "Payment complete" in html and "Damiana June" in html)
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
            check("600-point pack raises balance to 900",
                  w1_row["points"] == 900, "got %d" % w1_row["points"])
            check("bought points do not raise points_earned",
                  w1_row["points_earned"] == 0)
            ppur = pcon.execute(
                "SELECT * FROM purchases WHERE user_id=? AND item_type="
                "'points'", (w1_row["id"],)).fetchone()
            check("points purchase recorded at $3.99",
                  ppur is not None and ppur["status"] == "completed"
                  and ppur["amount"] == "3.99")
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
            check("1200-point pack raises balance to 2100",
                  w1_row["points"] == 2100, "got %d" % w1_row["points"])
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
        check("chooser starts with 300 points", ch_row["points"] == 300)
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

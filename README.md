# Escamo Hoodie Babies Trade — tradable app

A real multi-user trading app (not a demo page): people sign up, get a free
hoodie baby, and trade babies and points with each other. Built with only the
Python standard library + SQLite. No paid services, nothing published,
nothing charged.

## How to start it

```bash
cd ~/workspace/escamo-trading-app
python3 app.py
```

Then open **http://127.0.0.1:8090** in a browser.

- Port: `PORT=8091 python3 app.py` to use another port.
- Database: a file named `app.db` is created next to `app.py` on first run
  (override with `ESCAMO_DB=/path/to/file.db`).
- Stop it with Ctrl+C.

## What it does

- **Accounts:** sign up / log in with a username and password. Passwords are
  hashed with PBKDF2 (salted) — never stored in plain text. No email needed.
- **Joining:** signup is two steps. Step 1: username, password, optional
  invite code, and the **tribe you want to join — Blues, Browns, or Reds**
  (Blacks are not a starter tribe; they are the rare Miners, found only
  through the weekly sale). Step 2: **choose your free hoodie baby** from
  your tribe's starter roster (Blues: Marina, Coral, Pearl, Tide, Kai;
  Browns: Willow, Fern, Hazel, Rowan, Ivy, Maple, Juniper, Sage; Reds:
  Ember, Storm, Skye, Ash, Blaze, Aurora, Cinder, Tempest) and **name it
  yourself** — the name field starts prefilled with the roster name and
  can be overwritten (blank keeps the roster name, max 40 characters).
  The account is created only at the end of step 2, so abandoning step 2
  creates nothing; unfinished signups expire after 30 minutes. Every new
  account gets that one free baby (a unique, 1-of-1 baby with its own
  certificate number, tribe, hoodie pattern, and rhinestone colors) and
  **300 points**. Existing accounts keep the free baby they already got.
- **Collections:** "My Collection" shows your babies. You can put any baby up
  for trade (or take it down) from there.
- **Trading:** on the Trade Board you browse other users' babies marked for
  trade and send an offer — your baby and/or points for their baby. The other
  user can **accept, decline, or counter** (a counter sends a new offer back
  with their own baby + points). Accepting swaps the babies, moves any
  offered points, and awards **20 points to each trader**. Traded babies
  leave the trade board, and other pending offers touching those babies are
  withdrawn.
- **Points + creator:** points earned are tracked separately. At **every 300
  points earned**, you unlock adding your own custom character (name, tribe,
  hoodie pattern, rhinestone colors) from "My Collection".
- **Invites:** every user has an 8-character invite code shown on
  "My Collection". Share a link shaped like
  `/signup?invite=YOURCODE` — signing up through it records who invited them.
- **World page:** the tribes and leaders as specified — Blues, rulers over
  water, led by Damiana June (Controller of Water, blue slanted eyes,
  fish-like face with a surprised crying look, rare, purchasable when live);
  the Blues run the sea and have only five characters, not including her
  (Marina, Coral, Pearl, Tide, Kai — sea and ocean names); Browns, rulers
  of the forest, led by Aaliyah Bee (red stone in the middle of her
  forehead, keeper of the woods and animals, animal-looking grouchy face);
  Reds, led by Jazmyn, ruler of the sky, more powerful than the other
  controllers (glowing diamond head, black eyes, all-black hoodie with stones
  of every different color, hoodie over her face, wand with a fire-red tip,
  angel-looking face with a slight curious smile — Jazmyn is the elder of
  them all, the first hoodie baby to arrive); Blacks ARE the Miners (no
  ruler, scavengers, underground-raised, they pop up out of anywhere, mean
  no harm, protectors out to get what they can to go back underground,
  Diamond eyes, all-white hoodies with black stones, angry creature-like
  faces). Faces: Blues fish-like, Browns animal-like, Reds angel-like,
  Blacks angry creature-like.
- **Prices:** leaders **$10.00** to purchase, and everyone is free to
  purchase a leader. Individual characters **$1.99** to purchase. Gods
  **$40.00** to purchase — there are only 3 gods, over everyone, and
  their trades are almost impossible; more gods will arrive over new
  tribes.
- **Trade slots:** at most **1 pending offer at once** for a regular
  trader, **2** for a leader owner, **5** for a god owner (highest
  applicable wins). The perk follows current ownership — trade away
  your leader or god and the cap drops back. Enforced when an offer
  (or counter-offer) is sent, with a message naming the cap.
- **Shop + checkout (PayPal and Cash App Pay, test modes):** the Shop
  page sells individual characters for **$1.99** each, leaders for
  **$10.00** each, god slots for **$40.00** each, and points packs
  (**300 for $1.99, 600 for $3.99, 900 for $9.99, 1,200 for $20.00**)
  through PayPal or through Cash App Pay (via Stripe Checkout). A
  logged-in user buys, pays, and is sent back — only a successful
  capture (PayPal) or a verified paid session (Stripe) grants the
  purchase and records it; a failed, unpaid, or cancelled checkout
  grants nothing, and an already-sold item grants nothing. Checkout
  runs in **PayPal sandbox / Stripe test mode by default: no real
  money moves**. Leader purchases let the buyer choose 1 of the 3
  leaders (Damiana June, Aaliyah Bee, Jazmyn); each leader is unique,
  so once one is sold it is gone, and a bought leader joins the
  buyer's collection and trades like any other baby. God purchases
  grant 1 of the 3 named gods — **Muzle Back, Baraka, Sirus V** (names
  exactly as Doneil wrote them). Points packs add to the buyer's point
  **balance only** — they
  never raise points_earned, so bought points do not unlock the
  character creator, which stays trade-earned only.
- **Blacks / weekly sale:** once a week, one Black pops up for sale on the
  Trade Board (owned by the system "underground" account, rotating weekly
  so only the current week's Black is listed). Trading for a Black starts
  at **1,200 points**: an offer targeting a Black is refused unless the
  offerer holds at least 1,200 points (enforced when the offer is sent and
  again at acceptance). **Auto-accept rule (Doneil's decision):** the
  Underground auto-accepts the first qualifying offer immediately — the
  buyer receives the Black, the offered baby (if any) and offered points
  go to the Underground, the Black leaves the Trade Board, other pending
  offers touching that Black are withdrawn, and a notice is appended to
  the Notices board saying the Black was caught and by whom. No Black
  sale price is set; the offer's own contents are what the buyer gives.
  **Trading for a Black is worth 60 points** to the buyer (not the usual
  20); the Underground system account earns nothing. Normal trades
  between two users stay at 20 points each.
  Once the Black is gone, later offers against it fail cleanly.
- **Collections + notices:** the current babies are the First Beginnings.
  Summer, Fall, and Winter collections are coming. A Notices board lists
  every addition with where it was found (seeded with the founders and the
  First Beginnings arrivals; new custom characters and each weekly Black
  append a notice).

## Test: two users completing a trade end-to-end

Automated test (drives the real server over HTTP, then checks the database):

```bash
cd ~/workspace/escamo-trading-app
python3 test_trade.py
```

It verifies: Alice and Bob sign up (Bob via Alice's invite code), each gets
1 free baby + 300 points, Bob puts his baby up for trade, Alice offers her
baby + 10 points for it, Bob accepts — and afterward Alice owns Bob's old
baby, Bob owns Alice's old baby, both babies leave the trade board,
Alice has 310 points (300 − 10 + 20), Bob has 330 (300 + 10 + 20), each has
20 points earned, and the creator is still locked at 20/300. It also
verifies the new spec: the World page shows leader $10.00, individual
$1.99, god $40.00, the 3 gods, five Blues characters, fish-like /
animal-like / angel-like faces, and Blacks as the Miners with Diamond eyes;
the Trade Board lists the weekly Black with the 1,200-point gate; a Black
offer below 1,200 points is refused with no pending offer created, while
one at 1,500 points is immediately auto-accepted by the Underground (buyer
owns the Black, offered baby and points move to the Underground, a catch
notice is recorded), and a second offer against the same Black fails
cleanly; and the Notices board lists arrivals. It then runs two mocked
PayPal shop servers (no real credentials, no network calls to PayPal):
a successful capture grants the listed $1.99 baby and records the
purchase as completed, and a failed capture grants nothing and leaves
the shop item unsold. Finally it runs two mocked Stripe shop servers
(no real keys, no network calls to Stripe): a paid Cash App Pay session
grants the listed $1.99 baby and records the purchase as completed,
while an unpaid session and a cancelled session each grant nothing and
leave the shop item unsold. It then verifies the leader/god/points
rules on mocked servers: a $10.00 leader purchase (buyer's choice of
the 3 leaders) grants the leader baby and marks it sold, a sold-out
leader cannot be bought by anyone else, a failed leader capture grants
nothing; a $40.00 god purchase grants a god slot baby; the trade-slot
caps hold (regular 1, leader owner 2, god owner 5 pending offers, with
the cap dropping when the leader is traded away); and points packs
(600 via PayPal, 1,200 via Cash App Pay) raise the balance only, never
points_earned, so the creator stays locked — while failed/unpaid packs
grant nothing. Finally it verifies hardening on dedicated instances:
POSTs without a CSRF token (or with a wrong one) are rejected with
403 and create nothing, the CSRF cookie carries the Secure flag when
`COOKIE_SECURE=1`, an 8-day-old session no longer logs in while a
fresh one does, and 5 failed logins (or signups) from one IP trigger
a 429 block.

To try it by hand in two browser windows (or a normal + private window):

1. Sign up as `alice`, then sign up as `bob`.
2. As Bob, go to My Collection and click "Put up for trade" on his baby.
3. As Alice, open the Trade Board, pick Bob's baby, choose her baby and
   10 points, and send the offer.
4. As Bob, open Offers and click Accept.
5. Check both collections and point totals — they should match the numbers
   above.

## PayPal setup (sandbox first, live later)

No real charges happen until live credentials are set AND the app is
publicly hosted over HTTPS. Until then everything runs in PayPal's
sandbox (test mode).

1. **Create a free PayPal Business account** at paypal.com (a personal
   account cannot take business checkout payments).
2. **Create an app in PayPal Developer** (developer.paypal.com → My Apps
   & Credentials): this gives you a **Client ID** and **Secret** for
   **Sandbox** first, and later a separate pair for **Live**.
3. **Set the environment variables** when starting the app:
   ```bash
   PAYPAL_CLIENT_ID=your_sandbox_client_id \
   PAYPAL_CLIENT_SECRET=your_sandbox_secret \
   PAYPAL_MODE=sandbox \
   PUBLIC_BASE_URL=http://127.0.0.1:8090 \
   python3 app.py
   ```
   Credentials come only from these variables — they are never written
   into the code or the database.
4. **Test in sandbox** with a PayPal sandbox buyer account (created in
   PayPal Developer → Sandbox → Accounts). Sandbox payments move test
   money only.
5. **Go live later:** set `PAYPAL_MODE=live`, swap in the live Client ID
   and Secret, and set `PUBLIC_BASE_URL` to the app's real
   `https://your-domain` address. **Live checkout requires public HTTPS
   hosting** — PayPal will not redirect buyers back to a localhost
   address on a live account.

Note: the sandbox flow works on localhost for testing, but real buyers
need the hosted app (see below).

## Stripe + Cash App Pay setup (test mode first, live later)

Cash App Pay at checkout runs through Stripe, not PayPal — Stripe is a
second provider alongside PayPal. No real charges happen until a live
key is set AND the app is publicly hosted over HTTPS. Until then
everything runs in Stripe's test mode.

1. **Create a Stripe account** at stripe.com (free; no monthly fee on
   the standard plan).
2. **Enable Cash App Pay** in the Stripe Dashboard (Settings → Payment
   methods → Cash App Pay) — it is available to US accounts.
3. **Get your API keys** in the Dashboard (Developers → API keys): use
   the **test** secret key (`sk_test_…`) first, and later the **live**
   secret key (`sk_live_…`).
4. **Set the environment variables** when starting the app:
   ```bash
   STRIPE_SECRET_KEY=your_test_secret_key \
   STRIPE_MODE=test \
   PUBLIC_BASE_URL=http://127.0.0.1:8090 \
   python3 app.py
   ```
   The key comes only from this variable — it is never written into
   the code or the database.
5. **Test in test mode** with Stripe's test flow: a Checkout Session
   offers Cash App Pay as the payment method; test payments move test
   money only. (Automated tests use `STRIPE_MOCK=1` instead of real
   keys — see the test section above.)
6. **Go live later:** set `STRIPE_MODE=live`, swap in the live secret
   key, and set `PUBLIC_BASE_URL` to the app's real `https://your-domain`
   address. **Live checkout requires public HTTPS hosting** — Stripe
   will not return buyers to a localhost address on a live account.

Note: the test flow works on localhost for testing, but real buyers
need the hosted app (see below).

## What remains for public hosting (real people outside this VM)

This app currently binds to `127.0.0.1`, so only this machine can reach it.
To let real people in, a host step is still needed (nothing here is
published yet):

1. **Pick a host** — any always-on machine/VPS (or a platform that runs
   Python apps). Copy this whole folder to it.
2. **Expose the port** — run the app behind a real web server/reverse proxy
   (e.g. Caddy or nginx) with HTTPS, and set `HOST=0.0.0.0` (default is
   `127.0.0.1`, localhost only). Never expose the bare dev server
   to the internet as-is.
3. **Set a public base URL** — invite links are currently relative
   (`/signup?invite=CODE`); once hosted, share them as
   `https://your-domain/signup?invite=CODE`, and set `PUBLIC_BASE_URL`
   to it so PayPal/Stripe return buyers to the right address.
4. **Back up `app.db`** — it holds all accounts, babies, and trades.

## Hardening (done — the app is ready to face the internet)

- **CSRF tokens:** every POST form carries a double-submit token
  (random cookie value echoed into a hidden form field); a POST with a
  missing or wrong token is rejected with 403 before any action runs.
- **Rate limiting:** 5 failed logins or signups from one IP within
  10 minutes blocks further attempts from that IP with a 429 page
  (per-process, in-memory state — fine for this single-process server).
- **Session expiry:** sessions last at most 7 days
  (`SESSION_MAX_AGE_DAYS` env var); an expired session no longer logs
  anyone in.
- **Cookies:** session and CSRF cookies are HttpOnly with SameSite=Lax;
  set `COOKIE_SECURE=1` when running behind HTTPS so they also get the
  Secure flag. Only set that flag behind real HTTPS — a Secure cookie
  is never sent over plain HTTP, so logins would stop working on
  localhost with it on.
- **Bind address:** `HOST` env var (default `127.0.0.1`); set to
  `0.0.0.0` only behind the reverse proxy.

## GO-LIVE CHECKLIST (what is still needed before real traffic)

- [ ] **A host + domain + HTTPS** — an always-on machine, a domain
  name, and a reverse proxy (Caddy/nginx) terminating HTTPS; run with
  `HOST=0.0.0.0`, `PUBLIC_BASE_URL=https://your-domain`, and
  `COOKIE_SECURE=1`.
- [ ] **Live PayPal credentials** — a PayPal Business account plus live
  API credentials; set `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, and
  `PAYPAL_MODE=live`.
- [ ] **Live Stripe keys (Cash App Pay enabled)** — a Stripe account
  with Cash App Pay enabled in the Dashboard; set `STRIPE_SECRET_KEY`
  and `STRIPE_MODE=live`.
- [ ] **Keep test mode until launch day** — the app stays in PayPal
  sandbox / Stripe test mode (no real charges) until the three items
  above are all in place.

## Deploying on Render

Doneil chose Render as the host. Deployment prep is done; nothing is
deployed yet, and no account has been created — that part needs his
login and card.

- `render.yaml` — blueprint for ONE web service: Python, Starter plan
  (never sleeps; the free tier sleeps and is wrong for a live trading
  app), no-op build (standard library only), start command
  `python3 app.py`, env vars `HOST=0.0.0.0`, `COOKIE_SECURE=1`,
  `PAYPAL_MODE=sandbox`, `STRIPE_MODE=test`, `ESCAMO_DB=/data/app.db`,
  plus a 1 GB persistent disk at `/data` so the SQLite database
  survives restarts and redeploys. No credentials or keys are in the
  file, and PORT is deliberately not set (Render injects it).
- `DEPLOY.md` — step-by-step for a non-technical owner: create a
  Render account, connect the repo, confirm the Starter plan, disk,
  and env vars, deploy, then set `PUBLIC_BASE_URL` to the live
  `.onrender.com` address and redeploy. Trading works immediately;
  real-money checkout stays in test mode until live PayPal and Stripe
  keys are added in the dashboard later.
- The GO-LIVE CHECKLIST above still applies: the host step is now
  "follow DEPLOY.md", and live payments still need the PayPal
  Business account and Stripe account with Cash App Pay enabled.

## Deliberately not built yet

- **God names:** done — the 3 gods are Muzle Back, Baraka, and
  Sirus V, exactly as Doneil wrote them.
- **Black sale completion:** resolved — the Underground auto-accepts the
  first qualifying offer (see "Blacks / weekly sale" above).
- **Starter baby names for Browns/Reds, and Black roster names** are
  placeholders — Blues use sea and ocean names as specified; the rest can
  be replaced with Doneil's.
- **Password-reset flow:** not built — there is no email on accounts,
  so a reset would need a separate design (e.g. an admin reset or a
  security question). Sessions expire and logins are rate-limited, but
  a forgotten password currently has no self-serve recovery.
- **Photos/artwork:** babies are described in text (pattern, stones, face).
  The artifact demo page has the artwork; this app's visuals are plain by
  design.

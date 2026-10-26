# Putting Hoodie Babies Trade live on Render

Plain-language version. Nothing here needs a programmer — but two steps
need Doneil's own login and card, so those are his steps, not anyone
else's.

## What Render is, in one line

Render is a hosting service: a company that runs the app on a computer
that stays on day and night, and gives it a web address with the
padlock (HTTPS) switched on automatically. Right now the app only runs
on one private machine; after this, anyone on the internet can open it.

## Before you start

- The app's code needs to be in a GitHub repo (a free GitHub account
  works). Render connects to that repo to get the code.
- Render's Starter plan costs money every month (about $7 at the time
  of writing — check the price on Render's site before paying). That
  is why a card is needed. The free plan is NOT used on purpose: free
  apps fall asleep when nobody visits, which would break a live
  trading app.

## Steps

### 1. Create a Render account

Go to render.com and sign up. Use an email you check, and add a payment
card when it asks — the Starter plan is a paid plan.

### 2. Put the code on GitHub

Create a new repo on GitHub and upload this whole
`escamo-trading-app` folder to it (including `render.yaml`,
`app.py`, `README.md`, and `DEPLOY.md`). If the folder is already in
a repo, skip this step.

### 3. Create the web service in Render

1. In the Render dashboard, click **New** and choose **Web Service**.
2. Connect your GitHub account if asked, and pick the repo from step 2.
3. Render will find `render.yaml` and fill in the settings itself:
   - **Plan: Starter** (never sleeps — do not switch this to Free).
   - **Build command:** does nothing (the app needs no installs).
   - **Start command:** `python3 app.py`.
   - **Disk:** 1 GB at `/data` — this is what keeps every account,
     baby, trade, and purchase safe when the app restarts or updates.
     Do not remove it.
   - **Environment variables** (already filled in from `render.yaml`):
     `HOST=0.0.0.0`, `COOKIE_SECURE=1`, `PAYPAL_MODE=sandbox`,
     `STRIPE_MODE=test`, `ESCAMO_DB=/data/app.db`.
4. Click **Create Web Service** and wait for the first deploy to
   finish. The logs should end with the app saying it is running.

### 4. Check that it opens

Render will give the app an address like
`https://hoodie-babies-trading-app.onrender.com`. Open it in a browser. You
should see the Hoodie Babies home page. Trading, signing up, and the free
baby + 300 points all work right away.

### 5. Set the live address, then redeploy (do not skip this)

The app needs to know its own live address so PayPal and Stripe can
send buyers back to the right place after payment.

1. Copy the `.onrender.com` address from step 4.
2. In the Render dashboard, open the service, go to
   **Environment**, and add:
   - Key: `PUBLIC_BASE_URL`
   - Value: your address, exactly as shown, e.g.
     `https://hoodie-babies-trading-app.onrender.com`
3. Save. Render will redeploy automatically. Wait for it to finish,
   then open the address again and confirm the home page still loads.

### 6. That is launch day — with one limit

Trading works immediately. **Real-money checkout does NOT work yet.**
The app is still in PayPal sandbox and Stripe test mode on purpose,
so no real money can move. That is the safe way to launch.

To switch on real payments later, in the Render dashboard's
**Environment** section, add the live keys (from a PayPal Business
account and a Stripe account with Cash App Pay enabled) and change
`PAYPAL_MODE` to `live` and `STRIPE_MODE` to `live`, then redeploy.
Never paste a key into the code, into GitHub, or into a chat message
— keys go in the Render dashboard only.

## If something goes wrong

- **The page won't load:** in the Render dashboard, open the service
  and read the **Logs** tab. The last lines usually say what failed.
- **Accounts vanished after an update:** the disk in step 3 is what
  prevents this. If the disk was removed, tell whoever set it up
  before anyone trades again.
- **A payment button errors:** that is expected until live keys are
  added (step 6 above). Test-mode buttons need test keys to do a full
  test checkout; without any keys they show a setup message instead
  of charging anyone.

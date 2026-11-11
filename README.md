# Clinic Booking API: Backend Implementation Plan

Status: **Backend code written in `backend/` (Phases 0 to 6 and the page serving from 7). Not yet run
against a database: waiting for a Neon `DATABASE_URL`. See "Running it" at the bottom.**

The contract is the OpenAPI file at https://koyaacademy.com/assessment-api/openapi.json.
The frontend already exists in `web/` (plain HTML, CSS and JS) and calls this API.

---

## 1. Goal and scope

Build a Django JSON API that satisfies all five levels, serve the existing page from the same
service, and deploy it on Render with Postgres.

| Level | Requirement |
|---|---|
| 1 | List slots, book one, look up a booking, `POST /reset` for graders |
| 2 | `X-API-Key` required everywhere (401). Validation (400/404). Trim name and email, lower-case email. |
| 3 | No double booking under concurrency (409). Cancel with `DELETE` (409 if already cancelled). |
| 4 | `Idempotency-Key` (same key and body replays, different body is 422). Cursor paging, oldest first. |
| 5 | Max 5 attempts per email per minute, failures included. 6th is 429 with `Retry-After`. |

Non-negotiables from the spec:
- Every error body is exactly `{"error": "<message>"}`.
- IDs are strings. Dates are ISO UTC (`2026-12-10T09:00:00Z`).
- `/reset` accepts slots in the past and wipes all bookings.

## 2. Decisions (please confirm or change)

| # | Decision | Default |
|---|---|---|
| D1 | Framework | Django with **plain JSON views** (no DRF). The error format is strict and the surface is small. |
| D2 | Database | **Postgres on Neon** (needed for row locks, partial unique index, advisory locks). |
| D3 | Local Postgres | **Not used.** Local and live both point at Neon through `DATABASE_URL`. |
| D4 | Host | Render web service plus Render Postgres (free). |
| D5 | Frontend delivery | Django serves `web/` via WhiteNoise, and injects the API key into the page. |
| D6 | Python version | 3.12 (will check what is installed). |
| D7 | Dependencies | `django`, `psycopg[binary]`, `gunicorn`, `dj-database-url`. Nothing else. |

Changes made while building: Django serves the 3 frontend files itself (no WhiteNoise needed).
All backend files live in `backend/`; the frontend stays in `web/`. The rate limiter counts an
attempt as soon as a valid email can be read, even if other fields are bad. Advisory locks are
transaction-level, so they work with Neon's pooled connection strings.

## 3. Target project layout

```
booking_api/
  README.md              this file
  web/                   frontend (index.html, styles.css, app.js)
  backend/
    manage.py
    requirements.txt
    .env                 local secrets (git-ignored); .env.example is the template
    config/              settings, urls, wsgi
    bookings/
      models.py          Slot, Booking, BookingAttempt, IdempotencyRecord
      validators.py      trim/normalise/email/limit parsing
      errors.py          domain exceptions -> status codes
      services.py        all business logic (transactions, locks, idempotency)
      ratelimit.py       per-email window
      cursors.py         encode/decode paging cursor
      views.py           thin views + serving the frontend
      middleware.py      API-key check
      migrations/
      tests.py           unit tests + API/concurrency tests
```

Rule: **views stay thin; every decision lives in `services.py`.**

## 4. Step-by-step plan

Each step ends with a **check** I run and report before moving on. I stop after each
level-sized group of steps for your review.

### Phase 0: Setup

**Step 0.1: Environment.** Create a virtualenv, `requirements.txt`, start the Django project
(`config`) and the `bookings` app. Read all settings from environment variables:
`API_KEY`, `DATABASE_URL`, `SECRET_KEY`, `ALLOWED_HOSTS`, `DEBUG`.
*Check:* `manage.py check` passes and the server boots.

**Step 0.2: Local Postgres.** Connect through `DATABASE_URL`.
*Check:* `migrate` runs against Postgres.

**Step 0.3: Hardening of defaults.** Remove sessions, CSRF, admin, messages and templates we do
not use. Disable `APPEND_SLASH`. URLs work with no trailing slash.
*Check:* `POST /bookings` returns no redirect.

### Phase 1: Foundation (Level 2 groundwork)

**Step 1.1: JSON error contract.** Domain exceptions (`ValidationFailed` 400, `NotFound` 404,
`Conflict` 409, `UnprocessableKey` 422, `RateLimited` 429) and one handler that turns them
into `{"error": ...}`. Also JSON for 401, 404 (unknown route), 405 and 500.
*Check:* unknown route, wrong method and a forced exception all return `{"error": ...}`.

**Step 1.2: API-key middleware.** Runs first on every path. Constant-time comparison of
`X-API-Key` to `API_KEY`. Exempt only `/health` and the frontend page and its static files.
*Check:* no key and wrong key return 401. Correct key passes.

**Step 1.3: Health endpoint.** `GET /health` (no key), used by Render and an uptime pinger.

### Phase 2: Level 1 (core data and endpoints)

**Step 2.1: Models and migrations.**
- `Slot(id str PK, provider, start, end)`
- `Booking(id str PK, slot FK, name, email, status, created_at, seq auto-increment, idempotency_key nullable)`
- `BookingAttempt(email, created_at)`
- `IdempotencyRecord(key unique, fingerprint, status_code, response_body, created_at)`
- Constraints: **partial unique on `Booking.slot` where status = confirmed**; indexes on
  `Booking(email, seq)`, `BookingAttempt(email, created_at)`, `Slot(start)`.
*Check:* migration applies. Inserting two confirmed bookings for one slot fails at DB level.

**Step 2.2: `POST /reset`.** One transaction: delete bookings, attempts and idempotency
records, delete slots, insert the new slots (past dates allowed). Returns 200.
*Check:* load slots, create a booking, reset, confirm everything is gone.

**Step 2.3: `GET /slots`.** Ordered by `start`, optional exact `provider` filter, `available`
computed from whether a confirmed booking exists.

**Step 2.4: `POST /bookings` (basic) and `GET /bookings/{id}`.** Create returns 201, lookup
returns 200 or 404.
*Check (end of Level 1):* curl the flow, then point the frontend at it.

### Phase 3: Level 2 (validation)

**Step 3.1: Validation in spec order.**
1. Body must be a JSON object, otherwise 400.
2. `slot_id`, `name`, `email` must be non-empty strings after trimming, otherwise 400.
3. Trim name and email. Lower-case the email.
4. Email format check, otherwise 400.
5. Slot not found: 404.
6. Slot already started: 400.
*Check:* table-driven tests covering each rejection and its status.

### Phase 4: Level 3 (concurrency and cancel)

**Step 4.1: Transactional booking.** Inside one transaction: lock the slot row
(`select_for_update`), check for a confirmed booking (409), insert. Catch the unique-violation
as a 409 backstop.

**Step 4.2: `DELETE /bookings/{id}`.** Lock the booking row. 404 if unknown, 409 if already
cancelled, otherwise set `cancelled` and return it.

**Step 4.3: Concurrency test.** Fire about 20 simultaneous POSTs at one slot using real
Postgres. Run Gunicorn with several threads or workers.
*Check:* exactly one 201 and the rest 409. Cancel, then rebook succeeds.

### Phase 5: Level 4 (idempotency and paging)

**Step 5.1: Idempotency.** Fingerprint = hash of the normalised body.
- Same key and same fingerprint: replay stored status and body (same booking id).
- Same key and a different fingerprint: 422.
- New key: process, then store the result in the same transaction.
- Race: the unique key decides the winner. The loser re-reads and returns the stored response.
- The key is checked **before** the slot-taken check, so a retry never looks like a conflict.
- Failures (400/404/409) are stored and replayed too.

**Step 5.2: Cursor paging.** `GET /bookings?email&limit&cursor`:
- `email` required and case-insensitive. Missing email is 400.
- `limit` integer 1 to 50, default 10. Anything else is 400.
- Order by `seq` ascending (oldest first). Cursor is base64 of the last `seq`.
- Fetch `limit + 1` to decide `next_cursor`. Bad cursor is 400.
- Includes both confirmed and cancelled bookings.
*Check:* replay x5 gives one booking. Different body gives 422. Paging 5 bookings with
`limit=2` has no gaps or repeats and ends with `next_cursor: null`.

### Phase 6: Level 5 (rate limit)

**Step 6.1: Per-email limiter.** Take a Postgres advisory lock keyed on the email, count
attempts in the last 60 seconds, and reject the 6th with **429** and `Retry-After`
(whole seconds, rounded up, at least 1). Record each attempt in its **own commit** so failed
attempts still count. Applies only once a valid email can be read.

**Step 6.2: Placement.** After email validation, before the idempotency lookup. Replays count
as attempts.
*Check:* 6 rapid attempts give 429 on the sixth. A different email is unaffected. `/reset`
clears counters.

### Phase 7: Frontend integration

**Step 7.1: Serve the page.** Move or reference `web/` through WhiteNoise. Inject the real
`API_KEY` into `window.APP_CONFIG`. `baseUrl` stays empty (same origin).
**Step 7.2: Seed a few demo slots** so the page is not empty for a human visitor
(graders replace them via `/reset`).
*Check:* run the full page flow in a browser against Django, including 409, 429 and cancel.

### Phase 8: Deploy

**Step 8.1: Production settings.** `DEBUG` off, `ALLOWED_HOSTS`, trusted proxy HTTPS header.
**Step 8.2: Render.** Build runs install, `collectstatic`, `migrate`. Start runs Gunicorn with
several threads. Set `API_KEY`, `DATABASE_URL`, `SECRET_KEY`.
**Step 8.3: Keep it awake.** Free uptime pinger on `/health`. Note the free Postgres expiry
date.
**Step 8.4: Live verification** with the checklist in section 6.

### Phase 9: Submit

Submit the HTTPS URL and the API key through the official channel. **After submitting, do not
redeploy or change anything** until the assessors make contact.

## 5. Key design rules (the spots most likely to break)

1. **Check order in `POST /bookings`:** parse, validate shape, normalise, email format, rate
   limit, idempotency, slot lookup, started check, lock, conflict check, insert.
2. **Rate-limit attempts commit separately** from the booking transaction, or failures vanish.
3. **Idempotency before conflict**, or a legitimate retry returns 409.
4. **`/reset` clears every table** that holds state, or one grader test pollutes the next.
5. **No trailing-slash redirects** (they break POST).
6. **Never use in-memory counters.** Multiple workers and restarts would break them.
7. **JSON everywhere,** including 404, 405 and 500. No HTML error pages.

## 6. Final verification checklist (live URL)

- [ ] Every endpoint: no key gives 401, wrong key gives 401, right key works
- [ ] `/reset` with past slots. `/slots` ordered and `provider` filter works.
- [ ] Missing, empty and whitespace fields, bad email, unknown slot (404), past slot (400)
- [ ] Name and email trimmed, email lower-cased in the response
- [ ] 20 parallel bookings on one slot: one 201, nineteen 409. Cancel then rebook works.
- [ ] DELETE twice: 200 then 409. Unknown id: 404.
- [ ] Same Idempotency-Key and body x5: same id and status. Different body: 422.
- [ ] Paging `limit=2` over 5 bookings: no gaps or repeats, `next_cursor` null at the end
- [ ] `limit=0`, `51`, `abc`: 400. Email match ignores case.
- [ ] 6 attempts for one email in a minute: 6th is 429 with whole-number `Retry-After`
- [ ] Other emails unaffected. Failed attempts count.
- [ ] Restart the service: bookings persist
- [ ] The web page works end to end on the live URL

## 7. Risks and open questions

| Risk | Mitigation |
|---|---|
| Free Render service sleeps, causing grader timeouts | Uptime pinger on `/health` |
| Free Postgres is deleted after a period | Note the expiry date and recreate before the deadline |
| Spec ambiguity: replay of failed requests under idempotency | Store and replay 4xx outcomes (safest reading) |
| Spec ambiguity: do replays count toward the rate limit | Yes, they are attempts |
| Spec ambiguity: missing email on `GET /bookings` | 400 |
| Spec ambiguity: unknown email on `GET /bookings` | 200 with an empty list |
| API key is visible in the page's JS | Accepted for this task. Documented limitation. |

## 8. What I need from you before coding

1. Approve this plan, or tell me what to change.
2. Answer **D3**: do you have Docker or Postgres installed locally?
3. Choose the **API key** value for production. I recommend a long random string, set only as
   an environment variable.
4. Confirm the **Render** account and a GitHub repo are available for deployment.

---

## 9. Running it

```
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
# put your Neon connection string in backend/.env as DATABASE_URL=...
python manage.py migrate
python manage.py test                       # unit + API + concurrency tests
gunicorn config.wsgi --workers 2 --threads 4   # then open http://127.0.0.1:8000/
```

Render settings: root directory `backend`, build `pip install -r requirements.txt && python manage.py migrate`,
start `gunicorn config.wsgi --workers 2 --threads 4`, and set `DATABASE_URL`, `API_KEY`, `SECRET_KEY`,
`DEBUG=false`, `ALLOWED_HOSTS`. Because the page is read from `../web`, deploy from the repo root and
use `cd backend && ...` in the build and start commands instead of a root directory.

## 10. Admin (Django admin + Jazzmin)

Open `/admin/`. It has its own login, so the API key isn't needed there. Create the login once:
`python manage.py createsuperuser` (run it yourself so you choose the password).

| Page | What it is for |
|---|---|
| Bookings | View-only. Search by reference, name, email or slot. Filter by status, provider, date. Action: **Cancel selected** (frees the slot). No add, edit or delete, so the admin can't bypass the API's rules. |
| Slots | Add, edit and delete slots. Shows Available or Booked. |
| Booking attempts | The rate-limit log. Delete someone's rows to lift their limit. |
| Idempotency records | View-only log of retry keys. |

On Render also set `CSRF_TRUSTED_ORIGINS=https://<your-app>.onrender.com` and run
`python manage.py collectstatic --noinput` in the build command.

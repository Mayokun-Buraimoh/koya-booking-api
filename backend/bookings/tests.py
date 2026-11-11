"""Tests.

Pure-logic tests (SimpleTestCase) need no database.
API tests need Postgres (Neon): they use advisory locks and a partial unique index.
Run all with:  python manage.py test
"""
import json
import threading
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.test import Client, SimpleTestCase, TransactionTestCase
from django.utils import timezone

from .cursors import decode_cursor, encode_cursor
from .errors import BadRequest
from .validators import normalise_email, parse_limit, validate_new_booking


# ---------------- no database needed ----------------

class ValidatorTests(SimpleTestCase):
    def test_trims_and_lowercases(self):
        self.assertEqual(
            validate_new_booking({"slot_id": " s1 ", "name": "  Ada Obi ", "email": "  ADA@Example.COM "}),
            ("s1", "Ada Obi", "ada@example.com"),
        )

    def test_rejects_bad_input(self):
        bad = [
            None, [], "x", {},
            {"slot_id": "s1", "name": "A"},
            {"slot_id": "s1", "name": "   ", "email": "a@b.co"},
            {"slot_id": "", "name": "A", "email": "a@b.co"},
            {"slot_id": "s1", "name": "A", "email": "not-an-email"},
            {"slot_id": 5, "name": "A", "email": "a@b.co"},
            {"slot_id": "s1", "name": "A", "email": "a b@c.co"},
        ]
        for body in bad:
            with self.subTest(body=body):
                with self.assertRaises(BadRequest):
                    validate_new_booking(body)

    def test_email_normalise(self):
        self.assertEqual(normalise_email(" A@B.co "), "a@b.co")
        self.assertIsNone(normalise_email("nope"))
        self.assertIsNone(normalise_email(5))

    def test_limit(self):
        self.assertEqual(parse_limit(None), 10)
        self.assertEqual(parse_limit("1"), 1)
        self.assertEqual(parse_limit("50"), 50)
        for bad in ("0", "51", "-1", "abc", "", "1.5", " 5"):
            with self.subTest(limit=bad):
                with self.assertRaises(BadRequest):
                    parse_limit(bad)

    def test_cursor_roundtrip_and_garbage(self):
        self.assertEqual(decode_cursor(encode_cursor(42)), 42)
        for bad in ("!!!", "bm9wZQ==", encode_cursor("x")):
            with self.subTest(cursor=bad):
                with self.assertRaises(BadRequest):
                    decode_cursor(bad)


# ---------------- needs the database (Neon) ----------------

def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


class ApiTests(TransactionTestCase):
    def setUp(self):
        self.c = Client()
        self.h = {"HTTP_X_API_KEY": settings.API_KEY}
        now = timezone.now().replace(microsecond=0)
        self.slots = [
            {"id": "past", "provider": "Dr Ade", "start": iso(now - timedelta(hours=2)), "end": iso(now - timedelta(hours=1))},
        ] + [
            {"id": f"s{i}", "provider": "Dr Ade" if i % 2 else "Dr Bola",
             "start": iso(now + timedelta(days=1, hours=i)), "end": iso(now + timedelta(days=1, hours=i, minutes=30))}
            for i in range(1, 9)
        ]
        self.call("POST", "/reset", {"slots": self.slots})
        self.n = 0

    def call(self, method, path, body=None, headers=None, key=True):
        kw = dict(self.h) if key else {}
        kw.update(headers or {})
        data = json.dumps(body) if body is not None else None
        r = getattr(self.c, method.lower())(path, data=data, content_type="application/json", **kw)
        try:
            payload = r.json()
        except ValueError:
            payload = None
        return r, payload

    def email(self):
        self.n += 1
        return f"user{self.n}@example.com"

    def book(self, slot="s1", email=None, name="Ada", headers=None):
        return self.call("POST", "/bookings", {"slot_id": slot, "name": name, "email": email or self.email()}, headers)

    # ---- level 1 / 2 ----
    def test_auth(self):
        for method, path in (("GET", "/slots"), ("POST", "/reset"), ("GET", "/bookings/x"), ("GET", "/nothing")):
            r, p = self.call(method, path, key=False)
            self.assertEqual((r.status_code, list(p)), (401, ["error"]))
        r, _ = self.call("GET", "/slots", headers={"HTTP_X_API_KEY": "wrong"}, key=False)
        self.assertEqual(r.status_code, 401)

    def test_slots_sorted_filtered_available(self):
        r, p = self.call("GET", "/slots")
        starts = [s["start"] for s in p["slots"]]
        self.assertEqual(starts, sorted(starts))
        self.assertEqual(len(p["slots"]), 9)  # past slots are listed too
        r, p = self.call("GET", "/slots?provider=Dr%20Ade")
        self.assertTrue(all(s["provider"] == "Dr Ade" for s in p["slots"]))
        self.book("s1")
        _, p = self.call("GET", "/slots")
        self.assertFalse(next(s for s in p["slots"] if s["id"] == "s1")["available"])

    def test_book_and_lookup(self):
        r, b = self.call("POST", "/bookings", {"slot_id": " s2 ", "name": "  Ada Obi ", "email": " ADA@Example.com "})
        self.assertEqual(r.status_code, 201)
        self.assertEqual((b["name"], b["email"], b["status"], b["slot_id"]), ("Ada Obi", "ada@example.com", "confirmed", "s2"))
        r, again = self.call("GET", f"/bookings/{b['id']}")
        self.assertEqual((r.status_code, again), (200, b))
        r, p = self.call("GET", "/bookings/nope")
        self.assertEqual((r.status_code, list(p)), (404, ["error"]))

    def test_validation_statuses(self):
        cases = [
            ({"name": "A", "email": "a@b.co"}, 400),
            ({"slot_id": "s1", "name": "", "email": "a@b.co"}, 400),
            ({"slot_id": "s1", "name": "A", "email": "bad"}, 400),
            ({"slot_id": "nope", "name": "A", "email": "a@b.co"}, 404),
            ({"slot_id": "past", "name": "A", "email": "a@b.co"}, 400),
        ]
        for i, (body, status) in enumerate(cases):
            body = dict(body)
            if "email" in body and body["email"] != "bad":
                body["email"] = f"v{i}@b.co"
            with self.subTest(body=body):
                r, p = self.call("POST", "/bookings", body)
                self.assertEqual(r.status_code, status)
                self.assertEqual(list(p), ["error"])
        r = self.c.post("/bookings", data="{not json", content_type="application/json", **self.h)
        self.assertEqual(r.status_code, 400)

    # ---- level 3 ----
    def test_conflict_cancel_rebook(self):
        r, b = self.book("s3")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.book("s3")[0].status_code, 409)
        r, c = self.call("DELETE", f"/bookings/{b['id']}")
        self.assertEqual((r.status_code, c["status"]), (200, "cancelled"))
        self.assertEqual(self.call("DELETE", f"/bookings/{b['id']}")[0].status_code, 409)
        self.assertEqual(self.call("DELETE", "/bookings/nope")[0].status_code, 404)
        self.assertEqual(self.book("s3")[0].status_code, 201)  # slot freed

    def test_concurrent_bookings_one_winner(self):
        results = []

        def worker(i):
            try:
                c = Client()
                r = c.post("/bookings", data=json.dumps({"slot_id": "s4", "name": "X", "email": f"race{i}@example.com"}),
                           content_type="application/json", **self.h)
                results.append(r.status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sorted(results), [201] + [409] * 19)

    # ---- level 4 ----
    def test_idempotency(self):
        email = self.email()
        h = {"HTTP_IDEMPOTENCY_KEY": "k-1"}
        r1, b1 = self.book("s5", email, headers=h)
        r2, b2 = self.book("s5", email, headers=h)
        self.assertEqual((r1.status_code, r2.status_code, b1["id"]), (201, 201, b2["id"]))
        r3, _ = self.book("s6", email, headers=h)           # same key, different body
        self.assertEqual(r3.status_code, 422)
        _, p = self.call("GET", f"/bookings?email={email}")
        self.assertEqual(len(p["bookings"]), 1)

    def test_paging(self):
        email = "pager@example.com"
        ids = []
        for s in ("s1", "s2", "s3", "s4", "s5"):
            r, b = self.book(s, email)
            self.assertEqual(r.status_code, 201)
            ids.append(b["id"])
        seen, cursor, pages = [], None, 0
        while True:
            q = f"/bookings?email=PAGER@example.com&limit=2" + (f"&cursor={cursor}" if cursor else "")
            r, p = self.call("GET", q)
            self.assertEqual(r.status_code, 200)
            seen += [b["id"] for b in p["bookings"]]
            pages += 1
            cursor = p["next_cursor"]
            if not cursor:
                break
        self.assertEqual((seen, pages), (ids, 3))
        for bad in ("0", "51", "abc"):
            self.assertEqual(self.call("GET", f"/bookings?email={email}&limit={bad}")[0].status_code, 400)

    # ---- level 5 ----
    def test_rate_limit(self):
        email = "spam@example.com"
        codes = [self.book("nope", email)[0].status_code for _ in range(5)]   # failures count
        self.assertEqual(codes, [404] * 5)
        r, p = self.book("s7", email)
        self.assertEqual(r.status_code, 429)
        self.assertTrue(r["Retry-After"].isdigit() and int(r["Retry-After"]) >= 1)
        self.assertEqual(self.book("s7", "other@example.com")[0].status_code, 201)

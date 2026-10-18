import json
import os
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from time import perf_counter
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
MONEY = Decimal("0.01")


def get(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=12) as r:
        return json.loads(r.read())


def load_rows():
    root = get(os.environ["DATA_URL"])
    rates = {k: Decimal(str(v)) for k, v in get(root["links"]["rates"])["usd_per_unit"].items()}
    rows = {}
    url = root["links"]["orders"]
    while url:
        page = get(url)
        for row in page["orders"]:
            if row["id"] not in rows or row["updated_at"] > rows[row["id"]]["updated_at"]:
                rows[row["id"]] = row
        url = page.get("next")
    return list(rows.values()), rates


def expected(question, rows, rates):
    q = " ".join(question.casefold().split())
    selected = rows[:]
    for field, values in (("region", ["north", "south", "east", "west", "central"]),
                          ("status", ["paid", "refunded", "void"])):
        hit = next((v for v in values if re.search(rf"\b{v}\b", q)), None)
        if hit:
            selected = [r for r in selected if r[field].casefold() == hit]
    m = re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})", q)
    if m:
        months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
        month = months[m.group(1)[:3]]
        selected = [r for r in selected if (lambda d: d.year == int(m.group(2)) and d.month == month)(datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone(IST).date())]
    if re.search(r"\bq1\s+2026\b", q):
        selected = [r for r in selected if date(2026, 1, 1) <= datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone(IST).date() < date(2026, 4, 1)]
    elif not m:
        year = re.search(r"\b(20\d{2})\b", q)
        if year:
            selected = [r for r in selected if datetime.fromisoformat(r["created_at"].replace("Z", "+00:00")).astimezone(IST).date().year == int(year.group(1))]
    def usd(r):
        return int((Decimal(str(r["amount"])) * rates[r["currency"]] * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if "refund" in q:
        return float(Decimal(sum(usd(r) for r in selected if r["status"] == "refunded")) / 100)
    if "net" in q:
        return float(Decimal(sum(usd(r) for r in selected if r["status"] == "paid") - sum(usd(r) for r in selected if r["status"] == "refunded")) / 100)
    if "unique" in q and "customer" in q:
        return len({r["customer"] for r in selected})
    if "average" in q:
        paid = [r for r in selected if r["status"] == "paid"]
        return float((Decimal(sum(usd(r) for r in paid)) / max(1, len(paid)) / 100).quantize(MONEY, rounding=ROUND_HALF_UP))
    if "how many" in q or "count" in q or "orders" in q and "revenue" not in q:
        return len(selected)
    return float(Decimal(sum(usd(r) for r in selected if r["status"] == "paid")) / 100)


def call(question):
    body = json.dumps({"question": question}).encode()
    req = urllib.request.Request(os.environ.get("ENDPOINT", "http://127.0.0.1:8000/"), data=body, method="POST", headers={"Content-Type": "application/json"})
    started = perf_counter()
    with urllib.request.urlopen(req, timeout=15) as r:
        answer = json.loads(r.read())["answer"]
    return answer, perf_counter() - started


def main():
    rows, rates = load_rows()
    questions = [
        "What was the total revenue in USD from the North region in March 2026?",
        "How much money did we make in the north in Mar 2026?",
        "March 2026 north-region sales total, in dollars?",
        "What were the refunds in March 2026?",
        "What was net revenue in Q1 2026?",
        "How many unique customers did we have?",
        "What was the average order value in April 2026?",
        "How many orders were in the South region?",
        "What was revenue in EUR? (USD total)",
        "What revenue did the West region have in 2026?",
        "What was revenue in February 2026?",
        "What was revenue in May 2026?",
        "What was revenue in a nonexistent month January 2030?",
        "What was revenue in the unknown Atlantis region?",
        "What was revenue for the typo Blenderx product?",
        "  WHAT was the total revenue from the north in MARCH 2026? ",
        "How many paid orders were in March 2026?",
        "What were the refunds in Q1 2026?",
        "What is the net revenue in the Central region?",
        "How many orders were in 2025?",
        "What is the total revenue in June 2026?",
        "What is the total revenue in the East region?",
    ]
    expected_answers = [expected(q, rows, rates) for q in questions]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(call, questions))
    failures = []
    for i, ((actual, latency), want) in enumerate(zip(results, expected_answers), 1):
        ok = actual == want
        print(f"{'PASS' if ok else 'FAIL'} {i:02d} {latency:.3f}s expected={want!r} actual={actual!r}")
        if not ok:
            failures.append((i, want, actual))
    print(f"{len(questions) - len(failures)}/{len(questions)} passed; max latency={max(x[1] for x in results):.3f}s")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

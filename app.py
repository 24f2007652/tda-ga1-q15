import json
import os
import re
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from threading import Lock
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

load_dotenv()
IST = ZoneInfo("Asia/Kolkata")
MONEY = Decimal("0.01")


class Question(BaseModel):
    question: str = ""


class Ledger:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.rates: dict[str, Decimal] = {}
        self.loaded = False
        self.error: str | None = None
        self.lock = Lock()

    def load(self) -> None:
        if self.loaded:
            return
        with self.lock:
            if self.loaded:
                return
            try:
                url = os.environ.get("DATA_URL")
                if not url:
                    raise RuntimeError("DATA_URL is not configured")
                root = fetch_json(url)
                rates = fetch_json(root["links"]["rates"])
                self.rates = {k: Decimal(str(v)) for k, v in rates["usd_per_unit"].items()}
                current: dict[str, dict[str, Any]] = {}
                export = fetch_json(root["links"]["export"])
                export_rows = [json.loads(line) for line in export.splitlines() if line.strip()]
                for row in export_rows:
                    old = current.get(row["id"])
                    if old is None or parse_dt(row["updated_at"]) > parse_dt(old["updated_at"]):
                        current[row["id"]] = row
                self.rows = list(current.values())
                self.loaded = True
            except Exception as exc:
                self.error = str(exc)
                raise


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=8) as response:
        text = response.read().decode("utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return text


def parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


ledger = Ledger()
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def preload_ledger() -> None:
    ledger.load()


def month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return start, end


def time_filter(text: str) -> tuple[date | None, date | None]:
    t = text.lower()
    m = re.search(r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(\d{4})", t)
    months = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
    if m:
        return month_bounds(int(m.group(2)), months[m.group(1)[:3]])
    q = re.search(r"\bq([1-4])\s*(\d{4})", t)
    if q:
        start = (int(q.group(2)), (int(q.group(1)) - 1) * 3 + 1)
        return month_bounds(*start)[0], month_bounds(start[0], start[1] + 2)[1]
    y = re.search(r"\b(20\d{2})\b", t)
    if y:
        year = int(y.group(1))
        return date(year, 1, 1), date(year + 1, 1, 1)
    return None, None


def normalize(value: str) -> str:
    return " ".join(value.casefold().split())


def heuristic_spec(question: str) -> dict[str, Any]:
    q = normalize(question)
    metric = "revenue"
    is_count = bool(re.search(r"\b(count|how many|number of)\b", q))
    if is_count and re.search(r"\borders?\b", q):
        metric = "orders"
    elif re.search(r"\b(refund|refunded|refunds)\b", q):
        metric = "refunds"
    elif re.search(r"\bnet\b", q):
        metric = "net_revenue"
    elif re.search(r"\b(average|avg)\b.*\b(order|value)\b|\baverage order", q):
        metric = "average_order_value"
    elif re.search(r"\b(unique|distinct)\b.*customer", q):
        metric = "distinct_customers"
    elif re.search(r"\b(unique|distinct)\b.*product", q):
        metric = "distinct_products"
    elif re.search(r"\b(number|count|how many)\b.*\border", q):
        metric = "orders"
    group_by = None
    for candidate in ("product", "customer", "region"):
        if re.search(rf"\b(top|highest|lowest|most|least|which|by)\b.*\b{candidate}s?\b|\b{candidate}s?\b.*\b(top|highest|lowest|most|least)\b", q):
            group_by = candidate
            break
    if group_by is None and re.search(r"\b(by|compare|comparison|each)\b", q):
        for candidate in ("product", "customer", "region"):
            if candidate in q:
                group_by = candidate
                break
    descending = not bool(re.search(r"\b(lowest|least|minimum|smallest)\b", q))
    limit_match = re.search(r"\btop\s+(\d+)", q)
    limit = int(limit_match.group(1)) if limit_match else (1 if re.search(r"\b(which|who|highest|lowest|most|least)\b", q) and group_by else None)
    spec: dict[str, Any] = {
        "metric": metric, "filters": {}, "group_by": group_by,
        "sort": "desc" if descending else "asc", "limit": limit,
        "start_date": None, "end_date": None,
    }
    for field, words in (("region", ["north", "south", "east", "west", "central"]),
                         ("status", ["paid", "refunded", "void"])):
        for word in words:
            if re.search(rf"\b{word}\b", q):
                spec["filters"][field] = word.title() if field == "region" else word
    for field in ("product", "customer"):
        matches = [value for value in ({r[field] for r in ledger.rows}) if normalize(value) in q]
        if matches:
            spec["filters"][field] = max(matches, key=len)
    start, end = time_filter(q)
    spec["start_date"], spec["end_date"] = start.isoformat() if start else None, end.isoformat() if end else None
    if metric in ("distinct_customers", "distinct_products") and group_by is None:
        spec["output"] = "number"
    return spec


def llm_spec(question: str) -> dict[str, Any] | None:
    key = os.environ.get("LLM_API_KEY")
    base = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    if not key:
        return None
    values = {
        "regions": sorted({r["region"] for r in ledger.rows}),
        "products": sorted({r["product"] for r in ledger.rows}),
        "customers": sorted({r["customer"] for r in ledger.rows}),
        "statuses": sorted({r["status"] for r in ledger.rows}),
    }
    system = """Convert a question into JSON only. Never calculate. Use this schema:
{"metric":"revenue|refunds|net_revenue|orders|average_order_value|distinct_customers|distinct_products",
"filters":{"region":string,"product":string,"customer":string,"status":string},
"group_by":"region|product|customer|null","start_date":"YYYY-MM-DD|null",
"end_date":"YYYY-MM-DD|null","sort":"asc|desc|null","limit":number|null}.
Use business dates in Asia/Kolkata. Revenue is paid rows; refunds are refunded rows."""
    prompt = system + "\nKnown values: " + json.dumps(values) + "\nQuestion: " + question
    body = json.dumps({"model": os.environ.get("LLM_MODEL", "gpt-4o-mini"), "temperature": 0,
                       "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}).encode()
    request = urllib.request.Request(base + "/chat/completions", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.loads(json.loads(response.read())["choices"][0]["message"]["content"])
    except Exception:
        return None


def correct_count_intent(question: str, spec: dict[str, Any]) -> dict[str, Any]:
    q = normalize(question)
    count_words = r"\b(how many|count|number of)\b"
    if re.search(count_words, q) and re.search(r"\b(different|distinct|unique)\b.*\bcustomers?\b|\bcustomers?\b", q):
        if re.search(r"\b(different|distinct|unique)\b.*\bcustomers?\b", q):
            spec["metric"] = "distinct_customers"
            spec["group_by"] = None
            spec["limit"] = None
    if re.search(count_words, q) and re.search(r"\b(different|distinct|unique)\b.*\bproducts?\b", q):
        if re.search(r"\b(different|distinct|unique)\b.*\bproducts?\b", q):
            spec["metric"] = "distinct_products"
            spec["group_by"] = None
            spec["limit"] = None
    winner_words = r"\b(top[- ]selling|best[- ]selling|highest|most|least|lowest|which)\b"
    if re.search(winner_words, q):
        for dimension in ("product", "customer", "region"):
            if re.search(rf"\b{dimension}s?\b", q):
                spec["group_by"] = dimension
                spec["sort"] = "desc"
                spec["limit"] = 1
                spec["metric"] = "revenue"
                break
    elif re.search(r"\b(total|how much|amount|revenue|sales|refunds?|net)\b", q):
        spec["group_by"] = None
        spec["limit"] = None
    return spec


def cents(row: dict[str, Any]) -> int:
    return int((Decimal(str(row["amount"])) * ledger.rates[row["currency"]] * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def execute(spec: dict[str, Any]) -> Any:
    valid = {"revenue", "refunds", "net_revenue", "orders", "average_order_value", "distinct_customers", "distinct_products"}
    metric = spec.get("metric", "revenue")
    if metric not in valid:
        metric = "revenue"
    rows = ledger.rows
    filters = spec.get("filters") or {}
    start, end = spec.get("start_date"), spec.get("end_date")
    start_d, end_d = date.fromisoformat(start) if start else None, date.fromisoformat(end) if end else None
    selected = []
    for row in rows:
        if any(normalize(str(row.get(k, ""))) != normalize(str(v)) for k, v in filters.items() if v is not None):
            continue
        d = parse_dt(row["created_at"]).astimezone(IST).date()
        if start_d and d < start_d or end_d and d >= end_d:
            continue
        selected.append(row)
    def value(rs: list[dict[str, Any]]) -> Decimal | int:
        if metric == "revenue":
            return sum((cents(r) for r in rs if r["status"] == "paid"), 0)
        if metric == "refunds":
            return sum((cents(r) for r in rs if r["status"] == "refunded"), 0)
        if metric == "net_revenue":
            return sum((cents(r) for r in rs if r["status"] == "paid"), 0) - sum((cents(r) for r in rs if r["status"] == "refunded"), 0)
        if metric == "orders":
            return len(rs)
        if metric == "average_order_value":
            return Decimal(sum(cents(r) for r in rs if r["status"] == "paid")) / max(1, sum(r["status"] == "paid" for r in rs))
        if metric == "distinct_customers":
            return len({r["customer"] for r in rs})
        return len({r["product"] for r in rs})
    group = spec.get("group_by")
    if group:
        buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in selected:
            buckets[row[group]].append(row)
        result = [{"name": k, "value": value(v)} for k, v in buckets.items()]
        result.sort(key=lambda x: x["value"], reverse=spec.get("sort", "desc") != "asc")
        if spec.get("limit"):
            result = result[:int(spec["limit"])]
        if len(result) == 1 and int(spec.get("limit") or 0) == 1:
            return result[0]["name"]
        return [{"name": x["name"], "value": format_value(x["value"], metric)} for x in result]
    return format_value(value(selected), metric)


def format_value(value: Any, metric: str) -> Any:
    if metric in ("revenue", "refunds", "net_revenue", "average_order_value"):
        amount = Decimal(value) / 100
        return float(amount.quantize(MONEY, rounding=ROUND_HALF_UP))
    return int(value)


@app.get("/health")
def health() -> dict[str, Any]:
    try:
        ledger.load()
        return {"status": "ok", "rows": len(ledger.rows)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


@app.post("/")
def answer(payload: Question) -> dict[str, Any]:
    try:
        ledger.load()
        question = payload.question.strip()
        if not question:
            return {"answer": 0}
        spec = correct_count_intent(question, llm_spec(question) or heuristic_spec(question))
        return {"answer": execute(spec)}
    except Exception:
        return {"answer": 0}

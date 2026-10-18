# Phase 1: Order-ledger API findings

## Resources and pagination

The root resource is the private `DATA_URL` with `path=/`. It returns:

- `service`: `"Acme Appliances order ledger"`
- `links.orders`: the paginated order collection
- `links.rates`: exchange-rate metadata
- `links.export`: an NDJSON export of the order collection
- `notes`: business rules

No additional links or resources for customers, products, refunds, schemas, changelogs, or notes were advertised by the root response. The order rows contain customer and product identifiers/names directly.

`/orders` pagination is page-based:

- The first request includes `page=1`.
- Each response has `page`, `pages`, `orders`, and `next`.
- Each page has 50 rows. The complete traversal returned 85 pages and 4,224 rows.
- `next` is the authoritative next URL; it is absent/false after page 85.
- Invalid pages and unsupported `limit`/`offset` parameters return 404, so the implementation follows `next` rather than synthesizing pagination URLs.
- The `/export` resource is a JSON string containing one JSON object per line (NDJSON), also 4,224 rows. Its ID/order sequence matches the paginated collection; timestamp strings can differ only in insignificant trailing-zero formatting.

## Exchange rates

The `/rates` response is:

```json
{
  "description": "USD value of one unit of each currency",
  "usd_per_unit": {
    "USD": 1,
    "EUR": 1.14,
    "INR": 0.01225
  }
}
```

These are fixed rates, not dated rates. USD conversion is `amount * usd_per_unit[currency]`; all money calculations retain integer cents and round only at final output.

## Order schema

Every order has all of the following non-null fields:

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Order identifier; not unique in the raw feed |
| `customer` | string | Customer identifier, e.g. `C1012` |
| `region` | string | One of `Central`, `East`, `North`, `South`, `West` |
| `product` | string | Exact API spelling |
| `qty` | integer | Quantity, observed range 1–6 |
| `unit_price` | number | Price per unit in the row's currency |
| `amount` | number | Observed to equal `qty * unit_price` |
| `currency` | string | One of `USD`, `EUR`, `INR` |
| `status` | string | One of `paid`, `refunded`, `void` |
| `created_at` | string | ISO-8601 timestamp, with either `Z` or an explicit offset |
| `updated_at` | string | ISO-8601 timestamp, with either `Z` or an explicit offset |

## Business rules and data traps

The API's own notes state:

1. Business dates use the `Asia/Kolkata` timezone.
2. An order ID can occur more than once; the row with the latest `updated_at` is current.
3. Only orders with status `paid` count as revenue.

Implementation consequences:

- Parse timestamps as timezone-aware datetimes and convert them to `Asia/Kolkata` before month/quarter/year/range filtering.
- Deduplicate by `id`, retaining the maximum `updated_at`. Raw rows: 4,224; current rows: 4,000; duplicated IDs: 224.
- Gross revenue is the USD-converted `amount` of current `paid` rows only. `refunded` and `void` rows do not count as revenue.
- There is no refund amount, refund timestamp, or refund endpoint. A refund metric is therefore the USD-converted `amount` of current rows whose status is `refunded`, attributed to the order's business date (`created_at` in Asia/Kolkata). This is the only reproducible refund interpretation supported by the API.
- Net revenue is treated as paid revenue minus refunded amount. A refunded current row is not also included in paid revenue, so this intentionally reports the ledger's paid-minus-refunded view rather than subtracting a refund twice from gross paid rows.
- Order counts are counts of current order IDs, not raw duplicate rows. Revenue/order metrics use the row's `amount`; the equality `amount == qty * unit_price` holds for all 4,224 raw rows.
- No negative or zero amounts/prices and no null fields were found.
- Product, region, and customer filters are matched case-insensitively after trimming user input, while returned product/customer/region names use the API's exact spelling. Unknown values produce an empty result/best-effort zero rather than being silently aliased.

## Summary statistics

Computed over the raw collection unless noted:

| Statistic | Value |
|---|---:|
| Raw rows | 4,224 |
| Current rows after latest-update deduplication | 4,000 |
| Distinct customers | 250 |
| Distinct regions | 5 |
| Distinct products | 8 |
| Date range (raw `created_at`) | 2025-12-31T20:18:56.792Z through 2026-06-30T21:18:24.606+05:30 |
| Currencies (raw) | USD 2,104; EUR 1,082; INR 1,038 |
| Statuses (raw) | paid 3,432; refunded 458; void 334 |
| Statuses (current) | paid 3,208; refunded 458; void 334 |
| Products | Air Fryer, Blender, Grinder, Juicer, Kettle, Mixer, Rice Cooker, Toaster |
| Regions | Central, East, North, South, West |

The private URL and its signature are intentionally not included in this file. Runtime configuration must provide them through `DATA_URL`.

# Roadmap and status

## Phase summary

| Phase | Status | What works and is tested | Incomplete / next |
|---|---|---|---|
| 1 Foundation | ✅ | Architecture, 26-table data model with Alembic migration, config, encrypted credentials, redacted logging, error handling, app shell with 10 sections | — |
| 2 Data integration | ✅ (unverified against a live store) | Shopify connector (both auth methods), bulk-operation sync, incremental watermarks, throttling/retries, partial-failure handling, scheduler, freshness; offers workbook, costs CSV, traffic CSV importers with preview, duplicate protection and row-level issues | First connection to a real store (see note below); orders CSV import for unconnected stores |
| 3 Analytics | ✅ | Deterministic metrics, store/product/variant/basket analysis, concentration, co-purchase, seasonality, customers, discount dependency; threshold, quantity, price, bundle, discount-depth, gift-vs-discount simulations | Price-elasticity estimation from price history (needs real price variation) |
| 4 Offers & recommendations | ✅ | Offer library/events/coupons, verification vs store, measured event results, monitoring alerts; 14 detectors, objective-aware prioritisation, frameworks with sources, optional Claude commentary | Calendar (month grid) view; editing inferred offer mechanics in the library |
| 5 Experiments & learning | ✅ | Lifecycle with guarded transitions, controlled-test statistics with power, before/after with confounders and year-ago adjustment, learning records feeding recommendations | Automatic arm metrics from a testing tool integration |
| 6 Controlled execution | ◐ deliberately partial | Proposals with exact current/proposed values, ±30% safeguard, typed approval, staleness re-check, idempotency, audit log, manual-apply + sync verification | Automated executors (`productVariantsBulkUpdate`, `discountCodeBasicCreate`, …) after write scopes are granted and tested on a development store, with rollback |
| 7 Hosting | ◐ prepared | Password auth, hosted-mode guard, Postgres-ready config, Dockerfile, documented migration | Choose host, Postgres driver, HTTPS, deploy and verify |

## Important limitations (honest list)

- **Shopify live verification pending**: shopify.dev was blocked in the build environment. The client
  was tested against an emulated API; the on-connect probe will surface any schema differences in the
  first real connection. Treat the first sync as the acceptance test.
- **Workbook import was validated on the Events sheet** (CSV export, 186 rows, 8 stores). The `.xlsx`
  path (Events + Fix Codes + Store Discounts) is covered by tests with a synthetic workbook of the same
  layout; run a preview on the real `.xlsx` first. The *Offers* (before/after LP comparisons) and *COGS*
  sheets are not imported (the COGS sheet was empty; the Offers sheet uses merged cells — re-enter its
  conclusions as experiments).
- The import flagged real data issues in the workbook (e.g. five sales amounts identical to another row,
  one end date before its start date, dates without a year); they are attached to the events, not corrected.
- Traffic/conversion requires an imported sessions export; ShopifyQL analytics access is not used yet.
- Recommendations assume a 90-day window by default; very small stores may produce few or no recommendations — by design.
- The scheduler runs in-process: if the app is not running, nothing syncs.
- The Dockerfile has not been built yet (container registries were not used in the build environment); build and test it as the first Phase 7 step.

## Suggested next steps

1. Connect one real store with a read-only Dev Dashboard app; run a full sync; compare totals with
   Shopify Analytics for one month (net sales, orders, discounts) and record differences.
2. Import the full `.xlsx` workbook (preview first) and costs for that store.
3. Review the first recommendations together and tune detector thresholds to the business.
4. Then: a traffic connector (GA4 or ShopifyQL), and Phase 6 executors on a development store.

# Metric definitions

All amounts are in the store's (shop) currency and exclude tax. Test and cancelled orders are
excluded. Periods are calendar days in the **store's timezone**. Implementation:
`backend/app/analytics/metrics.py` (formulas), `dataset.py` (inputs), `store_analysis.py`,
`product_analysis.py`, `basket_analysis.py`, `simulations.py`. A metric whose inputs are missing
is shown as "—", never as zero.

## Sales

| Metric | Formula | Notes |
|---|---|---|
| Gross sales | Σ line original unit price × quantity | Merchandise only; gift cards excluded. |
| Discounts | Σ order discounts (line + order level) | From Shopify `totalDiscountsSet`. |
| Net sales | Gross sales − Discounts | Before returns. Matches Shopify's "net sales before returns" convention. |
| Returns | Σ refunded merchandise (refund line subtotals) | If refund lines are unavailable, total refunded capped at the order's net sales (flagged *returns estimated*). |
| Net sales kept | Net sales − Returns | |
| Orders | count of orders | |
| AOV | Net sales ÷ Orders | Excludes shipping and tax. |
| Units per order | Σ units ÷ Orders | |
| Discount rate | Discounts ÷ Gross sales | Share of list-price value given away. |
| Discounted order share | orders with discount > 0 ÷ Orders | Exposure, not causation. |
| Return rate | Returns ÷ Net sales | Product level uses refunded units ÷ units. |

## Profitability

| Metric | Formula | Requirements |
|---|---|---|
| COGS | Σ unit cost × (quantity − refunded quantity) | Unit cost effective on the order date; manual/CSV costs take precedence over Shopify cost. Returned units are assumed restocked. Computed only when ≥95% of net sales have a cost (recorded or the store's declared fallback COGS %, which is labelled as an assumption). Uncosted lines below that threshold are listed as missing. |
| Gross profit | Net sales kept − COGS | |
| Gross margin % | Gross profit ÷ Net sales kept | |
| Contribution | Gross profit + shipping charged − shipping cost/order × orders − (payment fee % × customer paid + fixed fee × orders) − fulfilment cost/order × orders | Each missing component is listed; contribution is then overstated by that component. |

## Traffic and conversion

| Metric | Formula | Notes |
|---|---|---|
| Conversion rate | orders on days with traffic data ÷ sessions on those days | Requires imported sessions (Shopify Analytics / GA4 export). Days without traffic rows are excluded from both sides; the coverage is shown. |

## Customers

| Metric | Formula |
|---|---|
| New / returning order | customer order index = 1 / > 1 (cancelled and test orders excluded from the sequence) |
| 90-day repeat rate | customers whose **first** order fell in the period and ≥ 90 days before its end, who placed another order within 90 days ÷ those customers |

Requires `read_customers`. Only Shopify's customer ID is stored.

## Product intelligence

| Metric | Formula |
|---|---|
| Share of net sales | product net sales ÷ total product net sales |
| Growth | (net sales − previous-period net sales) ÷ previous-period net sales |
| Discounted unit share | units sold on lines with a discount ÷ units |
| Solo order share | orders containing only this product ÷ orders containing it |
| Days of cover | tracked inventory ÷ (units sold ÷ days in period) |
| HHI | Σ shareᵢ² over products; effective products = 1 ÷ HHI |
| Top-N share, products for 80% | cumulative share of the largest products |

### Co-purchase

- support(A,B) = orders with A and B ÷ all orders
- confidence(A→B) = orders with A and B ÷ orders with A
- lift(A,B) = confidence(A→B) ÷ (orders with B ÷ all orders); > 1 means bought together more than chance
- Pairs with fewer than max(3, 0.2% of orders) joint orders are hidden.

## Simulations (scenarios, not forecasts)

**Threshold incentive** (free gift / free shipping / reward above spend T):
- above = orders ≥ T (receive the incentive without changing behaviour: *deadweight*)
- reachable = orders in [T × (1 − w), T), default w = 25%
- for mover rate r: incremental revenue = r × Σ(T × (1 + overshoot) − value) over reachable;
  incremental GP = incremental revenue × margin; incentive cost = (above + r × reachable) × cost
- net = incremental GP − incentive cost; **break-even r** = above × cost ÷ (Σ top-ups × margin − reachable × cost)

**Quantity incentive** (buy N, save d%): reachable = orders with N − 1 units; per mover
GP = unit price × (1 − d) − unit cost − (reachable AOV × d); deadweight = d × value of orders already ≥ N.

**Price change**: price change p, unit margin m = (P − C)/P; break-even volume change
Δv = −p ÷ (m + p). Volume responses shown as scenarios.

**Bundle** A + B at discount d: GP per mover = P_B(1 − d) − P_A·d − C_B; cannibalisation =
orders already buying both × (P_A + P_B) × d; break-even attach uplift = cannibalisation ÷ (GP per mover × A-only orders).

**Discount depth**: GP per redeemed order = V(m − d); redemptions needed to match = R₁ × (m − d₁)/(m − d₂).

## Experiments

- Controlled designs: two-sided two-proportion z-test on conversion; value per visitor compared;
  required sample per arm for a 10% relative lift (α = 0.05, power = 0.8).
  *Strong* = p < 0.05 and adequately powered; *moderate* = p < 0.05 under-powered, or p < 0.10 powered;
  otherwise *inconclusive*.
- Before/after: KPIs per day in baseline vs test windows; year-ago windows give a
  difference-in-differences adjustment when data exists; traffic and overlapping promotions are listed
  as confounders. Maximum rating: *weak*.
- Learning records from weak/inconclusive experiments are shown but never change recommendations.

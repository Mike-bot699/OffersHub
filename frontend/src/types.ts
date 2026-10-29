export type DiscountAnalysis = {
  period: { start: string; end: string }
  totals: Record<string, number | null>
  aov_split: { segment: string; orders: number; aov: number | null; units_per_order: number | null }[]
  weekly: { week: string; orders: number; net_sales: number; discounts: number; discount_rate: number | null
    discounted_order_share: number | null; aov: number | null; units_per_order: number | null }[]
  by_code: { code: string; orders: number; order_share: number | null; net_sales: number; discounts: number
    discount_per_order: number | null; aov: number | null; units_per_order: number | null; new_customer_share: number | null }[]
  by_depth: { bucket: string; orders: number; order_share: number | null; aov: number | null; units_per_order: number | null; discounts: number }[]
  note: string
}

// Strategy types that belong to the offers / discounts / AOV focus of the application.
export const OFFER_STRATEGIES = [
  'discount_reduction', 'free_gift', 'spend_threshold', 'free_shipping', 'quantity_incentive', 'bundle',
  'cross_sell_upsell', 'promotion', 'pricing', 'cart_gamification', 'seasonal', 'segmentation', 'data_quality',
]

export function isOfferStrategy(s: string) {
  return OFFER_STRATEGIES.includes(s)
}

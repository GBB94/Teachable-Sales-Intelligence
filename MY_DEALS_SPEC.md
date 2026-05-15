# My Deals: Product Limitation Review

## Goal

Create a focused review page for Zach-owned closed-lost deals where a product limitation was marked in HubSpot. The page should make it easy to present customer feedback to the product team one account at a time.

## Source Of Truth

- Inclusion uses the HubSpot `loss_reason` property when it equals `Product Limitation`.
- The feature dropdown values that accompany the marker are not displayed.
- The visible feature/problem evidence comes from `notes_on_customer_feedback`.
- Deals without a note-derived feature signal still appear, because the marker is the counting source.

## Default View

- Owner: Zach / My Deals.
- Outcome: Closed Lost.
- Reason: Product Limitation.
- Period: All available win/loss window.
- Sort: newest closed date first.

## Primary Workflow

1. Open My Deals.
2. See the size and value of the product-limitation review set.
3. Optionally filter by note-derived need signal.
4. Use the jump list or scroll-snap cards to move deal by deal.
5. Read the customer feedback note as the primary source material.
6. Use the side panel for structured context: HubSpot loss type, marker status, note-derived signals, competitors, and closed-lost reason.

## UX Requirements

- The page should feel like a presentation deck, not a CRM table.
- Customer feedback should be visible by default.
- Each deal should be a large card with readable note text.
- Product need chips should be derived from the customer feedback note only.
- The side rail should allow quick jumping without replacing the card flow.
- Next/previous controls should support team readout flow.
- A reviewed deal can be dismissed locally, and dismissed deals can be restored.
- Empty or low-confidence extracted signals should be explicit rather than hiding the deal.

## Non Goals

- Do not publish raw HubSpot engagement note bodies.
- Do not display the product feedback dropdown values.
- Do not use the note-derived feature extraction as the inclusion gate when marker fields are present.

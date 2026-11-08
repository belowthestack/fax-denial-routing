# Dashboard design

Static dashboard in `dashboard/`. No build step, no framework. Light mode, matte, compact.
The visual language borrows from two of Arfa's other products: the flat editorial light palette
and status pills of a news reader, and the compact, colour-only-where-it-means-something browse
surface of a study app (category tints, pills, small uppercase status tags).

## Principles
- Depth comes from a light surface ladder and 1px hairlines. No drop shadows, no gradients on cards or buttons, no glow (the ARFA mark halo is the one sanctioned exception, per BRAND_ASSETS).
- One accent (deep teal), used for the primary action in a panel, focus, the active nav count and the "Synthetic data" tag. Everything else is an ink ramp.
- Colour appears only where it means something:
  - amber: a person needs to look (review queue, low confidence, high priority, appeal deadline)
  - red: failed and held (dead letter)
  - green: approved and filed
  - one flat tint per denial reason (board columns and the "Routed to" tag)
- Left-aligned, dense, asymmetric: eyebrow, title, lede, then panels. No centered hero.
- Every screen carries the "Synthetic data" tag.

## Tokens
| Token | Value |
|---|---|
| Surfaces | canvas `#f5f5f3`, sidebar `#fafaf9`, s1 card `#ffffff`, s2 chip `#f4f5f7`, s3 active `#edf1f6`, s4 track `#e4e8ee` |
| Hairlines | `#e7e7e5`, `#d9dde4`, `#c4cad4` |
| Ink | `#101114`, `#5f6470`, `#6b7080`, `#9aa0ad` (decorative only) |
| Accent | `#0e7a71`, hover `#0b665e`, soft `#e4f2f0` |
| Amber (needs a person) | `#8a5a00` on `#fbf1dc` |
| Red (dead letter) | `#b23a3a` on `#fbeaea` |
| Green (filed) | `#2f6f54` on `#e6f2ec` |
| Reason tints (hue on soft) | missing documentation `#3d5f8c`/`#e9eff7`, medical necessity `#6b4e99`/`#f0ebf7`, eligibility `#9a5418`/`#f8ede2`, expired authorization `#8e3b5a`/`#f7e8ee`, coding `#3f5a9e`/`#e8ecf7` |
| Spacing | 4, 8, 12, 16, 24, 32, 48 |
| Radius | tag 6, inner 8, button 10, card 14, pill 999 |

Every text pair above was checked for WCAG AA (4.5:1 or better) on the surface it sits on.

## Typography
- Display: Space Grotesk 700. Titles 30px at -0.035em, stat numbers 28px at -0.04em, panel and column titles 14 to 16px at -0.01 to -0.015em.
- Body: Inter 400 to 700 at 14px. Eyebrows and table headers are Inter 600, 11px, uppercase, 0.06 to 0.08em tracking.
- Mono: JetBrains Mono only for things a machine wrote: IDs, rule codes, file names, field names, timestamps.

## Components
- Status tags are 22px soft-tint labels with no outline (Approved, Human review, Dead letter, Appeal by). Rule IDs are the exception: white mono tags with a hairline, rule text in the tooltip.
- Denial board: one column per reason, filled with that reason's flat tint, a coloured dot in the header and a count pill. Cards are white, flat, and gain a hairline on hover.
- Stat strip: one card split by 1px hairlines (a grid gap over the hairline colour), so it reflows to 2 columns on phones without broken borders.
- Confidence is a number plus a 4px bar. Below 0.80 the bar turns amber.
- Buttons: solid accent for the one primary action in a panel, grey chip fill for the rest. Toasts use the dark card `#14171c`.
- Navigation: 40px rows, active row on s3 with the count in an accent-soft pill; the review count is amber when anything is waiting.

## Responsive
- Under 1100px the sidebar becomes a top bar with wrapping nav pills and Reset demo on the right.
- Board: 5 columns, 3 under 1280px, 2 under 1100px, 1 under 720px.
- Under 720px tables become stacked rows. `app.js` copies each header onto its cells (`data-label`) after render; this is presentation only. No horizontal page scroll at 375px.

## Brand
The navy ARFA mark on the light sidebar, blended so the file's white background disappears, with a soft breathing teal halo (off under reduced motion).

## Avoid
Inter for everything, glow for emphasis, gradients on buttons, uniform floating cards with shadows, colour used as decoration, emoji as icons, arbitrary spacing.

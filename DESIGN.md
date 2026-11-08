# Dashboard design

Static dashboard in `dashboard/`. No build step, no framework.

## Principles
- Depth comes from a surface ladder and 1px hairlines. No drop shadows, no gradients on cards or buttons.
- One accent (teal), used for primary actions, focus, the active nav item and "this is fine" states. Everything else is an ink ramp.
- Amber is the only other color and it means "a person needs to look".
- Left-aligned, dense, asymmetric: eyebrow, title, then panels. No centered hero.
- Every screen carries the "Synthetic data" tag.

## Tokens
| Token | Value |
|---|---|
| Surfaces | canvas `#060b18`, s1 `#0a1428` (navy base), s2 `#0e1a33`, s3 `#132243`, s4 `#192b52` |
| Hairlines | `#1a2846`, `#26385e`, `#3a4f7c` |
| Ink | `#e9eff9`, `#b0bdd4`, `#7684a1`, `#4e5c79` |
| Accent | `#18c7b8` |
| Warn | `#e3a94a` |
| Spacing | 4, 8, 12, 16, 24, 32, 48 |
| Radius | 6, 8, 12 |

## Typography
- Display: Bricolage Grotesque 700, tracking -0.035em at 32px, -0.02em at 18px. Titles and the large stat numbers.
- Body: Inter 400/500/600 at 14px.
- Mono: JetBrains Mono for IDs, rule codes, timestamps and eyebrows (uppercase, 0.12em tracking).

## Components
- Chips are 1px outlined mono labels. Rule IDs are always chips with the rule description in the tooltip.
- Confidence is a number plus a 4px bar. Below 0.80 the bar turns amber.
- Tables use mono uppercase headers on the surface color with hairline row dividers.
- Buttons: solid accent for the one primary action in a panel, hairline ghost for the rest.

## Brand
The ARFA white mark on the dark sidebar with the sanctioned halo (the only glow on the page).

## Avoid
Inter everywhere, glow for emphasis, gradients on buttons, uniform floating rounded cards, emoji as icons, arbitrary spacing.

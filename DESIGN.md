# Dashboard design

Static dashboard in `dashboard/`. No build step, no framework. Light mode, matte, compact.

The board is built around one person and one question. The person is a billing coordinator at a therapy clinic (ABA, speech). The question every morning is: which insurance faxes came in, and what do I do about each one?

## Layout

Two pages, switched by tabs in the top bar: **Board** and **How it works**.

**Board**
1. One plain sentence at the top: "3 need you today, 2 have a deadline within 30 days". It comes from `faxrouting/board.py`, not from JavaScript.
2. A dashed "Demo controls" strip, clearly marked as a demo: Simulate a new fax, Send the same fax again, Simulate an outage, Reset demo.
3. Four columns, left to right, in the order the work flows: **New** (just arrived, being read), **Needs you** (the system wasn't sure), **Ready to send** (sorted, next step drafted, Approve button), **Done** (filed, sent or ignored).
4. Needs you and Ready to send list the soonest deadline first. New and Done list the newest first.
5. Clicking a card opens a side panel: the fax page on the left; on the right what the letter says, why it is here, the next step (or the fields to check), what the system read with each field marked Sure or Check this, and a small "Details for technical reviewers" link that reveals rule codes and the history for that fax.

**How it works** is for technical reviewers. It leads with a plain-language explanation and a five-step strip, then each design idea with its technical term underneath: the flow, two readers that must agree, rules decide (not the AI), duplicate protection, failures kept and retried (the only place "dead letter" appears, explained), the append-only history, the recorded results on the 10 letters, the full rule list, honest limits, and a link to the repo.

On a phone the columns stack in the same order and the side panel becomes a full-screen sheet.

## What a card shows

Only these, in this order: patient (a made-up display name), insurance company, one line saying what the letter says, a flag line in plain words if a person must look, a deadline tag if there is one, a reason tag for denials, and the one next step. No file names, no rule codes, no internal IDs.

## Copy rules

- Write for a front desk worker with no tech background. Sentence case. No em dashes or en dashes.
- Banned on the board and in the side panel (outside Details): hash, dead letter, idempotent, pipeline, extraction, schema, rule codes, model names, error class names, file names.
- All wording comes from fixed templates in `faxrouting/messages.py`, keyed by document type and denial reason. A card is never worded one-off, so the same letter always reads the same.
- A partly approved letter says what was cut, never a misleading "480 of 480 units approved": "Partly approved: only through Jan 15, not Apr 30."
- `scripts/capture_screenshots.py` opens every card in every state (start, after confirming reviews, after simulating a fax and an outage, after approving everything) and fails if a banned word appears.

## Principles
- Depth comes from a light surface ladder and 1px hairlines. No drop shadows, no gradients on cards or buttons. The ARFA mark halo is the one sanctioned glow (per BRAND_ASSETS).
- One accent (deep teal) for the primary action, focus, the active tab and the "Synthetic data" tag.
- Colour appears only where it means something:
  - amber: a person needs to look (Needs you flag, "Check this", a deadline within 30 days)
  - red: couldn't read it yet
  - green: sent or filed
  - one flat tint per denial reason, on the reason tag only
- Left-aligned and dense. No centred hero.
- Every page carries the "Synthetic data" tag, and the demo controls say they are a demo.

## Tokens
| Token | Value |
|---|---|
| Surfaces | canvas `#f5f5f3`, bar `#fafaf9`, card `#ffffff`, chip `#f4f5f7`, column `#edf1f6`, track `#e4e8ee` |
| Hairlines | `#e7e7e5`, `#d9dde4`, `#c4cad4` |
| Ink | `#101114`, `#5f6470`, `#6b7080`, `#9aa0ad` (decorative only) |
| Accent | `#0e7a71`, hover `#0b665e`, soft `#e4f2f0` |
| Amber (needs a person) | `#8a5a00` on `#fbf1dc` |
| Red (couldn't read) | `#b23a3a` on `#fbeaea` |
| Green (sent, filed) | `#2f6f54` on `#e6f2ec` |
| Reason tints (hue on soft) | missing paperwork `#3d5f8c`/`#e9eff7`, medical necessity `#6b4e99`/`#f0ebf7`, coverage problem `#9a5418`/`#f8ede2`, approval expired `#8e3b5a`/`#f7e8ee`, billing code `#3f5a9e`/`#e8ecf7` |
| Spacing | 4, 8, 12, 16, 24, 32, 48 |
| Radius | tag 6, inner 8, button 10, card 14, pill 999 |

Every text pair above was checked for WCAG AA (4.5:1 or better) on the surface it sits on.

## Typography
- Display: Space Grotesk 700. The summary sentence 30px at -0.035em, patient names 15px, column titles 15px.
- Body: Inter 400 to 700 at 14px. Eyebrows are Inter 600, 11px, uppercase, 0.08em tracking.
- Mono: JetBrains Mono only for things a machine wrote: rule codes, history entries, and the field you type a member ID into.

## Components
- **Card**: white, flat, 14px radius, gains a hairline on hover. A card that just moved columns gets a 2px accent outline for about three seconds.
- **Flag line**: amber soft fill with amber text, one sentence, only on Needs you. A red version appears on a fax that couldn't be read.
- **Tags**: 22px soft-tint labels with no outline. Deadline tags turn amber when the deadline is within 30 days of the demo day.
- **Sure / Check this**: green and amber tags at the end of each field row. A Check this row also gets the amber soft fill.
- **Buttons**: solid accent for the one primary action in a place (Approve, Check it, Retry, Confirm), grey fill with a hairline for the rest.
- **Demo controls**: a dashed hairline box so it reads as a different kind of thing from the real work.
- **Toast**: dark card at the bottom centre, one plain sentence per action.

## Responsive
- Board columns: 4 wide, 2 under 1180px, 1 under 720px.
- Side panel: two columns (fax, then details) over 860px, stacked below. Full width on phones.
- On a phone the brand name hides under 480px so the tabs and the tag stay on one line.
- No horizontal page scroll at 1440 or 390px, checked by `scripts/capture_screenshots.py`.

## Brand
The navy ARFA mark on the light top bar, blended so the file's white background disappears, with a soft breathing teal halo (off under reduced motion).

## Avoid
Engineering words on the main screen, organizing by system parts instead of the person's job, file names as card titles, colour as decoration, shadows and gradients, emoji as icons, arbitrary spacing.

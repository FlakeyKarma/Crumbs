# Crumbs: five UI directions for the recipe tracker

## Context

Crumbs is a recipe management app (README). The repo holds one design direction so far: a cozy index-card "Recipe Rolodex" canvas at `src/mockup/flakeykarma/view/Recipe Rolodex Options.dc.html` (seven phone screens: browse, flipped card with notes, cook mode light/dark, tablet two-pane), plus a data model in `src/mockup/flakeykarma/tables/Recipes/schme.json` (recipe → ingredients with `source`/`percentage`, steps with `timing` and notes, recipe-level notes with name/body/date) and Users / Groups (admin, auditor, user) / Themes ("Soft") tables.

The user wants to explore **five distinct visual directions** before committing, each shown as **static mockups** on **both phone and desktop**, covering **browse, recipe detail, and add/edit**. Node.js is not installed, so the editable design-canvas format is not available; the user chose a **plain HTML mockup page** instead. Output lives in a new top-level folder **`userEyeDesigns/`** (outside `src/`).

Directions confirmed by the user:

| # | Direction | Look | Motivation | Main tradeoff |
|---|-----------|------|------------|---------------|
| 1 | Rolodex | Existing cozy index-card style, carried forward | Continuity with current mockup, warm and personal | Skeuomorphic, low information density |
| 2 | Ledger | Brutally minimal, monochrome, type-driven lists, hairline rules, tabular numerals | Fast scanning, ages well, trivial to theme | Can feel cold and utilitarian |
| 3 | Editorial | Magazine layout, big photo, serif display headlines, asymmetric grid, notes as pull-quotes | Makes recipes feel worth keeping; great for sharing | Depends on good photos; less dense |
| 4 | Kitchen Console | Dark, high-contrast utility; 56px touch targets, per-step timers, baker's-percentage column | Built for hands-on cooking; uses the schema's `timing` and `percentage` fields directly | Technical feel; dark-only tone |
| 5 | Retro Cookbook | 1970s community cookbook: mustard/olive/brick, slab serif, dotted leaders, numbered circle steps | Nostalgic and distinctive; strong personality | Novelty can wear; limited palette |

## Deliverable

One shareable page, `userEyeDesigns/crumbs-five-directions.html`, with a sticky nav and five sections. Each section has: direction name, one-line motivation, one-line tradeoff, a row of three phone frames (Browse, Detail, Add/Edit at 390×844), and two desktop frames (Library + detail pane, and Add/Edit at 1440×900, CSS-scaled to fit). 25 frames total. Published as an artifact and also openable locally.

## File layout

```
userEyeDesigns/
  PLAN.md                        this plan, saved alongside the mockups (first file written)
  README.md                      what this folder is, how to rebuild, direction summary table
  build.sh                       cat's the parts into crumbs-five-directions.html
  parts/
    00-head.html                 <title>, shared <style> (page chrome tokens, device frames, nav), nav markup
    01-rolodex.html              section: 3 phone + 2 desktop frames
    02-ledger.html
    03-editorial.html
    04-kitchen-console.html
    05-retro-cookbook.html
    99-foot.html                 closing markup
  crumbs-five-directions.html    built output (committed so it opens without the build step)
```

Splitting into parts keeps each file editable in one Write and lets a single direction be reworked later without touching the others.

## Implementation steps

0. **Save this plan** as `userEyeDesigns/PLAN.md` before any other file, so the folder documents its own brief. Edits are approved manually by the user, one at a time.
1. **Load the `artifact-design` skill** before writing any HTML (required by the Artifact tool), and follow its guidance for page chrome.
2. **Write `parts/00-head.html`.**
   - Page chrome is theme-aware per the Artifact rules: light tokens on `:root`, dark overrides under `@media (prefers-color-scheme: dark)` guarded with `:root:not([data-theme="light"])`, and again under `:root[data-theme="dark"]`; explicit `body` background.
   - Google Fonts `<link>`s for all five directions (the one allowed stylesheet host): Cormorant + Quicksand + Caveat (Rolodex, matching the existing canvas exactly), Archivo + JetBrains Mono (Ledger), DM Serif Display + Source Sans 3 (Editorial), Space Grotesk + JetBrains Mono (Kitchen Console), Zilla Slab + Karla (Retro Cookbook). Every face gets a metric-close fallback stack.
   - Shared frame classes: `.phone` (390×844 fixed root, rounded shell, no fake status bar or keyboard), `.desktop` (1440×900 root inside a wrapper using `transform: scale()` so the page never scrolls horizontally; the phone row scrolls inside its own `overflow-x: auto` container on narrow viewports).
   - Sticky nav with five anchor links and a short intro line.
3. **Write the five section parts.** Each section's frames carry their own fixed palette as inline styles (they are pictures of a UI, not themed by the viewer), scoped under a section-level CSS variable block so directions never bleed into each other.
   - **Rolodex**: lift exact tokens from the existing canvas (`--paper #F5EAD6`, `--card #FBF3E2`, `--ink #3A2C22`, `--inkSoft #7A6A57`, `--rust #A6522E`, `--rustDeep #8B4023`, `--green #4E6E4E`, tape colors, ruled-card background gradient, 16px card radius, Cormorant italic title). Browse = stacked card deck; Detail = card front with ingredients/steps and a "notes on the back" affordance; Add/Edit = a blank index card form. Desktop = card box on the left, open card on the right.
   - **Ledger**: near-black on toned off-white, 1px hairlines, tabular numerals for quantities and step timings, no cards or shadows. Desktop library is a dense table with a detail column.
   - **Editorial**: full-bleed photo placeholder, large serif headline, two-column ingredients/steps, recipe notes set as pull-quotes with the note date, byline from `author` and `timedate_added`.
   - **Kitchen Console**: dark ground with one oklch accent, ingredient rows with a right-aligned percentage column (schema `percentage`), step cards with a timer chip from `timing`, 56px primary controls. Desktop shows the library list plus a wide "cook" detail pane.
   - **Retro Cookbook**: cream paper, mustard/olive/brick blocks, slab-serif titles, dotted leaders between ingredient and quantity, numbered circle steps, a "from the kitchen of" byline.
   - Content rules: realistic sample recipes reflecting the schema (e.g. Grandma's Skillet Cornbread from the existing mockup, Green Eggs and Ham from `Recipes/table.txt`, plus a bread recipe that makes baker's percentages meaningful). Ingredients show `source` where it fits (e.g. "King Arthur"). Add/Edit forms include name, author, ingredient rows (name, amount, source, %), step rows (description, timing), a note field, and a "share with group" control using the real group names (admin, auditor, user). No lorem ipsum, no emoji, icons as inline stroke SVG, photos as visibly marked placeholder blocks.
4. **Write `build.sh`** (`cat parts/*.html > crumbs-five-directions.html`) and `README.md`, then run the build.
5. **Sanity check** the built file: run it through `python3 -c "html.parser"`-style parse for unclosed tags, confirm no `<html>/<head>/<body>` wrapper tags (the Artifact tool adds them), check file size well under 16 MB, and open it locally with `open` so the user can view it in a browser.
6. **Publish** the built file with the Artifact tool (title "Crumbs Directions", favicon, one-sentence description) and hand over the link.
7. **Second look**: after handover, run a background review agent that only reads `parts/*.html` against this brief (all 25 frames present, three screens per platform per direction, schema fields represented, no forbidden chrome), fix anything it finds, rebuild, republish to the same path.
8. **Save a memory** noting the `userEyeDesigns/` output convention and that this machine lacks Node so the plain-HTML route is preferred for mockups.

Do not commit unless asked; mention in the recap that the folder is uncommitted.

## Verification

- `bash userEyeDesigns/build.sh` succeeds and the output parses cleanly.
- Opening `userEyeDesigns/crumbs-five-directions.html` in a browser shows five sections, each with three phone frames and two desktop frames, no horizontal page scroll at a 1280px window, and readable text in both light and dark system themes for the page chrome.
- The published artifact link renders the same page.
- Each frame is checked against the brief: Browse, Detail, Add/Edit present for phone; Library+detail and Add/Edit present for desktop; ingredients, steps with timing, and notes with dates visible in every Detail screen.

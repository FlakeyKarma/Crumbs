https://claude.ai/code/artifact/66a6c9ad-7083-4d20-b69f-6b7b00741ffb

# userEyeDesigns

Visual direction studies for Crumbs, the recipe tracker. Each direction shows the same three jobs, browsing the library, reading one recipe, and adding or editing a recipe, on a phone (390 × 844) and on a desktop (1440 × 900). The mockups are static HTML with no build tooling.

## Files

| Path | What it is |
|------|------------|
| `PLAN.md` | The brief and plan these mockups were built from |
| `crumbs-five-directions.html` | The built review page. Open it in a browser. |
| `build.sh` | Rebuilds the page by concatenating `parts/*.html` in order |
| `parts/00-head.html` | Page chrome, shared frame styles, font links, icon sprite, nav |
| `parts/01-rolodex.html` … `parts/05-retro-cookbook.html` | One section per direction: 3 phone frames + 2 desktop frames |
| `parts/99-foot.html` | Footer and the script that scales desktop frames to fit |

To change one direction, edit its part and run `bash build.sh`.

## Directions

| # | Direction | Look | Why | Tradeoff |
|---|-----------|------|-----|----------|
| 1 | Rolodex | Cozy ruled index cards, tape, handwritten notes (carried forward from `src/mockup`) | Continuity, warm and personal | Skeuomorphic, low density |
| 2 | Ledger | Monochrome, hairlines, monospaced numbers | Fast scanning, ages well, trivial to theme | Cold and administrative |
| 3 | Editorial | Magazine feature: big photo, serif headline, notes as pull-quotes | Recipes feel worth keeping and sharing | Depends on photography |
| 4 | Kitchen Console | Dark utility, big touch targets, per-step timers, baker's percentages | Built for cooking with messy hands | Technical feel, dark only |
| 5 | Retro Cookbook | 1970s community cookbook: mustard, olive, brick, slab serif | Nostalgic and distinctive | Novelty can wear |

## Data model reflected

Sample content follows `src/mockup/flakeykarma/tables/Recipes/schme.json`: ingredients carry a `source` and a `percentage`, steps carry a `timing` in minutes, and notes carry a `name`, `body` and date. Sharing controls use the real group names from `tables/Groups/table.txt` (admin, auditor, user). Photos are placeholders.

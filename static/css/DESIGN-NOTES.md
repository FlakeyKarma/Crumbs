# Crumbs — visual direction

**Who it is for.** One household's recipe box, self-hosted. Two jobs, in two very
different postures: adding and tidying recipes at a desk, and *cooking* from one
with wet hands and a phone propped against the kettle.

**What I deliberately avoided.** The obvious move for a recipe site is warm cream
paper, a high-contrast serif, and a terracotta accent. That is simultaneously
the food-blog cliché and the current house style of machine-generated pages, so
the page would read as neither this household's nor anyone's.

**Where the look comes from instead.** Kitchen enamelware and a jam-jar label:
cool chalk white, a deep slate-teal ink, and one preserve red doing all the
interactive work. Cool rather than warm, which also keeps food photography
looking like food rather than like the background.

## Tokens

| Role | Light | Dark |
| --- | --- | --- |
| `--paper` page ground | `#F1F3EF` | `#141A1C` |
| `--surface` raised | `#FFFFFF` | `#1C2427` |
| `--ink` primary text | `#16232B` | `#E7ECE8` |
| `--ink-soft` secondary | `#4E6068` | `#9FB0B3` |
| `--rule` hairlines | `#CCD4CF` | `#2F3B3E` |
| `--jam` actions, accent | `#8E2C3F` | `#E0899A` |
| `--amber` live timers only | `#B6801C` | `#E3B44A` |

Two accents, strictly divided: red means "you can press this", amber means
"something is counting down". Nothing else is coloured.

The accent and the corner radius are not hard-coded: they come from the active
theme profile, chosen in the settings menu or by `CRUMBS_THEME`, and the table
above is the **Enamel** profile.
The remaining tokens — paper, surface, ink, rules — are fixed in `crumbs.css`,
because a theme that can change everything is just a second stylesheet.

## Type

- **Newsreader** for recipe titles and the wordmark. A literary serif that holds
  up at 3rem and gives a title the weight of a handwritten card.
- **IBM Plex Sans** for everything else, with `font-variant-numeric:
  tabular-nums` on quantities so a scaled ingredient list stays in a column.
  Quantities get tabular figures rather than a monospace face, which would read
  as a dashboard.

Body measure is capped around 68 characters. Step text is set larger than
default, because it is read from a distance.

## Layout

The index is a card catalogue, not a grid of cards: hairline-ruled rows, title
first, ingredient trail underneath, time and servings right-aligned.

```
  ┌────────────────────────────────────────────────┐
  │  Crumbs            [ search            ]  nav  │
  ├────────────────────────────────────────────────┤
  │  ▢  Sunday roast chicken              55 min   │
  │     chicken, lemon, thyme, butter    4 serves  │
  │  ────────────────────────────────────────────  │
  │  ▢  Brown butter banana bread         1 hr     │
  └────────────────────────────────────────────────┘
```

A recipe is a cook sheet: ingredients in a rail that stays put while the steps
scroll beside it, servings dial at the top of the rail. Steps are numbered —
they genuinely are a sequence, which is the only reason numbering is there.

```
  ┌───────────────┬────────────────────────────────┐
  │ − 4 servings + │  Sunday roast chicken         │
  │ ─────────────  │                               │
  │ 1.4 kg chicken │  1  Heat the oven to 220°C.   │
  │ 2    lemons    │  2  Rub the butter under ...  │
  │ ½ tsp salt     │  3  Roast for 45 min  ⏱       │
  └───────────────┴────────────────────────────────┘
```

**Cook mode** is the one place the design raises its voice: type steps up,
everything not a step recedes, tapping a step greys it out, and the screen wake
lock keeps the phone awake. It is the only state with its own visual register.

## Restraint

One page-level motion: the cook-mode transition. No hover lift on rows, no
entrance animations, no shadows on anything that is not genuinely floating.
`prefers-reduced-motion` turns the remaining transitions off.

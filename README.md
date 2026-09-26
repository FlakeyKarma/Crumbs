# Crumbs

Recipe management and viewing. A self-hosted recipe box: write recipes down
once, find them again, and cook from them without the page fighting you.

## What it does

- **Writing.** One page per recipe with ingredients and steps as inline rows you
  can add and remove. Ingredients are typed by name and matched to a shared
  list, so "smoked paprika" means the same thing across every recipe without you
  curating a master list first. Tags are typed as a comma-separated line.
- **Finding.** Search covers titles, summaries, notes, tags *and* ingredient
  names — "what can I do with the lentils" is the actual question. Filter by
  tag, sort by newest, alphabetical, quickest, or most often made.
- **Scaling.** The servings dial rewrites the ingredient amounts, and rewrites
  them the way a cook would: halving 1½ cups gives you ¾, not 0.75. It runs on
  the server through plain links, so it works before JavaScript loads and on a
  printout.
- **Cooking.** Cook mode enlarges the steps, hides everything that isn't one,
  crosses a step off when you tap it, and holds a screen wake lock so the phone
  doesn't go dark mid-sauce. Steps with a stated duration get a countdown.
- **Keeping track.** "I made this today" bumps a count and a date, so the index
  can sort by what actually gets cooked.
- **Sharing.** Every recipe is private to its author until you mark it shared.
  Shared recipes are readable by anyone who can reach the site, which on a home
  network is usually what you want.

## Quickstart

```bash
make install      # virtualenv, dependencies, migrations, database
make superuser    # an account to sign in with
make seed         # optional: three recipes to look at
make run
```

Then open http://127.0.0.1:8000/. The admin is at `/admin/`, and the settings
menu at `/settings/`.

`make` on its own lists every target. Anything can be overridden on the
command line: `make run PORT=9000`, `make run CRUMBS_THEME=inkwell`.

The same thing by hand, if you'd rather not use make:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py makemigrations recipes
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_recipes --shared
python manage.py runserver
```

`makemigrations` is a real first step, not a formality — no migration files are
committed, so the first run is where the schema gets created.

### Checking an installation

```bash
make health   # against a server you already have running
make smoke    # starts its own server, probes it, shuts it down
```

Both run the Django system checks (including the theme validation), confirm
the models match the migrations and the migrations are applied, then probe a
handful of URLs and check the **status codes**, not just reachability:

```
    ok    200  /                                the recipe index renders
    ok    200  /accounts/login/                 sign-in page renders
    ok    302  /settings/                       settings menu is behind a sign-in
    ok    404  /r/definitely-not-a-recipe/      missing recipes 404 rather than 500
```

A login page answering 500 isn't healthy, and neither is a settings page that
has stopped requiring a sign-in — so both are assertions rather than pings.
The probe is written in Python rather than curl so it needs nothing that isn't
already installed. If `make smoke` can't reach the server it prints the
server's own output, which is usually the actual answer.

`make test` runs the suite; `make audit` runs `check --deploy` with `DEBUG`
off, which is worth reading before putting this on a network.

To generate a secret key for anything beyond localhost:

```bash
python -c "from django.core.management.utils import get_random_secret_key as k; print(k())"
```

## Running the tests

```bash
python manage.py test
```

Covers quantity formatting (the fiddly part), recipe visibility rules, search,
servings clamping, formset saving and ordering, and the permission boundaries
around editing and deleting.

## Layout

```
crumbs/
├── Makefile                 install, run, health, and the rest
├── manage.py
├── crumbs/                  project settings, root URLs, wsgi/asgi
├── recipes/
│   ├── models.py            Recipe, RecipeIngredient, Step, Ingredient, Tag,
│   │                        Note and NoteShare, Theme, SiteSettings
│   ├── quantities.py        decimals -> ½, ¾, "1 hr 30 min"
│   ├── appearance.py        the six full themes, mode and season resolution
│   ├── theming.py           classic colour profiles, colour maths, validators
│   ├── checks.py            manage.py check validation for CRUMBS_THEMES
│   ├── forms.py             RecipeForm, the two inline formsets, settings forms
│   ├── views.py             index, detail, create/edit/delete, settings menu
│   ├── context_processors.py
│   ├── admin.py
│   ├── templatetags/
│   ├── management/commands/seed_recipes.py
│   ├── templates/recipes/
│   └── tests/
├── templates/               base.html and the login page
└── static/                  one stylesheet, two small scripts
```

### A few decisions worth knowing about

**Quantities are decimals in the database and fractions on the page.**
`DecimalField` because scaling is arithmetic; `quantities.format_quantity`
because nobody writes 0.375 cups. Amounts under 10 snap to the nearest eighth
when that is close enough to be honest, and fall back to a decimal when it
isn't. Amounts of 10 or more stay decimal — 250 g is a number, not a fraction.

**`position` is not a form field.** The order of the rows on the page is the
order, renumbered after saving by `forms.apply_order`. Inserting a step in the
middle therefore just works, with no numbering for you to keep straight.

**Ingredient rows point at a shared `Ingredient` row, on `PROTECT`.** Deleting an
ingredient that recipes still use raises rather than quietly emptying those
rows. Names are matched case-insensitively on the way in.

**Notes store placement even under a theme that ignores it.** A note has an
anchor (a step, or the recipe as a whole) *and* free coordinates, because
overlay and split modes need to know where in the recipe a note belongs while
icon mode needs somewhere to expand from. Switching theme therefore never
loses where you put something.

**Note read state is in `sessionStorage`, not the database.** "Pulses until
tapped once for each time the recipe is used" is per-cook, not per-note, so it
hangs off a cook-session token the server stamps on the page. A closed tab is
a finished cook, which is the right lifetime for free, and nobody's read state
leaks into anyone else's. The trade: it doesn't follow you to a second device
mid-cook, and a write to the database every time someone taps a triangle
seemed the worse deal.

**`SiteSettings.load()` is not cached between requests.** A short cache would
save a primary-key lookup, but with more than one worker process a change in
one of them would take the timeout to reach the others, and "I changed the
theme and nothing happened" is a worse bug than one indexed SELECT. Pass the
request to memoise it for that request, which is where the repeats are.

**Visibility lives in one place.** `Recipe.objects.visible_to(user)` is the only
rule, used by every view. Nothing else decides who can see what.

**No JavaScript is load-bearing.** Search, filtering, sorting, scaling, saving
and printing are all server-side. The two scripts add cook mode and formset rows
and nothing else.

## Themes you can choose

Six full themes sit alongside the Classic house style, picked from the header:

| theme | look | notes appear as |
| --- | --- | --- |
| **Classic** | chalk, slate and preserves; recoloured site-wide from the settings menu | overlay |
| **Corporate** | flat colour, square corners, ruled lines | split |
| **Artsy** | a shape and a colour per menu item, cycling together | icon |
| **Cutesy** | pink, white, soft edges | icon |
| **Vaporwave** | neon in the dark, flat in the light | overlay |
| **Woods** | bark down the left of every panel, foliage by season | icon |
| **Warm Retro** | a 1970s kitchen | overlay |

**Where the choice lives.** A cookie, not the database. A theme is a property
of the screen you're looking at, and the tablet propped against the kettle
wants a different answer from the laptop the recipe was added on. The cost is
that a second device starts from the site default again, which the settings
menu sets.

**Light and dark** is a separate cookie, cycling auto → light → dark → auto.
Auto resolves in that order: your explicit choice, then the browser's own
colour-scheme setting, then the clock. The clock fallback is a fixed
07:00/19:00 rather than real solar times, which would need a latitude we
don't collect — it is only reached when the browser reports no preference at
all, which is rare. Resolution happens in an inline script before first paint
(`_appearance_boot.html`), so there is no flash of the wrong palette.

**Woods follows the season** from the reader's own calendar and hemisphere,
inferred from the browser's timezone. Calendar season only: someone in
Singapore gets a northern season that means nothing where they are, and
fixing that properly needs a latitude.

Palettes live in `static/css/themes.css`, keyed off `data-theme`,
`data-mode` and `data-season` on `<html>`. Python's job
(`recipes/appearance.py`) is to pick a key and prove it's one we know about —
a cookie is user input, and these values end up in CSS.

### Contrast

All twelve light/dark palettes clear 4.5:1 for body text, secondary text,
links, primary button text and timer text. Four needed correcting to get
there, and two are worth knowing about if you edit them:

- Cutesy's pink has been darkened twice. `#d4557f` failed white button text;
  `#c04b73` fixed that but was still 4.35:1 as a link on the pink page. It is
  now `#b34169`.
- Vaporwave's dark mode no longer puts yellow on the primary button. The
  purple had to lighten to read as a link against a near-black page, and
  yellow on a purple that light is 2.8:1. The yellow-on-purple signature
  moved to the headings, which sit on the page rather than on the fill.

## The settings menu

Signed-in staff get a **Settings** link in the header (`/settings/`). Everyone
else gets a 403 rather than a bounce back to the login form they already used.
It holds two things.

**Site preferences**, stored in a single-row `SiteSettings` table, each of
which actually changes behaviour:

| setting | what it does |
| --- | --- |
| Site name | the wordmark, page titles, the footer |
| Tagline | the footer and the page description |
| Recipes per page | `get_paginate_by` on the index |
| Default servings | what a new recipe starts with |
| Share new recipes by default | pre-ticks the share box |
| Let signed-out visitors read shared recipes | turn off and the whole site needs a sign-in |

**An appearance section**, listing the seven themes with which one is the
site default for a browser that has never chosen, and a "Try it" that sets
your own cookie.

**A classic colour profile gallery**, showing every profile as a swatch of its own colours
rounded to its own radius, with "Use this" to activate. Built-in themes come
from settings.py and can't be edited in the browser; "Copy to edit" makes an
editable database copy. A stored theme with the same key as a built-in one
shadows it, so copying `enamel` to `enamel` overrides the original without
touching the file — and deleting the copy uncovers it again.

Anything structural — users, passwords, database location, allowed hosts —
stays in the admin, in environment variables, or in settings.py. A setting you
can change from a browser is a setting an attacker can change from a browser,
so only presentation and reading defaults live there.

The theme editor previews as you type: moving a colour picker repaints the
page and reports the link contrast against the page background. That preview
duplicates the colour maths in JavaScript; the server recomputes everything on
save, so `recipes/theming.py` is authoritative if they ever disagree.

## Classic colour profiles

These recolour the **Classic** theme only; the other six bring their own
palettes. The `# --- Theme` section of `crumbs/settings.py` holds the built-in
profiles.
Each one is a dict with five keys:

| key | what it does |
| --- | --- |
| `name` | what the theme is called |
| `description` | one line, so future-you remembers why it exists |
| `roundness` | corner radius on buttons, inputs, photos and thumbnails — a number of pixels, or `"0"`, `"6px"`, `"0.4rem"` |
| `primary` | links, primary buttons, step numbers — the accent |
| `secondary` | running timers and warnings, and nothing else |

Four ship with it: **Enamel** (the default), **Orchard** (green, soft corners),
**Inkwell** (navy, square corners, prints best) and **Clementine** (warm, very
round). Pick one in the settings menu, or set `CRUMBS_THEME`:

```bash
CRUMBS_THEME=inkwell python manage.py runserver
```

`CRUMBS_THEME` is the fallback. Once someone chooses a theme in the settings
menu, that choice wins — clear it there to hand control back to the file.

Everything else is derived from those two colours: the hover shade, lighter
versions for dark mode, and whether text sitting *on* the primary should be
black or white — measured by contrast ratio, not guessed, so a pale accent
doesn't end up with white text on it. Pin any of them by adding the key to the
profile: `primary_hover`, `primary_dark`, `primary_dark_hover`,
`secondary_dark`, `on_primary`, `on_primary_dark`.

The resolved values are written into a small `<style>` block in `base.html`,
after the stylesheet so they win, and repeated inside the dark-mode query so
they win there too. `recipes/theming.py` does the resolution and documents
that arrangement; `crumbs.css` holds everything a theme can't change.

Nothing is trusted on its way into that style block: colours are parsed and
re-emitted as hex and roundness is pattern-matched, so a mistake falls back
instead of reaching the page. `manage.py check` reports it and names the key:

```
recipes.E004: CRUMBS_THEMES['orchard']['primary'] is not a hex colour: 'octarine'.
```

If you add a profile, the contrast worth checking is the primary against
`--paper` (`#f1f3ef` light, `#141a1c` dark). All four shipped profiles clear
4.5:1 in both schemes.

### The one external request

`templates/base.html` pulls Newsreader and IBM Plex Sans from Google Fonts.
Delete the three `<link>` tags in `<head>` and the CSS falls back to Georgia and
your system sans with no other change. To self-host instead, drop the woff2
files in `static/fonts/` and add `@font-face` rules at the top of `crumbs.css`.

`static/css/DESIGN-NOTES.md` records the palette, type scale and layout
reasoning if you want to change the look without unpicking it first.

## Sticky notes

A note is a scrap of paper stuck to a recipe — "halve the sugar", "Dad's
version uses buttermilk", a photo of what it should look like. Any signed-in
reader can add one to a recipe they can see.

**How a note arrives is the theme's decision, not the note's:**

- **icon** — a shape at the note's own coordinates, pulsing until tapped,
  expanding in place at the note's own angle;
- **overlay** — a row of tabs; opening one pushes the note into the flow
  beside the step rather than covering it;
- **split** — every note in a panel down the right, or across the bottom on a
  phone.

**How a note looks is shared:** text on sticky, ruled or grid paper (the theme
picks which), pictures in a polaroid frame.

**Rotation is a feature, upside down included.** The whole container turns, so
the paper, the ruling and the polaroid frame turn together and the expanded
note inherits the icon's angle. There is no snap-upright escape hatch, by
choice. `prefers-reduced-motion` flattens the angle to zero and stops the
pulse without touching the stored value, and a screen reader gets the text in
reading order regardless. Tap anywhere on an open note to put it away — a
close button on an upside-down note sits where nobody looks for it.

**Three levels of access:**

| visibility | who can read it |
| --- | --- |
| part of the recipe | anyone who can see the recipe; only the recipe's owner can write one |
| private | the author, and nobody else — the default |
| shared | the author plus named people, through a join table rather than a boolean |

Sharing the first person flips a private note to shared; removing the last
flips it back. Creator notes are visually distinct from your own, because in
icon mode they can land on the same anchor.

Two things the spec left open, where I picked a default rather than guess in
silence: **collision** (overlapping icons stack rather than fan out) and
**forking** (there is no copy-a-recipe feature yet, so no decision was
forced). Both are worth settling before the note count gets high.

## Deploying it

Uncomment gunicorn and whitenoise in `requirements.txt`, then:

```bash
export CRUMBS_DEBUG=0
export CRUMBS_SECRET_KEY="$(python -c 'from django.core.management.utils import get_random_secret_key as k; print(k())')"
export CRUMBS_ALLOWED_HOSTS=crumbs.example.net
export CRUMBS_DB_PATH=/var/lib/crumbs/db.sqlite3
export CRUMBS_MEDIA_ROOT=/var/lib/crumbs/media

python manage.py migrate
python manage.py collectstatic --noinput
gunicorn crumbs.wsgi:application --bind 127.0.0.1:8000
```

Set `CRUMBS_BEHIND_TLS=1` only once https is actually terminating in front of
it, and set `CRUMBS_CSRF_TRUSTED_ORIGINS` to match. Serving over plain http on a
LAN with `CRUMBS_BEHIND_TLS=1` will redirect you into a loop.

SQLite is the right database for this. A recipe box gets a handful of writes a
week from a handful of people, and WAL mode is already on. If you ever outgrow
it, only `DATABASES` in `crumbs/settings.py` changes.

## Licence

GPL-3.0, matching the repository.

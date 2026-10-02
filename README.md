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
make install      # virtual environment, dependencies, migrations, database
make superuser    # an account to sign in with
make seed         # optional: starter recipes and the built-in health metrics
make run
```

Then open http://127.0.0.1:8000/. The admin is at `/admin/`, and the settings
menu at `/settings/`.

`make` on its own lists every target. Anything can be overridden on the
command line: `make run PORT=9000`, `make run CRUMBS_THEME=inkwell`.

To build against a particular interpreter: `make install PYTHON=python3.12`.

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

### Upgrading an existing copy

No migration files are committed, so a new version with new model fields
leaves your database a version behind. After extracting or pulling:

```bash
make migrate     # makemigrations, then migrate
make seed        # only if the release added built-in health metrics
```

Skipping it shows up as `no such column: …` on whichever page uses the new
field. `runserver` warns about it at startup (`recipes.W002`) and `make
health` fails on it, so it should not be a surprise — but it is the one
step an upgrade always needs.

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
├── pantry/                  food catalogue, stock ledger, FDC/OFF import
├── health/                  metrics, targets, what was eaten
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

## The Pantry

A catalogue of foods and a record of what's actually in the house. They are
deliberately two different things:

- **The catalogue** (`FoodEntry`) is global to the installation — one row per
  food per source, every nutrient stored **per 100 g**. Imported from USDA
  FoodData Central or Open Food Facts, or typed in. Retiring a food hides it
  from pickers but leaves it readable, because deleting it would rewrite
  everyone's logged history.
- **The stock** (`PantryItem` lots and the `StockMovement` ledger) is per
  person. A lot, not a running total: two boxes bought a month apart expire on
  different days, and "what goes off first" is a question people ask.

**How it meets recipes.** It doesn't, until you cook. Recipes stay authored
against `recipes.Ingredient` — a name a line points at, nothing more.
`IngredientDefault` is the bridge: per user, "when a recipe says *smoked
paprika*, I mean this food." Without one the picker opens and you choose;
with one, nutrition and stock resolve silently. Per user, because two people
can reasonably disagree about which brand of stock cube "stock cube" means.

**Nothing guesses.** A line that can't be converted — a count with no portion
weight, a volume with no density — is reported as a gap, not filled in with a
plausible number. A calorie figure that quietly omits the olive oil is worse
than one that says it's incomplete.

**Running short is not an error.** You can cook something you're low on, so
`draw()` returns what it actually took and what was missing, and
`cook_from_recipe()` reports shortfalls rather than refusing. Lots are drawn
soonest-to-expire first, with undated ones last.

**`consume` and `eaten` are not the same movement.** Cooking a batch draws
stock and feeds nobody — the food still exists, as a meal in the fridge. Only
`eaten` is intake.

### API keys

FoodData Central needs a key per user; Open Food Facts needs none, so having
no key is a normal state rather than an error — the FDC leg reports it and
the rest of the lookup still returns results. Keys are encrypted at rest with
a hand-rolled Fernet wrapper (`pantry/fields.py`), because
`django-fernet-fields` is unmaintained against current Django.

Set `CRUMBS_PANTRY_KEY`. Without it the encryption key is derived from
`SECRET_KEY`, which means rotating `SECRET_KEY` invalidates every stored API
key — recoverable, but a nasty surprise weeks later, so `manage.py check`
warns (`pantry.W001`) the whole time the fallback is in use.

## The nutrition schema

The `nutrition` app holds foods as **rows rather than columns**. `FoodEntry`
grew from 8 nutrient columns to 27, and each addition was a schema change;
here, adding vitamin K is an INSERT.

| table | what it holds |
| --- | --- |
| `Unit` | a unit and what it is worth in its dimension's base unit |
| `CoreMacro` | energy, protein, carbohydrate, fat, fibre |
| `Macro` | one component of one core macro, for one food |
| `MicroCategory` | minerals, vitamins |
| `Micro` | one micronutrient, for one food |
| `Food` | a food, and the quantity its rows describe |
| `Meal` | something built out of foods and other meals |
| `MealFood` | one portion of one food or meal — the centre of the model |
| `FoodTracking` | what somebody ate, and when |

**An absent row means unrecorded.** That is the point of the shape: the old
wide table could not tell "nobody recorded the iron" from "there is no iron
in it", because both were an empty cell.

**Totals are computed in base units.** A food recorded in micrograms and one
recorded in milligrams add up without either side knowing about the other,
because everything converts through gram, millilitre or kilocalorie.

### Three departures from the original table list

Each because the literal version could not hold the data:

- **Macro and Micro point at Food, not the reverse.** `Food.macro_table_id`
  was a single foreign key, so a food could have exactly one macro and one
  micro. The rows are created per food, so the key belongs on the row.
- **`unit_count` is decimal.** As an integer, 4.2 mg of iron becomes 4 and
  0.75 µg of B12 becomes 1 — the figures the table exists to hold are mostly
  fractional. Integer counts of micrograms would also have worked.
- **`Unit` and a reference quantity were added.** `unit_id` pointed at a
  table that did not exist, and nothing said what quantity of food the counts
  describe. `Food.reference_quantity` defaults to 100 g, which is what both
  importers produce.

`MealFood`'s source is two nullable keys plus a check constraint rather than
a bare `(is_meal, id)` pair. The flag is kept because it reads well, but the
database enforces that it agrees with whichever key is filled — an integer
pointing at the wrong table is a bug that only surfaces as missing food.

### Portions come from the recipe, not from the reference

A reference lists the foods it can stand for. It does not say *how much* —
that is worked out from the recipe line every time it is read:

```
250 g of a food listed per 100 g   ->  factor 2.5
8 oz  of a food listed per 100 g   ->  factor 2.268
2 tbsp of oil, density 0.92        ->  27.2 g,  factor 0.272
1 cup of flour, density 0.53       ->  125.4 g, factor 1.254
3 cloves of garlic, 4 g each       ->  12 g,    factor 0.12
```

Computed rather than stored because the same chosen food in a recipe calling
for 250 g and one calling for 2 tbsp is two different portions, and recording
either on the reference makes the other wrong.

The reference is paired to its ingredient line by the **same patterns** that
recognise the phrase in the prose, so one set of phrasings does both jobs.

**Crossing dimensions needs a number somebody has supplied.** Volume to mass
needs `Food.density_g_per_ml`; counting cloves or slices needs a
`FoodPortion` row saying what one weighs. Without them the portion comes back
as a stated gap — "no density recorded, so tbsp cannot become g" — rather
than a plausible invented figure, which would be indistinguishable from a
measured one.

### Meals inside meals

A curry contains a spice paste, which is useful and also the shape of an
infinite loop. A check constraint catches direct self-reference, `clean()`
walks the tree for indirect cycles, and the totals code carries the meals
already on its branch so a cycle that got in anyway is **reported rather
than hung on**. Portions multiply down the tree: half a curry containing half
a paste gives you a quarter of the paste.

### Moving across

```bash
make migrate
python manage.py seed_nutrition      # units, core macros, micro categories
python manage.py import_foodentries  # --dry-run first if you like
```

The import is re-runnable, rebuilds each food's rows from scratch, and turns
one populated column into one row. A NULL column becomes no row.

## The Health Panel

Sits on top of the Pantry. Metrics across nutrition, body and movement;
targets in four shapes (at least, at most, between, track-only) that are
**effective-dated rather than edited**, so changing a target next month
doesn't rewrite how you did last month.

All four shapes share one vocabulary — `state` is under/ok/over/unknown and
`fraction` is how far along the bar to fill — so the panel draws one kind of
measure rather than four.

**Nutrition is frozen onto each log entry** at the time you log it. Correcting
a food's numbers later fixes future entries and leaves history alone.

**Logging a food depletes stock; logging a recipe doesn't.** Eating an apple
from the cupboard is exactly when the cupboard should lose it. Eating a
portion of something you cooked yesterday shouldn't empty it twice — cooking
already drew the ingredients.

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

## Backup and restore

Two buttons in the settings menu, and the same two things from a terminal:

```bash
python manage.py backup --to /path/crumbs.tar.gz
python manage.py restore /path/crumbs.tar.gz            # merge
python manage.py restore /path/crumbs.tar.gz --replace  # empty first
```

An archive holds `MANIFEST.json`, `data.json` and `media/`. Media is in
there because a database dump alone restores recipes pointing at pictures
that are no longer on disk.

**Left out on purpose:** sessions, permissions, content types and admin log
entries. Sessions are worthless by morning; the other three are rebuilt from
the code, and including them is the usual reason a `loaddata` fails on a
content-type primary key that does not line up.

**Left in, and worth knowing:** password hashes, and the encrypted FoodData
Central keys. Those keys are tied to `PANTRY_ENCRYPTION_KEY`, or to
`SECRET_KEY` when that is unset — so restoring onto an install with a
different key leaves them unreadable. The manifest records which was in use
and `restore` warns when they differ, rather than letting it be discovered
weeks later. Treat a backup as a secret.

### Restoring is the one button that can lose work

So it is guarded three ways: the word RESTORE has to be typed, a backup of
the current state is written first, and *replacing* is a separate choice from
*merging*. Merge is the default — matching rows are overwritten, anything
added since stays. Replace empties the tables, which is what you want after
losing a disk and not what you want after a bad afternoon; it clears sessions
too, so it signs you out.

An archive is a file somebody hands you, so media is unpacked with
`filter="data"` and each path is checked against `MEDIA_ROOT` — and it is
unpacked **before** the database is touched, so an archive that tries to
escape is refused with the data still as it was.

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

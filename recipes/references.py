"""References: `**lean beef**` in a recipe, matched against a pattern library.

A reference used to be a row the recipe owned. Now it is a *pattern* kept in
the pantry, so one entry recognises every way a recipe might say the same
thing, and each entry carries a short list of portions it can stand for.

Three things worth knowing before editing this file.

**The patterns are user input, and `re` has no timeout.** A pattern like
`(a+)+b` backtracks for the rest of the afternoon on the right string. Three
things keep that survivable: patterns are length-capped, obviously explosive
shapes are rejected at validation, and matching only ever runs against the
phrase between the asterisks, itself capped at 80 characters. That bounds the
damage rather than removing it — someone with write access can still make a
page slow. Removing it properly means a subprocess with a timeout or a
non-backtracking engine, which is not worth it for a tool where the people
who can add patterns are the people who own the install.

**Matching is `fullmatch`, not `search`.** A pattern of `beef` recognises the
phrase "beef", not "beef stock" and "corned beef" as well. Someone who wants
that writes `.*beef.*`.

**`**` is also Markdown's bold.** Nothing here renders Markdown, so there is
no collision today; the day one arrives is the day to change the delimiter.
"""

import re

from django.utils.html import escape
from django.utils.safestring import mark_safe

#: No asterisks or newlines inside, and a length cap: an unclosed `**` should
#: not swallow the rest of the method hunting for its partner.
PHRASE = re.compile(r"\*\*([^*\n]{1,80})\*\*")

#: Patterns longer than this are refused. A reference phrase is a few words.
MAX_PATTERN = 200

#: Nested quantifiers — (x+)+ and friends — are the classic shape of
#: catastrophic backtracking. Refused outright rather than explained away.
EXPLOSIVE = re.compile(r"\([^)]*[+*]\s*\)\s*[+*{]")

_compiled = {}


def normalise(phrase):
    """One spelling per phrase. 'Lean  Beef ' and 'lean beef' are the same."""
    return " ".join(str(phrase).split()).lower()


def compile_pattern(pattern, is_regex=False):
    """Compile and cache a pattern, refusing the dangerous ones.

    ``is_regex=False`` — the default, and what most rows are — escapes the
    text first, so `beef (lean)` matches those exact characters instead of
    being read as a group and failing in a way nobody asked to understand.

    Raises ValueError with something a person can act on, which the model's
    `clean` turns into a form error.
    """
    pattern = (pattern or "").strip()
    if not pattern:
        raise ValueError("A reference needs something to match.")
    if len(pattern) > MAX_PATTERN:
        raise ValueError(f"Keep it under {MAX_PATTERN} characters.")

    if not is_regex:
        # Plain words. Collapse runs of whitespace so "lean  beef" and
        # "lean beef" are the same row, then escape the lot.
        source = r"\s+".join(re.escape(word) for word in pattern.split())
    else:
        if EXPLOSIVE.search(pattern):
            raise ValueError(
                "A repeated group inside another repeat can take effectively "
                "forever to match. Rewrite it without the nested + or *."
            )
        source = pattern

    key = (source, bool(is_regex))
    if key not in _compiled:
        try:
            _compiled[key] = re.compile(source, re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"Not a valid regular expression: {error}")
    return _compiled[key]


def forget(pattern=None):
    """Drop the compiled cache. Called whenever a pattern is edited."""
    _compiled.clear()


def library():
    """Every reference, in the order ties are resolved."""
    from .models import Reference

    # Patterns are rows now, and matching touches every one of them.
    return list(Reference.objects.prefetch_related("patterns"))


def match(phrase, references=None):
    """The first reference recognising this phrase, or None.

    First rather than best: two patterns both claiming "lean beef" is a
    configuration problem, and picking one is better than picking neither.
    The pantry's reference page lists which patterns collide.
    """
    phrase = normalise(phrase)
    if not phrase:
        return None
    for reference in references if references is not None else library():
        if reference.matches(phrase):
            return reference
    return None


def extract(*texts):
    """Every phrase written between asterisks, in order of first appearance."""
    found = []
    for text in texts:
        for hit in PHRASE.finditer(text or ""):
            phrase = normalise(hit.group(1))
            if phrase and phrase not in found:
                found.append(phrase)
    return found


def render(text, bindings=None, references=None):
    """Turn `**phrase**` into a button, and everything else into safe text.

    ``bindings`` maps a reference id to the portion this reader chose. A
    bound reference shows that portion's name: once you have said which beef
    you meant, the recipe should say it too.

    A phrase no pattern recognises still becomes a button — it opens the
    picker, which offers to create a reference for it. Rendering it as dead
    text would leave no way to act on the thing the author plainly marked as
    needing a choice.
    """
    if not text:
        return ""

    bindings = bindings or {}
    known = list(references) if references is not None else library()

    def button(hit):
        raw = hit.group(1)
        phrase = normalise(raw)
        reference = match(phrase, known)
        chosen = bindings.get(reference.pk) if reference else None
        label = str(chosen.source) if chosen else raw

        return (
            '<button type="button" class="reference"'
            f' data-reference="{reference.pk if reference else ""}"'
            f' data-phrase="{escape(phrase)}"'
            f' data-bound="{"true" if chosen else "false"}"'
            f' data-known="{"true" if reference else "false"}"'
            f' aria-label="{escape(label)} \u2014 choose a food">'
            f"{escape(label)}</button>"
        )

    # Escape first, substitute second: everything the author typed is text,
    # and only the markup this function adds is markup.
    return mark_safe(PHRASE.sub(button, escape(text)))


def bindings_for(user, recipe):
    """This reader's choices for this recipe, keyed by reference id."""
    from .models import UserFoodReference

    if not (user and user.is_authenticated):
        return {}
    rows = UserFoodReference.objects.filter(user=user, recipe=recipe).select_related(
        "reference", "meal_food", "meal_food__source_food", "meal_food__source_meal"
    )
    return {row.reference_id: row.meal_food for row in rows}


def collisions(references=None):
    """Phrases that more than one pattern claims.

    Not an error — a later pattern may be a deliberate special case — but
    worth showing, because the first match wins and nothing else would tell
    you which one that was.
    """
    known = list(references) if references is not None else library()
    clashes = []
    for index, reference in enumerate(known):
        # Every phrasing this reference claims, not just its name: two
        # references can agree on the term and still collide on a variant.
        samples = {normalise(reference.label)} | {
            normalise(row.pattern) for row in reference.patterns.all() if not row.is_regex
        }
        for other in known[index + 1 :]:
            for sample in sorted(samples):
                if other.matches(sample):
                    clashes.append(
                        {"first": reference, "second": other, "phrase": sample}
                    )
                    break
    return clashes


# --- What a choice actually amounts to ---------------------------------------


def line_for(reference, lines):
    """The ingredient line this reference names, if the recipe has one.

    Matched with the same patterns that recognise the phrase in the prose,
    so a reference set up to know `lean beef` also recognises the ingredient
    row called "lean beef" without anything being said twice.
    """
    for line in lines:
        if reference.matches(normalise(line.ingredient.name)):
            return line
    return None


def portions_for(user, recipe):
    """Each choice this reader has made, and what the recipe asks for of it.

    The portion is not stored anywhere: it is worked out from the recipe's
    own amount and unit every time, converted into whatever the food is
    measured in. That is the point — the same chosen food in a recipe
    calling for 250 g and one calling for 2 tbsp is two different portions,
    and recording one of them on the reference would make the other wrong.

    Returns a row per choice, each carrying either an amount or the reason
    there isn't one.
    """
    from nutrition.services import portion_factor, totals_for_food

    from .models import UserFoodReference

    if not (user and user.is_authenticated):
        return []

    lines = list(recipe.recipe_ingredients.select_related("ingredient"))
    rows = []

    choices = UserFoodReference.objects.filter(
        user=user, recipe=recipe
    ).select_related("reference", "meal_food__source_food", "meal_food__source_meal")

    for choice in choices:
        food = choice.meal_food.source_food
        line = line_for(choice.reference, lines)

        row = {
            "reference": choice.reference,
            "chosen": choice.meal_food,
            "line": line,
            "amount": None,
            "factor": None,
            "energy": None,
            "problem": None,
        }

        if food is None:
            # The choice is a meal, which has no per-unit figures to scale.
            row["problem"] = "that's a meal, so there is nothing to scale by weight"
        elif line is None:
            row["problem"] = "no ingredient line in this recipe matches it"
        else:
            factor, problem = portion_factor(line.quantity, line.unit, food)
            row["problem"] = problem
            if factor is not None:
                row["factor"] = factor
                row["amount"] = factor * food.reference_quantity
                totals, _ = totals_for_food(food, scale=factor)
                row["energy"] = next(
                    (
                        value
                        for key, value in totals.items()
                        if key[1] == "Energy"
                    ),
                    None,
                )
        rows.append(row)

    return rows

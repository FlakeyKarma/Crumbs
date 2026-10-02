# Crumbs — the handful of commands you actually type.
#
#   make install    set up from a fresh clone
#   make run        start the development server
#   make health     is this installation sound?  (the check, not the app)
#
# `make` on its own lists everything. Overrides go on the command line:
#
#   make run PORT=9000
#   make run CRUMBS_THEME=inkwell
#
# Assumes bash and a POSIX environment — Linux, macOS, WSL or Termux. On
# Windows without WSL, use the commands in the README directly.
#
# Targeting a different interpreter: make install PYTHON=python3.12

SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.ONESHELL:
.DEFAULT_GOAL := help

PYTHON ?= python3
VENV   ?= .venv
HOST   ?= 127.0.0.1
PORT   ?= 8000

BIN    := $(VENV)/bin
PY     := $(BIN)/python
PIP    := $(BIN)/pip
MANAGE := $(PY) manage.py
URL    := http://$(HOST):$(PORT)

.PHONY: help install run health smoke test check doctor repair audit migrate superuser seed static clean reset require-venv

# ---------------------------------------------------------------------------
# Setting up
# ---------------------------------------------------------------------------

$(PY):
	@echo "Creating $(VENV)"
	$(PYTHON) -m venv $(VENV)

install: $(PY)  ## Create the virtual environment, install dependencies, build the database
	@$(PIP) install --upgrade pip --quiet
	$(PIP) install -r requirements.txt
	# No migrations are committed, so the first run is where the schema is
	# created. On later runs this is a no-op unless the models changed.
	$(MANAGE) makemigrations recipes pantry health nutrition
	$(MANAGE) migrate
	echo
	echo "Installed. Next: 'make superuser', then 'make seed' and 'make run'."

require-venv:
	@test -x $(PY) || { echo "No virtual environment yet. Run 'make install' first."; exit 1; }

migrate: require-venv  ## Apply any outstanding migrations
	@$(MANAGE) makemigrations recipes pantry health nutrition
	$(MANAGE) migrate

superuser: require-venv  ## Create an administrator account
	@$(MANAGE) createsuperuser

seed: require-venv  ## Load the starter recipes and the built-in health metrics
	@$(MANAGE) seed_recipes --shared
	$(MANAGE) seed_health_metrics
	$(MANAGE) seed_nutrition

static: require-venv  ## Collect static files for a real deployment
	@$(MANAGE) collectstatic --noinput

# ---------------------------------------------------------------------------
# Running
# ---------------------------------------------------------------------------

run: require-venv  ## Start the development server
	@echo "Serving on $(URL) — Ctrl-C to stop."
	$(MANAGE) runserver $(HOST):$(PORT)

# ---------------------------------------------------------------------------
# Checking
# ---------------------------------------------------------------------------

test: require-venv  ## Run the test suite
	@$(MANAGE) test

check: require-venv  ## Django system checks, including the theme validation
	@$(MANAGE) check

doctor: require-venv  ## Why the database and the code disagree
	@$(MANAGE) doctor

repair: require-venv  ## Add columns migrate cannot, when doctor says to
	@$(MANAGE) repair_schema
	$(MANAGE) doctor --quiet

audit: require-venv  ## Production readiness check — expect complaints while DEBUG is on
	@CRUMBS_DEBUG=0 $(MANAGE) check --deploy

health: require-venv  ## Full check: configuration, migrations, and a live server
	@echo "==> configuration"
	$(MANAGE) check
	@echo "==> database and schema"
	$(MANAGE) doctor --quiet
	@echo "==> models match migrations"
	$(MANAGE) makemigrations --check --dry-run
	@echo "==> migrations applied"
	$(MANAGE) migrate --check
	@echo "==> serving $(URL)"
	CRUMBS_URL=$(URL) CRUMBS_WAIT=2 $(PY) - <<< "$$PROBE"

smoke: require-venv  ## Same as health, but starts and stops its own server
	@echo "==> configuration"
	$(MANAGE) check
	@echo "==> database and schema"
	$(MANAGE) doctor --quiet
	@echo "==> models match migrations"
	$(MANAGE) makemigrations --check --dry-run
	@echo "==> migrations applied"
	$(MANAGE) migrate --check
	@echo "==> starting a temporary server on $(URL)"
	# Keep the server's own output: if it never comes up — port already in
	# use, a bad setting — the probe would otherwise just time out and say
	# nothing is listening, which is true but unhelpful.
	log=$$(mktemp)
	trap 'kill $$server 2>/dev/null || true; wait $$server 2>/dev/null || true; rm -f "$$log"' EXIT
	$(MANAGE) runserver $(HOST):$(PORT) --noreload >"$$log" 2>&1 &
	server=$$!
	if ! CRUMBS_URL=$(URL) CRUMBS_WAIT=20 $(PY) - <<< "$$PROBE"; then
		echo
		echo "--- server output ---"
		cat "$$log"
		exit 1
	fi

# The probe. Written in Python rather than curl so it needs nothing that
# isn't already installed, and checks status codes rather than just
# reachability: a login page that has started answering 500 is not healthy,
# and neither is a settings page that has stopped requiring a sign-in.
define PROBE
import os, sys, time, urllib.error, urllib.request

BASE = os.environ["CRUMBS_URL"].rstrip("/")
DEADLINE = time.monotonic() + float(os.environ.get("CRUMBS_WAIT", "2"))

# path, what it should answer, why we care
CHECKS = [
    ("/", {200}, "the recipe index renders"),
    ("/tags/", {200}, "tag list renders"),
    ("/accounts/login/", {200}, "sign-in page renders"),
    ("/settings/", {302}, "settings menu is behind a sign-in"),
    ("/admin/login/", {200}, "admin is reachable"),
    ("/pantry/", {302}, "the pantry is behind a sign-in"),
    ("/health/", {302}, "the health panel is behind a sign-in"),
    ("/r/definitely-not-a-recipe/", {404}, "missing recipes 404 rather than 500"),
]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    """Report the redirect instead of quietly following it."""

    def redirect_request(self, *args, **kwargs):
        return None


opener = urllib.request.build_opener(NoRedirect)


def status(path):
    try:
        with opener.open(BASE + path, timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as error:
        return error.code
    except Exception:
        return None


while status("/") is None and time.monotonic() < DEADLINE:
    time.sleep(0.4)

if status("/") is None:
    print(f"    nothing is listening on {BASE}")
    print("    start one with 'make run', or use 'make smoke' to start one here")
    sys.exit(1)

failures = 0
for path, expected, why in CHECKS:
    code = status(path)
    ok = code in expected
    failures += 0 if ok else 1
    mark = "ok  " if ok else "FAIL"
    want = "" if ok else f"  (wanted {'/'.join(str(c) for c in sorted(expected))})"
    print(f"    {mark} {str(code or '---'):>4}  {path:<32} {why}{want}")

print()
if failures:
    print(f"{failures} check(s) failed.")
    sys.exit(1)
print("Healthy.")
endef
export PROBE

# ---------------------------------------------------------------------------
# Tidying
# ---------------------------------------------------------------------------

clean:  ## Remove bytecode and collected static files
	@find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf staticfiles
	echo "Cleaned. The database and uploaded photos are untouched."

reset: require-venv  ## Delete the database and uploaded photos, then rebuild
	@read -r -p "This deletes db.sqlite3 and everything in media/. Type yes to continue: " reply
	test "$$reply" = "yes" || { echo "Left alone."; exit 1; }
	rm -f db.sqlite3 db.sqlite3-wal db.sqlite3-shm
	rm -rf media
	$(MANAGE) migrate
	echo "Rebuilt. Run 'make superuser' to get back in."

help:  ## List these targets
	@echo "Crumbs — recipe management and viewing"
	echo
	grep -hE '^[a-z][a-zA-Z0-9_-]*:.*## ' $(MAKEFILE_LIST) \
		| sort \
		| awk 'BEGIN {FS = ":.*## "} {printf "  %-12s %s\n", $$1, $$2}'
	echo
	echo "  Variables: PYTHON=$(PYTHON) VENV=$(VENV) HOST=$(HOST) PORT=$(PORT)"

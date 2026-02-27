# TriPPlanner (Airport-to-Airport Links-Only Planner)

TriPPlanner is a Django 5 + DRF + Celery + Redis + PostgreSQL app that ranks airport-to-airport package estimates and returns outbound affiliate links only.

Links-only invariant:
- No booking engine
- No checkout
- No payments or reservations in TriPPlanner

## Repo Map

- `planner/models.py`
  Airport dataset model, expanded plan schema, package entity arrays.
- `planner/services/airports.py`
  Airport lookup, normalization, prefix search, dataset metadata.
- `planner/management/commands/seed_airports.py`
  Seeds bundled airport dataset into DB.
- `planner/services/destination_service.py`
  Airport candidate expansion for direct and explore modes.
- `planner/services/entities.py`, `planner/services/places.py`
  Entity builders for flights/hotels/tours/places + image fallbacks.
- `planner/tasks.py`
  Multi-stage async pipeline with partial-failure resilience.
- `planner/serializers.py`, `planner/api_views.py`, `planner/api_urls.py`
  Robust validation, compact error payloads, new airport search and planner parse APIs.
- `planner/templates/planner/planner_wizard.html`
  Airport autocomplete + chat-like parse UX.
- `planner/templates/planner/partials/package_cards.html`
  Entity-level card rendering with tracked outbound links.
- `planner/static/trippilot/app.css`
  Main CSS entrypoint importing modular design files.

## Core Behavior

`POST /api/plans/start` starts an async airport pipeline:

1. `validating`
2. `expanding_destinations`
3. `fetching_flight_signals`
4. `fetching_hotel_signals`
5. `fetching_tours`
6. `fetching_places`
7. `scoring`
8. `completed`

If one destination fails in any stage, pipeline continues with remaining destinations.

## Airport Dataset

Bundled dataset:
- `planner/data/airports.csv`
- Includes thousands of airports with `iata,name,city,country,country_code,lat,lon,timezone`

Seed command:

```bash
python manage.py seed_airports
```

API search:

```http
GET /api/airports/search?q=tbs
```

Returns top matches with `iata`, `display_name`, `city`, `country`.

## API Endpoints

- `GET /api/airports/search`
- `POST /api/plans/interpret` (chat-like text -> structured fields)
- `POST /api/plans/start`
- `POST /api/plans/<plan_id>/refresh`
- `GET /api/plans/<plan_id>/status`
- `GET /api/plans/<plan_id>/packages`
- `POST /api/packages/<package_id>/save-toggle`
- `POST /api/click`
- `GET /api/providers/status`
- `GET /api/providers/health`

Package response includes entity arrays:
- `flights[]`
- `hotels[]`
- `tours[]`
- `places[]`

Each entity includes title/name, link, image, and price (if available).

## Environment Variables

Copy env file:

- Windows: `copy .env.example .env`
- Mac/Linux: `cp .env.example .env`

Required/important:
- `DJANGO_SECRET_KEY`
- `TRIPPILOT_LINKS_ONLY=true`
- `TRIPPILOT_USER_AGENT="TriPPlanner/1.0 (contact: you@example.com)"`
- `TRAVELPAYOUTS_ENABLED=true`
- `TRAVELPAYOUTS_API_TOKEN=...`
- `TRAVELPAYOUTS_MARKER=...`
- `TRAVELPAYOUTS_BASE_CURRENCY=USD`
- `DEFAULT_ORIGIN_IATA=TBS`
- `OUTBOUND_URL_ALLOWED_DOMAINS=...` (optional whitelist)

Optional:
- `FX_API_KEY`, `FX_API_URL`, `FX_QUOTE_CURRENCIES`
- `TRIPPILOT_HTTP_CONNECT_TIMEOUT`, `TRIPPILOT_HTTP_READ_TIMEOUT`
- `UNSPLASH_ACCESS_KEY`
- `SENTRY_DSN`
- `SQLITE_TIMEOUT_SECONDS` (default `30` in local SQLite mode)

## SQLite Dev Reliability

- SQLite connections are configured with:
  - `timeout=30s`
  - `PRAGMA journal_mode=WAL`
  - `PRAGMA foreign_keys=ON`
  - `PRAGMA synchronous=NORMAL`
  - `PRAGMA busy_timeout=30000`
- If your repo lives in a synced folder (for example OneDrive), lock contention is more likely. A non-synced local path is recommended for SQLite development.

## Run Commands

### Windows (local)

1. `python -m pip install -r requirements.txt`
2. `python manage.py migrate`
3. `python manage.py seed_airports`
4. `python manage.py runserver`
5. Celery worker (Windows-safe):
   `celery -A trip_pilot worker --pool=solo --loglevel=info`
6. (Optional scheduler) `celery -A trip_pilot beat --loglevel=info`
7. Planner jobs are queued with Celery on request commit. If broker/worker is unavailable, plans are marked failed with a queue error instead of running inline in the web request.

### Mac/Linux (local)

1. `python3 -m pip install -r requirements.txt`
2. `python3 manage.py migrate`
3. `python3 manage.py seed_airports`
4. `python3 manage.py runserver`
5. `celery -A trip_pilot worker --loglevel=info`
6. `celery -A trip_pilot beat --loglevel=info`

## Docker

```bash
docker compose up --build
```

Then seed airports in the web container:

```bash
docker compose exec web python manage.py seed_airports
```

## Security + Observability

- Secrets are read from environment variables.
- Structured JSON logs include request/plan correlation IDs.
- Click tracking validates outbound URLs (`http/https`, optional domain whitelist).
- DRF throttles:
  - `airport_search`
  - `plan_start`
  - `click_track`
- `/api/providers/health` includes:
  - `travelpayouts` metrics
  - `airports_dataset` (`loaded_count`, `loaded_at`)
  - `places` metrics (`enabled`, `source`, `last_success_at`, error/latency)
  - `fx`

## Acceptance Quick Checks

1. Migrate + seed:
   - `python manage.py migrate`
   - `python manage.py seed_airports`
2. Airport search:
   - `GET /api/airports/search?q=tbs` -> includes `TBS`
3. Start plan:
   - `POST /api/plans/start` with `origin_iata=TBS`, `destination_iata=JFK`
4. Packages:
   - `GET /api/plans/<plan_id>/packages` returns `flights/hotels/tours/places`
5. Links-only:
   - No booking/checkout endpoints
   - Outbound links tracked via `/api/click`

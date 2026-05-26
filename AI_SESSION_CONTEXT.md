# TransitFlow — AI Session Context

> **Purpose:** Drop this file into any new AI session to restore full project context
> instantly. It captures all agreed schema, function signatures, and design decisions
> so a new session does not need to re-read the codebase from scratch.
>
> **Last updated:** 2026-05-26

---

## Project Overview

TransitFlow is a course AI chat assistant backed by three databases:

| Database | Role | Port |
|---|---|---|
| PostgreSQL (relational) | Stations, schedules, seats, users, bookings, payments, feedback | 5433 |
| PostgreSQL + pgvector | Policy document RAG (768-dim embeddings, Ollama default) | 5433 |
| Neo4j | Dual transit network graph — route finding, delay ripple | 7688 |

**LLM:** Ollama (`llama3.1:8B` local) or Gemini (via `.env`).  
**UI:** Gradio at `http://localhost:7860`.

---

## Coding Conventions

- **Naming:** `snake_case` for all Python names and SQL identifiers
- **Docstrings:** All functions must have a docstring with `Args:` and `Returns:` sections
- **Return types:** Use type hints. Read-only functions return `list[dict]` or `Optional[dict]`
- **Empty results:** Return `[]` or `None` (as documented), never raise an exception for "not found"
- **SQL:** Use `%s` placeholders for all user inputs — never string-format into SQL
- **Timestamps:** Always use `datetime.now(timezone.utc)`, never `datetime.now()`
- **Nullable guards:** Always check `if value is None` before calling `.strip()` or any string operation
- **Relational pattern:** Use `_connect()` helper + `psycopg2.extras.RealDictCursor`:
```python
  with _connect() as conn:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
          cur.execute("SELECT ...", (param,))
          return [dict(row) for row in cur.fetchall()]
```
- **Graph pattern:** Use `_driver()` helper + session:
```python
  with _driver() as driver:
      with driver.session() as session:
          result = session.run("MATCH ...", station_id=station_id)
          return [dict(record) for record in result]
```

---

### File editing rules (from README "Your Tasks")

**Tier 1 — Required** (you must implement these):
```
databases/relational/schema.sql
databases/relational/queries.py
databases/graph/queries.py
skeleton/seed_postgres.py
skeleton/seed_neo4j.py
databases/graph/seed.cypher              ← listed by README but NOT executed in current implementation (see ⚠️ below)
train-mock-data/refund_policy.json
train-mock-data/ticket_types.json
train-mock-data/booking_rules.json
train-mock-data/travel_policies.json
```

> ⚠️ **seed.cypher note:** README lists `databases/graph/seed.cypher` as a required file, but the
> current implementation in `skeleton/seed_neo4j.py` writes Cypher directly in Python and never
> loads `seed.cypher`. If you want to use seed.cypher, you must add a file-loading step to
> `seed_neo4j.py`.

**Tier 2 — Optional** (you may edit, but know what you're doing):
```
skeleton/agent.py
skeleton/ui.py
```
> README note: *"If you modify these files, make sure you understand what you are modifying."*

**Tier 3 — Locked** (do NOT modify):
```
skeleton/llm_provider.py
skeleton/config.py
skeleton/seed_vectors.py
```

---

## Agreed Relational Schema

> Source: `databases/relational/schema.sql`  
> Loaded automatically by Docker from `databases/relational/schema.sql` on first start.  
> To apply schema changes: `docker compose down -v && docker compose up -d`

### Users

```sql
CREATE TABLE IF NOT EXISTS users (
    user_id         VARCHAR(10)  PRIMARY KEY,
    full_name       VARCHAR(100) NOT NULL,
    email           VARCHAR(150) NOT NULL UNIQUE,
    password        VARCHAR(100) NOT NULL,       -- plaintext for now; Argon2id planned
    phone           VARCHAR(20),
    date_of_birth   DATE,
    secret_question TEXT,
    secret_answer   VARCHAR(200),                -- nullable; verify_secret_answer guards for NULL
    registered_at   TIMESTAMPTZ  DEFAULT NOW(),
    is_active       BOOLEAN      DEFAULT TRUE
);
```

### Metro

```sql
CREATE TABLE IF NOT EXISTS metro_stations (
    station_id                           VARCHAR(10)  PRIMARY KEY,
    name                                 VARCHAR(100) NOT NULL,
    is_interchange_metro                 BOOLEAN      DEFAULT FALSE,
    is_interchange_national_rail         BOOLEAN      DEFAULT FALSE,
    interchange_national_rail_station_id VARCHAR(10)  -- soft reference; NR stations inserted later
);

CREATE TABLE IF NOT EXISTS metro_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id),
    line        VARCHAR(5)  NOT NULL,
    PRIMARY KEY (station_id, line)
);

CREATE TABLE IF NOT EXISTS metro_schedules (
    schedule_id             VARCHAR(20)  PRIMARY KEY,
    line                    VARCHAR(5)   NOT NULL,
    direction               VARCHAR(20)  NOT NULL,
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    first_train_time        TIME         NOT NULL,
    last_train_time         TIME         NOT NULL,
    base_fare_usd           NUMERIC(6,2) NOT NULL,
    per_stop_rate_usd       NUMERIC(6,2) NOT NULL,
    frequency_min           INTEGER      NOT NULL
);

CREATE TABLE IF NOT EXISTS metro_schedule_days (
    schedule_id VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id),
    day_of_week VARCHAR(5)  NOT NULL CHECK (day_of_week IN ('mon','tue','wed','thu','fri','sat','sun')),
    PRIMARY KEY (schedule_id, day_of_week)
);

CREATE TABLE IF NOT EXISTS metro_schedule_stops (
    schedule_id                 VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id),
    station_id                  VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id),
    stop_order                  INTEGER     NOT NULL,
    travel_time_from_origin_min INTEGER     NOT NULL,
    PRIMARY KEY (schedule_id, station_id)
);
```

### National Rail

```sql
CREATE TABLE IF NOT EXISTS national_rail_stations (
    station_id                   VARCHAR(10)  PRIMARY KEY,
    name                         VARCHAR(100) NOT NULL,
    is_interchange_national_rail BOOLEAN      DEFAULT FALSE,
    is_interchange_metro         BOOLEAN      DEFAULT FALSE,
    interchange_metro_station_id VARCHAR(10)  -- soft reference to metro_stations
);

CREATE TABLE IF NOT EXISTS national_rail_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id),
    line        VARCHAR(5)  NOT NULL,
    PRIMARY KEY (station_id, line)
);

CREATE TABLE IF NOT EXISTS national_rail_schedules (
    schedule_id             VARCHAR(20)  PRIMARY KEY,
    line                    VARCHAR(5)   NOT NULL,
    service_type            VARCHAR(20)  NOT NULL,  -- 'normal' | 'express'
    direction               VARCHAR(20)  NOT NULL,
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    first_train_time        TIME         NOT NULL,
    last_train_time         TIME         NOT NULL,
    frequency_min           INTEGER      NOT NULL
);

CREATE TABLE IF NOT EXISTS national_rail_schedule_days (
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id),
    day_of_week VARCHAR(5)  NOT NULL CHECK (day_of_week IN ('mon','tue','wed','thu','fri','sat','sun')),
    PRIMARY KEY (schedule_id, day_of_week)
);

CREATE TABLE IF NOT EXISTS national_rail_schedule_stops (
    schedule_id                 VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id),
    station_id                  VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id),
    stop_order                  INTEGER     NOT NULL,
    travel_time_from_origin_min INTEGER     NOT NULL,
    stop_type                   VARCHAR(15) NOT NULL DEFAULT 'stop'
                                    CHECK (stop_type IN ('stop', 'pass_through')),
    PRIMARY KEY (schedule_id, station_id)
);

CREATE TABLE IF NOT EXISTS national_rail_fare_classes (
    schedule_id       VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id),
    fare_class        VARCHAR(20)  NOT NULL,  -- 'standard' | 'first'
    base_fare_usd     NUMERIC(6,2) NOT NULL,
    per_stop_rate_usd NUMERIC(6,2) NOT NULL,
    PRIMARY KEY (schedule_id, fare_class)
);

CREATE TABLE IF NOT EXISTS national_rail_seat_layouts (
    layout_id   VARCHAR(10) NOT NULL,
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id),
    coach       VARCHAR(5)  NOT NULL,
    fare_class  VARCHAR(20) NOT NULL,
    seat_id     VARCHAR(10) NOT NULL,
    row_num     INTEGER     NOT NULL,   -- renamed from 'row' to avoid SQL reserved word
    col_name    VARCHAR(5)  NOT NULL,   -- renamed from 'col' for clarity
    PRIMARY KEY (schedule_id, coach, seat_id)
);
```

### Bookings & Travel History

```sql
CREATE TABLE IF NOT EXISTS national_rail_bookings (
    booking_id              VARCHAR(10)  PRIMARY KEY,   -- format: BK-XXXXXX (generated)
    user_id                 VARCHAR(10)  NOT NULL REFERENCES users(user_id),
    schedule_id             VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id),
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    travel_date             DATE         NOT NULL,
    departure_time          TIME         NOT NULL,
    ticket_type             VARCHAR(20)  NOT NULL,
    fare_class              VARCHAR(20)  NOT NULL,
    coach                   VARCHAR(5),
    seat_id                 VARCHAR(10),
    stops_travelled         INTEGER,
    amount_usd              NUMERIC(8,2) NOT NULL,
    status                  VARCHAR(20)  NOT NULL,  -- 'confirmed' | 'completed' | 'cancelled'
    booked_at               TIMESTAMPTZ,
    travelled_at            TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS metro_travels (
    trip_id                 VARCHAR(10)  PRIMARY KEY,
    user_id                 VARCHAR(10)  NOT NULL REFERENCES users(user_id),
    schedule_id             VARCHAR(20)  NOT NULL REFERENCES metro_schedules(schedule_id),
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    travel_date             DATE         NOT NULL,
    ticket_type             VARCHAR(20)  NOT NULL,  -- 'single' | 'day_pass'
    day_pass_ref            VARCHAR(10),            -- references another trip_id for day_pass legs
    stops_travelled         INTEGER,
    amount_usd              NUMERIC(8,2) NOT NULL,
    status                  VARCHAR(20)  NOT NULL,
    purchased_at            TIMESTAMPTZ,
    travelled_at            TIMESTAMPTZ
);
```

### Payments & Feedback

```sql
CREATE TABLE IF NOT EXISTS payments (
    payment_id    VARCHAR(10)  PRIMARY KEY,   -- format: PM-XXXXXX (generated)
    booking_id    VARCHAR(10)  NOT NULL,       -- soft ref: BK* (rail) or MT* (metro) — no FK
    booking_type  VARCHAR(10)  NOT NULL CHECK (booking_type IN ('rail', 'metro')),
    amount_usd    NUMERIC(8,2) NOT NULL,
    method        VARCHAR(30)  NOT NULL,  -- 'credit_card' | 'debit_card' | 'ewallet'
    status        VARCHAR(20)  NOT NULL,  -- 'paid' | 'refunded'
    paid_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id   VARCHAR(10) PRIMARY KEY,
    booking_id    VARCHAR(10) NOT NULL,        -- soft ref: BK* or MT* — no FK
    booking_type  VARCHAR(10) NOT NULL CHECK (booking_type IN ('rail', 'metro')),
    user_id       VARCHAR(10) NOT NULL REFERENCES users(user_id),
    rating        SMALLINT    NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    submitted_at  TIMESTAMPTZ DEFAULT NOW()
);
```

### Indexes

```sql
CREATE INDEX IF NOT EXISTS idx_nr_bookings_user_date   ON national_rail_bookings (user_id, travel_date);
CREATE INDEX IF NOT EXISTS idx_nr_bookings_seat        ON national_rail_bookings (schedule_id, travel_date, seat_id);
CREATE INDEX IF NOT EXISTS idx_metro_travels_user_date ON metro_travels (user_id, travel_date);
CREATE INDEX IF NOT EXISTS idx_payments_booking        ON payments (booking_id);
CREATE INDEX IF NOT EXISTS idx_feedback_booking        ON feedback (booking_id);
```

### Vector (RAG) — do not modify

```sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,
    content     TEXT         NOT NULL,
    embedding   vector(768),    -- 768 for Ollama; change to vector(3072) for Gemini
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_policy_documents_embedding
    ON policy_documents USING hnsw (embedding vector_cosine_ops);
```

> **Gemini note:** If switching to Gemini, change `vector(768)` → `vector(3072)` in schema.sql,
> then `docker compose down -v && docker compose up -d` and re-run `seed_vectors.py`.

---

## Agreed Graph Schema

> Source: `skeleton/seed_neo4j.py`, `databases/graph/queries.py`  
> Network: city metro (M1–M4, stations MS01–MS20) + national rail (NR1–NR2, stations NR01–NR10)

### Node Labels

| Label | Properties |
|---|---|
| `MetroStation` | `station_id` (String, PK), `name` (String), `lines` (List\<String\>), `is_interchange_metro` (Boolean), `is_interchange_national_rail` (Boolean), `interchange_national_rail_station_id` (String, nullable) |
| `NationalRailStation` | `station_id` (String, PK), `name` (String), `lines` (List\<String\>), `is_interchange_national_rail` (Boolean), `is_interchange_metro` (Boolean), `interchange_metro_station_id` (String, nullable) |

### Relationship Types

| Type | Between | Properties |
|---|---|---|
| `METRO_LINK` | MetroStation → MetroStation | `line` (String), `travel_time_min` (Integer) |
| `RAIL_LINK` | NationalRailStation → NationalRailStation | `line` (String), `travel_time_min` (Integer) |
| `INTERCHANGE_TO` | MetroStation ↔ NationalRailStation | `travel_time_min = 5` (both directions, hardcoded) |

### Seed counts (from train-mock-data/)

| Element | Count |
|---|---|
| MetroStation nodes | 20 |
| NationalRailStation nodes | 10 |
| METRO_LINK relationships | 42 |
| RAIL_LINK relationships | 18 |
| INTERCHANGE_TO pairs | 3 (6 directed edges) |

### Key implementation notes

- Station ID prefix determines network: `MS*` → metro, `NR*` → national rail.
- `_rel_type(station_id)` in `graph/queries.py` infers the correct relationship type from the prefix. It raises `ValueError` if `station_id` is `None`.
- Route finding uses `apoc.algo.dijkstra` (APOC plugin, enabled in `docker-compose.yml`).
- Cross-network routing uses `'METRO_LINK|RAIL_LINK|INTERCHANGE_TO'` as the relationship filter.

---

## Function Signatures

### `databases/relational/queries.py`

```python
# ── Availability & Fares ──────────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,      # ISO date string e.g. "2026-06-01"
) -> list[dict]:
    """
    Returns schedules where origin stop comes before destination stop.
    Each result includes: schedule_id, line, service_type, direction,
    first/last_train_time, frequency_min, origin/destination names,
    stop orders, stops_travelled, travel_time_min, fares (list), and
    seat_availability (list, only if travel_date provided).
    Adds date_warning field to each result if travel_date is in the past.
    """

def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """Returns {fare_class, base_fare_usd, per_stop_rate_usd, total_fare_usd} or None."""

def query_metro_schedules(
    origin_id: str,
    destination_id: str,
) -> list[dict]:
    """Returns metro schedules serving both stations in correct order, with total_fare_usd."""

def query_metro_fare(
    schedule_id: str,
    stops_travelled: int,
) -> Optional[dict]:
    """Returns {base_fare_usd, per_stop_rate_usd, total_fare_usd} or None."""

# ── Seat Selection ────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """Returns [{seat_id, coach, row, column}, ...] for seats not already booked."""

def auto_select_adjacent_seats(
    available_seats: list[dict],
    count: int,
) -> list[str]:
    """Picks seats from the same row if possible. Returns list of seat_id strings."""

# ── User & Booking Queries ────────────────────────────────────────────────────

def query_user_profile(user_email: str) -> Optional[dict]:
    """Returns user dict (no password) or None if not found."""

def query_user_bookings(user_email: str) -> dict:
    """Returns {"national_rail": [...], "metro": [...]} booking history."""

def query_payment_info(booking_id: str) -> Optional[dict]:
    """Returns {payment_id, booking_id, booking_type, amount_usd, method, status, paid_at} or None."""

# ── Transactional Operations ──────────────────────────────────────────────────

def execute_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    fare_class: str,
    seat_id: str,                   # pass "any" to auto-assign
    ticket_type: str = "single",
) -> tuple[bool, dict | str]:
    """
    Creates national_rail_bookings row and payments row (method always 'credit_card').
    Returns (True, booking_dict) on success, (False, error_message) on failure.
    Payment is always inserted with booking_type='rail', method='credit_card'.
    """

def execute_cancellation(
    booking_id: str,
    user_id: str,
) -> tuple[bool, dict | str]:
    """
    Cancels booking, sets payment status to 'refunded'.
    Refund policy applied:
      Normal (RF001): ≥48h → 100% | 24–48h → 75% | 2–24h → 50% | <2h → 0%
      Express (RF002): ≥48h → 100% | 2–48h → 50% | <2h → 0%
    Returns (True, result_dict) or (False, error_message).
    """

# ── Authentication ────────────────────────────────────────────────────────────

def register_user(
    email: str,
    first_name: str,
    surname: str,
    year_of_birth: int,
    password: str,                  # stored plaintext; Argon2id planned
    secret_question: str,
    secret_answer: str,
) -> tuple[bool, str]:
    """Returns (True, user_id) or (False, error_message). user_id format: RU{N:02d}."""

def login_user(email: str, password: str) -> Optional[dict]:
    """Returns user dict with first_name/surname split from full_name, or None."""

def get_user_secret_question(email: str) -> Optional[str]:
    """Returns the secret question string or None if email not found."""

def verify_secret_answer(email: str, answer: str) -> bool:
    """
    Case-insensitive comparison. Returns False (not crash) if secret_answer
    is NULL in the database — secret_answer column is nullable.
    """

def update_password(email: str, new_password: str) -> bool:
    """Returns True if a row was updated, False otherwise."""

# ── Vector / RAG ─────────────────────────────────────────────────────────────

def query_policy_vector_search(
    embedding: list[float],
    top_k: int = VECTOR_TOP_K,     # default from config (3)
) -> list[dict]:
    """Cosine similarity search. Filters by VECTOR_SIMILARITY_THRESHOLD (default 0.5)."""

def store_policy_document(
    title: str,
    category: str,
    content: str,
    embedding: list[float],
    source_file: str = "",
) -> int:
    """Inserts a policy document and returns its auto-generated id."""
```

---

### `databases/graph/queries.py`

```python
def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",          # "metro" | "rail" | "auto" (inferred from ID prefix)
) -> dict:
    """
    Dijkstra by travel_time_min via apoc.algo.dijkstra.
    Returns: {found, origin_id, destination_id, total_time_min, path, legs}
    or {found: False, error} if no route.
    """

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """
    Shortest hop count via shortestPath (fewest stops ≈ lowest fare).
    Returns approximate fare at $0.50/stop. Exact fares from relational DB.
    Returns: {found, stops, total_fare_usd, fare_note, stations, legs}
    """

def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[list[dict]]:
    """
    Paths avoiding avoid_station_id. Searches up to depth 15, deduplicates,
    skips cyclic paths, returns up to max_routes sorted by total travel time.
    Each route is a list of leg dicts.
    """

def query_interchange_path(
    origin_id: str,
    destination_id: str,
) -> dict:
    """
    Cross-network path (metro ↔ national rail) using METRO_LINK|RAIL_LINK|INTERCHANGE_TO.
    Returns: {found, total_time_min, interchange_points, stations, legs}
    interchange_points = station IDs at either end of INTERCHANGE_TO legs.
    """

def query_delay_ripple(
    delayed_station_id: str,
    hops: int = 2,
) -> list[dict]:
    """
    All stations within N hops via apoc.path.expandConfig.
    Returns: [{station_id, name, hops_away, lines_affected}, ...]
    Works on both metro and national rail networks.
    """

def query_station_connections(station_id: str) -> list[dict]:
    """
    Direct outbound neighbours via METRO_LINK or RAIL_LINK.
    Returns: [{station_id, name, line, travel_time_min, network}, ...]
    """
```

---

## Team Decisions Log

> Ordered chronologically. Each decision records **what**, **why**, and **implications**
> for future teammates adding features.

---

### D01 — Table naming: `national_rail_*` prefix (not `nr_*`)

**Decision:** All national rail tables use the full prefix `national_rail_` rather than the
abbreviated `nr_`.

**Why:** `nr_` is ambiguous (could mean "not required", "north region", etc.). Full names are
self-documenting when someone runs `\dt` in psql and sees the table list without context.

**Affected tables:** `national_rail_stations`, `national_rail_station_lines`,
`national_rail_schedules`, `national_rail_schedule_days`, `national_rail_schedule_stops`,
`national_rail_fare_classes`, `national_rail_seat_layouts`, `national_rail_bookings`.

**Implication:** Any new national rail table must follow the same prefix. Do not abbreviate.

---

### D02 — Metro stops stored as a relational table, not JSONB

**Decision:** `metro_schedule_stops` is a proper table with one row per stop, not a JSONB
column on `metro_schedules`.

**Why:** Relational rows allow foreign key constraints to `metro_stations`, support indexed
lookups by `station_id`, and are queryable with standard SQL JOINs. JSONB would require
JSON path operators for every stop-order query.

**Implication:** Adding stops to a schedule means inserting into `metro_schedule_stops`.
National rail follows the same pattern (`national_rail_schedule_stops`).

---

### D03 — `stop_type` enum replaces `is_express_skip` boolean

**Decision:** `national_rail_schedule_stops.stop_type VARCHAR(15) CHECK IN ('stop', 'pass_through')`
replaces the original boolean column `is_express_skip`.

**Why:** A boolean can only ever mean "skipped or not". `stop_type` is extensible — future
values like `'request_stop'` or `'depot_only'` can be added without a schema change. The
semantics are also clearer: `stop_type = 'stop'` vs `is_express_skip = FALSE`.

**Implication for queries:** Always filter `AND stop_type = 'stop'` when joining stops for
availability or booking calculations. Pass-through stations have `stop_order = 0` and must
not be used as origin or destination. Both `query_national_rail_availability` and
`execute_booking` already apply this filter.

**Note:** A UNIQUE constraint on `stop_order` was explicitly skipped because pass-through
stations all receive `stop_order = 0` — multiple pass-through stations per schedule would
violate such a constraint.

---

### D04 — `booking_type` column on `payments` and `feedback`

**Decision:** Both `payments` and `feedback` carry a `booking_type VARCHAR(10) CHECK IN ('rail', 'metro')`
column.

**Why:** `booking_id` is a soft reference that points to either `national_rail_bookings`
(prefix `BK`) or `metro_travels` (prefix `MT`). Without `booking_type`, application code
must parse the booking_id prefix to know which table to join. With `booking_type`, the
routing is explicit and queryable.

**Inference rule:** Implemented in `seed_postgres.py` as `_infer_booking_type(booking_id)`,
which raises `ValueError` on unknown prefixes (no silent fallback).

**Implication:** Any new booking type must assign a new prefix and add it to both the
`booking_type` CHECK constraint and the `_infer_booking_type` helper.

---

### D05 — `INTERCHANGE_TO` carries `travel_time_min = 5`

**Decision:** Both directed edges of each `INTERCHANGE_TO` pair have `travel_time_min = 5`
(minutes), hardcoded in `skeleton/seed_neo4j.py`.

**Why:** Dijkstra requires a numeric weight on every edge to compute total travel time.
Without a weight, interchange legs would have zero cost and distort route comparisons.
5 minutes is a reasonable platform-change estimate.

**Implication:** `query_interchange_path` and `query_shortest_route` (cross-network) correctly
include the interchange penalty in `total_time_min`. If real interchange times become
available from data, update the `SET mr.travel_time_min` / `SET rm.travel_time_min` lines
in `seed_neo4j.py` and re-seed Neo4j.

---

### D06 — `day_of_week` CHECK constraint on schedule_days tables

**Decision:** Both `metro_schedule_days.day_of_week` and `national_rail_schedule_days.day_of_week`
have `CHECK (day_of_week IN ('mon','tue','wed','thu','fri','sat','sun'))`.

**Why:** Prevents invalid strings like `'Monday'`, `'monday'`, `'1'`, `'MON'` from being
inserted. Enforces a consistent lowercase 3-letter format across the entire dataset.

**Implication:** Always use lowercase 3-letter abbreviations when inserting operating days.
The seed scripts read directly from JSON (which already uses this format).

---

### D07 — All timestamps use `TIMESTAMPTZ`

**Decision:** Every timestamp column in the schema is `TIMESTAMPTZ` (timestamp with time zone),
not `TIMESTAMP` (without time zone).

**Why:** `TIMESTAMP` stores a local time with no timezone information. When the server or
container timezone changes, stored values become ambiguous. `TIMESTAMPTZ` stores UTC
internally and converts on retrieval — no ambiguity, no DST bugs.

**Affected columns:** `users.registered_at`, `national_rail_bookings.booked_at`,
`national_rail_bookings.travelled_at`, `metro_travels.purchased_at`,
`metro_travels.travelled_at`, `payments.paid_at`, `feedback.submitted_at`,
`policy_documents.created_at`.

**Implication:** Always pass timezone-aware datetimes from Python:
`datetime.now(timezone.utc)` not `datetime.now()`.

---

### D08 — Passwords stored as plaintext; Argon2id planned

**Decision:** `users.password` is `VARCHAR(100)` storing the raw password string.
This is intentional for the current course phase.

**Why (current state):** Simplifies authentication during development. The course focus is
database design, not security engineering.

**Planned upgrade:** Hash with Argon2id before storing. When implementing:
1. Add `argon2-cffi` to `requirements.txt`
2. In `register_user`, replace `password` with `ph.hash(password)` (PasswordHasher)
3. In `login_user`, replace `WHERE password = %s` with a fetch + `ph.verify(stored, input)`
4. The column must widen — default argon2-cffi hashes are 97 chars, leaving only 3 chars
   of headroom in the current `VARCHAR(100)`. Any increase in security parameters overflows it.
   Use `VARCHAR(255)` (industry convention for password hash columns)
5. Existing seeded passwords will need re-hashing or a migration script

**Do not implement this without team agreement** — it changes the auth flow in `skeleton/ui.py`
(which calls `login_user` and `register_user` directly). Note: `skeleton/agent.py` does not
handle login/register — those are wired in `ui.py`, which is Tier 2 Optional.

---

### D09 — `secret_answer` is nullable; `verify_secret_answer` returns False for NULL

**Decision:** `users.secret_answer VARCHAR(200)` has no `NOT NULL` constraint.
`verify_secret_answer` returns `False` (not crash) when the stored value is `NULL`.

**Why:** Some users may be seeded without a secret answer. The function previously crashed
with `AttributeError` on `None.strip()`. The guard `if row[0] is None: return False`
makes the password-reset flow fail gracefully instead.

**Implication:** A user with no secret answer cannot reset their password via the secret
question flow. This is the correct behaviour — do not change it to `True`.

---

### D10 — `row_num` and `col_name` column names in `national_rail_seat_layouts`

**Decision:** Seat position columns are named `row_num` and `col_name`, not `row` and `col`.

**Why:** `row` is a reserved word in PostgreSQL and many SQL dialects. Using it as a column
name requires quoting everywhere and causes subtle bugs. `col` is non-standard but similarly
risky. The renamed versions are unambiguous.

**Implication:** When querying seat layouts, always use `sl.row_num` and `sl.col_name`.
`query_available_seats` aliases them as `row` and `column` for the API response:
`SELECT sl.row_num AS row, sl.col_name AS column`.

---

## Seed Script Reference

### `skeleton/seed_postgres.py`

Run order (respects FK dependencies):
```
metro_stations → metro_station_lines
national_rail_stations → national_rail_station_lines
metro_schedules → metro_schedule_days → metro_schedule_stops
national_rail_schedules → national_rail_schedule_days → national_rail_schedule_stops → national_rail_fare_classes
national_rail_seat_layouts
users
national_rail_bookings
metro_travels
payments → feedback
```

Safe to re-run: all inserts use `ON CONFLICT DO NOTHING`.  
`booking_type` is inferred by `_infer_booking_type(booking_id)` — raises `ValueError` on unknown prefix.

### `skeleton/seed_neo4j.py`

Clears all graph data first (`MATCH (n) DETACH DELETE n`), then recreates:
- MetroStation nodes (20)
- NationalRailStation nodes (10)
- METRO_LINK edges from `adjacent_stations` in `metro_stations.json`
- RAIL_LINK edges from `adjacent_stations` in `national_rail_stations.json`
- INTERCHANGE_TO pairs (bidirectional, `travel_time_min = 5`) from `is_interchange_national_rail` flags

---

## Database Reset Commands

```bash
# Full reset (schema change or first time):
docker compose down -v && docker compose up -d
python3 skeleton/seed_postgres.py
python3 skeleton/seed_neo4j.py
python3 skeleton/seed_vectors.py

# Neo4j only (graph change):
python3 skeleton/seed_neo4j.py

# Vectors only (policy document change):
python3 skeleton/seed_vectors.py
```

---

## Known Limitations (acceptable for course scope)

| # | Limitation | Location | Notes |
|---|---|---|---|
| L01 | Payment method always `'credit_card'` | `execute_booking` line ~440 | Schema supports debit_card/ewallet but function doesn't expose the choice. Extending requires adding a `payment_method` param and updating agent.py. |
| L03 | `user_id` generation uses `COUNT(*) + 1` | `register_user` | Not safe for concurrent registrations. Fine for single-user course demo. |
| L04 | `stop_order = 0` convention for pass-through stations | `national_rail_schedule_stops` | Not enforced by constraint. Application code must filter `stop_type = 'stop'`. |
| L05 | UI model list hardcoded to `llama3.2:1b` / `llama3.1:8b` (lowercase) | `skeleton/ui.py` line 67 | Model pulled as `llama3.1:8B` (uppercase) shows as `(not pulled)` in UI — cosmetic only, backend works correctly. |

---

## Known Bugs (Pending Fix)

- **O1** `execute_cancellation` in `databases/relational/queries.py`:
  departure time hardcoded to midnight instead of actual departure_time.
  Causes wrong refund tier calculation. Fix: one-line change, no DB reset needed.

---

## Current Progress

- ✅ PostgreSQL schema (schema1/schema2 split)
- ✅ Argon2id password hashing (schema2.credentials)
- ✅ Neo4j graph queries (6 functions)
- ✅ pgvector RAG
- ❌ O1 退款計算 bug 未修

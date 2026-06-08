-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
-- ============================================================

CREATE SCHEMA IF NOT EXISTS schema1;
CREATE SCHEMA IF NOT EXISTS schema2;

-- ============================================================
--  RELATIONAL TABLES  (schema1)
-- ============================================================

-- Users
-- Delete strategy: soft delete via is_active flag — records are never physically removed,
-- preserving audit trail and avoiding FK violations on financial history (payments, feedback).
CREATE TABLE IF NOT EXISTS schema1.users (
    -- UUID: user records are created dynamically at registration; UUID prevents sequential
    -- guessing of other users' IDs and is safe to expose in session tokens and API responses.
    user_id         UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    full_name       VARCHAR(100) NOT NULL,
    email           VARCHAR(150) NOT NULL UNIQUE,
    phone           VARCHAR(20),
    date_of_birth   DATE,
    secret_question TEXT,
    secret_answer   VARCHAR(200),
    registered_at   TIMESTAMPTZ  DEFAULT NOW(),
    is_active       BOOLEAN      DEFAULT TRUE
);

-- ── Metro ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS schema1.metro_stations (
    -- SERIAL: metro_stations is a fixed infrastructure lookup table loaded once from seed data;
    -- a compact auto-increment integer gives the best B-tree index performance for FK joins.
    id                                   SERIAL       PRIMARY KEY,
    station_code                         VARCHAR(10)  NOT NULL UNIQUE,
    name                                 VARCHAR(100) NOT NULL,
    is_interchange_metro                 BOOLEAN      DEFAULT FALSE,
    is_interchange_national_rail         BOOLEAN      DEFAULT FALSE,
    interchange_national_rail_station_id VARCHAR(10)  -- soft reference; NR stations inserted later
);

-- Lines served by each metro station (one row per line)
CREATE TABLE IF NOT EXISTS schema1.metro_station_lines (
    station_id  INTEGER    NOT NULL REFERENCES schema1.metro_stations(id) ON DELETE CASCADE,
    line        VARCHAR(5) NOT NULL,
    PRIMARY KEY (station_id, line)
);

CREATE TABLE IF NOT EXISTS schema1.metro_schedules (
    -- SERIAL: metro_schedules is a fixed timetable loaded once from seed data;
    -- SERIAL is sufficient for static reference data with no cross-system identity requirement.
    id                      SERIAL        PRIMARY KEY,
    schedule_code           VARCHAR(20)   NOT NULL UNIQUE,
    line                    VARCHAR(5)    NOT NULL,
    direction               VARCHAR(20)   NOT NULL,
    origin_station_id       INTEGER       NOT NULL REFERENCES schema1.metro_stations(id) ON DELETE RESTRICT,
    destination_station_id  INTEGER       NOT NULL REFERENCES schema1.metro_stations(id) ON DELETE RESTRICT,
    first_train_time        TIME          NOT NULL,
    last_train_time         TIME          NOT NULL,
    base_fare_usd           NUMERIC(6,2)  NOT NULL,
    per_stop_rate_usd       NUMERIC(6,2)  NOT NULL,
    frequency_min           INTEGER       NOT NULL
);

-- Operating days per metro schedule
CREATE TABLE IF NOT EXISTS schema1.metro_schedule_days (
    schedule_id INTEGER    NOT NULL REFERENCES schema1.metro_schedules(id) ON DELETE CASCADE,
    day_of_week VARCHAR(5) NOT NULL CHECK (day_of_week IN ('mon','tue','wed','thu','fri','sat','sun')),
    PRIMARY KEY (schedule_id, day_of_week)
);

-- Stop-level detail for each metro schedule
CREATE TABLE IF NOT EXISTS schema1.metro_schedule_stops (
    schedule_id                 INTEGER NOT NULL REFERENCES schema1.metro_schedules(id) ON DELETE CASCADE,
    station_id                  INTEGER NOT NULL REFERENCES schema1.metro_stations(id) ON DELETE RESTRICT,
    stop_order                  INTEGER NOT NULL,
    travel_time_from_origin_min INTEGER NOT NULL,
    PRIMARY KEY (schedule_id, station_id)
);

-- ── National Rail ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS schema1.national_rail_stations (
    -- SERIAL: national_rail_stations is a fixed infrastructure lookup table loaded once from seed data;
    -- a compact auto-increment integer gives the best B-tree index performance for FK joins.
    id                           SERIAL       PRIMARY KEY,
    station_code                 VARCHAR(10)  NOT NULL UNIQUE,
    name                         VARCHAR(100) NOT NULL,
    is_interchange_national_rail BOOLEAN      DEFAULT FALSE,
    is_interchange_metro         BOOLEAN      DEFAULT FALSE,
    interchange_metro_station_id VARCHAR(10)  -- soft reference to metro_stations
);

-- Lines served by each national rail station
CREATE TABLE IF NOT EXISTS schema1.national_rail_station_lines (
    station_id  INTEGER    NOT NULL REFERENCES schema1.national_rail_stations(id) ON DELETE CASCADE,
    line        VARCHAR(5) NOT NULL,
    PRIMARY KEY (station_id, line)
);

CREATE TABLE IF NOT EXISTS schema1.national_rail_schedules (
    -- SERIAL: national_rail_schedules is a fixed timetable loaded once from seed data;
    -- SERIAL is sufficient for static reference data with no cross-system identity requirement.
    id                      SERIAL       PRIMARY KEY,
    schedule_code           VARCHAR(20)  NOT NULL UNIQUE,
    line                    VARCHAR(5)   NOT NULL,
    service_type            VARCHAR(20)  NOT NULL,  -- 'normal', 'express'
    direction               VARCHAR(20)  NOT NULL,
    origin_station_id       INTEGER      NOT NULL REFERENCES schema1.national_rail_stations(id) ON DELETE RESTRICT,
    destination_station_id  INTEGER      NOT NULL REFERENCES schema1.national_rail_stations(id) ON DELETE RESTRICT,
    first_train_time        TIME         NOT NULL,
    last_train_time         TIME         NOT NULL,
    frequency_min           INTEGER      NOT NULL
);

-- Operating days per national rail schedule
CREATE TABLE IF NOT EXISTS schema1.national_rail_schedule_days (
    schedule_id INTEGER    NOT NULL REFERENCES schema1.national_rail_schedules(id) ON DELETE CASCADE,
    day_of_week VARCHAR(5) NOT NULL CHECK (day_of_week IN ('mon','tue','wed','thu','fri','sat','sun')),
    PRIMARY KEY (schedule_id, day_of_week)
);

-- Stop-level detail for each national rail schedule
CREATE TABLE IF NOT EXISTS schema1.national_rail_schedule_stops (
    schedule_id                 INTEGER NOT NULL REFERENCES schema1.national_rail_schedules(id) ON DELETE CASCADE,
    station_id                  INTEGER NOT NULL REFERENCES schema1.national_rail_stations(id) ON DELETE RESTRICT,
    stop_order                  INTEGER NOT NULL,
    travel_time_from_origin_min INTEGER NOT NULL,
    stop_type                   VARCHAR(15) NOT NULL DEFAULT 'stop'
                                    CHECK (stop_type IN ('stop', 'pass_through')),
    PRIMARY KEY (schedule_id, station_id)
);

-- Fare classes per national rail schedule
CREATE TABLE IF NOT EXISTS schema1.national_rail_fare_classes (
    schedule_id       INTEGER      NOT NULL REFERENCES schema1.national_rail_schedules(id) ON DELETE CASCADE,
    fare_class        VARCHAR(20)  NOT NULL,  -- 'standard', 'first'
    base_fare_usd     NUMERIC(6,2) NOT NULL,
    per_stop_rate_usd NUMERIC(6,2) NOT NULL,
    PRIMARY KEY (schedule_id, fare_class)
);

-- Seat layouts (one row per seat)
CREATE TABLE IF NOT EXISTS schema1.national_rail_seat_layouts (
    layout_id   VARCHAR(10) NOT NULL,
    schedule_id INTEGER     NOT NULL REFERENCES schema1.national_rail_schedules(id) ON DELETE CASCADE,
    coach       VARCHAR(5)  NOT NULL,
    fare_class  VARCHAR(20) NOT NULL,
    seat_id     VARCHAR(10) NOT NULL,
    row_num     INTEGER     NOT NULL,
    col_name    VARCHAR(5)  NOT NULL,
    PRIMARY KEY (schedule_id, coach, seat_id)
);

-- ── Bookings & Travel History ──────────────────────────────────

CREATE TABLE IF NOT EXISTS schema1.national_rail_bookings (
    -- UUID: booking records are created dynamically at runtime; UUID prevents sequential
    -- guessing of other users' booking references and is safe to share with customers.
    booking_id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID         NOT NULL REFERENCES schema1.users(user_id) ON DELETE RESTRICT,
    schedule_id             INTEGER      NOT NULL REFERENCES schema1.national_rail_schedules(id) ON DELETE RESTRICT,
    origin_station_id       INTEGER      NOT NULL REFERENCES schema1.national_rail_stations(id) ON DELETE RESTRICT,
    destination_station_id  INTEGER      NOT NULL REFERENCES schema1.national_rail_stations(id) ON DELETE RESTRICT,
    travel_date             DATE         NOT NULL,
    departure_time          TIME         NOT NULL,
    ticket_type             VARCHAR(20)  NOT NULL,
    fare_class              VARCHAR(20)  NOT NULL,
    coach                   VARCHAR(5),
    seat_id                 VARCHAR(10),
    stops_travelled         INTEGER,
    amount_usd              NUMERIC(8,2) NOT NULL,
    status                  VARCHAR(20)  NOT NULL,  -- 'confirmed','completed','cancelled'
    booked_at               TIMESTAMPTZ,
    travelled_at            TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS schema1.metro_travels (
    -- UUID: travel records are created dynamically at purchase time; UUID is consistent
    -- with national_rail_bookings and prevents sequential enumeration of trip records.
    trip_id                 UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID         NOT NULL REFERENCES schema1.users(user_id) ON DELETE RESTRICT,
    schedule_id             INTEGER      NOT NULL REFERENCES schema1.metro_schedules(id) ON DELETE RESTRICT,
    origin_station_id       INTEGER      NOT NULL REFERENCES schema1.metro_stations(id) ON DELETE RESTRICT,
    destination_station_id  INTEGER      NOT NULL REFERENCES schema1.metro_stations(id) ON DELETE RESTRICT,
    travel_date             DATE         NOT NULL,
    ticket_type             VARCHAR(20)  NOT NULL,  -- 'single', 'day_pass'
    day_pass_ref            UUID,                   -- references another trip_id for linked day_pass legs
    stops_travelled         INTEGER,
    amount_usd              NUMERIC(8,2) NOT NULL,
    status                  VARCHAR(20)  NOT NULL,
    purchased_at            TIMESTAMPTZ,
    travelled_at            TIMESTAMPTZ
);

-- ── Payments ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS schema1.payments (
    -- UUID: payment records are created dynamically alongside bookings; UUID matches the
    -- UUID booking_id they reference and prevents sequential enumeration of payment records.
    payment_id    UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id    UUID         NOT NULL,  -- soft ref to national_rail_bookings or metro_travels (cross-table)
    booking_type  VARCHAR(10)  NOT NULL CHECK (booking_type IN ('rail', 'metro')),
    amount_usd    NUMERIC(8,2) NOT NULL,
    method        VARCHAR(30)  NOT NULL,  -- 'credit_card','debit_card','ewallet'
    status        VARCHAR(20)  NOT NULL,  -- 'paid','refunded'
    paid_at       TIMESTAMPTZ
);

-- ── Feedback ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS schema1.feedback (
    -- UUID: feedback records are created dynamically after travel; UUID is consistent with
    -- the UUID booking references they link to and safe to expose in summary queries.
    feedback_id   UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id    UUID        NOT NULL,  -- soft ref to national_rail_bookings or metro_travels
    booking_type  VARCHAR(10) NOT NULL CHECK (booking_type IN ('rail', 'metro')),
    user_id       UUID        NOT NULL REFERENCES schema1.users(user_id) ON DELETE RESTRICT,
    rating        SMALLINT    NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    submitted_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_nr_bookings_user_date   ON schema1.national_rail_bookings (user_id, travel_date);
CREATE INDEX IF NOT EXISTS idx_nr_bookings_seat        ON schema1.national_rail_bookings (schedule_id, travel_date, seat_id);
CREATE INDEX IF NOT EXISTS idx_metro_travels_user_date ON schema1.metro_travels (user_id, travel_date);
CREATE INDEX IF NOT EXISTS idx_payments_booking        ON schema1.payments (booking_id);
CREATE INDEX IF NOT EXISTS idx_feedback_booking        ON schema1.feedback (booking_id);

-- ============================================================
--  VECTOR SCHEMA  (RAG / Help Desk) — do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema1.policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,
    content     TEXT         NOT NULL,
    embedding   vector(768),
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_policy_documents_embedding ON schema1.policy_documents USING hnsw (embedding vector_cosine_ops);

-- ============================================================
--  CREDENTIALS  (schema2) — Argon2id hashes isolated here
-- ============================================================

CREATE TABLE IF NOT EXISTS schema2.credentials (
    id          SERIAL       PRIMARY KEY,
    user_id     UUID         NOT NULL REFERENCES schema1.users(user_id) ON DELETE CASCADE,
    stored_hash VARCHAR(255) NOT NULL,
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

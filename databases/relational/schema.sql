-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
-- ============================================================

-- ============================================================
--  RELATIONAL TABLES
-- ============================================================

-- Users
CREATE TABLE IF NOT EXISTS users (
    user_id         VARCHAR(10)  PRIMARY KEY,
    full_name       VARCHAR(100) NOT NULL,
    email           VARCHAR(150) NOT NULL UNIQUE,
    password        VARCHAR(255) NOT NULL,
    phone           VARCHAR(20),
    date_of_birth   DATE,
    secret_question TEXT,
    secret_answer   VARCHAR(200),
    registered_at   TIMESTAMPTZ  DEFAULT NOW(),
    is_active       BOOLEAN      DEFAULT TRUE
);

-- ── Metro ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS metro_stations (
    station_id                      VARCHAR(10)  PRIMARY KEY,
    name                            VARCHAR(100) NOT NULL,
    is_interchange_metro            BOOLEAN      DEFAULT FALSE,
    is_interchange_national_rail    BOOLEAN      DEFAULT FALSE,
    interchange_national_rail_station_id  VARCHAR(10)  -- soft reference; NR stations inserted later
);

-- Lines served by each metro station (one row per line)
CREATE TABLE IF NOT EXISTS metro_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id),
    line        VARCHAR(5)  NOT NULL,
    PRIMARY KEY (station_id, line)
);

CREATE TABLE IF NOT EXISTS metro_schedules (
    schedule_id             VARCHAR(20)   PRIMARY KEY,
    line                    VARCHAR(5)    NOT NULL,
    direction               VARCHAR(20)   NOT NULL,
    origin_station_id       VARCHAR(10)   NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id  VARCHAR(10)   NOT NULL REFERENCES metro_stations(station_id),
    first_train_time        TIME          NOT NULL,
    last_train_time         TIME          NOT NULL,
    base_fare_usd           NUMERIC(6,2)  NOT NULL,
    per_stop_rate_usd       NUMERIC(6,2)  NOT NULL,
    frequency_min           INTEGER       NOT NULL
);

-- Operating days per metro schedule
CREATE TABLE IF NOT EXISTS metro_schedule_days (
    schedule_id VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id),
    day_of_week VARCHAR(5)  NOT NULL CHECK (day_of_week IN ('mon','tue','wed','thu','fri','sat','sun')),
    PRIMARY KEY (schedule_id, day_of_week)
);

-- Stop-level detail for each metro schedule
CREATE TABLE IF NOT EXISTS metro_schedule_stops (
    schedule_id                 VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id),
    station_id                  VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id),
    stop_order                  INTEGER     NOT NULL,
    travel_time_from_origin_min INTEGER     NOT NULL,
    PRIMARY KEY (schedule_id, station_id)
);

-- ── National Rail ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS national_rail_stations (
    station_id                   VARCHAR(10)  PRIMARY KEY,
    name                         VARCHAR(100) NOT NULL,
    is_interchange_national_rail BOOLEAN      DEFAULT FALSE,
    is_interchange_metro         BOOLEAN      DEFAULT FALSE,
    interchange_metro_station_id VARCHAR(10)  -- soft reference to metro_stations
);

-- Lines served by each national rail station
CREATE TABLE IF NOT EXISTS national_rail_station_lines (
    station_id  VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id),
    line        VARCHAR(5)  NOT NULL,
    PRIMARY KEY (station_id, line)
);

CREATE TABLE IF NOT EXISTS national_rail_schedules (
    schedule_id             VARCHAR(20)  PRIMARY KEY,
    line                    VARCHAR(5)   NOT NULL,
    service_type            VARCHAR(20)  NOT NULL,  -- 'normal', 'express'
    direction               VARCHAR(20)  NOT NULL,
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    first_train_time        TIME         NOT NULL,
    last_train_time         TIME         NOT NULL,
    frequency_min           INTEGER      NOT NULL
);

-- Operating days per national rail schedule
CREATE TABLE IF NOT EXISTS national_rail_schedule_days (
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id),
    day_of_week VARCHAR(5)  NOT NULL CHECK (day_of_week IN ('mon','tue','wed','thu','fri','sat','sun')),
    PRIMARY KEY (schedule_id, day_of_week)
);

-- Stop-level detail for each national rail schedule
CREATE TABLE IF NOT EXISTS national_rail_schedule_stops (
    schedule_id                 VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id),
    station_id                  VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id),
    stop_order                  INTEGER     NOT NULL,
    travel_time_from_origin_min INTEGER     NOT NULL,
    stop_type                   VARCHAR(15) NOT NULL DEFAULT 'stop'
                                    CHECK (stop_type IN ('stop', 'pass_through')),
    PRIMARY KEY (schedule_id, station_id)
);

-- Fare classes per national rail schedule
CREATE TABLE IF NOT EXISTS national_rail_fare_classes (
    schedule_id       VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id),
    fare_class        VARCHAR(20)  NOT NULL,  -- 'standard', 'first'
    base_fare_usd     NUMERIC(6,2) NOT NULL,
    per_stop_rate_usd NUMERIC(6,2) NOT NULL,
    PRIMARY KEY (schedule_id, fare_class)
);

-- Seat layouts (one row per seat)
CREATE TABLE IF NOT EXISTS national_rail_seat_layouts (
    layout_id   VARCHAR(10) NOT NULL,
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id),
    coach       VARCHAR(5)  NOT NULL,
    fare_class  VARCHAR(20) NOT NULL,
    seat_id     VARCHAR(10) NOT NULL,
    row_num     INTEGER     NOT NULL,
    col_name    VARCHAR(5)  NOT NULL,
    PRIMARY KEY (schedule_id, coach, seat_id)
);

-- ── Bookings & Travel History ──────────────────────────────────

CREATE TABLE IF NOT EXISTS national_rail_bookings (
    booking_id              VARCHAR(10)  PRIMARY KEY,
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
    status                  VARCHAR(20)  NOT NULL,  -- 'confirmed','completed','cancelled'
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
    ticket_type             VARCHAR(20)  NOT NULL,  -- 'single', 'day_pass'
    day_pass_ref            VARCHAR(10),            -- references another trip_id for linked day_pass legs
    stops_travelled         INTEGER,
    amount_usd              NUMERIC(8,2) NOT NULL,
    status                  VARCHAR(20)  NOT NULL,
    purchased_at            TIMESTAMPTZ,
    travelled_at            TIMESTAMPTZ
);

-- ── Payments ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS payments (
    payment_id    VARCHAR(10)  PRIMARY KEY,
    booking_id    VARCHAR(10)  NOT NULL,  -- references BK* or MT* IDs (cross-table soft ref)
    booking_type  VARCHAR(10)  NOT NULL CHECK (booking_type IN ('rail', 'metro')),
    amount_usd    NUMERIC(8,2) NOT NULL,
    method        VARCHAR(30)  NOT NULL,  -- 'credit_card','debit_card','ewallet'
    status        VARCHAR(20)  NOT NULL,  -- 'paid','refunded'
    paid_at       TIMESTAMPTZ
);

-- ── Feedback ──────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id   VARCHAR(10) PRIMARY KEY,
    booking_id    VARCHAR(10) NOT NULL,  -- references BK* or MT* (soft ref)
    booking_type  VARCHAR(10) NOT NULL CHECK (booking_type IN ('rail', 'metro')),
    user_id       VARCHAR(10) NOT NULL REFERENCES users(user_id),
    rating        SMALLINT    NOT NULL CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    submitted_at  TIMESTAMPTZ DEFAULT NOW()
);

-- ── Indexes ───────────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_nr_bookings_user_date   ON national_rail_bookings (user_id, travel_date);
CREATE INDEX IF NOT EXISTS idx_nr_bookings_seat        ON national_rail_bookings (schedule_id, travel_date, seat_id);
CREATE INDEX IF NOT EXISTS idx_metro_travels_user_date ON metro_travels (user_id, travel_date);
CREATE INDEX IF NOT EXISTS idx_payments_booking        ON payments (booking_id);
CREATE INDEX IF NOT EXISTS idx_feedback_booking        ON feedback (booking_id);

-- ============================================================
--  VECTOR SCHEMA  (RAG / Help Desk) — do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,
    content     TEXT         NOT NULL,
    embedding   vector(768),
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_policy_documents_embedding ON policy_documents USING hnsw (embedding vector_cosine_ops);

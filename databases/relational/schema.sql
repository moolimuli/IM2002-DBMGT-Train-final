-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational  → dual-network transit data you design below
--    2. Vector      → policy documents for RAG (provided — do not modify)
-- ============================================================

-- ============================================================
--  RELATIONAL SCHEMA — TransitFlow dual-network transit data
-- ============================================================

-- ── Stations ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS metro_stations (
    station_id                           VARCHAR(10)  PRIMARY KEY,
    name                                 VARCHAR(100) NOT NULL,
    lines                                TEXT[]       NOT NULL,
    is_interchange_metro                 BOOLEAN      DEFAULT FALSE,
    is_interchange_national_rail         BOOLEAN      DEFAULT FALSE,
    interchange_national_rail_station_id VARCHAR(10)
);

CREATE TABLE IF NOT EXISTS national_rail_stations (
    station_id                       VARCHAR(10)  PRIMARY KEY,
    name                             VARCHAR(100) NOT NULL,
    lines                            TEXT[]       NOT NULL,
    is_interchange_national_rail     BOOLEAN      DEFAULT FALSE,
    is_interchange_metro             BOOLEAN      DEFAULT FALSE,
    interchange_metro_station_id     VARCHAR(10)
);

-- ── Schedules ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS metro_schedules (
    schedule_id                  VARCHAR(20)   PRIMARY KEY,
    line                         VARCHAR(5)    NOT NULL,
    direction                    VARCHAR(20)   NOT NULL,
    origin_station_id            VARCHAR(10)   REFERENCES metro_stations(station_id),
    destination_station_id       VARCHAR(10)   REFERENCES metro_stations(station_id),
    stops_in_order               TEXT[]        NOT NULL,
    first_train_time             TIME          NOT NULL,
    last_train_time              TIME          NOT NULL,
    travel_time_from_origin_min  JSONB         NOT NULL,
    base_fare_usd                NUMERIC(5,2)  NOT NULL,
    per_stop_rate_usd            NUMERIC(5,2)  NOT NULL,
    frequency_min                INTEGER       NOT NULL,
    operates_on                  TEXT[]        NOT NULL
);

CREATE TABLE IF NOT EXISTS national_rail_schedules (
    schedule_id                  VARCHAR(20)   PRIMARY KEY,
    line                         VARCHAR(5)    NOT NULL,
    service_type                 VARCHAR(20)   NOT NULL,
    direction                    VARCHAR(20)   NOT NULL,
    origin_station_id            VARCHAR(10)   REFERENCES national_rail_stations(station_id),
    destination_station_id       VARCHAR(10)   REFERENCES national_rail_stations(station_id),
    stops_in_order               TEXT[]        NOT NULL,
    passed_through_stations      TEXT[],
    first_train_time             TIME          NOT NULL,
    last_train_time              TIME          NOT NULL,
    travel_time_from_origin_min  JSONB         NOT NULL,
    frequency_min                INTEGER       NOT NULL,
    operates_on                  TEXT[]        NOT NULL
);

-- Fare classes per schedule (standard / first)
CREATE TABLE IF NOT EXISTS national_rail_fare_classes (
    schedule_id        VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id),
    fare_class         VARCHAR(20)  NOT NULL,
    base_fare_usd      NUMERIC(5,2) NOT NULL,
    per_stop_rate_usd  NUMERIC(5,2) NOT NULL,
    PRIMARY KEY (schedule_id, fare_class)
);

-- ── Seat Layouts ──────────────────────────────────────────────────────────────

-- Flattened from national_rail_seat_layouts.json coaches[].seats[]
CREATE TABLE IF NOT EXISTS national_rail_seats (
    schedule_id  VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id),
    seat_id      VARCHAR(10) NOT NULL,
    coach        VARCHAR(5)  NOT NULL,
    fare_class   VARCHAR(20) NOT NULL,
    row          INTEGER     NOT NULL,
    col          VARCHAR(5)  NOT NULL,
    PRIMARY KEY (schedule_id, seat_id)
);

-- ── Users ─────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    user_id          VARCHAR(10)  PRIMARY KEY,
    full_name        VARCHAR(150) NOT NULL,
    email            VARCHAR(150) UNIQUE NOT NULL,
    password         VARCHAR(255) NOT NULL,
    phone            VARCHAR(20),
    date_of_birth    DATE,
    secret_question  VARCHAR(255),
    secret_answer    VARCHAR(255),
    registered_at    TIMESTAMPTZ  DEFAULT NOW(),
    is_active        BOOLEAN      DEFAULT TRUE
);

-- ── Bookings & Travel History ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS national_rail_bookings (
    booking_id              VARCHAR(20)   PRIMARY KEY,
    user_id                 VARCHAR(10)   NOT NULL REFERENCES users(user_id),
    schedule_id             VARCHAR(20)   NOT NULL REFERENCES national_rail_schedules(schedule_id),
    origin_station_id       VARCHAR(10)   NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id  VARCHAR(10)   NOT NULL REFERENCES national_rail_stations(station_id),
    travel_date             DATE          NOT NULL,
    departure_time          TIME          NOT NULL,
    ticket_type             VARCHAR(20)   NOT NULL,
    fare_class              VARCHAR(20)   NOT NULL,
    coach                   VARCHAR(5),
    seat_id                 VARCHAR(10),
    stops_travelled         INTEGER       NOT NULL,
    amount_usd              NUMERIC(10,2) NOT NULL,
    status                  VARCHAR(20)   NOT NULL DEFAULT 'confirmed',
    booked_at               TIMESTAMPTZ   DEFAULT NOW(),
    travelled_at            TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS metro_travels (
    trip_id                 VARCHAR(20)   PRIMARY KEY,
    user_id                 VARCHAR(10)   NOT NULL REFERENCES users(user_id),
    schedule_id             VARCHAR(20)   NOT NULL REFERENCES metro_schedules(schedule_id),
    origin_station_id       VARCHAR(10)   NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id  VARCHAR(10)   NOT NULL REFERENCES metro_stations(station_id),
    travel_date             DATE          NOT NULL,
    ticket_type             VARCHAR(20)   NOT NULL,
    day_pass_ref            VARCHAR(20),
    stops_travelled         INTEGER,
    amount_usd              NUMERIC(10,2) NOT NULL,
    status                  VARCHAR(20)   NOT NULL DEFAULT 'completed',
    purchased_at            TIMESTAMPTZ,
    travelled_at            TIMESTAMPTZ
);

-- ── Payments & Feedback ───────────────────────────────────────────────────────

-- booking_id references national_rail_bookings.booking_id (BK...) or metro_travels.trip_id (MT...)
CREATE TABLE IF NOT EXISTS payments (
    payment_id  VARCHAR(20)   PRIMARY KEY,
    booking_id  VARCHAR(20)   NOT NULL,
    amount_usd  NUMERIC(10,2) NOT NULL,
    method      VARCHAR(20)   NOT NULL,
    status      VARCHAR(20)   NOT NULL,
    paid_at     TIMESTAMPTZ   NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    feedback_id   VARCHAR(20) PRIMARY KEY,
    booking_id    VARCHAR(20) NOT NULL,
    user_id       VARCHAR(10) NOT NULL REFERENCES users(user_id),
    rating        INTEGER     CHECK (rating BETWEEN 1 AND 5),
    comment       TEXT,
    submitted_at  TIMESTAMPTZ NOT NULL
);




-- ============================================================
--  VECTOR SCHEMA  (RAG / Help Desk) — do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,  -- 'refund', 'booking', 'conduct'
    content     TEXT         NOT NULL,
    -- 768-dim  → Ollama nomic-embed-text (default)
    -- 3072-dim → Gemini gemini-embedding-001
    -- If you switch LLM_PROVIDER to gemini, change to vector(3072) and reset the database.
    embedding   vector(768),
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

-- Index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS idx_policy_embedding ON policy_documents USING hnsw (embedding vector_cosine_ops);

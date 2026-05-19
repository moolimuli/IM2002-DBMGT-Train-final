-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational  → dual-network transit data you design below
--    2. Vector      → policy documents for RAG (provided — do not modify)
-- ============================================================

-- ============================================================
--  STUDENT TASK — Design and create your relational tables here
--
--  Start from the mock data in train-mock-data/:
--    metro_stations.json, national_rail_stations.json
--    metro_schedules.json, national_rail_schedules.json
--    national_rail_seat_layouts.json
--    registered_users.json
--    bookings.json, metro_travel_history.json
--    payments.json, feedback.json
--
--  Think about:
--    - What tables do you need?
--    - What columns and data types?
--    - Which fields are primary keys? Which are foreign keys?
--    - What constraints make sense?
--
--  Apply your schema with:
--    docker-compose down -v && docker-compose up -d
-- ============================================================
-- ============================================================
--  STUDENT TASK — Design and create your relational tables here
-- ============================================================

-- Users
CREATE TABLE IF NOT EXISTS users (
    user_id         VARCHAR(10)  PRIMARY KEY,
    full_name       VARCHAR(100) NOT NULL,
    email           VARCHAR(150) NOT NULL UNIQUE,
    password        VARCHAR(100) NOT NULL,
    phone           VARCHAR(20),
    date_of_birth   DATE,
    secret_question VARCHAR(200),
    secret_answer   VARCHAR(100),
    registered_at   TIMESTAMPTZ  DEFAULT NOW(),
    is_active       BOOLEAN      DEFAULT TRUE
);

-- National Rail Stations (建在 metro 之前，因為 metro 會參照它)
CREATE TABLE IF NOT EXISTS national_rail_stations (
    station_id                   VARCHAR(10)  PRIMARY KEY,
    name                         VARCHAR(100) NOT NULL,
    is_interchange_metro         BOOLEAN      DEFAULT FALSE,
    interchange_metro_station_id VARCHAR(10)
);

-- Metro Stations
CREATE TABLE IF NOT EXISTS metro_stations (
    station_id                            VARCHAR(10)  PRIMARY KEY,
    name                                  VARCHAR(100) NOT NULL,
    is_interchange_metro                  BOOLEAN      DEFAULT FALSE,
    is_interchange_national_rail          BOOLEAN      DEFAULT FALSE,
    interchange_national_rail_station_id  VARCHAR(10)  REFERENCES national_rail_stations(station_id)
);

-- National Rail Schedules
CREATE TABLE IF NOT EXISTS national_rail_schedules (
    schedule_id             VARCHAR(20)  PRIMARY KEY,
    line                    VARCHAR(10)  NOT NULL,
    service_type            VARCHAR(20)  NOT NULL,
    direction               VARCHAR(20)  NOT NULL,
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    first_train_time        TIME         NOT NULL,
    last_train_time         TIME         NOT NULL,
    frequency_min           INT          NOT NULL,
    standard_base_fare_usd  NUMERIC(6,2),
    standard_per_stop_usd   NUMERIC(6,2),
    first_base_fare_usd     NUMERIC(6,2),
    first_per_stop_usd      NUMERIC(6,2)
);

-- National Rail Schedule Stops (停靠站順序與時間)
CREATE TABLE IF NOT EXISTS national_rail_schedule_stops (
    schedule_id                 VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id),
    station_id                  VARCHAR(10) NOT NULL REFERENCES national_rail_stations(station_id),
    stop_order                  INT         NOT NULL,
    travel_time_from_origin_min INT         NOT NULL,
    PRIMARY KEY (schedule_id, station_id)
);

-- National Rail Schedule Operating Days
CREATE TABLE IF NOT EXISTS national_rail_schedule_days (
    schedule_id VARCHAR(20) NOT NULL REFERENCES national_rail_schedules(schedule_id),
    day_of_week VARCHAR(5)  NOT NULL,
    PRIMARY KEY (schedule_id, day_of_week)
);

-- National Rail Seat Layouts
CREATE TABLE IF NOT EXISTS national_rail_seat_layouts (
    layout_id   VARCHAR(10)  NOT NULL,
    schedule_id VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id),
    coach       VARCHAR(5)   NOT NULL,
    fare_class  VARCHAR(20)  NOT NULL,
    seat_id     VARCHAR(10)  NOT NULL,
    seat_row    INT          NOT NULL,
    seat_column VARCHAR(5)   NOT NULL,
    PRIMARY KEY (layout_id, seat_id)
);

-- Metro Schedules
CREATE TABLE IF NOT EXISTS metro_schedules (
    schedule_id             VARCHAR(20)  PRIMARY KEY,
    line                    VARCHAR(10)  NOT NULL,
    direction               VARCHAR(20)  NOT NULL,
    origin_station_id       VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id  VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    first_train_time        TIME         NOT NULL,
    last_train_time         TIME         NOT NULL,
    base_fare_usd           NUMERIC(6,2) NOT NULL,
    per_stop_rate_usd       NUMERIC(6,2) NOT NULL,
    frequency_min           INT          NOT NULL
);

-- Metro Schedule Stops
CREATE TABLE IF NOT EXISTS metro_schedule_stops (
    schedule_id                 VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id),
    station_id                  VARCHAR(10) NOT NULL REFERENCES metro_stations(station_id),
    stop_order                  INT         NOT NULL,
    travel_time_from_origin_min INT         NOT NULL,
    PRIMARY KEY (schedule_id, station_id)
);

-- Metro Schedule Operating Days
CREATE TABLE IF NOT EXISTS metro_schedule_days (
    schedule_id VARCHAR(20) NOT NULL REFERENCES metro_schedules(schedule_id),
    day_of_week VARCHAR(5)  NOT NULL,
    PRIMARY KEY (schedule_id, day_of_week)
);

-- National Rail Bookings
CREATE TABLE IF NOT EXISTS national_rail_bookings (
    booking_id             VARCHAR(10)  PRIMARY KEY,
    user_id                VARCHAR(10)  NOT NULL REFERENCES users(user_id),
    schedule_id            VARCHAR(20)  NOT NULL REFERENCES national_rail_schedules(schedule_id),
    origin_station_id      VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    destination_station_id VARCHAR(10)  NOT NULL REFERENCES national_rail_stations(station_id),
    travel_date            DATE         NOT NULL,
    departure_time         TIME         NOT NULL,
    ticket_type            VARCHAR(20)  NOT NULL,
    fare_class             VARCHAR(20)  NOT NULL,
    coach                  VARCHAR(5),
    seat_id                VARCHAR(10),
    stops_travelled        INT,
    amount_usd             NUMERIC(6,2) NOT NULL,
    status                 VARCHAR(20)  NOT NULL DEFAULT 'confirmed',
    booked_at              TIMESTAMPTZ  DEFAULT NOW(),
    travelled_at           TIMESTAMPTZ
);

-- Metro Travel History
CREATE TABLE IF NOT EXISTS metro_travels (
    trip_id                VARCHAR(10)  PRIMARY KEY,
    user_id                VARCHAR(10)  NOT NULL REFERENCES users(user_id),
    schedule_id            VARCHAR(20)  NOT NULL REFERENCES metro_schedules(schedule_id),
    origin_station_id      VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    destination_station_id VARCHAR(10)  NOT NULL REFERENCES metro_stations(station_id),
    travel_date            DATE         NOT NULL,
    ticket_type            VARCHAR(20)  NOT NULL,
    day_pass_ref           VARCHAR(10),
    stops_travelled        INT,
    amount_usd             NUMERIC(6,2) NOT NULL,
    status                 VARCHAR(20)  NOT NULL DEFAULT 'completed',
    purchased_at           TIMESTAMPTZ,
    travelled_at           TIMESTAMPTZ
);

-- Payments
CREATE TABLE IF NOT EXISTS payments (
    payment_id VARCHAR(10)  PRIMARY KEY,
    booking_id VARCHAR(20)  NOT NULL,
    amount_usd NUMERIC(6,2) NOT NULL,
    method     VARCHAR(30)  NOT NULL,
    status     VARCHAR(20)  NOT NULL DEFAULT 'paid',
    paid_at    TIMESTAMPTZ  DEFAULT NOW()
);

-- Feedback
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id  VARCHAR(10)  PRIMARY KEY,
    booking_id   VARCHAR(20),
    user_id      VARCHAR(10)  REFERENCES users(user_id),
    rating       INT          CHECK (rating BETWEEN 1 AND 5),
    comment      TEXT,
    submitted_at TIMESTAMPTZ  DEFAULT NOW()
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
CREATE INDEX IF NOT EXISTS policy_documents_embedding_idx ON policy_documents USING hnsw (embedding vector_cosine_ops);
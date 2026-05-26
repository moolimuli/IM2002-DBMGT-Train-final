"""
Seed PostgreSQL with all TransitFlow mock data from train-mock-data/.

Usage:
    python skeleton/seed_postgres.py

Run AFTER docker-compose up -d.
Safe to re-run: all inserts use ON CONFLICT DO NOTHING.
"""

import json
import os
import sys

import psycopg2
from psycopg2.extras import execute_values

from argon2 import PasswordHasher
_ph = PasswordHasher()

# ── resolve paths ────────────────────────────────────────────────────────────
SCRIPT_DIR  = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
DATA_DIR    = os.path.join(PROJECT_DIR, "train-mock-data")

sys.path.insert(0, PROJECT_DIR)
from skeleton import config as cfg


def load(filename):
    with open(os.path.join(DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def connect():
    return psycopg2.connect(
        host=cfg.PG_HOST,
        port=cfg.PG_PORT,
        dbname=cfg.PG_DB,
        user=cfg.PG_USER,
        password=cfg.PG_PASSWORD,
    )


def insert_many(cur, table, columns, rows):
    """Bulk insert with ON CONFLICT DO NOTHING. Returns row count inserted."""
    if not rows:
        return 0
    sql = (
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES %s "
        f"ON CONFLICT DO NOTHING"
    )
    execute_values(cur, sql, rows)
    # cur.rowcount can be -1 after execute_values + ON CONFLICT DO NOTHING
    # in some psycopg2 versions; fall back to len(rows) in that case.
    return cur.rowcount if cur.rowcount >= 0 else len(rows)


# ── helpers ──────────────────────────────────────────────────────────────────

def _infer_booking_type(booking_id: str) -> str:
    """Return 'rail' for BK* IDs and 'metro' for MT* IDs. Raises on unknown prefix."""
    if booking_id.startswith("BK"):
        return 'rail'
    elif booking_id.startswith("MT"):
        return 'metro'
    raise ValueError(
        f"Unknown booking_id prefix: {booking_id!r} — expected 'BK' (rail) or 'MT' (metro)"
    )


# ── seeders ──────────────────────────────────────────────────────────────────

def seed_metro_stations(cur):
    data = load("metro_stations.json")

    # Main station records
    rows = [
        (
            s["station_id"],
            s["name"],
            s["is_interchange_metro"],
            s["is_interchange_national_rail"],
            s.get("interchange_national_rail_station_id"),  # may be None
        )
        for s in data
    ]
    n = insert_many(cur, "schema1.metro_stations",
                    ["station_id", "name", "is_interchange_metro",
                     "is_interchange_national_rail", "interchange_national_rail_station_id"],
                    rows)
    print(f"  schema1.metro_stations: {n} rows")

    # Lines per station (each station has a list of lines)
    line_rows = []
    for s in data:
        for line in s["lines"]:
            line_rows.append((s["station_id"], line))
    n = insert_many(cur, "schema1.metro_station_lines", ["station_id", "line"], line_rows)
    print(f"  schema1.metro_station_lines: {n} rows")


def seed_national_rail_stations(cur):
    data = load("national_rail_stations.json")

    rows = [
        (
            s["station_id"],
            s["name"],
            s["is_interchange_national_rail"],
            s["is_interchange_metro"],
            s.get("interchange_metro_station_id"),
        )
        for s in data
    ]
    n = insert_many(cur, "schema1.national_rail_stations",
                    ["station_id", "name", "is_interchange_national_rail",
                     "is_interchange_metro", "interchange_metro_station_id"],
                    rows)
    print(f"  schema1.national_rail_stations: {n} rows")

    line_rows = []
    for s in data:
        for line in s["lines"]:
            line_rows.append((s["station_id"], line))
    n = insert_many(cur, "schema1.national_rail_station_lines", ["station_id", "line"], line_rows)
    print(f"  schema1.national_rail_station_lines: {n} rows")


def seed_metro_schedules(cur):
    data = load("metro_schedules.json")

    # Main schedule record
    rows = [
        (
            s["schedule_id"],
            s["line"],
            s["direction"],
            s["origin_station_id"],
            s["destination_station_id"],
            s["first_train_time"],
            s["last_train_time"],
            s["base_fare_usd"],
            s["per_stop_rate_usd"],
            s["frequency_min"],
        )
        for s in data
    ]
    n = insert_many(cur, "schema1.metro_schedules",
                    ["schedule_id", "line", "direction", "origin_station_id",
                     "destination_station_id", "first_train_time", "last_train_time",
                     "base_fare_usd", "per_stop_rate_usd", "frequency_min"],
                    rows)
    print(f"  schema1.metro_schedules: {n} rows")

    # Operating days
    day_rows = []
    for s in data:
        for day in s["operates_on"]:
            day_rows.append((s["schedule_id"], day))
    n = insert_many(cur, "schema1.metro_schedule_days", ["schedule_id", "day_of_week"], day_rows)
    print(f"  schema1.metro_schedule_days: {n} rows")

    # Stops — stops_in_order gives the ordered list;
    # travel_time_from_origin_min gives the time for each station id.
    stop_rows = []
    for s in data:
        times = s["travel_time_from_origin_min"]
        for order, station_id in enumerate(s["stops_in_order"], start=1):
            stop_rows.append((
                s["schedule_id"],
                station_id,
                order,
                times[station_id],
            ))
    n = insert_many(cur, "schema1.metro_schedule_stops",
                    ["schedule_id", "station_id", "stop_order",
                     "travel_time_from_origin_min"],
                    stop_rows)
    print(f"  schema1.metro_schedule_stops: {n} rows")


def seed_national_rail_schedules(cur):
    data = load("national_rail_schedules.json")

    rows = [
        (
            s["schedule_id"],
            s["line"],
            s["service_type"],
            s["direction"],
            s["origin_station_id"],
            s["destination_station_id"],
            s["first_train_time"],
            s["last_train_time"],
            s["frequency_min"],
        )
        for s in data
    ]
    n = insert_many(cur, "schema1.national_rail_schedules",
                    ["schedule_id", "line", "service_type", "direction",
                     "origin_station_id", "destination_station_id",
                     "first_train_time", "last_train_time", "frequency_min"],
                    rows)
    print(f"  schema1.national_rail_schedules: {n} rows")

    # Operating days
    day_rows = []
    for s in data:
        for day in s["operates_on"]:
            day_rows.append((s["schedule_id"], day))
    n = insert_many(cur, "schema1.national_rail_schedule_days", ["schedule_id", "day_of_week"], day_rows)
    print(f"  schema1.national_rail_schedule_days: {n} rows")

    # Stops — express services also have "passed_through_stations" that are skipped
    stop_rows = []
    for s in data:
        times = s["travel_time_from_origin_min"]
        passed = set(s.get("passed_through_stations", []))
        for order, station_id in enumerate(s["stops_in_order"], start=1):
            stop_rows.append((
                s["schedule_id"],
                station_id,
                order,
                times[station_id],
                'stop',  # stop_type — these are actual stops
            ))
        # Also record the skipped stations (order=0 marks them as pass-through)
        for station_id in passed:
            stop_rows.append((
                s["schedule_id"],
                station_id,
                0,
                0,
                'pass_through',
            ))
    n = insert_many(cur, "schema1.national_rail_schedule_stops",
                    ["schedule_id", "station_id", "stop_order",
                     "travel_time_from_origin_min", "stop_type"],
                    stop_rows)
    print(f"  schema1.national_rail_schedule_stops: {n} rows")

    # Fare classes
    fare_rows = []
    for s in data:
        for fare_class, details in s["fare_classes"].items():
            fare_rows.append((
                s["schedule_id"],
                fare_class,
                details["base_fare_usd"],
                details["per_stop_rate_usd"],
            ))
    n = insert_many(cur, "schema1.national_rail_fare_classes",
                    ["schedule_id", "fare_class", "base_fare_usd", "per_stop_rate_usd"],
                    fare_rows)
    print(f"  schema1.national_rail_fare_classes: {n} rows")


def seed_seat_layouts(cur):
    data = load("national_rail_seat_layouts.json")

    rows = []
    for layout in data:
        for coach_info in layout["coaches"]:
            for seat in coach_info["seats"]:
                rows.append((
                    layout["layout_id"],
                    layout["schedule_id"],
                    coach_info["coach"],
                    coach_info["fare_class"],
                    seat["seat_id"],
                    seat["row"],
                    seat["column"],
                ))
    n = insert_many(cur, "schema1.national_rail_seat_layouts",
                    ["layout_id", "schedule_id", "coach", "fare_class",
                     "seat_id", "row_num", "col_name"],
                    rows)
    print(f"  schema1.national_rail_seat_layouts: {n} rows")


def seed_users(cur):
    """
    Two-step insert:
      1. schema1.users  — profile fields only (no password)
      2. schema2.credentials — Argon2id hash of the plaintext password
    """
    data = load("registered_users.json")

    # Step 1: profile rows (no password column)
    user_rows = [
        (
            u["user_id"],
            u["full_name"],
            u["email"],
            u.get("phone"),
            u.get("date_of_birth"),
            u.get("secret_question"),
            u.get("secret_answer"),
            u["registered_at"],
            u["is_active"],
        )
        for u in data
    ]
    n = insert_many(cur, "schema1.users",
                    ["user_id", "full_name", "email", "phone",
                     "date_of_birth", "secret_question", "secret_answer",
                     "registered_at", "is_active"],
                    user_rows)
    print(f"  schema1.users: {n} rows")

    # Step 2: Argon2id hashes
    cred_rows = [
        (u["user_id"], _ph.hash(u["password"]))
        for u in data
    ]
    n = insert_many(cur, "schema2.credentials",
                    ["user_id", "stored_hash"],
                    cred_rows)
    print(f"  schema2.credentials: {n} rows")


def seed_national_rail_bookings(cur):
    data = load("bookings.json")

    rows = [
        (
            b["booking_id"],
            b["user_id"],
            b["schedule_id"],
            b["origin_station_id"],
            b["destination_station_id"],
            b["travel_date"],
            b["departure_time"],
            b["ticket_type"],
            b["fare_class"],
            b.get("coach"),
            b.get("seat_id"),
            b.get("stops_travelled"),
            b["amount_usd"],
            b["status"],
            b.get("booked_at"),
            b.get("travelled_at"),
        )
        for b in data
    ]
    n = insert_many(cur, "schema1.national_rail_bookings",
                    ["booking_id", "user_id", "schedule_id",
                     "origin_station_id", "destination_station_id",
                     "travel_date", "departure_time", "ticket_type", "fare_class",
                     "coach", "seat_id", "stops_travelled",
                     "amount_usd", "status", "booked_at", "travelled_at"],
                    rows)
    print(f"  schema1.national_rail_bookings: {n} rows")


def seed_metro_travels(cur):
    data = load("metro_travel_history.json")

    rows = [
        (
            t["trip_id"],
            t["user_id"],
            t["schedule_id"],
            t["origin_station_id"],
            t["destination_station_id"],
            t["travel_date"],
            t["ticket_type"],
            t.get("day_pass_ref"),       # None for the primary pass, trip_id for legs
            t.get("stops_travelled"),    # None for day_pass legs
            t["amount_usd"],
            t["status"],
            t.get("purchased_at"),
            t.get("travelled_at"),
        )
        for t in data
    ]
    n = insert_many(cur, "schema1.metro_travels",
                    ["trip_id", "user_id", "schedule_id",
                     "origin_station_id", "destination_station_id",
                     "travel_date", "ticket_type", "day_pass_ref",
                     "stops_travelled", "amount_usd", "status",
                     "purchased_at", "travelled_at"],
                    rows)
    print(f"  schema1.metro_travels: {n} rows")


def seed_payments(cur):
    data = load("payments.json")

    rows = [
        (
            p["payment_id"],
            p["booking_id"],
            _infer_booking_type(p["booking_id"]),
            p["amount_usd"],
            p["method"],
            p["status"],
            p.get("paid_at"),
        )
        for p in data
    ]
    n = insert_many(cur, "schema1.payments",
                    ["payment_id", "booking_id", "booking_type", "amount_usd",
                     "method", "status", "paid_at"],
                    rows)
    print(f"  schema1.payments: {n} rows")


def seed_feedback(cur):
    data = load("feedback.json")

    rows = [
        (
            f["feedback_id"],
            f["booking_id"],
            _infer_booking_type(f["booking_id"]),
            f["user_id"],
            f["rating"],
            f.get("comment"),    # may be None
            f["submitted_at"],
        )
        for f in data
    ]
    n = insert_many(cur, "schema1.feedback",
                    ["feedback_id", "booking_id", "booking_type", "user_id",
                     "rating", "comment", "submitted_at"],
                    rows)
    print(f"  schema1.feedback: {n} rows")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to PostgreSQL...")
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Seeding tables (dependency order):")
        # Stations must come before schedules (foreign keys)
        seed_metro_stations(cur)
        seed_national_rail_stations(cur)
        # Schedules depend on stations
        seed_metro_schedules(cur)
        seed_national_rail_schedules(cur)
        seed_seat_layouts(cur)
        # Users must come before bookings/travels/feedback
        # seed_users inserts into schema1.users AND schema2.credentials
        seed_users(cur)
        # Bookings depend on users, stations, schedules
        seed_national_rail_bookings(cur)
        seed_metro_travels(cur)
        # Payments and feedback reference booking IDs (soft refs, no FK constraint)
        seed_payments(cur)
        seed_feedback(cur)
        conn.commit()
        print("\nAll done. Database seeded successfully.")
    except Exception as e:
        conn.rollback()
        print(f"\nError: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()

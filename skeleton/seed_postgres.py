"""
Seed PostgreSQL with all TransitFlow mock data from train-mock-data/.

Usage:
    python skeleton/seed_postgres.py

Run AFTER docker-compose up -d.
You must first design and create your tables in databases/relational/schema.sql.
Safe to re-run: implement your inserts with ON CONFLICT DO NOTHING.
"""

import json
import os
import sys

import psycopg2
from psycopg2.extras import execute_values

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
    return cur.rowcount


# ── seeders ──────────────────────────────────────────────────────────────────

def seed_national_rail_stations(cur):
    data = load("national_rail_stations.json")
    rows = [
        (
            s["station_id"],
            s["name"],
            s["is_interchange_metro"],
            s.get("interchange_metro_station_id"),
        )
        for s in data
    ]
    n = insert_many(cur, "national_rail_stations",
                    ["station_id", "name", "is_interchange_metro",
                     "interchange_metro_station_id"], rows)
    print(f"  national_rail_stations: {n} rows")


def seed_metro_stations(cur):
    data = load("metro_stations.json")
    rows = [
        (
            s["station_id"],
            s["name"],
            s["is_interchange_metro"],
            s["is_interchange_national_rail"],
            s.get("interchange_national_rail_station_id"),
        )
        for s in data
    ]
    n = insert_many(cur, "metro_stations",
                    ["station_id", "name", "is_interchange_metro",
                     "is_interchange_national_rail",
                     "interchange_national_rail_station_id"], rows)
    print(f"  metro_stations: {n} rows")


def seed_national_rail_schedules(cur):
    data = load("national_rail_schedules.json")

    # Main schedule rows
    sched_rows = [
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
            s["fare_classes"]["standard"]["base_fare_usd"],
            s["fare_classes"]["standard"]["per_stop_rate_usd"],
            s["fare_classes"]["first"]["base_fare_usd"],
            s["fare_classes"]["first"]["per_stop_rate_usd"],
        )
        for s in data
    ]
    n = insert_many(cur, "national_rail_schedules",
                    ["schedule_id", "line", "service_type", "direction",
                     "origin_station_id", "destination_station_id",
                     "first_train_time", "last_train_time", "frequency_min",
                     "standard_base_fare_usd", "standard_per_stop_usd",
                     "first_base_fare_usd", "first_per_stop_usd"], sched_rows)
    print(f"  national_rail_schedules: {n} rows")

    # Schedule stops
    stop_rows = []
    for s in data:
        for order, station_id in enumerate(s["stops_in_order"]):
            travel_time = s["travel_time_from_origin_min"][station_id]
            stop_rows.append((s["schedule_id"], station_id, order, travel_time))
    n = insert_many(cur, "national_rail_schedule_stops",
                    ["schedule_id", "station_id", "stop_order",
                     "travel_time_from_origin_min"], stop_rows)
    print(f"  national_rail_schedule_stops: {n} rows")

    # Operating days
    day_rows = []
    for s in data:
        for day in s["operates_on"]:
            day_rows.append((s["schedule_id"], day))
    n = insert_many(cur, "national_rail_schedule_days",
                    ["schedule_id", "day_of_week"], day_rows)
    print(f"  national_rail_schedule_days: {n} rows")


def seed_metro_schedules(cur):
    data = load("metro_schedules.json")

    # Main schedule rows
    sched_rows = [
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
    n = insert_many(cur, "metro_schedules",
                    ["schedule_id", "line", "direction",
                     "origin_station_id", "destination_station_id",
                     "first_train_time", "last_train_time",
                     "base_fare_usd", "per_stop_rate_usd",
                     "frequency_min"], sched_rows)
    print(f"  metro_schedules: {n} rows")

    # Schedule stops
    stop_rows = []
    for s in data:
        for order, station_id in enumerate(s["stops_in_order"]):
            travel_time = s["travel_time_from_origin_min"][station_id]
            stop_rows.append((s["schedule_id"], station_id, order, travel_time))
    n = insert_many(cur, "metro_schedule_stops",
                    ["schedule_id", "station_id", "stop_order",
                     "travel_time_from_origin_min"], stop_rows)
    print(f"  metro_schedule_stops: {n} rows")

    # Operating days
    day_rows = []
    for s in data:
        for day in s["operates_on"]:
            day_rows.append((s["schedule_id"], day))
    n = insert_many(cur, "metro_schedule_days",
                    ["schedule_id", "day_of_week"], day_rows)
    print(f"  metro_schedule_days: {n} rows")


def seed_seat_layouts(cur):
    data = load("national_rail_seat_layouts.json")
    rows = []
    for layout in data:
        for coach in layout["coaches"]:
            for seat in coach["seats"]:
                rows.append((
                    layout["layout_id"],
                    layout["schedule_id"],
                    coach["coach"],
                    coach["fare_class"],
                    seat["seat_id"],
                    seat["row"],
                    seat["column"],
                ))
    n = insert_many(cur, "national_rail_seat_layouts",
                    ["layout_id", "schedule_id", "coach", "fare_class",
                     "seat_id", "seat_row", "seat_column"], rows)
    print(f"  national_rail_seat_layouts: {n} rows")


def seed_users(cur):
    data = load("registered_users.json")
    rows = [
        (
            u["user_id"],
            u["full_name"],
            u["email"],
            u["password"],
            u.get("phone"),
            u.get("date_of_birth"),
            u.get("secret_question"),
            u.get("secret_answer"),
            u["registered_at"],
            u["is_active"],
        )
        for u in data
    ]
    n = insert_many(cur, "users",
                    ["user_id", "full_name", "email", "password", "phone",
                     "date_of_birth", "secret_question", "secret_answer",
                     "registered_at", "is_active"], rows)
    print(f"  users: {n} rows")


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
            b["booked_at"],
            b.get("travelled_at"),
        )
        for b in data
    ]
    n = insert_many(cur, "national_rail_bookings",
                    ["booking_id", "user_id", "schedule_id",
                     "origin_station_id", "destination_station_id",
                     "travel_date", "departure_time", "ticket_type",
                     "fare_class", "coach", "seat_id", "stops_travelled",
                     "amount_usd", "status", "booked_at", "travelled_at"], rows)
    print(f"  national_rail_bookings: {n} rows")


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
            t.get("day_pass_ref"),
            t.get("stops_travelled"),
            t["amount_usd"],
            t["status"],
            t.get("purchased_at"),
            t.get("travelled_at"),
        )
        for t in data
    ]
    n = insert_many(cur, "metro_travels",
                    ["trip_id", "user_id", "schedule_id",
                     "origin_station_id", "destination_station_id",
                     "travel_date", "ticket_type", "day_pass_ref",
                     "stops_travelled", "amount_usd", "status",
                     "purchased_at", "travelled_at"], rows)
    print(f"  metro_travels: {n} rows")


def seed_payments(cur):
    data = load("payments.json")
    rows = [
        (
            p["payment_id"],
            p["booking_id"],
            p["amount_usd"],
            p["method"],
            p["status"],
            p["paid_at"],
        )
        for p in data
    ]
    n = insert_many(cur, "payments",
                    ["payment_id", "booking_id", "amount_usd",
                     "method", "status", "paid_at"], rows)
    print(f"  payments: {n} rows")


def seed_feedback(cur):
    data = load("feedback.json")
    rows = [
        (
            f["feedback_id"],
            f["booking_id"],
            f["user_id"],
            f["rating"],
            f.get("comment"),
            f["submitted_at"],
        )
        for f in data
    ]
    n = insert_many(cur, "feedback",
                    ["feedback_id", "booking_id", "user_id",
                     "rating", "comment", "submitted_at"], rows)
    print(f"  feedback: {n} rows")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    print("Connecting to PostgreSQL...")
    conn = connect()
    conn.autocommit = False
    cur = conn.cursor()

    try:
        print("Seeding tables (dependency order):")
        seed_national_rail_stations(cur)
        seed_metro_stations(cur)
        seed_national_rail_schedules(cur)
        seed_metro_schedules(cur)
        seed_seat_layouts(cur)
        seed_users(cur)
        seed_national_rail_bookings(cur)
        seed_metro_travels(cur)
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
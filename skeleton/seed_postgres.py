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


# ── seeders ──────────────────────────────────────────────────────────────────

def seed_metro_stations(cur):
    """Returns metro_station_map: {'MS01': <serial_id>, ...}"""
    data = load("metro_stations.json")

    rows = [
        (
            s["station_id"],   # stored as station_code
            s["name"],
            s["is_interchange_metro"],
            s["is_interchange_national_rail"],
            s.get("interchange_national_rail_station_id"),
        )
        for s in data
    ]
    n = insert_many(cur, "schema1.metro_stations",
                    ["station_code", "name", "is_interchange_metro",
                     "is_interchange_national_rail", "interchange_national_rail_station_id"],
                    rows)
    print(f"  schema1.metro_stations: {n} rows")

    # Build station_code → SERIAL id mapping via SELECT (re-run safe)
    cur.execute("SELECT station_code, id FROM schema1.metro_stations")
    metro_station_map = {row[0]: row[1] for row in cur.fetchall()}

    # Lines per station — station_id FK is now INTEGER
    line_rows = [
        (metro_station_map[s["station_id"]], line)
        for s in data
        for line in s["lines"]
    ]
    n = insert_many(cur, "schema1.metro_station_lines", ["station_id", "line"], line_rows)
    print(f"  schema1.metro_station_lines: {n} rows")

    return metro_station_map


def seed_national_rail_stations(cur):
    """Returns nr_station_map: {'NR01': <serial_id>, ...}"""
    data = load("national_rail_stations.json")

    rows = [
        (
            s["station_id"],   # stored as station_code
            s["name"],
            s["is_interchange_national_rail"],
            s["is_interchange_metro"],
            s.get("interchange_metro_station_id"),
        )
        for s in data
    ]
    n = insert_many(cur, "schema1.national_rail_stations",
                    ["station_code", "name", "is_interchange_national_rail",
                     "is_interchange_metro", "interchange_metro_station_id"],
                    rows)
    print(f"  schema1.national_rail_stations: {n} rows")

    # Build station_code → SERIAL id mapping via SELECT (re-run safe)
    cur.execute("SELECT station_code, id FROM schema1.national_rail_stations")
    nr_station_map = {row[0]: row[1] for row in cur.fetchall()}

    line_rows = [
        (nr_station_map[s["station_id"]], line)
        for s in data
        for line in s["lines"]
    ]
    n = insert_many(cur, "schema1.national_rail_station_lines", ["station_id", "line"], line_rows)
    print(f"  schema1.national_rail_station_lines: {n} rows")

    return nr_station_map


def seed_metro_schedules(cur, metro_station_map):
    """Returns metro_schedule_map: {'MS_SCH01': <serial_id>, ...}"""
    data = load("metro_schedules.json")

    rows = [
        (
            s["schedule_id"],   # stored as schedule_code
            s["line"],
            s["direction"],
            metro_station_map[s["origin_station_id"]],
            metro_station_map[s["destination_station_id"]],
            s["first_train_time"],
            s["last_train_time"],
            s["base_fare_usd"],
            s["per_stop_rate_usd"],
            s["frequency_min"],
        )
        for s in data
    ]
    n = insert_many(cur, "schema1.metro_schedules",
                    ["schedule_code", "line", "direction", "origin_station_id",
                     "destination_station_id", "first_train_time", "last_train_time",
                     "base_fare_usd", "per_stop_rate_usd", "frequency_min"],
                    rows)
    print(f"  schema1.metro_schedules: {n} rows")

    # Build schedule_code → SERIAL id mapping via SELECT (re-run safe)
    cur.execute("SELECT schedule_code, id FROM schema1.metro_schedules")
    metro_schedule_map = {row[0]: row[1] for row in cur.fetchall()}

    # Operating days — schedule_id FK is now INTEGER
    day_rows = [
        (metro_schedule_map[s["schedule_id"]], day)
        for s in data
        for day in s["operates_on"]
    ]
    n = insert_many(cur, "schema1.metro_schedule_days", ["schedule_id", "day_of_week"], day_rows)
    print(f"  schema1.metro_schedule_days: {n} rows")

    # Stops — both schedule_id and station_id are now INTEGER
    stop_rows = []
    for s in data:
        sched_id = metro_schedule_map[s["schedule_id"]]
        times = s["travel_time_from_origin_min"]
        for order, station_code in enumerate(s["stops_in_order"], start=1):
            stop_rows.append((
                sched_id,
                metro_station_map[station_code],
                order,
                times[station_code],
            ))
    n = insert_many(cur, "schema1.metro_schedule_stops",
                    ["schedule_id", "station_id", "stop_order",
                     "travel_time_from_origin_min"],
                    stop_rows)
    print(f"  schema1.metro_schedule_stops: {n} rows")

    return metro_schedule_map


def seed_national_rail_schedules(cur, nr_station_map):
    """Returns nr_schedule_map: {'NR_SCH01': <serial_id>, ...}"""
    data = load("national_rail_schedules.json")

    rows = [
        (
            s["schedule_id"],   # stored as schedule_code
            s["line"],
            s["service_type"],
            s["direction"],
            nr_station_map[s["origin_station_id"]],
            nr_station_map[s["destination_station_id"]],
            s["first_train_time"],
            s["last_train_time"],
            s["frequency_min"],
        )
        for s in data
    ]
    n = insert_many(cur, "schema1.national_rail_schedules",
                    ["schedule_code", "line", "service_type", "direction",
                     "origin_station_id", "destination_station_id",
                     "first_train_time", "last_train_time", "frequency_min"],
                    rows)
    print(f"  schema1.national_rail_schedules: {n} rows")

    # Build schedule_code → SERIAL id mapping via SELECT (re-run safe)
    cur.execute("SELECT schedule_code, id FROM schema1.national_rail_schedules")
    nr_schedule_map = {row[0]: row[1] for row in cur.fetchall()}

    # Operating days
    day_rows = [
        (nr_schedule_map[s["schedule_id"]], day)
        for s in data
        for day in s["operates_on"]
    ]
    n = insert_many(cur, "schema1.national_rail_schedule_days", ["schedule_id", "day_of_week"], day_rows)
    print(f"  schema1.national_rail_schedule_days: {n} rows")

    # Stops — express services also have "passed_through_stations"
    stop_rows = []
    for s in data:
        sched_id = nr_schedule_map[s["schedule_id"]]
        times = s["travel_time_from_origin_min"]
        passed = set(s.get("passed_through_stations", []))
        for order, station_code in enumerate(s["stops_in_order"], start=1):
            stop_rows.append((
                sched_id,
                nr_station_map[station_code],
                order,
                times[station_code],
                'stop',
            ))
        for station_code in passed:
            stop_rows.append((
                sched_id,
                nr_station_map[station_code],
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
        sched_id = nr_schedule_map[s["schedule_id"]]
        for fare_class, details in s["fare_classes"].items():
            fare_rows.append((
                sched_id,
                fare_class,
                details["base_fare_usd"],
                details["per_stop_rate_usd"],
            ))
    n = insert_many(cur, "schema1.national_rail_fare_classes",
                    ["schedule_id", "fare_class", "base_fare_usd", "per_stop_rate_usd"],
                    fare_rows)
    print(f"  schema1.national_rail_fare_classes: {n} rows")

    return nr_schedule_map


def seed_seat_layouts(cur, nr_schedule_map):
    data = load("national_rail_seat_layouts.json")

    rows = []
    for layout in data:
        sched_id = nr_schedule_map[layout["schedule_id"]]
        for coach_info in layout["coaches"]:
            for seat in coach_info["seats"]:
                rows.append((
                    layout["layout_id"],
                    sched_id,
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
    Returns user_code_to_uuid: {'RU01': <uuid>, ...}

    Two-step insert:
      1. schema1.users  — profile fields only (no password); UUID PK auto-generated,
                          retrieved via RETURNING user_id
      2. schema2.credentials — Argon2id hash of the plaintext password
    """
    data = load("registered_users.json")
    user_code_to_uuid = {}

    for u in data:
        cur.execute("""
            INSERT INTO schema1.users
                (full_name, email, phone, date_of_birth,
                 secret_question, secret_answer, registered_at, is_active)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING user_id
        """, (
            u["full_name"], u["email"], u.get("phone"), u.get("date_of_birth"),
            u.get("secret_question"), u.get("secret_answer"),
            u["registered_at"], u["is_active"],
        ))
        result = cur.fetchone()
        if result:
            user_code_to_uuid[u["user_id"]] = result[0]

    print(f"  schema1.users: {len(user_code_to_uuid)} rows")

    # Credentials — user_id FK is now UUID
    cred_rows = [
        (user_code_to_uuid[u["user_id"]], _ph.hash(u["password"]))
        for u in data
        if u["user_id"] in user_code_to_uuid
    ]
    n = insert_many(cur, "schema2.credentials", ["user_id", "stored_hash"], cred_rows)
    print(f"  schema2.credentials: {n} rows")

    return user_code_to_uuid


def seed_national_rail_bookings(cur, user_code_to_uuid, nr_schedule_map, nr_station_map):
    """
    Returns booking_code_to_uuid: {'BK001': {'uuid': <uuid>, 'type': 'rail'}, ...}
    UUID PK auto-generated, retrieved via RETURNING booking_id.
    """
    data = load("bookings.json")
    booking_code_to_uuid = {}

    for b in data:
        cur.execute("""
            INSERT INTO schema1.national_rail_bookings
                (user_id, schedule_id, origin_station_id, destination_station_id,
                 travel_date, departure_time, ticket_type, fare_class,
                 coach, seat_id, stops_travelled, amount_usd, status, booked_at, travelled_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING booking_id
        """, (
            user_code_to_uuid[b["user_id"]],
            nr_schedule_map[b["schedule_id"]],
            nr_station_map[b["origin_station_id"]],
            nr_station_map[b["destination_station_id"]],
            b["travel_date"], b["departure_time"],
            b["ticket_type"], b["fare_class"],
            b.get("coach"), b.get("seat_id"), b.get("stops_travelled"),
            b["amount_usd"], b["status"],
            b.get("booked_at"), b.get("travelled_at"),
        ))
        result = cur.fetchone()
        if result:
            booking_code_to_uuid[b["booking_id"]] = {"uuid": result[0], "type": "rail"}

    print(f"  schema1.national_rail_bookings: {len(booking_code_to_uuid)} rows")
    return booking_code_to_uuid


def seed_metro_travels(cur, user_code_to_uuid, metro_schedule_map, metro_station_map):
    """
    Returns trip_code_to_uuid: {'MT001': {'uuid': <uuid>, 'type': 'metro'}, ...}
    UUID PK auto-generated, retrieved via RETURNING trip_id.
    day_pass_ref is inserted as NULL first, then resolved in a second UPDATE pass
    after all trips are in the mapping.
    """
    data = load("metro_travel_history.json")
    trip_code_to_uuid = {}

    # Pass 1: insert all trips with day_pass_ref = NULL
    for t in data:
        cur.execute("""
            INSERT INTO schema1.metro_travels
                (user_id, schedule_id, origin_station_id, destination_station_id,
                 travel_date, ticket_type, day_pass_ref,
                 stops_travelled, amount_usd, status, purchased_at, travelled_at)
            VALUES (%s, %s, %s, %s, %s, %s, NULL, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING trip_id
        """, (
            user_code_to_uuid[t["user_id"]],
            metro_schedule_map[t["schedule_id"]],
            metro_station_map[t["origin_station_id"]],
            metro_station_map[t["destination_station_id"]],
            t["travel_date"], t["ticket_type"],
            t.get("stops_travelled"),
            t["amount_usd"], t["status"],
            t.get("purchased_at"), t.get("travelled_at"),
        ))
        result = cur.fetchone()
        if result:
            trip_code_to_uuid[t["trip_id"]] = {"uuid": result[0], "type": "metro"}

    print(f"  schema1.metro_travels: {len(trip_code_to_uuid)} rows")

    # Pass 2: resolve day_pass_ref now that the full mapping is built
    resolved = 0
    for t in data:
        ref_code = t.get("day_pass_ref")
        if ref_code and t["trip_id"] in trip_code_to_uuid and ref_code in trip_code_to_uuid:
            cur.execute(
                "UPDATE schema1.metro_travels SET day_pass_ref = %s WHERE trip_id = %s",
                (trip_code_to_uuid[ref_code]["uuid"], trip_code_to_uuid[t["trip_id"]]["uuid"]),
            )
            resolved += 1
    if resolved:
        print(f"  schema1.metro_travels: {resolved} day_pass_ref resolved")

    return trip_code_to_uuid


def seed_payments(cur, booking_code_to_uuid, trip_code_to_uuid):
    data = load("payments.json")

    rows = []
    for p in data:
        code = p["booking_id"]
        if code in booking_code_to_uuid:
            entry = booking_code_to_uuid[code]
        elif code in trip_code_to_uuid:
            entry = trip_code_to_uuid[code]
        else:
            continue
        rows.append((
            entry["uuid"], entry["type"],
            p["amount_usd"], p["method"], p["status"], p.get("paid_at"),
        ))

    n = insert_many(cur, "schema1.payments",
                    ["booking_id", "booking_type", "amount_usd", "method", "status", "paid_at"],
                    rows)
    print(f"  schema1.payments: {n} rows")


def seed_feedback(cur, user_code_to_uuid, booking_code_to_uuid, trip_code_to_uuid):
    data = load("feedback.json")

    rows = []
    for f in data:
        code = f["booking_id"]
        if code in booking_code_to_uuid:
            entry = booking_code_to_uuid[code]
        elif code in trip_code_to_uuid:
            entry = trip_code_to_uuid[code]
        else:
            continue
        rows.append((
            entry["uuid"], entry["type"],
            user_code_to_uuid[f["user_id"]],
            f["rating"], f.get("comment"), f["submitted_at"],
        ))

    n = insert_many(cur, "schema1.feedback",
                    ["booking_id", "booking_type", "user_id",
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
        metro_station_map = seed_metro_stations(cur)
        nr_station_map    = seed_national_rail_stations(cur)
        # Schedules depend on stations
        metro_schedule_map = seed_metro_schedules(cur, metro_station_map)
        nr_schedule_map    = seed_national_rail_schedules(cur, nr_station_map)
        seed_seat_layouts(cur, nr_schedule_map)
        # Users must come before bookings/travels/feedback
        user_code_to_uuid = seed_users(cur)
        # Bookings depend on users, stations, schedules
        booking_code_to_uuid = seed_national_rail_bookings(
            cur, user_code_to_uuid, nr_schedule_map, nr_station_map
        )
        trip_code_to_uuid = seed_metro_travels(
            cur, user_code_to_uuid, metro_schedule_map, metro_station_map
        )
        # Payments and feedback resolve booking UUIDs from the two mapping dicts
        seed_payments(cur, booking_code_to_uuid, trip_code_to_uuid)
        seed_feedback(cur, user_code_to_uuid, booking_code_to_uuid, trip_code_to_uuid)
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

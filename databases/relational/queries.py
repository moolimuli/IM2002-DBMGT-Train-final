"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================
This module handles all queries to PostgreSQL.

TWO ROLES ARE SERVED HERE:
  1. Relational  → dual-network transit (metro + national rail),
                   availability, fares, bookings, seat selection
  2. Vector      → policy document similarity search (pgvector)

STUDENT TASK
------------
Design your schema in databases/relational/schema.sql, seed it with
skeleton/seed_postgres.py, then implement the query functions below.

Functions prefixed with `query_`  are read-only lookups called by the agent.
Functions prefixed with `execute_` are write operations (booking/cancellation).

The vector functions (query_policy_vector_search, store_policy_document)
are already implemented — do not modify them.
"""

from __future__ import annotations

import json
import random
import string
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD


def _connect():
    """Return a new psycopg2 connection with autocommit enabled."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    return conn


def _gen_booking_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"BK-{suffix}"


def _gen_payment_id() -> str:
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"PM-{suffix}"


# ── Example ───────────────────────────────────────────────────────────────────
# The block below shows the query pattern: open a cursor, run SQL, return rows.
# Use _connect() for read-only queries; for write operations use a manual
# connection with conn.commit() / conn.rollback() (see execute_booking below).

def example_query() -> dict:
    """Example: returns the name of the connected database."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT current_database() AS db;")
            return dict(cur.fetchone())

# ── NATIONAL RAIL AVAILABILITY ────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    """
    Return national rail schedules that serve both origin and destination stations
    in the correct order, along with seat occupancy for the requested travel date.
    """
    sql = """
        SELECT
            s.schedule_id,
            s.line,
            s.service_type,
            s.direction,
            s.first_train_time::text,
            s.last_train_time::text,
            s.frequency_min,
            s.operates_on,
            orig.name   AS origin_name,
            dest.name   AS destination_name,
            (array_position(s.stops_in_order, %(dest)s)
             - array_position(s.stops_in_order, %(orig)s)) AS stops_travelled,
            ((s.travel_time_from_origin_min->>%(dest)s)::int
             - (s.travel_time_from_origin_min->>%(orig)s)::int) AS travel_time_min
        FROM national_rail_schedules s
        JOIN national_rail_stations orig ON orig.station_id = %(orig)s
        JOIN national_rail_stations dest ON dest.station_id = %(dest)s
        WHERE %(orig)s = ANY(s.stops_in_order)
          AND %(dest)s = ANY(s.stops_in_order)
          AND array_position(s.stops_in_order, %(orig)s)
            < array_position(s.stops_in_order, %(dest)s)
        ORDER BY s.first_train_time
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, {"orig": origin_id, "dest": destination_id})
            schedules = [dict(r) for r in cur.fetchall()]

    if travel_date:
        for sched in schedules:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT COUNT(*) FROM national_rail_bookings
                        WHERE schedule_id = %s AND travel_date = %s
                          AND origin_station_id = %s AND destination_station_id = %s
                          AND status != 'cancelled'
                        """,
                        (sched["schedule_id"], travel_date, origin_id, destination_id),
                    )
                    sched["bookings_on_date"] = cur.fetchone()[0]

    # Attach available fare classes
    schedule_ids = [s["schedule_id"] for s in schedules]
    if schedule_ids:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM national_rail_fare_classes WHERE schedule_id = ANY(%s)",
                    (schedule_ids,),
                )
                fare_rows = cur.fetchall()
        fare_map: dict[str, list] = {}
        for fr in fare_rows:
            fare_map.setdefault(fr["schedule_id"], []).append(dict(fr))
        for sched in schedules:
            sched["fare_classes"] = fare_map.get(sched["schedule_id"], [])

    return schedules


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """Calculate the fare for a national rail journey."""
    sql = """
        SELECT fare_class, base_fare_usd, per_stop_rate_usd,
               base_fare_usd + per_stop_rate_usd * %s AS total_fare_usd
        FROM national_rail_fare_classes
        WHERE schedule_id = %s AND fare_class = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (stops_travelled, schedule_id, fare_class))
            row = cur.fetchone()
    return dict(row) if row else None


# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """Return metro schedules that serve both origin and destination in the correct order."""
    sql = """
        SELECT
            s.schedule_id,
            s.line,
            s.direction,
            s.first_train_time::text,
            s.last_train_time::text,
            s.frequency_min,
            s.operates_on,
            s.base_fare_usd,
            s.per_stop_rate_usd,
            orig.name AS origin_name,
            dest.name AS destination_name,
            (array_position(s.stops_in_order, %(dest)s)
             - array_position(s.stops_in_order, %(orig)s)) AS stops_travelled,
            ((s.travel_time_from_origin_min->>%(dest)s)::int
             - (s.travel_time_from_origin_min->>%(orig)s)::int) AS travel_time_min
        FROM metro_schedules s
        JOIN metro_stations orig ON orig.station_id = %(orig)s
        JOIN metro_stations dest ON dest.station_id = %(dest)s
        WHERE %(orig)s = ANY(s.stops_in_order)
          AND %(dest)s = ANY(s.stops_in_order)
          AND array_position(s.stops_in_order, %(orig)s)
            < array_position(s.stops_in_order, %(dest)s)
        ORDER BY s.line, s.direction
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, {"orig": origin_id, "dest": destination_id})
            return [dict(r) for r in cur.fetchall()]


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """Calculate the metro fare for a single-ticket journey."""
    sql = """
        SELECT base_fare_usd, per_stop_rate_usd,
               base_fare_usd + per_stop_rate_usd * %s AS total_fare_usd
        FROM metro_schedules WHERE schedule_id = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (stops_travelled, schedule_id))
            row = cur.fetchone()
    return dict(row) if row else None


# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """Return available seats for a national rail journey on a given date."""
    sql = """
        SELECT s.seat_id, s.coach, s.row, s.col AS column
        FROM national_rail_seats s
        WHERE s.schedule_id = %s
          AND s.fare_class = %s
          AND (s.schedule_id, s.seat_id) NOT IN (
              SELECT schedule_id, seat_id
              FROM national_rail_bookings
              WHERE schedule_id = %s AND travel_date = %s AND status != 'cancelled'
          )
        ORDER BY s.coach, s.row, s.col
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id, fare_class, schedule_id, travel_date))
            return [dict(r) for r in cur.fetchall()]


def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """Select `count` seats as close together as possible."""
    if not available_seats or count <= 0:
        return []
    if count >= len(available_seats):
        return [s["seat_id"] for s in available_seats[:count]]

    from collections import defaultdict
    rows: dict[int, list[dict]] = defaultdict(list)
    for seat in available_seats:
        rows[seat["row"]].append(seat)

    for row_seats in sorted(rows.values(), key=lambda s: s[0]["row"]):
        if len(row_seats) >= count:
            return [s["seat_id"] for s in row_seats[:count]]

    sorted_seats = sorted(available_seats, key=lambda s: (s["row"], s["column"]))
    return [s["seat_id"] for s in sorted_seats[:count]]


# ── USER & BOOKING QUERIES ────────────────────────────────────────────────────

def query_user_profile(user_email: str) -> Optional[dict]:
    """Return a user's profile by email."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id, full_name, email, phone, date_of_birth, is_active "
                "FROM users WHERE email = %s",
                (user_email,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def query_user_bookings(user_email: str) -> dict:
    """Return a user's combined booking history (national rail + metro)."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT user_id FROM users WHERE email = %s", (user_email,))
            user = cur.fetchone()
    if not user:
        return {"national_rail": [], "metro": []}
    user_id = user["user_id"]

    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT b.booking_id, b.travel_date, b.departure_time::text,
                       b.ticket_type, b.fare_class, b.coach, b.seat_id,
                       b.stops_travelled, b.amount_usd, b.status,
                       orig.name AS origin_name, dest.name AS destination_name,
                       s.line, s.service_type
                FROM national_rail_bookings b
                JOIN national_rail_stations orig ON orig.station_id = b.origin_station_id
                JOIN national_rail_stations dest ON dest.station_id = b.destination_station_id
                JOIN national_rail_schedules s ON s.schedule_id = b.schedule_id
                WHERE b.user_id = %s
                ORDER BY b.travel_date DESC
                """,
                (user_id,),
            )
            nr_bookings = [dict(r) for r in cur.fetchall()]

            cur.execute(
                """
                SELECT t.trip_id, t.travel_date, t.ticket_type, t.stops_travelled,
                       t.amount_usd, t.status,
                       orig.name AS origin_name, dest.name AS destination_name,
                       s.line
                FROM metro_travels t
                JOIN metro_stations orig ON orig.station_id = t.origin_station_id
                JOIN metro_stations dest ON dest.station_id = t.destination_station_id
                JOIN metro_schedules s ON s.schedule_id = t.schedule_id
                WHERE t.user_id = %s
                ORDER BY t.travel_date DESC
                """,
                (user_id,),
            )
            metro_trips = [dict(r) for r in cur.fetchall()]

    return {"national_rail": nr_bookings, "metro": metro_trips}


def query_payment_info(booking_id: str) -> Optional[dict]:
    """Return payment record for a booking or metro trip."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM payments WHERE booking_id = %s ORDER BY paid_at DESC LIMIT 1",
                (booking_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


# ── TRANSACTIONAL OPERATIONS ──────────────────────────────────────────────────

def execute_booking(
    user_id: str,
    schedule_id: str,
    origin_station_id: str,
    destination_station_id: str,
    travel_date: str,
    fare_class: str,
    seat_id: str,
    ticket_type: str = "single",
) -> tuple[bool, dict | str]:
    """Create a national rail booking for a logged-in user."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get schedule stops to calculate stops_travelled
            cur.execute(
                "SELECT stops_in_order FROM national_rail_schedules WHERE schedule_id = %s",
                (schedule_id,),
            )
            row = cur.fetchone()
            if not row:
                return False, f"Schedule {schedule_id} not found."
            stops = row["stops_in_order"]
            if origin_station_id not in stops or destination_station_id not in stops:
                return False, "Origin or destination not served by this schedule."
            orig_idx = stops.index(origin_station_id)
            dest_idx = stops.index(destination_station_id)
            if orig_idx >= dest_idx:
                return False, "Origin must come before destination on this schedule."
            stops_travelled = dest_idx - orig_idx

            # Calculate fare
            cur.execute(
                "SELECT base_fare_usd, per_stop_rate_usd FROM national_rail_fare_classes "
                "WHERE schedule_id = %s AND fare_class = %s",
                (schedule_id, fare_class),
            )
            fare_row = cur.fetchone()
            if not fare_row:
                return False, f"Fare class '{fare_class}' not found for this schedule."
            amount_usd = float(fare_row["base_fare_usd"]) + float(fare_row["per_stop_rate_usd"]) * stops_travelled

            # Resolve seat
            if seat_id.lower() == "any":
                available = query_available_seats(schedule_id, travel_date, fare_class)
                if not available:
                    return False, "No available seats for this journey."
                chosen = auto_select_adjacent_seats(available, 1)
                seat_id = chosen[0]

            # Check seat not already booked
            cur.execute(
                "SELECT 1 FROM national_rail_bookings "
                "WHERE schedule_id = %s AND travel_date = %s AND seat_id = %s AND status != 'cancelled'",
                (schedule_id, travel_date, seat_id),
            )
            if cur.fetchone():
                return False, f"Seat {seat_id} is already booked on {travel_date}."

            # Get seat's coach
            cur.execute(
                "SELECT coach FROM national_rail_seats WHERE schedule_id = %s AND seat_id = %s",
                (schedule_id, seat_id),
            )
            seat_row = cur.fetchone()
            coach = seat_row["coach"] if seat_row else None

            # Get departure time
            cur.execute(
                "SELECT first_train_time FROM national_rail_schedules WHERE schedule_id = %s",
                (schedule_id,),
            )
            dep_time = cur.fetchone()["first_train_time"]

    # Insert booking and payment in a transaction
    booking_id = _gen_booking_id()
    payment_id = _gen_payment_id()
    now = datetime.now(timezone.utc)

    conn = psycopg2.connect(PG_DSN)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO national_rail_bookings
                    (booking_id, user_id, schedule_id, origin_station_id, destination_station_id,
                     travel_date, departure_time, ticket_type, fare_class, coach, seat_id,
                     stops_travelled, amount_usd, status, booked_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'confirmed',%s)
                """,
                (booking_id, user_id, schedule_id, origin_station_id, destination_station_id,
                 travel_date, dep_time, ticket_type, fare_class, coach, seat_id,
                 stops_travelled, amount_usd, now),
            )
            cur.execute(
                "INSERT INTO payments (payment_id, booking_id, amount_usd, method, status, paid_at) "
                "VALUES (%s,%s,%s,'credit_card','paid',%s)",
                (payment_id, booking_id, amount_usd, now),
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()

    return True, {
        "booking_id": booking_id,
        "schedule_id": schedule_id,
        "origin_station_id": origin_station_id,
        "destination_station_id": destination_station_id,
        "travel_date": travel_date,
        "fare_class": fare_class,
        "seat_id": seat_id,
        "coach": coach,
        "stops_travelled": stops_travelled,
        "amount_usd": amount_usd,
        "status": "confirmed",
    }


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """Cancel a national rail booking and calculate refund."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT b.*, s.service_type
                FROM national_rail_bookings b
                JOIN national_rail_schedules s ON s.schedule_id = b.schedule_id
                WHERE b.booking_id = %s
                """,
                (booking_id,),
            )
            booking = cur.fetchone()
    if not booking:
        return False, f"Booking {booking_id} not found."
    if booking["user_id"] != user_id:
        return False, "You can only cancel your own bookings."
    if booking["status"] == "cancelled":
        return False, "Booking is already cancelled."

    # Refund policy based on service type
    now = datetime.now(timezone.utc)
    travel_dt = datetime.combine(booking["travel_date"], booking["departure_time"])
    travel_dt = travel_dt.replace(tzinfo=timezone.utc)
    hours_until = (travel_dt - now).total_seconds() / 3600
    amount = float(booking["amount_usd"])

    if booking["service_type"] == "express":
        if hours_until >= 24:
            refund_pct, policy_note = 1.0, "RF002: >24h before express — 100% refund"
        elif hours_until >= 2:
            refund_pct, policy_note = 0.5, "RF002: 2–24h before express — 50% refund"
        else:
            refund_pct, policy_note = 0.0, "RF002: <2h before express — no refund"
    else:
        if hours_until >= 48:
            refund_pct, policy_note = 1.0, "RF001: >48h before — 100% refund"
        elif hours_until >= 24:
            refund_pct, policy_note = 0.75, "RF001: 24–48h before — 75% refund"
        elif hours_until >= 2:
            refund_pct, policy_note = 0.5, "RF001: 2–24h before — 50% refund"
        else:
            refund_pct, policy_note = 0.0, "RF001: <2h before — no refund"

    refund_amount = round(amount * refund_pct, 2)
    payment_id = _gen_payment_id()

    conn = psycopg2.connect(PG_DSN)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE national_rail_bookings SET status = 'cancelled' WHERE booking_id = %s",
                (booking_id,),
            )
            if refund_amount > 0:
                cur.execute(
                    "INSERT INTO payments (payment_id, booking_id, amount_usd, method, status, paid_at) "
                    "VALUES (%s,%s,%s,'credit_card','refunded',%s)",
                    (payment_id, booking_id, -refund_amount, now),
                )
        conn.commit()
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()

    return True, {
        "booking_id": booking_id,
        "status": "cancelled",
        "refund_amount_usd": refund_amount,
        "policy_note": policy_note,
    }


# ── AUTHENTICATION QUERIES ────────────────────────────────────────────────────

def register_user(
    email: str,
    first_name: str,
    surname: str,
    year_of_birth: int,
    password: str,
    secret_question: str,
    secret_answer: str,
) -> tuple[bool, str]:
    """Register a new user. Returns (True, user_id) or (False, error_message)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                return False, "An account with this email already exists."

    new_id = "RU" + "".join(random.choices(string.digits, k=4))
    full_name = f"{first_name} {surname}"
    dob = f"{year_of_birth}-01-01"

    conn = psycopg2.connect(PG_DSN)
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (user_id, full_name, email, password, date_of_birth,
                                   secret_question, secret_answer)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                (new_id, full_name, email, password, dob, secret_question, secret_answer),
            )
        conn.commit()
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()

    return True, new_id


def login_user(email: str, password: str) -> Optional[dict]:
    """Verify credentials. Returns user dict on success or None on failure."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id, email, full_name, phone, date_of_birth, is_active, password "
                "FROM users WHERE email = %s",
                (email,),
            )
            row = cur.fetchone()
    if not row or row["password"] != password or not row["is_active"]:
        return None
    result = dict(row)
    result.pop("password", None)
    name_parts = result["full_name"].split(" ", 1)
    result["first_name"] = name_parts[0]
    result["surname"] = name_parts[1] if len(name_parts) > 1 else ""
    return result


def get_user_secret_question(email: str) -> Optional[str]:
    """Return the secret question for a registered email, or None if not found."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT secret_question FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
    return row[0] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """Return True if the answer matches the stored secret answer (case-insensitive)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT secret_answer FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
    if not row:
        return False
    return row[0].strip().lower() == answer.strip().lower()


def update_password(email: str, new_password: str) -> bool:
    """Update the password for a user. Returns True if updated."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password = %s WHERE email = %s",
                (new_password, email),
            )
            return cur.rowcount > 0


# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────

def query_policy_vector_search(embedding: list[float], top_k: int = VECTOR_TOP_K) -> list[dict]:
    """
    Find the most relevant policy documents for a given query embedding.

    Args:
        embedding: Query vector from llm.embed(user_question)
        top_k:     Number of results to return

    Returns:
        List of dicts with title, category, content, and similarity score
    """
    sql = """
        SELECT
            title,
            category,
            content,
            1 - (embedding <=> %s::vector) AS similarity
        FROM policy_documents
        WHERE 1 - (embedding <=> %s::vector) > %s
        ORDER BY embedding <=> %s::vector
        LIMIT %s
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (vec_str, vec_str, VECTOR_SIMILARITY_THRESHOLD, vec_str, top_k))
            return [dict(row) for row in cur.fetchall()]


def store_policy_document(
    title: str,
    category: str,
    content: str,
    embedding: list[float],
    source_file: str = "",
) -> int:
    """
    Insert a policy document with its embedding into the database.
    Used by skeleton/seed_vectors.py — students don't need to call this directly.

    Returns:
        The new document's id
    """
    sql = """
        INSERT INTO policy_documents (title, category, content, embedding, source_file)
        VALUES (%s, %s, %s, %s::vector, %s)
        RETURNING id
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (title, category, content, vec_str, source_file))
            return cur.fetchone()[0]

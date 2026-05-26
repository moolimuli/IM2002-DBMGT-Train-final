"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================
This module handles all queries to PostgreSQL.

TWO ROLES ARE SERVED HERE:
  1. Relational  → dual-network transit (metro + national rail),
                   availability, fares, bookings, seat selection
  2. Vector      → policy document similarity search (pgvector)
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
    Return national rail schedules that serve both origin and destination
    in the correct order, with seat occupancy for the requested travel date.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Find schedules where origin stop comes before destination stop
            cur.execute("""
                SELECT
                    s.schedule_id,
                    s.line,
                    s.service_type,
                    s.direction,
                    s.first_train_time,
                    s.last_train_time,
                    s.frequency_min,
                    orig_s.name  AS origin_name,
                    dest_s.name  AS destination_name,
                    o.stop_order AS origin_stop_order,
                    d.stop_order AS destination_stop_order,
                    d.stop_order - o.stop_order AS stops_travelled,
                    d.travel_time_from_origin_min - o.travel_time_from_origin_min AS travel_time_min
                FROM national_rail_schedules s
                JOIN national_rail_schedule_stops o ON o.schedule_id = s.schedule_id
                    AND o.station_id = %s AND o.stop_type = 'stop'
                JOIN national_rail_schedule_stops d ON d.schedule_id = s.schedule_id
                    AND d.station_id = %s AND d.stop_type = 'stop'
                JOIN national_rail_stations orig_s ON orig_s.station_id = %s
                JOIN national_rail_stations dest_s ON dest_s.station_id = %s
                WHERE d.stop_order > o.stop_order
                ORDER BY s.schedule_id
            """, (origin_id, destination_id, origin_id, destination_id))
            schedules = [dict(r) for r in cur.fetchall()]

            if not schedules:
                return []

            # For each schedule, get fare classes and seat occupancy
            for sched in schedules:
                sid = sched["schedule_id"]
                stops = sched["stops_travelled"]

                # Fare classes
                cur.execute("""
                    SELECT fare_class, base_fare_usd, per_stop_rate_usd,
                           ROUND(base_fare_usd + per_stop_rate_usd * %s, 2) AS total_fare_usd
                    FROM national_rail_fare_classes
                    WHERE schedule_id = %s
                """, (stops, sid))
                sched["fares"] = [dict(r) for r in cur.fetchall()]

                # Seat occupancy on travel_date
                if travel_date:
                    cur.execute("""
                        SELECT
                            sl.fare_class,
                            COUNT(sl.seat_id) AS total_seats,
                            COUNT(b.seat_id)  AS booked_seats
                        FROM national_rail_seat_layouts sl
                        LEFT JOIN national_rail_bookings b
                            ON b.schedule_id = sl.schedule_id
                            AND b.seat_id    = sl.seat_id
                            AND b.coach      = sl.coach
                            AND b.travel_date = %s
                            AND b.status NOT IN ('cancelled')
                        WHERE sl.schedule_id = %s
                        GROUP BY sl.fare_class
                    """, (travel_date, sid))
                    sched["seat_availability"] = [dict(r) for r in cur.fetchall()]
                    sched["travel_date"] = travel_date

            # Warn if travel_date is in the past
            if travel_date:
                try:
                    td = datetime.fromisoformat(travel_date).date()
                    if td < datetime.now(timezone.utc).date():
                        for sched in schedules:
                            sched["date_warning"] = "Travel date is in the past"
                except ValueError:
                    pass

            return schedules


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    """Calculate the fare for a national rail journey."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    fare_class,
                    base_fare_usd,
                    per_stop_rate_usd,
                    ROUND(base_fare_usd + per_stop_rate_usd * %s, 2) AS total_fare_usd
                FROM national_rail_fare_classes
                WHERE schedule_id = %s AND fare_class = %s
            """, (stops_travelled, schedule_id, fare_class))
            row = cur.fetchone()
            return dict(row) if row else None


# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """Return metro schedules serving both origin and destination in the correct order."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    s.schedule_id,
                    s.line,
                    s.direction,
                    s.first_train_time,
                    s.last_train_time,
                    s.frequency_min,
                    s.base_fare_usd,
                    s.per_stop_rate_usd,
                    orig_st.name AS origin_name,
                    dest_st.name AS destination_name,
                    o.stop_order AS origin_stop_order,
                    d.stop_order AS destination_stop_order,
                    d.stop_order - o.stop_order AS stops_travelled,
                    d.travel_time_from_origin_min - o.travel_time_from_origin_min AS travel_time_min,
                    ROUND(s.base_fare_usd + s.per_stop_rate_usd * (d.stop_order - o.stop_order), 2) AS total_fare_usd
                FROM metro_schedules s
                JOIN metro_schedule_stops o ON o.schedule_id = s.schedule_id
                    AND o.station_id = %s
                JOIN metro_schedule_stops d ON d.schedule_id = s.schedule_id
                    AND d.station_id = %s
                JOIN metro_stations orig_st ON orig_st.station_id = %s
                JOIN metro_stations dest_st ON dest_st.station_id = %s
                WHERE d.stop_order > o.stop_order
                ORDER BY s.schedule_id
            """, (origin_id, destination_id, origin_id, destination_id))
            return [dict(r) for r in cur.fetchall()]


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """Calculate the metro fare for a single-ticket journey."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    base_fare_usd,
                    per_stop_rate_usd,
                    ROUND(base_fare_usd + per_stop_rate_usd * %s, 2) AS total_fare_usd
                FROM metro_schedules
                WHERE schedule_id = %s
            """, (stops_travelled, schedule_id))
            row = cur.fetchone()
            return dict(row) if row else None


# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """Return available seats for a national rail journey on a given date."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT sl.seat_id, sl.coach, sl.row_num AS row, sl.col_name AS column
                FROM national_rail_seat_layouts sl
                WHERE sl.schedule_id = %s
                  AND sl.fare_class  = %s
                  AND NOT EXISTS (
                      SELECT 1 FROM national_rail_bookings b
                      WHERE b.schedule_id  = sl.schedule_id
                        AND b.seat_id      = sl.seat_id
                        AND b.coach        = sl.coach
                        AND b.travel_date  = %s
                        AND b.status NOT IN ('cancelled')
                  )
                ORDER BY sl.row_num, sl.col_name
            """, (schedule_id, fare_class, travel_date))
            return [dict(r) for r in cur.fetchall()]


def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """Select seats that are as close together as possible."""
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
            cur.execute("""
                SELECT user_id, full_name, email, phone, date_of_birth,
                       registered_at, is_active
                FROM users
                WHERE email = %s
            """, (user_email,))
            row = cur.fetchone()
            return dict(row) if row else None


def query_user_bookings(user_email: str) -> dict:
    """Return a user's combined booking history (national rail + metro)."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get user_id first
            cur.execute("SELECT user_id FROM users WHERE email = %s", (user_email,))
            row = cur.fetchone()
            if not row:
                return {"national_rail": [], "metro": []}
            user_id = row["user_id"]

            # National rail bookings
            cur.execute("""
                SELECT
                    b.booking_id,
                    b.travel_date,
                    b.departure_time::text,
                    b.ticket_type,
                    b.fare_class,
                    b.coach,
                    b.seat_id,
                    b.stops_travelled,
                    b.amount_usd,
                    b.status,
                    b.booked_at,
                    b.travelled_at,
                    orig.name  AS origin_name,
                    dest.name  AS destination_name,
                    s.line,
                    s.service_type
                FROM national_rail_bookings b
                JOIN national_rail_stations orig ON orig.station_id = b.origin_station_id
                JOIN national_rail_stations dest ON dest.station_id = b.destination_station_id
                JOIN national_rail_schedules s ON s.schedule_id = b.schedule_id
                WHERE b.user_id = %s
                ORDER BY b.travel_date DESC
            """, (user_id,))
            nr_bookings = [dict(r) for r in cur.fetchall()]

            # Metro travel history
            cur.execute("""
                SELECT
                    t.trip_id,
                    t.travel_date,
                    t.ticket_type,
                    t.day_pass_ref,
                    t.stops_travelled,
                    t.amount_usd,
                    t.status,
                    t.purchased_at,
                    t.travelled_at,
                    orig.name AS origin_name,
                    dest.name AS destination_name,
                    s.line
                FROM metro_travels t
                JOIN metro_stations orig ON orig.station_id = t.origin_station_id
                JOIN metro_stations dest ON dest.station_id = t.destination_station_id
                JOIN metro_schedules s ON s.schedule_id = t.schedule_id
                WHERE t.user_id = %s
                ORDER BY t.travel_date DESC
            """, (user_id,))
            metro_travels = [dict(r) for r in cur.fetchall()]

            return {"national_rail": nr_bookings, "metro": metro_travels}


def query_payment_info(booking_id: str) -> Optional[dict]:
    """Return payment record for a booking or metro trip."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT payment_id, booking_id, booking_type, amount_usd, method, status, paid_at
                FROM payments
                WHERE booking_id = %s
            """, (booking_id,))
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
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get stop orders to calculate stops_travelled
            cur.execute("""
                SELECT o.stop_order AS orig_order, d.stop_order AS dest_order,
                       s.first_train_time::text AS departure_time
                FROM national_rail_schedule_stops o
                JOIN national_rail_schedule_stops d ON d.schedule_id = o.schedule_id
                JOIN national_rail_schedules s ON s.schedule_id = o.schedule_id
                WHERE o.schedule_id = %s
                  AND o.station_id  = %s AND o.stop_type = 'stop'
                  AND d.station_id  = %s AND d.stop_type = 'stop'
            """, (schedule_id, origin_station_id, destination_station_id))
            route = cur.fetchone()
            if not route:
                return False, "Route not found for the given schedule and stations."

            stops_travelled = route["dest_order"] - route["orig_order"]

            # Calculate fare
            cur.execute("""
                SELECT ROUND(base_fare_usd + per_stop_rate_usd * %s, 2) AS amount_usd
                FROM national_rail_fare_classes
                WHERE schedule_id = %s AND fare_class = %s
            """, (stops_travelled, schedule_id, fare_class))
            fare_row = cur.fetchone()
            if not fare_row:
                return False, "Fare class not found."
            amount_usd = fare_row["amount_usd"]

            # Auto-assign seat if needed
            if seat_id == "any":
                available = query_available_seats(schedule_id, travel_date, fare_class)
                if not available:
                    return False, "No available seats."
                seat_id = available[0]["seat_id"]
                coach = available[0]["coach"]
            else:
                cur.execute("""
                    SELECT coach FROM national_rail_seat_layouts
                    WHERE schedule_id = %s AND seat_id = %s AND fare_class = %s
                """, (schedule_id, seat_id, fare_class))
                seat_row = cur.fetchone()
                if not seat_row:
                    return False, f"Seat {seat_id} not found."
                coach = seat_row["coach"]

            booking_id = _gen_booking_id()
            now = datetime.now(timezone.utc)

            cur.execute("""
                INSERT INTO national_rail_bookings
                    (booking_id, user_id, schedule_id, origin_station_id,
                     destination_station_id, travel_date, departure_time,
                     ticket_type, fare_class, coach, seat_id,
                     stops_travelled, amount_usd, status, booked_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'confirmed',%s)
            """, (booking_id, user_id, schedule_id, origin_station_id,
                  destination_station_id, travel_date, route["departure_time"],
                  ticket_type, fare_class, coach, seat_id,
                  stops_travelled, amount_usd, now))

            payment_id = _gen_payment_id()
            cur.execute("""
                INSERT INTO payments (payment_id, booking_id, booking_type, amount_usd, method, status, paid_at)
                VALUES (%s, %s, 'rail', %s, 'credit_card', 'paid', %s)
            """, (payment_id, booking_id, amount_usd, now))

            conn.commit()
            return True, {
                "booking_id": booking_id,
                "schedule_id": schedule_id,
                "origin_station_id": origin_station_id,
                "destination_station_id": destination_station_id,
                "travel_date": travel_date,
                "fare_class": fare_class,
                "seat_id": seat_id,
                "coach": coach,
                "amount_usd": float(amount_usd),
                "status": "confirmed",
                "payment_id": payment_id,
            }
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """Cancel a national rail booking and calculate refund."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT b.*, s.service_type
                FROM national_rail_bookings b
                JOIN national_rail_schedules s ON s.schedule_id = b.schedule_id
                WHERE b.booking_id = %s AND b.user_id = %s
            """, (booking_id, user_id))
            booking = cur.fetchone()
            if not booking:
                return False, "Booking not found or does not belong to this user."
            if booking["status"] == "cancelled":
                return False, "Booking is already cancelled."

            # Calculate refund based on hours until travel
            travel_dt = datetime.combine(booking["travel_date"], datetime.min.time()).replace(tzinfo=timezone.utc)
            hours_until = (travel_dt - datetime.now(timezone.utc)).total_seconds() / 3600
            amount = float(booking["amount_usd"])

            if booking["service_type"] == "express":
                if hours_until >= 48:
                    refund_pct, policy = 1.0, "RF002: >48h — 100% refund"
                elif hours_until >= 2:
                    refund_pct, policy = 0.5, "RF002: 2–48h — 50% refund"
                else:
                    refund_pct, policy = 0.0, "RF002: <2h — no refund"
            else:
                if hours_until >= 48:
                    refund_pct, policy = 1.0, "RF001: >48h — 100% refund"
                elif hours_until >= 24:
                    refund_pct, policy = 0.75, "RF001: 24–48h — 75% refund"
                elif hours_until >= 2:
                    refund_pct, policy = 0.5, "RF001: 2–24h — 50% refund"
                else:
                    refund_pct, policy = 0.0, "RF001: <2h — no refund"

            refund_amount = round(amount * refund_pct, 2)

            cur.execute("""
                UPDATE national_rail_bookings SET status = 'cancelled' WHERE booking_id = %s
            """, (booking_id,))
            cur.execute("""
                UPDATE payments SET status = 'refunded' WHERE booking_id = %s
            """, (booking_id,))
            conn.commit()

            return True, {
                "booking_id": booking_id,
                "status": "cancelled",
                "original_amount_usd": amount,
                "refund_amount_usd": refund_amount,
                "policy_applied": policy,
            }
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


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
    """Register a new user."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            # Check email uniqueness
            cur.execute("SELECT 1 FROM users WHERE email = %s", (email,))
            if cur.fetchone():
                return False, "Email already registered."

            # Generate user_id
            cur.execute("SELECT COUNT(*) FROM users")
            count = cur.fetchone()[0]
            user_id = f"RU{count + 1:02d}"

            full_name = f"{first_name} {surname}"
            dob = f"{year_of_birth}-01-01"  # approximate from year only

            cur.execute("""
                INSERT INTO users
                    (user_id, full_name, email, password, date_of_birth,
                     secret_question, secret_answer, registered_at, is_active)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,TRUE)
            """, (user_id, full_name, email, password, dob,
                  secret_question, secret_answer, datetime.now(timezone.utc)))
            conn.commit()
            return True, user_id
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def login_user(email: str, password: str) -> Optional[dict]:
    """Verify credentials and return user dict on success."""
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT user_id, email, full_name, phone, date_of_birth, is_active
                FROM users
                WHERE email = %s AND password = %s
            """, (email, password))
            row = cur.fetchone()
            if not row:
                return None
            result = dict(row)
            # Derive first_name and surname from full_name
            parts = result["full_name"].split(" ", 1)
            result["first_name"] = parts[0]
            result["surname"] = parts[1] if len(parts) > 1 else ""
            return result


def get_user_secret_question(email: str) -> Optional[str]:
    """Return the secret question for a registered email."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT secret_question FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            return row[0] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """Return True if the provided answer matches (case-insensitive)."""
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT secret_answer FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
            if not row:
                return False
            if row[0] is None:
                return False
            return row[0].strip().lower() == answer.strip().lower()


def update_password(email: str, new_password: str) -> bool:
    """Update the password for a user."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE users SET password = %s WHERE email = %s
            """, (new_password, email))
            conn.commit()
            return cur.rowcount > 0
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────

def query_policy_vector_search(embedding: list[float], top_k: int = VECTOR_TOP_K) -> list[dict]:
    """Find the most relevant policy documents for a given query embedding."""
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
    """Insert a policy document with its embedding into the database."""
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
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

# TODO: Implement the query_ and execute_ functions below.
# ─────────────────────────────────────────────────────────────────────────────


# ── NATIONAL RAIL AVAILABILITY ────────────────────────────────────────────────

def query_national_rail_availability(
    origin_id: str,
    destination_id: str,
    travel_date: Optional[str] = None,
) -> list[dict]:
    sql = """
        SELECT
            s.schedule_id,
            s.line,
            s.service_type,
            s.direction,
            s.first_train_time::text,
            s.last_train_time::text,
            s.frequency_min,
            orig_st.name AS origin_name,
            dest_st.name AS destination_name,
            orig.travel_time_from_origin_min AS origin_time,
            dest.travel_time_from_origin_min AS dest_time,
            (dest.travel_time_from_origin_min - orig.travel_time_from_origin_min) AS journey_time_min,
            dest.stop_order - orig.stop_order AS stops_travelled,
            ROUND((s.standard_base_fare_usd + s.standard_per_stop_usd * (dest.stop_order - orig.stop_order))::numeric, 2) AS standard_fare_usd,
            ROUND((s.first_base_fare_usd + s.first_per_stop_usd * (dest.stop_order - orig.stop_order))::numeric, 2) AS first_fare_usd
        FROM national_rail_schedules s
        JOIN national_rail_schedule_stops orig
            ON orig.schedule_id = s.schedule_id AND orig.station_id = %s
        JOIN national_rail_schedule_stops dest
            ON dest.schedule_id = s.schedule_id AND dest.station_id = %s
        JOIN national_rail_stations orig_st ON orig_st.station_id = %s
        JOIN national_rail_stations dest_st ON dest_st.station_id = %s
        WHERE dest.stop_order > orig.stop_order
        ORDER BY s.service_type DESC, s.schedule_id
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (origin_id, destination_id, origin_id, destination_id))
            results = [dict(r) for r in cur.fetchall()]

    # 如果有 travel_date，計算當天已訂座位數
    if travel_date and results:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                for r in results:
                    cur.execute("""
                        SELECT
                            COUNT(*) FILTER (WHERE fare_class = 'standard' AND status != 'cancelled') AS booked_standard,
                            COUNT(*) FILTER (WHERE fare_class = 'first' AND status != 'cancelled') AS booked_first
                        FROM national_rail_bookings
                        WHERE schedule_id = %s AND travel_date = %s
                    """, (r["schedule_id"], travel_date))
                    counts = cur.fetchone()

                    # 總座位數
                    cur.execute("""
                        SELECT
                            COUNT(*) FILTER (WHERE fare_class = 'standard') AS total_standard,
                            COUNT(*) FILTER (WHERE fare_class = 'first') AS total_first
                        FROM national_rail_seat_layouts
                        WHERE schedule_id = %s
                    """, (r["schedule_id"],))
                    totals = cur.fetchone()

                    r["available_standard_seats"] = int(totals["total_standard"]) - int(counts["booked_standard"])
                    r["available_first_seats"] = int(totals["total_first"]) - int(counts["booked_first"])
                    r["travel_date"] = travel_date

    return results


def query_national_rail_fare(
    schedule_id: str,
    fare_class: str,
    stops_travelled: int,
) -> Optional[dict]:
    sql = """
        SELECT standard_base_fare_usd, standard_per_stop_usd,
               first_base_fare_usd, first_per_stop_usd
        FROM national_rail_schedules WHERE schedule_id = %s
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()
            if not row:
                return None
            if fare_class == "first":
                base = float(row["first_base_fare_usd"])
                per_stop = float(row["first_per_stop_usd"])
            else:
                base = float(row["standard_base_fare_usd"])
                per_stop = float(row["standard_per_stop_usd"])
            total = base + per_stop * stops_travelled
            return {
                "fare_class": fare_class,
                "base_fare_usd": base,
                "per_stop_rate_usd": per_stop,
                "total_fare_usd": round(total, 2),
            }


# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    sql = """
        SELECT
            s.schedule_id,
            s.line,
            s.direction,
            s.first_train_time::text,
            s.last_train_time::text,
            s.frequency_min,
            orig_st.name AS origin_name,
            dest_st.name AS destination_name,
            dest.stop_order - orig.stop_order AS stops_travelled,
            ROUND((s.base_fare_usd + s.per_stop_rate_usd * (dest.stop_order - orig.stop_order))::numeric, 2) AS fare_usd
        FROM metro_schedules s
        JOIN metro_schedule_stops orig
            ON orig.schedule_id = s.schedule_id AND orig.station_id = %s
        JOIN metro_schedule_stops dest
            ON dest.schedule_id = s.schedule_id AND dest.station_id = %s
        JOIN metro_stations orig_st ON orig_st.station_id = %s
        JOIN metro_stations dest_st ON dest_st.station_id = %s
        WHERE dest.stop_order > orig.stop_order
        ORDER BY s.schedule_id
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (origin_id, destination_id, origin_id, destination_id))
            return [dict(r) for r in cur.fetchall()]


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    sql = "SELECT base_fare_usd, per_stop_rate_usd FROM metro_schedules WHERE schedule_id = %s"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id,))
            row = cur.fetchone()
            if not row:
                return None
            base = float(row["base_fare_usd"])
            per_stop = float(row["per_stop_rate_usd"])
            return {
                "base_fare_usd": base,
                "per_stop_rate_usd": per_stop,
                "total_fare_usd": round(base + per_stop * stops_travelled, 2),
            }


# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    sql = """
        SELECT l.seat_id, l.coach, l.seat_row AS row, l.seat_column AS column
        FROM national_rail_seat_layouts l
        WHERE l.schedule_id = %s AND l.fare_class = %s
          AND l.seat_id NOT IN (
              SELECT seat_id FROM national_rail_bookings
              WHERE schedule_id = %s AND travel_date = %s
                AND status NOT IN ('cancelled')
                AND seat_id IS NOT NULL
          )
        ORDER BY l.seat_row, l.seat_column
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (schedule_id, fare_class, schedule_id, travel_date))
            return [dict(r) for r in cur.fetchall()]


def auto_select_adjacent_seats(available_seats: list[dict], count: int) -> list[str]:
    """
    Select `count` seats that are as close together as possible (same row preferred,
    then adjacent rows). Returns a list of seat_ids.

    Args:
        available_seats: output of query_available_seats()
        count:           number of seats needed
    """
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
    sql = "SELECT * FROM users WHERE email = %s"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (user_email,))
            row = cur.fetchone()
            return dict(row) if row else None


def query_user_bookings(user_email: str) -> dict:
    user = query_user_profile(user_email)
    if not user:
        return {"national_rail": [], "metro": []}
    uid = user["user_id"]
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    b.booking_id,
                    b.travel_date::text,
                    b.departure_time::text,
                    b.ticket_type,
                    b.fare_class,
                    b.coach,
                    b.seat_id,
                    b.stops_travelled,
                    b.amount_usd,
                    b.status,
                    b.booked_at::text,
                    b.travelled_at::text,
                    b.schedule_id,
                    s.line,
                    s.service_type,
                    o.station_id AS origin_id,
                    o.name AS origin_name,
                    d.station_id AS destination_id,
                    d.name AS destination_name
                FROM national_rail_bookings b
                JOIN national_rail_schedules s ON s.schedule_id = b.schedule_id
                JOIN national_rail_stations o ON o.station_id = b.origin_station_id
                JOIN national_rail_stations d ON d.station_id = b.destination_station_id
                WHERE b.user_id = %s
                ORDER BY b.travel_date DESC, b.departure_time DESC
            """, (uid,))
            nr = [dict(r) for r in cur.fetchall()]

            cur.execute("""
                SELECT
                    t.trip_id,
                    t.travel_date::text,
                    t.ticket_type,
                    t.day_pass_ref,
                    t.stops_travelled,
                    t.amount_usd,
                    t.status,
                    t.purchased_at::text,
                    t.travelled_at::text,
                    t.schedule_id,
                    ms.line,
                    o.station_id AS origin_id,
                    o.name AS origin_name,
                    d.station_id AS destination_id,
                    d.name AS destination_name
                FROM metro_travels t
                JOIN metro_schedules ms ON ms.schedule_id = t.schedule_id
                JOIN metro_stations o ON o.station_id = t.origin_station_id
                JOIN metro_stations d ON d.station_id = t.destination_station_id
                WHERE t.user_id = %s
                ORDER BY t.travel_date DESC
            """, (uid,))
            metro = [dict(r) for r in cur.fetchall()]

    return {"national_rail": nr, "metro": metro}

def query_payment_info(booking_id: str) -> Optional[dict]:
    sql = "SELECT * FROM payments WHERE booking_id = %s"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (booking_id,))
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
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                # 計算停靠站數
                cur.execute("""
                    SELECT dest.stop_order - orig.stop_order AS stops
                    FROM national_rail_schedule_stops orig
                    JOIN national_rail_schedule_stops dest
                        ON dest.schedule_id = orig.schedule_id
                    WHERE orig.schedule_id = %s
                      AND orig.station_id = %s
                      AND dest.station_id = %s
                """, (schedule_id, origin_station_id, destination_station_id))
                row = cur.fetchone()
                if not row:
                    return False, "Route not found for this schedule."
                stops = row["stops"]

                # 計算票價
                fare = query_national_rail_fare(schedule_id, fare_class, stops)
                if not fare:
                    return False, "Could not calculate fare."
                amount = fare["total_fare_usd"]

                # 取得出發時間
                cur.execute("""
                    SELECT first_train_time::text FROM national_rail_schedules
                    WHERE schedule_id = %s
                """, (schedule_id,))
                sched = cur.fetchone()
                departure_time = sched["first_train_time"] if sched else "00:00:00"

                # 自動選座
                if seat_id == "any" or not seat_id:
                    available = query_available_seats(schedule_id, travel_date, fare_class)
                    if not available:
                        return False, "No seats available."
                    seat_id = available[0]["seat_id"]
                    coach = available[0]["coach"]
                else:
                    cur.execute("""
                        SELECT coach FROM national_rail_seat_layouts
                        WHERE schedule_id = %s AND seat_id = %s
                    """, (schedule_id, seat_id))
                    r = cur.fetchone()
                    coach = r["coach"] if r else "B"

                booking_id = _gen_booking_id()
                payment_id = _gen_payment_id()

                cur.execute("""
                    INSERT INTO national_rail_bookings
                      (booking_id, user_id, schedule_id, origin_station_id,
                       destination_station_id, travel_date, departure_time,
                       ticket_type, fare_class, coach, seat_id, stops_travelled,
                       amount_usd, status, booked_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'confirmed',NOW())
                """, (booking_id, user_id, schedule_id, origin_station_id,
                      destination_station_id, travel_date, departure_time,
                      ticket_type, fare_class, coach, seat_id, stops, amount))

                cur.execute("""
                    INSERT INTO payments (payment_id, booking_id, amount_usd, method, status, paid_at)
                    VALUES (%s,%s,%s,'credit_card','paid',NOW())
                """, (payment_id, booking_id, amount))

                return True, {
                    "booking_id": booking_id,
                    "schedule_id": schedule_id,
                    "origin_station_id": origin_station_id,
                    "destination_station_id": destination_station_id,
                    "travel_date": travel_date,
                    "fare_class": fare_class,
                    "seat_id": seat_id,
                    "coach": coach,
                    "amount_usd": amount,
                    "status": "confirmed",
                }
    except Exception as e:
        return False, str(e)


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    try:
        with _connect() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT b.*, s.service_type,
                           (b.travel_date::timestamptz + b.departure_time) AS departs_at
                    FROM national_rail_bookings b
                    JOIN national_rail_schedules s ON s.schedule_id = b.schedule_id
                    WHERE b.booking_id = %s AND b.user_id = %s
                """, (booking_id, user_id))
                booking = cur.fetchone()
                if not booking:
                    return False, "Booking not found or does not belong to this user."
                if booking["status"] == "cancelled":
                    return False, "Booking is already cancelled."

                now = datetime.now(timezone.utc)
                departs_at = booking["departs_at"]
                if departs_at.tzinfo is None:
                    from datetime import timezone as tz
                    departs_at = departs_at.replace(tzinfo=tz.utc)
                hours_until = (departs_at - now).total_seconds() / 3600
                amount = float(booking["amount_usd"])
                service = booking["service_type"]

                # 退款規則
                if service == "express":
                    if hours_until >= 48:
                        pct, fee, note = 100, 1.00, "RF002_W1: 100% refund, $1.00 admin fee"
                    elif hours_until >= 24:
                        pct, fee, note = 50, 1.00, "RF002_W2: 50% refund, $1.00 admin fee"
                    else:
                        pct, fee, note = 0, 0, "RF002_W3: No refund"
                else:
                    if hours_until >= 48:
                        pct, fee, note = 100, 0, "RF001_W1: 100% refund"
                    elif hours_until >= 24:
                        pct, fee, note = 75, 0.50, "RF001_W2: 75% refund, $0.50 admin fee"
                    elif hours_until >= 2:
                        pct, fee, note = 50, 0.50, "RF001_W3: 50% refund, $0.50 admin fee"
                    else:
                        pct, fee, note = 0, 0, "RF001_W4: No refund"

                refund = max(0, round(amount * pct / 100 - fee, 2))

                cur.execute("""
                    UPDATE national_rail_bookings SET status = 'cancelled'
                    WHERE booking_id = %s
                """, (booking_id,))
                cur.execute("""
                    UPDATE payments SET status = 'refunded'
                    WHERE booking_id = %s
                """, (booking_id,))

                return True, {
                    "booking_id": booking_id,
                    "original_amount_usd": amount,
                    "refund_amount_usd": refund,
                    "policy_applied": note,
                    "status": "cancelled",
                }
    except Exception as e:
        return False, str(e)


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
    import random, string
    user_id = "RU" + "".join(random.choices(string.digits, k=4))
    full_name = f"{first_name} {surname}"
    dob = f"{year_of_birth}-01-01"
    sql = """
        INSERT INTO users
          (user_id, full_name, email, password, date_of_birth,
           secret_question, secret_answer, is_active)
        VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE)
    """
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (user_id, full_name, email, password,
                                  dob, secret_question, secret_answer))
        return True, user_id
    except Exception as e:
        return False, str(e)

def login_user(email: str, password: str) -> Optional[dict]:
    sql = "SELECT * FROM users WHERE email = %s AND password = %s AND is_active = TRUE"
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, (email, password))
            row = cur.fetchone()
            if not row:
                return None
            row = dict(row)
            parts = row["full_name"].split(" ", 1)
            row["first_name"] = parts[0]
            row["surname"] = parts[1] if len(parts) > 1 else ""
            return row


def get_user_secret_question(email: str) -> Optional[str]:
    sql = "SELECT secret_question FROM users WHERE email = %s"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            return row[0] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    sql = "SELECT secret_answer FROM users WHERE email = %s"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (email,))
            row = cur.fetchone()
            return bool(row and row[0].lower() == answer.lower())


def update_password(email: str, new_password: str) -> bool:
    sql = "UPDATE users SET password = %s WHERE email = %s"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (new_password, email))
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

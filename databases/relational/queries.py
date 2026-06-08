# TASK 6 EXTENSION: Added query_feedback_summary() for feedback statistics
# and recent comments from schema1.feedback table.
"""
TransitFlow — PostgreSQL / Relational Database Layer
=====================================================
This module handles all queries to PostgreSQL.

TWO ROLES ARE SERVED HERE:
  1. Relational  → dual-network transit (metro + national rail),
                   availability, fares, bookings, seat selection
  2. Vector      → policy document similarity search (pgvector)

SCHEMA LAYOUT:
  schema1  — all relational and vector tables
  schema2  — credentials table (Argon2id hashes, isolated from profile data)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import psycopg2
import psycopg2.extras

from skeleton.config import PG_DSN, VECTOR_TOP_K, VECTOR_SIMILARITY_THRESHOLD

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

_ph = PasswordHasher()


def _connect():
    """Return a new psycopg2 connection with autocommit enabled."""
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = True
    return conn


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

    Args:
        origin_id:       Station ID of the origin (e.g. "NR01")
        destination_id:  Station ID of the destination (e.g. "NR05")
        travel_date:     ISO date string e.g. "2026-06-01" (optional)

    Returns:
        List of schedule dicts, each with fares and optionally seat_availability.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Find schedules where origin stop comes before destination stop
            cur.execute("""
                SELECT
                    s.id,
                    s.schedule_code AS schedule_id,
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
                FROM schema1.national_rail_schedules s
                JOIN schema1.national_rail_stations orig_s ON orig_s.station_code = %s
                JOIN schema1.national_rail_stations dest_s ON dest_s.station_code = %s
                JOIN schema1.national_rail_schedule_stops o ON o.schedule_id = s.id
                    AND o.station_id = orig_s.id AND o.stop_type = 'stop'
                JOIN schema1.national_rail_schedule_stops d ON d.schedule_id = s.id
                    AND d.station_id = dest_s.id AND d.stop_type = 'stop'
                WHERE d.stop_order > o.stop_order
                ORDER BY s.schedule_code
            """, (origin_id, destination_id))
            schedules = [dict(r) for r in cur.fetchall()]

            if not schedules:
                return []

            # For each schedule, get fare classes and seat occupancy
            for sched in schedules:
                sid_int = sched["id"]   # INTEGER PK, used for FK lookups
                stops = sched["stops_travelled"]

                # Fare classes
                cur.execute("""
                    SELECT fare_class, base_fare_usd, per_stop_rate_usd,
                           ROUND(base_fare_usd + per_stop_rate_usd * %s, 2) AS total_fare_usd
                    FROM schema1.national_rail_fare_classes
                    WHERE schedule_id = %s
                """, (stops, sid_int))
                sched["fares"] = [dict(r) for r in cur.fetchall()]

                # Seat occupancy on travel_date
                if travel_date:
                    cur.execute("""
                        SELECT
                            sl.fare_class,
                            COUNT(sl.seat_id) AS total_seats,
                            COUNT(b.seat_id)  AS booked_seats
                        FROM schema1.national_rail_seat_layouts sl
                        LEFT JOIN schema1.national_rail_bookings b
                            ON b.schedule_id = sl.schedule_id
                            AND b.seat_id    = sl.seat_id
                            AND b.coach      = sl.coach
                            AND b.travel_date = %s
                            AND b.status NOT IN ('cancelled')
                        WHERE sl.schedule_id = %s
                        GROUP BY sl.fare_class
                    """, (travel_date, sid_int))
                    sched["seat_availability"] = [dict(r) for r in cur.fetchall()]
                    sched["available_seats"] = sum(
                        r["total_seats"] - r["booked_seats"]
                        for r in sched["seat_availability"]
                    )
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
    """
    Calculate the fare for a national rail journey.

    Args:
        schedule_id:     Schedule ID (e.g. "NR_SCH01")
        fare_class:      "standard" or "first"
        stops_travelled: Number of stops between origin and destination

    Returns:
        Dict with fare_class, base_fare_usd, per_stop_rate_usd, total_fare_usd, or None.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    fare_class,
                    base_fare_usd,
                    per_stop_rate_usd,
                    ROUND(base_fare_usd + per_stop_rate_usd * %s, 2) AS total_fare_usd
                FROM schema1.national_rail_fare_classes
                WHERE schedule_id = (SELECT id FROM schema1.national_rail_schedules WHERE schedule_code = %s)
                  AND fare_class = %s
            """, (stops_travelled, schedule_id, fare_class))
            row = cur.fetchone()
            return dict(row) if row else None


# ── METRO SCHEDULES & FARE ────────────────────────────────────────────────────

def query_metro_schedules(origin_id: str, destination_id: str) -> list[dict]:
    """
    Return metro schedules serving both origin and destination in the correct order.

    Args:
        origin_id:       Metro station ID (e.g. "MS01")
        destination_id:  Metro station ID (e.g. "MS09")

    Returns:
        List of schedule dicts with total_fare_usd included.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    s.id,
                    s.schedule_code AS schedule_id,
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
                FROM schema1.metro_schedules s
                JOIN schema1.metro_stations orig_st ON orig_st.station_code = %s
                JOIN schema1.metro_stations dest_st ON dest_st.station_code = %s
                JOIN schema1.metro_schedule_stops o ON o.schedule_id = s.id
                    AND o.station_id = orig_st.id
                JOIN schema1.metro_schedule_stops d ON d.schedule_id = s.id
                    AND d.station_id = dest_st.id
                WHERE d.stop_order > o.stop_order
                ORDER BY s.schedule_code
            """, (origin_id, destination_id))
            return [dict(r) for r in cur.fetchall()]


def query_metro_fare(schedule_id: str, stops_travelled: int) -> Optional[dict]:
    """
    Calculate the metro fare for a single-ticket journey.

    Args:
        schedule_id:     Metro schedule ID
        stops_travelled: Number of stops between origin and destination

    Returns:
        Dict with base_fare_usd, per_stop_rate_usd, total_fare_usd, or None.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    base_fare_usd,
                    per_stop_rate_usd,
                    ROUND(base_fare_usd + per_stop_rate_usd * %s, 2) AS total_fare_usd
                FROM schema1.metro_schedules
                WHERE schedule_code = %s
            """, (stops_travelled, schedule_id))
            row = cur.fetchone()
            return dict(row) if row else None


# ── SEAT SELECTION ────────────────────────────────────────────────────────────

def query_available_seats(
    schedule_id: str,
    travel_date: str,
    fare_class: str,
) -> list[dict]:
    """
    Return available seats for a national rail journey on a given date.

    Args:
        schedule_id:  National rail schedule ID
        travel_date:  ISO date string e.g. "2026-06-01"
        fare_class:   "standard" or "first"

    Returns:
        List of dicts with seat_id, coach, row, column.
    """
    # NOT EXISTS is used instead of LEFT JOIN / IS NULL
    # to correctly handle multi-booking edge cases on the same seat
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT sl.seat_id, sl.coach, sl.row_num AS row, sl.col_name AS column
                FROM schema1.national_rail_seat_layouts sl
                WHERE sl.schedule_id = (SELECT id FROM schema1.national_rail_schedules WHERE schedule_code = %s)
                  AND sl.fare_class  = %s
                  AND NOT EXISTS (
                      SELECT 1 FROM schema1.national_rail_bookings b
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
    """
    Select seats that are as close together as possible.

    Args:
        available_seats: List of seat dicts from query_available_seats
        count:           Number of seats to select

    Returns:
        List of seat_id strings.
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
    """
    Return a user's profile by email (no credentials fields).

    Args:
        user_email: The user's email address

    Returns:
        Dict with user profile fields, or None if not found.
    """
    ###nini fix
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT user_id, full_name, full_name AS name, email, phone, date_of_birth,
                       EXTRACT(YEAR FROM date_of_birth)::int AS year_of_birth,
                       registered_at, is_active
                FROM schema1.users
                WHERE email = %s
            """, (user_email,))
            row = cur.fetchone()
            return dict(row) if row else None
            ###nini fix end


def query_user_bookings(user_email: str) -> dict:
    """
    Return a user's combined booking history (national rail + metro).

    Args:
        user_email: The user's email address

    Returns:
        Dict with keys "national_rail" and "metro", each a list of booking dicts.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Get user_id first
            cur.execute("SELECT user_id FROM schema1.users WHERE email = %s", (user_email,))
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
                FROM schema1.national_rail_bookings b
                JOIN schema1.national_rail_stations orig ON orig.id = b.origin_station_id
                JOIN schema1.national_rail_stations dest ON dest.id = b.destination_station_id
                JOIN schema1.national_rail_schedules s ON s.id = b.schedule_id
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
                FROM schema1.metro_travels t
                JOIN schema1.metro_stations orig ON orig.id = t.origin_station_id
                JOIN schema1.metro_stations dest ON dest.id = t.destination_station_id
                JOIN schema1.metro_schedules s ON s.id = t.schedule_id
                WHERE t.user_id = %s
                ORDER BY t.travel_date DESC
            """, (user_id,))
            metro_travels = [dict(r) for r in cur.fetchall()]

            return {"national_rail": nr_bookings, "metro": metro_travels}


def query_payment_info(booking_id: str) -> Optional[dict]:
    """
    Return payment record for a booking or metro trip.

    Args:
        booking_id: BK* or MT* booking ID

    Returns:
        Dict with payment fields, or None if not found.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT payment_id, booking_id, booking_type, amount_usd, method, status, paid_at
                FROM schema1.payments
                WHERE booking_id = %s
            """, (booking_id,))
            row = cur.fetchone()
            return dict(row) if row else None


"""
── ADDED 2026-05-29 ──────────────────────────────────────────────────────────
Feedback query functions for the agent to retrieve passenger ratings and
comments from the schema1.feedback table.

Problem: feedback data existed in PostgreSQL but the agent had no tool to
query it, so questions like "how many 5-star ratings?" could not be answered.

Fix: add query_feedback_summary() to return rating statistics and recent
comments, enabling a new get_feedback_summary tool in agent.py.
── END ADDED 2026-05-29 ──────────────────────────────────────────────────────
"""


def query_feedback_summary(booking_id: str = None) -> dict:
    """
    Return feedback statistics and recent comments.

    Args:
        booking_id: (optional) filter by specific booking ID (BK* or MT*)

    Returns:
        Dict with rating_summary (count per star), average_rating,
        total_feedback_count, and recent_comments (latest 10).
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if booking_id:
                # Feedback for a specific booking
                cur.execute("""
                    SELECT f.feedback_id, f.booking_id, f.booking_type,
                           f.rating, f.comment, f.submitted_at,
                           u.full_name AS user_name
                    FROM schema1.feedback f
                    JOIN schema1.users u ON u.user_id = f.user_id
                    WHERE f.booking_id = %s
                    ORDER BY f.submitted_at DESC
                """, (booking_id,))
                rows = [dict(r) for r in cur.fetchall()]
                return {
                    "booking_id": booking_id,
                    "feedback_count": len(rows),
                    "feedback": rows,
                }
            else:
                # Overall summary
                # Rating distribution
                cur.execute("""
                    SELECT rating, COUNT(*) AS count
                    FROM schema1.feedback
                    GROUP BY rating
                    ORDER BY rating DESC
                """)
                rating_rows = [dict(r) for r in cur.fetchall()]
                rating_summary = {str(r["rating"]) + "_star": r["count"] for r in rating_rows}

                # Average and total
                cur.execute("""
                    SELECT COUNT(*) AS total, ROUND(AVG(rating), 2) AS average_rating
                    FROM schema1.feedback
                """)
                stats = dict(cur.fetchone())

                # Recent comments (latest 10)
                cur.execute("""
                    SELECT f.feedback_id, f.booking_id, f.booking_type,
                           f.rating, f.comment, f.submitted_at,
                           u.full_name AS user_name
                    FROM schema1.feedback f
                    JOIN schema1.users u ON u.user_id = f.user_id
                    WHERE f.comment IS NOT NULL AND f.comment != ''
                    ORDER BY f.submitted_at DESC
                    LIMIT 10
                """)
                recent = [dict(r) for r in cur.fetchall()]

                return {
                    "total_feedback_count": stats["total"],
                    "average_rating": float(stats["average_rating"]) if stats["average_rating"] else 0,
                    "rating_summary": rating_summary,
                    "recent_comments": recent,
                }


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
    """
    Create a national rail booking for a logged-in user.

    Args:
        user_id:                 User's ID
        schedule_id:             National rail schedule ID
        origin_station_id:       Origin station ID
        destination_station_id:  Destination station ID
        travel_date:             ISO date string e.g. "2026-06-01"
        fare_class:              "standard" or "first"
        seat_id:                 Specific seat ID, or "any" for auto-assignment
        ticket_type:             "single" (default)

    Returns:
        (True, booking_dict) on success, (False, error_message) on failure.
        Payment is always inserted with booking_type='rail', method='credit_card'.
    """
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Resolve station/schedule codes to INTEGER IDs
            cur.execute("SELECT id FROM schema1.national_rail_schedules WHERE schedule_code = %s", (schedule_id,))
            row = cur.fetchone()
            if not row:
                return False, f"Schedule not found."
            schedule_int = row["id"]

            cur.execute("SELECT id FROM schema1.national_rail_stations WHERE station_code = %s", (origin_station_id,))
            row = cur.fetchone()
            if not row:
                return False, f"Origin station not found."
            origin_int = row["id"]

            cur.execute("SELECT id FROM schema1.national_rail_stations WHERE station_code = %s", (destination_station_id,))
            row = cur.fetchone()
            if not row:
                return False, f"Destination station not found."
            dest_int = row["id"]

            # Get stop orders to calculate stops_travelled
            cur.execute("""
                SELECT o.stop_order AS orig_order, d.stop_order AS dest_order,
                       s.first_train_time::text AS departure_time
                FROM schema1.national_rail_schedule_stops o
                JOIN schema1.national_rail_schedule_stops d ON d.schedule_id = o.schedule_id
                JOIN schema1.national_rail_schedules s ON s.id = o.schedule_id
                WHERE o.schedule_id = %s
                  AND o.station_id  = %s AND o.stop_type = 'stop'
                  AND d.station_id  = %s AND d.stop_type = 'stop'
            """, (schedule_int, origin_int, dest_int))
            route = cur.fetchone()
            if not route:
                return False, "Route not found for the given schedule and stations."

            stops_travelled = route["dest_order"] - route["orig_order"]

            # Calculate fare
            cur.execute("""
                SELECT ROUND(base_fare_usd + per_stop_rate_usd * %s, 2) AS amount_usd
                FROM schema1.national_rail_fare_classes
                WHERE schedule_id = %s AND fare_class = %s
            """, (stops_travelled, schedule_int, fare_class))
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
                    SELECT coach FROM schema1.national_rail_seat_layouts
                    WHERE schedule_id = %s AND seat_id = %s AND fare_class = %s
                """, (schedule_int, seat_id, fare_class))
                seat_row = cur.fetchone()
                if not seat_row:
                    return False, f"Seat {seat_id} not found."
                coach = seat_row["coach"]

                cur.execute("""
                    SELECT 1 FROM schema1.national_rail_bookings
                    WHERE schedule_id = %s AND seat_id = %s AND coach = %s
                      AND travel_date = %s AND status != 'cancelled'
                """, (schedule_int, seat_id, coach, travel_date))
                if cur.fetchone():
                    return False, f"Seat {seat_id} is already booked for {travel_date}."

            now = datetime.now(timezone.utc)

            cur.execute("""
                INSERT INTO schema1.national_rail_bookings
                    (user_id, schedule_id, origin_station_id,
                     destination_station_id, travel_date, departure_time,
                     ticket_type, fare_class, coach, seat_id,
                     stops_travelled, amount_usd, status, booked_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'confirmed',%s)
                RETURNING booking_id
            """, (user_id, schedule_int, origin_int,
                  dest_int, travel_date, route["departure_time"],
                  ticket_type, fare_class, coach, seat_id,
                  stops_travelled, amount_usd, now))
            booking_id = cur.fetchone()["booking_id"]

            cur.execute("""
                INSERT INTO schema1.payments
                    (booking_id, booking_type, amount_usd, method, status, paid_at)
                VALUES (%s, 'rail', %s, 'credit_card', 'paid', %s)
                RETURNING payment_id
            """, (booking_id, amount_usd, now))
            payment_id = cur.fetchone()["payment_id"]

            ###nini fix
            conn.commit()
            return True, {
                "booking_id": str(booking_id),
                "user_id": user_id,
                "schedule_id": schedule_id,
                "origin_station_id": origin_station_id,
                "destination_station_id": destination_station_id,
                "travel_date": travel_date,
                "fare_class": fare_class,
                "seat_id": seat_id,
                "coach": coach,
                "amount_usd": float(amount_usd),
                "status": "confirmed",
                "payment_id": str(payment_id),
            }
            ###nini fix end
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def execute_cancellation(booking_id: str, user_id: str) -> tuple[bool, dict | str]:
    """
    Cancel a national rail booking and calculate refund.

    Args:
        booking_id: The booking ID to cancel (BK* format)
        user_id:    The user who owns the booking (ownership check)

    Returns:
        (True, result_dict) with refund info, or (False, error_message).
        Refund policy:
          Normal  (RF001): ≥48h → 100% | 24–48h → 75% | 2–24h → 50% | <2h → 0%
          Express (RF002): ≥48h → 100% | 2–48h  → 50% | <2h   → 0%
    """
    conn = psycopg2.connect(PG_DSN)
    # autocommit disabled so booking + payment are atomic;
    # if payment insert fails, booking is rolled back too
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT b.*, s.service_type
                FROM schema1.national_rail_bookings b
                JOIN schema1.national_rail_schedules s ON s.id = b.schedule_id
                WHERE b.booking_id = %s AND b.user_id = %s
            """, (booking_id, user_id))
            booking = cur.fetchone()
            if not booking:
                return False, "Booking not found or does not belong to this user."
            if booking["status"] == "cancelled":
                return False, "Booking is already cancelled."

            # Calculate refund based on hours until travel
            travel_dt = datetime.combine(booking["travel_date"], booking["departure_time"]).replace(tzinfo=timezone.utc)
            hours_until = (travel_dt - datetime.now(timezone.utc)).total_seconds() / 3600
            amount = float(booking["amount_usd"])

            # RF001 (normal): tiered refund based on hours before departure
            # RF002 (express): simpler 2-tier — stricter because express seats are limited  
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
                UPDATE schema1.national_rail_bookings SET status = 'cancelled' WHERE booking_id = %s
            """, (booking_id,))
            cur.execute("""
                UPDATE schema1.payments SET status = 'refunded' WHERE booking_id = %s
            """, (booking_id,))
            conn.commit()

            return True, {
                "booking_id": booking_id,
                "status": "cancelled",
                "original_amount_usd": amount,
                "refund_amount": refund_amount,
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
    """
    Register a new user with Argon2id hashed password.
    Inserts profile into schema1.users, hash into schema2.credentials.

    Args:
        email:            User's email address (must be unique)
        first_name:       First name
        surname:          Last name
        year_of_birth:    Year of birth (used to set date_of_birth to Jan 1)
        password:         Plaintext password — hashed with Argon2id before storage
        secret_question:  Security question for password reset
        secret_answer:    Answer to the security question

    Returns:
        (True, user_id) on success, (False, error_message) on failure.
        user_id is a UUID generated by the database.
    """
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            # Check email uniqueness
            cur.execute("SELECT 1 FROM schema1.users WHERE email = %s", (email,))
            if cur.fetchone():
                return False, "Email already registered."

            full_name = f"{first_name} {surname}"
            dob = f"{year_of_birth}-01-01"
            hashed_password = _ph.hash(password)

            # Step 1: insert profile; UUID PK auto-generated, retrieved via RETURNING
            cur.execute("""
                INSERT INTO schema1.users
                    (full_name, email, date_of_birth,
                     secret_question, secret_answer, registered_at, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, TRUE)
                RETURNING user_id
            """, (full_name, email, dob,
                  secret_question, secret_answer, datetime.now(timezone.utc)))
            user_id = cur.fetchone()[0]

            # credentials stored in schema2 (isolated from schema1.users)
            # so profile queries never accidentally expose password hashes
            cur.execute("""
                INSERT INTO schema2.credentials (user_id, stored_hash)
                VALUES (%s, %s)
            """, (user_id, hashed_password))

            conn.commit()
            return True, str(user_id)
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def login_user(email: str, password: str) -> Optional[dict]:
    """
    Verify credentials using Argon2id and return user dict on success.
    Joins schema1.users with schema2.credentials for verification.

    Args:
        email:    User's email address
        password: Plaintext password to verify against stored Argon2id hash

    Returns:
        User dict (with first_name/surname, no stored_hash) on success, or None.
    """
    with _connect() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT u.user_id, u.email, u.full_name, u.phone, u.date_of_birth,
                       u.is_active, c.stored_hash
                FROM schema1.users u
                JOIN schema2.credentials c ON c.user_id = u.user_id
                WHERE u.email = %s AND u.is_active = TRUE
            """, (email,))
            row = cur.fetchone()
            if not row:
                return None
            result = dict(row)
            try:
                _ph.verify(result["stored_hash"], password)
            except (VerifyMismatchError, VerificationError, InvalidHashError):
                return None
            # Remove hash before returning to caller
            result.pop("stored_hash")
            parts = result["full_name"].split(" ", 1)
            result["first_name"] = parts[0]
            result["surname"] = parts[1] if len(parts) > 1 else ""
            return result


def get_user_secret_question(email: str) -> Optional[str]:
    """
    Return the secret question for a registered email.

    Args:
        email: User's email address

    Returns:
        Secret question string, or None if email not found.
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT secret_question FROM schema1.users WHERE email = %s",
                (email,)
            )
            row = cur.fetchone()
            return row[0] if row else None


def verify_secret_answer(email: str, answer: str) -> bool:
    """
    Return True if the provided answer matches the stored answer (case-insensitive).
    Returns False if email not found or secret_answer is NULL.

    Args:
        email:  User's email address
        answer: Candidate answer to compare

    Returns:
        True on match, False otherwise (never raises for not-found or NULL).
    """
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT secret_answer FROM schema1.users WHERE email = %s",
                (email,)
            )
            row = cur.fetchone()
            if not row:
                return False
            if row[0] is None:
                return False
            return row[0].strip().lower() == answer.strip().lower()


def update_password(email: str, new_password: str) -> bool:
    """
    Update the Argon2id hash in schema2.credentials for the given email.

    Args:
        email:        User's email address
        new_password: New plaintext password — hashed before storage

    Returns:
        True if a row was updated, False otherwise.
    """
    hashed = _ph.hash(new_password)
    conn = psycopg2.connect(PG_DSN)
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE schema2.credentials
                SET stored_hash = %s
                WHERE user_id = (
                    SELECT user_id FROM schema1.users WHERE email = %s
                )
            """, (hashed, email))
            conn.commit()
            return cur.rowcount > 0
    except Exception:
        conn.rollback()
        return False
    finally:
        conn.close()


# ── VECTOR / RAG QUERIES — do not modify ─────────────────────────────────────

def query_policy_vector_search(embedding: list[float], top_k: int = VECTOR_TOP_K) -> list[dict]:
    """
    Find the most relevant policy documents for a given query embedding.

    Args:
        embedding: Query vector (768-dim for Ollama, 3072-dim for Gemini)
        top_k:     Maximum number of results to return

    Returns:
        List of dicts with title, category, content, similarity score.
    """
    sql = """
        SELECT
            title,
            category,
            content,
            1 - (embedding <=> %s::vector) AS similarity
        FROM schema1.policy_documents
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
    Insert a policy document with its embedding into schema1.policy_documents.

    Args:
        title:       Document title
        category:    Document category
        content:     Full text content
        embedding:   Vector embedding
        source_file: Source filename (optional)

    Returns:
        Auto-generated id of the inserted row.
    """
    sql = """
        INSERT INTO schema1.policy_documents (title, category, content, embedding, source_file)
        VALUES (%s, %s, %s, %s::vector, %s)
        RETURNING id
    """
    vec_str = "[" + ",".join(str(x) for x in embedding) + "]"
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (title, category, content, vec_str, source_file))
            return cur.fetchone()[0]

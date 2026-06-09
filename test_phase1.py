"""
Phase 1 Live Testing — 直接呼叫 function 驗證
對照 STUDENT_GUIDE_LIVE.md 的評分標準逐項確認

執行方式（從 project root）：
    python test_phase1.py
    python test_phase1.py --section A   # 只跑某一個 section
    python test_phase1.py --section B
    python test_phase1.py --section C
"""

import sys
import json
import argparse
import traceback
import psycopg2
import psycopg2.extras
from neo4j import GraphDatabase

sys.path.insert(0, ".")
from skeleton.config import PG_DSN, NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD
from databases.relational.queries import (
    query_national_rail_availability,
    query_national_rail_fare,
    query_metro_schedules,
    query_metro_fare,
    query_available_seats,
    query_user_profile,
    query_user_bookings,
    query_payment_info,
    execute_booking,
    execute_cancellation,
)
from databases.graph.queries import (
    query_shortest_route,
    query_cheapest_route,
    query_alternative_routes,
    query_interchange_path,
    query_delay_ripple,
    query_station_connections,
)

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

# ── Counters ──────────────────────────────────────────────────────────────────
_passed = 0
_failed = 0
_errors = 0


def _check(label: str, condition: bool, hint: str = "", value=None):
    global _passed, _failed
    icon = f"{GREEN}PASS{RESET}" if condition else f"{RED}FAIL{RESET}"
    print(f"  {icon}  {label}")
    if not condition:
        _failed += 1
        if hint:
            print(f"        {YELLOW}hint: {hint}{RESET}")
        if value is not None:
            print(f"        got:  {repr(value)[:200]}")
    else:
        _passed += 1


def _section(title: str):
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}{title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}")


def _case(label: str):
    print(f"\n  {BOLD}{label}{RESET}")


def _show(label: str, value):
    text = json.dumps(value, default=str, indent=2) if isinstance(value, (dict, list)) else repr(value)
    lines = text.splitlines()
    preview = "\n    ".join(lines[:20])
    if len(lines) > 20:
        preview += f"\n    ... ({len(lines) - 20} more lines)"
    print(f"    {YELLOW}{label}:{RESET}\n    {preview}")


def _safe_call(fn, *args, **kwargs):
    """呼叫函數，捕捉 exception 並回傳 (result, error_message)"""
    global _errors
    try:
        return fn(*args, **kwargs), None
    except Exception as e:
        _errors += 1
        return None, f"{type(e).__name__}: {e}"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION A — Seeding & Setup
# ══════════════════════════════════════════════════════════════════════════════

def run_section_a():
    _section("SECTION A — Seeding & Setup  (/15)")

    conn = psycopg2.connect(PG_DSN)
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # A2 — required tables populated
    _case("A2 — Required PG tables have data")
    for table in [
        "schema1.metro_stations",
        "schema1.national_rail_stations",
        "schema1.metro_schedules",
        "schema1.users",
        "schema1.national_rail_seat_layouts",
    ]:
        cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
        n = cur.fetchone()["n"]
        _check(f"{table}: {n} rows", n > 0, f"table is empty")

    # A3 — fare columns are numeric, FK references valid
    _case("A3 — Fare columns are numeric (not strings)")
    cur.execute("""
        SELECT data_type FROM information_schema.columns
        WHERE table_schema='schema1' AND table_name='national_rail_fare_classes'
          AND column_name='base_fare_usd'
    """)
    row = cur.fetchone()
    _check("national_rail_fare_classes.base_fare_usd is numeric type",
           row and row["data_type"] in ("numeric", "decimal", "real", "double precision", "integer"),
           f"got data_type={row['data_type'] if row else 'NULL'}")

    cur.execute("""
        SELECT data_type FROM information_schema.columns
        WHERE table_schema='schema1' AND table_name='metro_schedules'
          AND column_name='base_fare_usd'
    """)
    row = cur.fetchone()
    _check("metro_schedules.base_fare_usd is numeric type",
           row and row["data_type"] in ("numeric", "decimal", "real", "double precision", "integer"),
           f"got data_type={row['data_type'] if row else 'NULL'}")

    # A6 — pgvector policy_documents populated
    _case("A6 — pgvector policy_documents table")
    try:
        cur.execute("SELECT COUNT(*) AS n FROM schema1.policy_documents")
        n = cur.fetchone()["n"]
        _check(f"policy_documents: {n} rows", n > 0)
    except Exception as e:
        print(f"  {RED}ERROR{RESET} policy_documents query failed: {e}")
        _errors += 1

    # A5 — Neo4j nodes and relationships
    _case("A5 — Neo4j station nodes and link relationships")
    try:
        driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
        with driver.session() as sess:
            res = sess.run("MATCH (n:MetroStation) RETURN count(n) AS n").single()
            _check(f"MetroStation nodes: {res['n']}", res["n"] > 0)
            res = sess.run("MATCH (n:NationalRailStation) RETURN count(n) AS n").single()
            _check(f"NationalRailStation nodes: {res['n']}", res["n"] > 0)
            res = sess.run("MATCH ()-[r:METRO_LINK]->() RETURN count(r) AS n").single()
            _check(f"METRO_LINK relationships: {res['n']}", res["n"] > 0)
            res = sess.run("MATCH ()-[r:RAIL_LINK]->() RETURN count(r) AS n").single()
            _check(f"RAIL_LINK relationships: {res['n']}", res["n"] > 0)
        driver.close()
    except Exception as e:
        print(f"  {RED}ERROR{RESET} Neo4j connection failed: {e}")
        _errors += 1

    cur.close()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION B — PostgreSQL Queries
# ══════════════════════════════════════════════════════════════════════════════

def run_section_b():
    _section("SECTION B — PostgreSQL Queries  (/50)")

    # ── B1 ─────────────────────────────────────────────────────────────────────
    _case("B1 — query_national_rail_availability")

    result, err = _safe_call(query_national_rail_availability, "NR01", "NR05", "2026-07-01")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("NR01→NR05 on 2026-07-01", result)
        _check("returns a list (not None/exception)", isinstance(result, list), value=result)
        _check("list is non-empty (services exist)", len(result) > 0, value=result)
        if result:
            first = result[0]
            _check("each item has 'schedule_id'",
                   all("schedule_id" in r for r in result), value=result[0])
            # grading says available_seats — check both possible key names
            has_avail = all(
                "available_seats" in r or "seat_availability" in r
                for r in result
            )
            _check("each item has 'available_seats' or 'seat_availability'",
                   has_avail,
                   hint="grading requires 'available_seats' key — check your key name",
                   value=list(first.keys()))

    # B1 — no-service scenario (stations on different lines)
    result_empty, err = _safe_call(query_national_rail_availability, "NR03", "NR07", "2026-07-01")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("NR03→NR07 (different lines, expect [])", result_empty)
        _check("no shared schedule → returns []", result_empty == [],
               hint="should return [] not None", value=result_empty)

    # ── B2 ─────────────────────────────────────────────────────────────────────
    _case("B2 — query_metro_schedules")

    result, err = _safe_call(query_metro_schedules, "MS01", "MS03")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("MS01→MS03 (shared line MS_SCH01)", result)
        _check("returns a list", isinstance(result, list), value=result)
        _check("non-empty (shared line exists)", len(result) > 0, value=result)
        if result:
            _check("each item has line name or schedule_id",
                   all("schedule_id" in r or "line" in r for r in result), value=result[0])
            _check("each item has stop sequence info",
                   all("stops_travelled" in r or "stop_order" in r or "origin_stop_order" in r
                       for r in result),
                   hint="need stop sequence info",
                   value=list(result[0].keys()))

    # B2 — no shared line
    result_empty, err = _safe_call(query_metro_schedules, "MS04", "MS08")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("MS04→MS08 (no shared line, expect [])", result_empty)
        _check("no shared metro line → returns []", result_empty == [],
               hint="should return [] not None", value=result_empty)

    # ── B3 ─────────────────────────────────────────────────────────────────────
    _case("B3 — query_national_rail_fare")

    # NR01→NR05 = 4 stops; standard: 2.50 + 1.50*4 = 8.50
    result, err = _safe_call(query_national_rail_fare, "NR_SCH01", "standard", 4)
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("NR_SCH01 standard 4 stops (expect total=8.50)", result)
        _check("returns a dict", isinstance(result, dict), value=result)
        _check("has 'base_fare_usd'",   "base_fare_usd"    in (result or {}), value=result)
        _check("has 'per_stop_rate_usd'","per_stop_rate_usd" in (result or {}), value=result)
        _check("has 'total_fare_usd'",   "total_fare_usd"   in (result or {}), value=result)
        if result and all(k in result for k in ("base_fare_usd","per_stop_rate_usd","total_fare_usd")):
            _check("all values are numeric (not strings)",
                   all(not isinstance(result[k], str) for k in
                       ("base_fare_usd","per_stop_rate_usd","total_fare_usd")),
                   value=result)
            expected = round(float(result["base_fare_usd"]) + float(result["per_stop_rate_usd"]) * 4, 2)
            got      = round(float(result["total_fare_usd"]), 2)
            _check(f"total = base + rate×4  (expect {expected}, got {got})",
                   abs(got - expected) < 0.01, value=result)

    # B3 — first class (rate should differ)
    result_first, err = _safe_call(query_national_rail_fare, "NR_SCH01", "first", 4)
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("NR_SCH01 first class 4 stops (expect total=14.00)", result_first)
        if result_first and result:
            _check("first class per_stop_rate differs from standard",
                   result_first.get("per_stop_rate_usd") != result.get("per_stop_rate_usd"),
                   hint="first class rate should be higher than standard",
                   value={"standard_rate": result.get("per_stop_rate_usd"),
                          "first_rate": result_first.get("per_stop_rate_usd")})
            if "total_fare_usd" in result_first:
                expected = round(float(result_first["base_fare_usd"]) + float(result_first["per_stop_rate_usd"]) * 4, 2)
                got      = round(float(result_first["total_fare_usd"]), 2)
                _check(f"first class total recalculates correctly (expect {expected}, got {got})",
                       abs(got - expected) < 0.01, value=result_first)

    # ── B4 ─────────────────────────────────────────────────────────────────────
    _case("B4 — query_metro_fare")

    # MS_SCH01: base=0.8, per_stop=0.3; 2 stops → total=0.8+0.3*2=1.40
    result, err = _safe_call(query_metro_fare, "MS_SCH01", 2)
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("MS_SCH01 2 stops (expect total=1.40)", result)
        _check("returns a dict", isinstance(result, dict), value=result)
        for key in ("base_fare_usd", "per_stop_rate_usd", "total_fare_usd"):
            _check(f"has '{key}'", key in (result or {}), value=result)
        if result and all(k in result for k in ("base_fare_usd","per_stop_rate_usd","total_fare_usd")):
            _check("all values are numeric (not strings)",
                   all(not isinstance(result[k], str) for k in
                       ("base_fare_usd","per_stop_rate_usd","total_fare_usd")),
                   value=result)
            expected = round(float(result["base_fare_usd"]) + float(result["per_stop_rate_usd"]) * 2, 2)
            got      = round(float(result["total_fare_usd"]), 2)
            _check(f"total = base + rate×2  (expect {expected}, got {got})",
                   abs(got - expected) < 0.01, value=result)

    # ── B5 ─────────────────────────────────────────────────────────────────────
    _case("B5 — query_available_seats")

    result, err = _safe_call(query_available_seats, "NR_SCH01", "2026-07-01", "standard")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show(f"NR_SCH01 standard seats on 2026-07-01 ({len(result or [])} seats)", result[:3] if result else result)
        _check("returns a list", isinstance(result, list), value=result)
        _check("standard coach seats only (coach B for NR_SCH01)",
               all(s.get("coach") == "B" for s in (result or [])),
               hint="first class seats (coach A) should be excluded",
               value=[s.get("coach") for s in (result or [])][:5])
        _check("each item has 'seat_id'",
               all("seat_id" in s for s in (result or [])),
               value=result[0] if result else None)

    # B5 — first class seats
    result_first, err = _safe_call(query_available_seats, "NR_SCH01", "2026-07-01", "first")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _check("first class only returns coach A seats",
               all(s.get("coach") == "A" for s in (result_first or [])),
               hint="standard seats (coach B) should be excluded",
               value=[s.get("coach") for s in (result_first or [])][:5])

    # ── B6 ─────────────────────────────────────────────────────────────────────
    _case("B6 — query_user_profile")

    result, err = _safe_call(query_user_profile, "alice.tan@email.com")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("alice.tan@email.com", result)
        _check("returns a dict for known email", isinstance(result, dict), value=result)
        for key in ("email", "name", "year_of_birth"):
            _check(f"dict has '{key}'", key in (result or {}), value=result)

    result_none, err = _safe_call(query_user_profile, "nobody@nonexistent.com")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _check("unknown email → returns None (not exception)", result_none is None,
               hint="must return None, not raise an exception", value=result_none)

    # ── B7 ─────────────────────────────────────────────────────────────────────
    _case("B7 — query_user_bookings")

    # alice has rail bookings (BK001)
    result, err = _safe_call(query_user_bookings, "alice.tan@email.com")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("alice.tan (has rail booking BK001)", result)
        _check("returns a dict", isinstance(result, dict), value=result)
        _check("has 'national_rail' key", "national_rail" in (result or {}), value=result)
        _check("has 'metro' key",         "metro"         in (result or {}), value=result)
        _check("national_rail list is non-empty for alice",
               len((result or {}).get("national_rail", [])) > 0,
               value=(result or {}).get("national_rail"))

    # ben has metro bookings (MT001)
    result_ben, err = _safe_call(query_user_bookings, "ben.lim@email.com")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("ben.lim (has metro booking MT001)", result_ben)
        _check("ben: metro list is non-empty",
               len((result_ben or {}).get("metro", [])) > 0,
               hint="MT001 should appear in metro list",
               value=(result_ben or {}).get("metro"))

    # new user with no bookings (register fresh, then immediately query)
    from databases.relational.queries import register_user
    _safe_call(register_user, "test_nobooking@test.com", "TestUser", "P@ssword1",
               "1995-01-01", "07900000000", "pet name?", "fluffy")
    result_empty, err = _safe_call(query_user_bookings, "test_nobooking@test.com")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("fresh user (no bookings, expect both empty lists)", result_empty)
        _check("no-booking user → {'national_rail':[], 'metro':[]}",
               result_empty == {"national_rail": [], "metro": []},
               hint="both keys must be present and both lists must be empty",
               value=result_empty)

    # ── B8 ─────────────────────────────────────────────────────────────────────
    _case("B8 — query_payment_info")

    result, err = _safe_call(query_payment_info, "BK001")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("BK001 payment", result)
        _check("returns a dict for known booking_id", isinstance(result, dict), value=result)
        for key in ("amount_usd", "status", "method"):
            _check(f"has '{key}'",
                   key in (result or {}),
                   hint=f"grading checks for amount, status, and payment method",
                   value=list((result or {}).keys()))

    result_none, err = _safe_call(query_payment_info, "BK-DOESNOTEXIST")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _check("unknown booking_id → returns None (not exception)", result_none is None,
               value=result_none)

    # ── B9 ─────────────────────────────────────────────────────────────────────
    _case("B9 — execute_booking")

    ok, data = _safe_call(execute_booking,
        user_id="RU01",
        schedule_id="NR_SCH01",
        origin_station_id="NR01",
        destination_station_id="NR05",
        travel_date="2026-08-01",
        fare_class="standard",
        seat_id="any",
        ticket_type="single",
    )
    if ok is None:
        print(f"  {RED}EXCEPTION{RESET}: {data}")
    else:
        success, booking = ok
        _show(f"execute_booking result (success={success})", booking)
        _check("returns (True, dict) on success", success is True and isinstance(booking, dict),
               value=(success, type(booking).__name__))
        if success and isinstance(booking, dict):
            for key in ("booking_id", "user_id", "schedule_id", "seat_id"):
                _check(f"booking_dict has '{key}'", key in booking, value=list(booking.keys()))

            # Verify DB has both booking AND payment
            conn = psycopg2.connect(PG_DSN)
            cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            bid  = booking["booking_id"]
            cur.execute("SELECT 1 FROM schema1.national_rail_bookings WHERE booking_id=%s", (bid,))
            _check(f"booking {bid} exists in DB", cur.fetchone() is not None,
                   hint="booking record must be in the database")
            cur.execute("SELECT 1 FROM schema1.payments WHERE booking_id=%s", (bid,))
            _check(f"payment for {bid} exists in DB", cur.fetchone() is not None,
                   hint="payment record must also be inserted atomically")
            cur.close()
            conn.close()

            # ── B10 uses the booking we just created ───────────────────────
            _case("B10 — execute_cancellation (using booking just created)")

            ok2, data2 = _safe_call(execute_cancellation,
                booking_id=bid,
                user_id="RU01",
            )
            if ok2 is None:
                print(f"  {RED}EXCEPTION{RESET}: {data2}")
            else:
                success2, result2 = ok2
                _show(f"cancel {bid} (should succeed)", result2)
                _check("returns (True, dict) on valid cancellation",
                       success2 is True and isinstance(result2, dict),
                       value=(success2, type(result2).__name__))
                if success2 and isinstance(result2, dict):
                    _check("result_dict has 'refund_amount'", "refund_amount" in result2,
                           value=list(result2.keys()))

                # Verify DB status changed
                conn = psycopg2.connect(PG_DSN)
                cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute("SELECT status FROM schema1.national_rail_bookings WHERE booking_id=%s", (bid,))
                row = cur.fetchone()
                _check(f"DB status is 'cancelled' after cancellation",
                       row and row["status"] == "cancelled",
                       value=row["status"] if row else None)
                cur.close()
                conn.close()

                # Cancel again → should fail gracefully
                ok3, data3 = _safe_call(execute_cancellation, booking_id=bid, user_id="RU01")
                if ok3 is None:
                    print(f"  {RED}EXCEPTION{RESET}: {data3}")
                else:
                    success3, result3 = ok3
                    _show("cancel already-cancelled booking (should fail)", result3)
                    _check("already-cancelled → returns (False, message)",
                           success3 is False and isinstance(result3, str),
                           hint="should return (False, message) not raise exception",
                           value=(success3, result3))

    # B9 — duplicate seat
    _case("B9 — execute_booking: duplicate seat")
    # BK001 has seat B05 on NR_SCH01 for 2026-04-02 (status=completed, but still occupies seat)
    # Use a date where we know a seat is booked: BK009 = B04 on 2026-04-20 NR_SCH01
    ok, data = _safe_call(execute_booking,
        user_id="RU01",
        schedule_id="NR_SCH01",
        origin_station_id="NR01",
        destination_station_id="NR05",
        travel_date="2026-04-20",
        fare_class="standard",
        seat_id="B04",   # already taken by BK009 (confirmed)
        ticket_type="single",
    )
    if ok is None:
        print(f"  {RED}EXCEPTION{RESET}: {data}")
    else:
        success, msg = ok
        _show("booking already-taken seat B04 on 2026-04-20", (success, msg))
        _check("duplicate seat → returns (False, message) not exception",
               success is False and isinstance(msg, str),
               hint="should return (False, message)",
               value=(success, msg))


# ══════════════════════════════════════════════════════════════════════════════
# SECTION C — Neo4j Routing Queries
# ══════════════════════════════════════════════════════════════════════════════

def run_section_c():
    _section("SECTION C — Neo4j Routing Queries  (/35)")

    # ── C1 ─────────────────────────────────────────────────────────────────────
    _case("C1 — query_shortest_route (Metro)")

    result, err = _safe_call(query_shortest_route, "MS01", "MS04")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("MS01→MS04 shortest route", result)
        _check("returns a dict", isinstance(result, dict), value=result)
        _check("has 'path' key",          "path" in (result or {}), value=result)
        _check("has 'total_time_min' key", "total_time_min" in (result or {}), value=result)
        _check("path is a list",
               isinstance((result or {}).get("path"), list), value=(result or {}).get("path"))
        _check("total_time_min is numeric",
               isinstance((result or {}).get("total_time_min"), (int, float)),
               value=(result or {}).get("total_time_min"))

    # C1 — Rail
    _case("C1 — query_shortest_route (Rail)")

    result, err = _safe_call(query_shortest_route, "NR01", "NR05")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("NR01→NR05 shortest route", result)
        _check("returns a dict", isinstance(result, dict), value=result)
        _check("has 'path' key",          "path" in (result or {}), value=result)
        _check("has 'total_time_min' key", "total_time_min" in (result or {}), value=result)

    # C1 — unconnected stations (no route)
    result_none, err = _safe_call(query_shortest_route, "NR01", "MS09")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("NR01→MS09 (different networks, expect no route)", result_none)
        _check("unconnected stations → returns dict without crash",
               isinstance(result_none, dict),
               hint="must return a dict (empty result), not raise an exception",
               value=result_none)

    # ── C2 ─────────────────────────────────────────────────────────────────────
    _case("C2 — query_cheapest_route (standard)")

    result_std, err = _safe_call(query_cheapest_route, "NR01", "NR05", fare_class="standard")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("NR01→NR05 cheapest standard", result_std)
        _check("returns a dict", isinstance(result_std, dict), value=result_std)
        has_path = "path" in (result_std or {})
        has_cost = any(k in (result_std or {}) for k in ("total_cost_usd", "cost", "total_fare_usd"))
        _check("has 'path' key", has_path, value=result_std)
        _check("has a cost key (total_cost_usd / cost / total_fare_usd)", has_cost,
               value=list((result_std or {}).keys()))

    # C2 — first class (cost should differ)
    result_first, err = _safe_call(query_cheapest_route, "NR01", "NR05", fare_class="first")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("NR01→NR05 cheapest first class", result_first)
        if result_std and result_first:
            cost_key = next((k for k in ("total_cost_usd", "cost", "total_fare_usd")
                             if k in result_std), None)
            if cost_key and cost_key in result_first:
                _check("first class cost differs from standard",
                       result_first[cost_key] != result_std[cost_key],
                       hint="fare_class parameter must visibly affect the cost",
                       value={"standard": result_std[cost_key], "first": result_first[cost_key]})

    # ── C3 ─────────────────────────────────────────────────────────────────────
    _case("C3 — query_alternative_routes (avoid station)")

    result, err = _safe_call(query_alternative_routes, "NR01", "NR05", avoid_station_id="NR03")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("NR01→NR05 avoid NR03", result)
        _check("returns a list", isinstance(result, list), value=result)
        if result:
            all_paths_avoid = all(
                "NR03" not in [s.get("station_id", "") for s in r.get("path", [])]
                for r in result
            )
            _check("no path passes through avoided station NR03",
                   all_paths_avoid,
                   hint="NR03 must not appear in any returned path",
                   value=[r.get("path") for r in result])

    # C3 — max_routes limit
    result_1, err = _safe_call(query_alternative_routes, "NR01", "NR05",
                               avoid_station_id="NR03", max_routes=1)
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("max_routes=1", result_1)
        _check("max_routes=1 → at most 1 route returned",
               isinstance(result_1, list) and len(result_1) <= 1,
               value=len(result_1) if isinstance(result_1, list) else result_1)

    # ── C4 ─────────────────────────────────────────────────────────────────────
    _case("C4 — query_interchange_path (Metro→Rail cross-network)")

    result, err = _safe_call(query_interchange_path, "MS01", "NR05")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("MS01→NR05 interchange path", result)
        _check("returns a dict", isinstance(result, dict), value=result)
        _check("has 'path' key", "path" in (result or {}), value=result)
        if result and result.get("path"):
            station_ids = [s.get("station_id", "") for s in result["path"]]
            has_metro = any(sid.startswith("MS") for sid in station_ids)
            has_rail  = any(sid.startswith("NR") for sid in station_ids)
            _check("path includes Metro stations (MS*)", has_metro,
                   hint="cross-network path must traverse both networks",
                   value=station_ids)
            _check("path includes Rail stations (NR*)", has_rail,
                   hint="cross-network path must traverse both networks",
                   value=station_ids)

    # C4 — same-network input
    result_same, err = _safe_call(query_interchange_path, "NR01", "NR05")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("NR01→NR05 same-network (should not crash)", result_same)
        _check("same-network input → returns dict without crash",
               isinstance(result_same, dict),
               hint="must not raise exception for same-network input",
               value=result_same)

    # ── C5 ─────────────────────────────────────────────────────────────────────
    _case("C5 — query_delay_ripple")

    result, err = _safe_call(query_delay_ripple, "NR01", hops=2)
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("NR01 hops=2", result)
        _check("returns a list", isinstance(result, list), value=result)
        _check("each item has 'hops_away' key",
               all("hops_away" in s for s in (result or [])),
               hint="hops_away count must be in each result",
               value=result[0] if result else None)
        _check("no station has hops_away > 2",
               all(s.get("hops_away", 0) <= 2 for s in (result or [])),
               value=[s.get("hops_away") for s in (result or [])])

    # C5 — hops=0 returns only the delayed station itself
    result_0, err = _safe_call(query_delay_ripple, "NR01", hops=0)
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("NR01 hops=0 (expect only NR01 itself)", result_0)
        _check("hops=0 → returns only the delayed station itself",
               len(result_0 or []) == 1 and (result_0 or [{}])[0].get("station_id") == "NR01",
               hint="hops=0 must return exactly [NR01], not its neighbours",
               value=result_0)

    # ── C6 ─────────────────────────────────────────────────────────────────────
    _case("C6 — query_station_connections")

    result, err = _safe_call(query_station_connections, "MS01")
    if err:
        print(f"  {RED}EXCEPTION{RESET}: {err}")
    else:
        _show("MS01 direct neighbours", result)
        _check("returns a list", isinstance(result, list), value=result)
        _check("list is non-empty", len(result or []) > 0, value=result)
        _check("each neighbour has 'travel_time_min'",
               all("travel_time_min" in s for s in (result or [])),
               hint="travel_time_min must be present for each neighbour",
               value=result[0] if result else None)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", choices=["A", "B", "C"], default=None,
                        help="Run only a specific section (A / B / C)")
    args = parser.parse_args()

    print(f"\n{BOLD}TransitFlow — Phase 1 Live Testing Verification{RESET}")
    print(f"Connects directly to PostgreSQL and Neo4j (no UI, no LLM needed)")

    section = args.section
    if section is None or section == "A":
        run_section_a()
    if section is None or section == "B":
        run_section_b()
    if section is None or section == "C":
        run_section_c()

    total = _passed + _failed
    print(f"\n{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}結果  PASS {GREEN}{_passed}{RESET} / FAIL {RED}{_failed}{RESET} / TOTAL {total}{RESET}", end="")
    if _errors:
        print(f"  (+ {YELLOW}{_errors} exception(s){RESET})", end="")
    print()
    if _failed == 0 and _errors == 0:
        print(f"{GREEN}{BOLD}全部通過！{RESET}")
    else:
        print(f"{YELLOW}請修正上方標示為 FAIL 的項目後再重新執行。{RESET}")
    print()


if __name__ == "__main__":
    main()

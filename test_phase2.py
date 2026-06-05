"""
Phase 2 — 透過 agent.py 驗證 LLM 能正確選 tool 並帶齊參數

測試路徑：自然語言 → run_agent(debug=True) → 解析 tool_calls → 確認 tool + params
這才是 TA live testing 的真實路徑。

執行方式（從 project root）：
    python test_phase2.py
    python test_phase2.py --section B
    python test_phase2.py --section C
"""

import sys
import re
import json
import argparse

sys.path.insert(0, ".")
from skeleton.agent import run_agent

# ── ANSI colours ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

_passed = 0
_failed = 0

LOGGED_IN_USER = "alice.tan@email.com"   # RU01, has rail bookings


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ask(prompt: str, logged_in: bool = False) -> tuple[str, list[str]]:
    """Call run_agent with debug=True, return (reply, debug_lines)."""
    email = LOGGED_IN_USER if logged_in else None
    result = run_agent(prompt, [], debug=True, current_user_email=email)
    reply, _, debug_text = result
    debug_lines = debug_text.splitlines() if debug_text else []
    return reply, debug_lines


def _called_tools(debug_lines: list[str]) -> list[dict]:
    """
    Parse debug lines for '**Calling:** `tool_name(params)`' entries.
    Returns list of {"name": str, "params": dict}.
    """
    tools = []
    for line in debug_lines:
        m = re.search(r'\*\*Calling:\*\*\s*`([^(]+)\((\{.*\})\)`', line)
        if m:
            name = m.group(1).strip()
            try:
                params = json.loads(m.group(2).replace("'", '"'))
            except Exception:
                params = {}
            tools.append({"name": name, "params": params})
    return tools


def _check(label: str, condition: bool, hint: str = "", extra: str = ""):
    global _passed, _failed
    icon = f"{GREEN}PASS{RESET}" if condition else f"{RED}FAIL{RESET}"
    print(f"  {icon}  {label}")
    if not condition:
        _failed += 1
        if hint:
            print(f"        {YELLOW}hint : {hint}{RESET}")
        if extra:
            print(f"        got  : {extra[:200]}")
    else:
        _passed += 1


def _section(title: str):
    print(f"\n{BOLD}{CYAN}{'─'*60}{RESET}")
    print(f"{BOLD}{CYAN}{title}{RESET}")
    print(f"{BOLD}{CYAN}{'─'*60}{RESET}")


def _case(label: str, prompt: str, logged_in: bool = False):
    tag = f" {YELLOW}[logged in]{RESET}" if logged_in else ""
    print(f"\n  {BOLD}{label}{tag}{RESET}")
    print(f"  {YELLOW}prompt:{RESET} \"{prompt}\"")
    reply, debug_lines = _ask(prompt, logged_in=logged_in)
    tools = _called_tools(debug_lines)
    called_names = [t["name"] for t in tools]
    print(f"  {YELLOW}tools called:{RESET} {called_names if called_names else '(none)'}")
    return reply, tools, debug_lines


# ══════════════════════════════════════════════════════════════════════════════
# SECTION B
# ══════════════════════════════════════════════════════════════════════════════

def run_section_b():
    _section("SECTION B — PostgreSQL via agent  (/50)")

    # ── B1 ─────────────────────────────────────────────────────────────────────
    reply, tools, dbg = _case(
        "B1 — check_national_rail_availability (with date)",
        "What trains run from Central Station (NR01) to Stonehaven (NR05) on 2026-07-01?",
    )
    called = [t["name"] for t in tools]
    _check("called check_national_rail_availability",
           "check_national_rail_availability" in called,
           hint="should call check_national_rail_availability, not find_route",
           extra=str(called))
    avail_call = next((t for t in tools if t["name"] == "check_national_rail_availability"), None)
    if avail_call:
        p = avail_call["params"]
        _check("origin_id present",     bool(p.get("origin_id")),      extra=str(p))
        _check("destination_id present", bool(p.get("destination_id")), extra=str(p))
        _check("travel_date present",    bool(p.get("travel_date")),
               hint="missing travel_date → no seat availability in result", extra=str(p))
        _check("travel_date = 2026-07-01", p.get("travel_date") == "2026-07-01", extra=str(p))

    # B1 no-service scenario
    reply2, tools2, _ = _case(
        "B1 — no service (different lines)",
        "Are there any trains from NR03 to NR07?",
    )
    called2 = [t["name"] for t in tools2]
    _check("still calls check_national_rail_availability (even for no-service)",
           "check_national_rail_availability" in called2,
           extra=str(called2))

    # ── B2 ─────────────────────────────────────────────────────────────────────
    reply, tools, _ = _case(
        "B2 — check_metro_availability",
        "What metro lines run from Central Square (MS01) to Northgate (MS03)?",
    )
    called = [t["name"] for t in tools]
    _check("called check_metro_availability",
           "check_metro_availability" in called,
           extra=str(called))
    metro_call = next((t for t in tools if t["name"] == "check_metro_availability"), None)
    if metro_call:
        p = metro_call["params"]
        _check("origin_id present",      bool(p.get("origin_id")),      extra=str(p))
        _check("destination_id present", bool(p.get("destination_id")), extra=str(p))

    # ── B3 ─────────────────────────────────────────────────────────────────────
    reply, tools, _ = _case(
        "B3 — get_national_rail_fare",
        "What is the standard fare from Central Station (NR01) to Stonehaven (NR05)?",
    )
    called = [t["name"] for t in tools]
    _check("called get_national_rail_fare or check_national_rail_availability",
           any(n in called for n in ("get_national_rail_fare", "check_national_rail_availability")),
           hint="either tool should surface fare info",
           extra=str(called))

    # ── B4 ─────────────────────────────────────────────────────────────────────
    reply, tools, _ = _case(
        "B4 — get_metro_fare / calculate_metro_fare",
        "How much does a metro trip from Central Square (MS01) to Northgate (MS03) cost?",
    )
    called = [t["name"] for t in tools]
    _check("called a metro fare tool",
           any(n in called for n in ("get_metro_fare", "calculate_metro_fare", "check_metro_availability")),
           extra=str(called))

    # ── B5 ─────────────────────────────────────────────────────────────────────
    reply, tools, _ = _case(
        "B5 — get_available_seats",
        "What standard class seats are available on schedule NR_SCH01 on 2026-07-01?",
    )
    called = [t["name"] for t in tools]
    _check("called get_available_seats",
           "get_available_seats" in called,
           extra=str(called))
    seat_call = next((t for t in tools if t["name"] == "get_available_seats"), None)
    if seat_call:
        p = seat_call["params"]
        _check("schedule_id present", bool(p.get("schedule_id")), extra=str(p))
        _check("travel_date present", bool(p.get("travel_date")), extra=str(p))
        _check("fare_class present",  bool(p.get("fare_class")),  extra=str(p))

    # ── B6/B7 ──────────────────────────────────────────────────────────────────
    reply, tools, _ = _case(
        "B6/B7 — get_user_bookings (logged in)",
        "Show me my bookings.",
        logged_in=True,
    )
    called = [t["name"] for t in tools]
    _check("called get_user_bookings",
           "get_user_bookings" in called,
           hint="logged-in user asking about bookings must trigger get_user_bookings",
           extra=str(called))

    # B6/B7 — not logged in, should NOT call the tool
    reply2, tools2, _ = _case(
        "B6/B7 — no booking access when not logged in",
        "Show me my bookings.",
        logged_in=False,
    )
    called2 = [t["name"] for t in tools2]
    # Even if LLM calls get_user_bookings without login, execution layer
    # returns {"error": "No user is currently logged in."} — check that.
    _check("NOT logged in → get_user_bookings returns error (not booking data)",
           "get_user_bookings" not in called2 or
           "No user is currently logged in" in reply2 or
           "log in" in reply2.lower(),
           hint="guest should see a login error, not booking data",
           extra=f"tools={called2} | reply={reply2[:100]}")

    # ── B8 ─────────────────────────────────────────────────────────────────────
    # payment info surfaces through bookings; no direct tool — skip direct check
    # TA will verify via B7 booking records which include payment linkage

    # ── B9 ─────────────────────────────────────────────────────────────────────
    reply, tools, _ = _case(
        "B9 — make_booking flow (logged in)",
        "I want to book a standard class seat from Central Station (NR01) to Stonehaven (NR05) on 2026-09-01.",
        logged_in=True,
    )
    called = [t["name"] for t in tools]
    _check("called check_national_rail_availability first (booking flow step 1)",
           "check_national_rail_availability" in called,
           hint="agent should check availability before booking",
           extra=str(called))

    # B9 — not logged in, must not book
    reply2, tools2, _ = _case(
        "B9 — make_booking blocked when not logged in",
        "Book me a seat NR01 to NR05 on 2026-09-01.",
        logged_in=False,
    )
    called2 = [t["name"] for t in tools2]
    _check("NOT logged in → make_booking NOT called",
           "make_booking" not in called2,
           hint="guest must be told to log in first",
           extra=str(called2))

    # ── B10 ────────────────────────────────────────────────────────────────────
    reply, tools, _ = _case(
        "B10 — cancel_booking (logged in)",
        "Please cancel my booking BK001.",
        logged_in=True,
    )
    called = [t["name"] for t in tools]
    _check("called cancel_booking",
           "cancel_booking" in called,
           extra=str(called))
    cancel_call = next((t for t in tools if t["name"] == "cancel_booking"), None)
    if cancel_call:
        p = cancel_call["params"]
        _check("booking_id present", bool(p.get("booking_id")), extra=str(p))
        _check("booking_id = BK001", p.get("booking_id") == "BK001", extra=str(p))


# ══════════════════════════════════════════════════════════════════════════════
# SECTION C
# ══════════════════════════════════════════════════════════════════════════════

def run_section_c():
    _section("SECTION C — Neo4j via agent  (/35)")

    # ── C1 shortest route ──────────────────────────────────────────────────────
    reply, tools, _ = _case(
        "C1 — find_route (fastest, metro)",
        "What is the fastest route from Central Square (MS01) to Elm Park (MS04)?",
    )
    called = [t["name"] for t in tools]
    _check("called find_route",
           "find_route" in called,
           extra=str(called))
    rt = next((t for t in tools if t["name"] == "find_route"), None)
    if rt:
        p = rt["params"]
        _check("origin_id present",      bool(p.get("origin_id")),      extra=str(p))
        _check("destination_id present", bool(p.get("destination_id")), extra=str(p))
        _check("optimise_by = time",
               p.get("optimise_by", "time") == "time",
               hint="'fastest' should map to optimise_by=time",
               extra=str(p))

    reply2, tools2, _ = _case(
        "C1 — find_route (fastest, rail)",
        "Fastest route from Central Station (NR01) to Stonehaven (NR05)?",
    )
    called2 = [t["name"] for t in tools2]
    _check("called find_route for rail",
           "find_route" in called2,
           extra=str(called2))

    # ── C2 cheapest route ──────────────────────────────────────────────────────
    reply, tools, _ = _case(
        "C2 — find_route (cheapest)",
        "What is the cheapest route from Central Station (NR01) to Stonehaven (NR05)?",
    )
    called = [t["name"] for t in tools]
    _check("called find_route",
           "find_route" in called,
           extra=str(called))
    rt = next((t for t in tools if t["name"] == "find_route"), None)
    if rt:
        p = rt["params"]
        _check("optimise_by = cost",
               p.get("optimise_by") == "cost",
               hint="'cheapest' should map to optimise_by=cost",
               extra=str(p))

    # ── C3 alternative routes ──────────────────────────────────────────────────
    reply, tools, _ = _case(
        "C3 — find_alternative_routes",
        "Are there alternative routes from Central Station (NR01) to Stonehaven (NR05) that avoid Old Town Junction (NR03)?",
    )
    called = [t["name"] for t in tools]
    _check("called find_alternative_routes",
           "find_alternative_routes" in called,
           extra=str(called))
    alt = next((t for t in tools if t["name"] == "find_alternative_routes"), None)
    if alt:
        p = alt["params"]
        _check("avoid_station_id present", bool(p.get("avoid_station_id")), extra=str(p))
        _check("avoid_station_id = NR03",
               p.get("avoid_station_id", "").upper() == "NR03",
               extra=str(p))

    # ── C4 interchange path ────────────────────────────────────────────────────
    reply, tools, _ = _case(
        "C4 — cross-network interchange",
        "How do I get from Central Square (MS01) to Stonehaven (NR05)?",
    )
    called = [t["name"] for t in tools]
    _check("called find_route (cross-network handled by find_route)",
           "find_route" in called,
           hint="cross-network journey should use find_route",
           extra=str(called))

    # ── C5 delay ripple ────────────────────────────────────────────────────────
    reply, tools, _ = _case(
        "C5 — get_delay_ripple",
        "If Central Station (NR01) is delayed, which nearby stations are affected?",
    )
    called = [t["name"] for t in tools]
    _check("called get_delay_ripple",
           "get_delay_ripple" in called,
           extra=str(called))
    ripple = next((t for t in tools if t["name"] == "get_delay_ripple"), None)
    if ripple:
        p = ripple["params"]
        _check("delayed_station_id present",
               bool(p.get("delayed_station_id") or p.get("station_id")),
               extra=str(p))

    # ── C6 station connections ─────────────────────────────────────────────────
    reply, tools, _ = _case(
        "C6 — get_station_connections",
        "What stations are directly connected to Central Square (MS01)?",
    )
    called = [t["name"] for t in tools]
    _check("called get_station_connections",
           "get_station_connections" in called,
           extra=str(called))
    conn = next((t for t in tools if t["name"] == "get_station_connections"), None)
    if conn:
        p = conn["params"]
        _check("station_id present", bool(p.get("station_id")), extra=str(p))


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--section", choices=["B", "C"], default=None)
    args = parser.parse_args()

    print(f"\n{BOLD}TransitFlow — Phase 2: Agent Tool-Call Verification{RESET}")
    print(f"Tests that run_agent() triggers the correct tool with correct params.")
    print(f"Logged-in user for auth tests: {LOGGED_IN_USER}\n")

    if args.section is None or args.section == "B":
        run_section_b()
    if args.section is None or args.section == "C":
        run_section_c()

    total = _passed + _failed
    print(f"\n{BOLD}{'═'*60}{RESET}")
    print(f"{BOLD}結果  PASS {GREEN}{_passed}{RESET} / FAIL {RED}{_failed}{RESET} / TOTAL {total}{RESET}")
    if _failed == 0:
        print(f"{GREEN}{BOLD}全部通過！{RESET}")
    else:
        print(f"{YELLOW}請檢查上方 FAIL 項目 — LLM 沒有呼叫到正確 tool 或參數不完整。{RESET}")
    print()


if __name__ == "__main__":
    main()

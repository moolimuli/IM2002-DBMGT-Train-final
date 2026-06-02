# TASK 6 EXTENSION: Added feedback query tool, policy keyword fallback routing,
# content truncation fix, lost property / accessibility keyword support,
# and _strip_metadata embedding optimisation integration.
"""
TransitFlow — Intelligent Agent
================================
This is the brain of the system.

HOW IT WORKS (the pipeline students should understand):
  1. User asks a natural language question
  2. The LLM reads the question and decides which databases to query
     (this is called "tool use" or "function calling")
  3. Each database query runs and returns structured data
  4. The LLM reads all the data and writes a helpful answer
  5. The answer is returned to the Gradio UI

THE THREE DATABASE ROLES IN THIS FILE:
  - Relational (PostgreSQL)  → schedules, fares, bookings, seat layouts, users
  - Vector (pgvector / RAG)  → policy documents (refunds, conduct, luggage, etc.)
  - Graph (Neo4j)            → route finding, delay ripple, cross-network paths

STUDENT TASK
------------
You do NOT need to rewrite this file.
Your goal is to make the database queries richer by:
  1. Adding more data to PostgreSQL (new tables, more seed data)
  2. Writing better Cypher in databases/graph/queries.py
  3. Adding more policy documents (databases/vector/documents.py)

The agent will automatically use whatever you put in the databases.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

from skeleton.llm_provider import llm
from databases.relational.queries import (
    query_national_rail_availability,
    query_national_rail_fare,
    query_metro_schedules,
    query_metro_fare,
    query_available_seats,
    auto_select_adjacent_seats,
    query_user_profile,
    query_user_bookings,
    execute_booking,
    execute_cancellation,
    query_policy_vector_search,
    # ── ADDED 2026-05-29: feedback query for get_feedback_summary tool ────
    query_feedback_summary,
)
from databases.graph.queries import (
    query_shortest_route,
    query_cheapest_route,
    query_alternative_routes,
    query_interchange_path,
    query_delay_ripple,
    query_station_connections,
)


# ── Station name → ID lookup (resolved in Python, not by the LLM) ────────────

_STATION_INDEX: dict[str, str] = {
    # Metro
    "central square": "MS01", "riverside":   "MS02", "northgate":  "MS03",
    "elm park":       "MS04", "westfield":   "MS05", "harbour view": "MS06",
    "old town":       "MS07", "university":  "MS08", "queensbridge": "MS09",
    "parkside":       "MS10", "greenhill":   "MS11", "lakeshore":  "MS12",
    "clifton":        "MS13", "eastwick":    "MS14", "ferndale":   "MS15",
    "hilltop":        "MS16", "broadmoor":   "MS17", "sunnyvale":  "MS18",
    "redwood":        "MS19", "thornton":    "MS20",
    # National Rail (longer/specific names first so they match before shorter substrings)
    "central station":   "NR01", "maplewood":     "NR02",
    "old town junction": "NR03", "ashford":        "NR04",
    "stonehaven":        "NR05", "bridgeport":     "NR06",
    "ferndale halt":     "NR07", "coalport":       "NR08",
    "dunmore":           "NR09", "langford end":   "NR10",
}


def _inject_station_ids(text: str) -> str:
    """
    Replace station names in text with 'name (ID)' so the LLM reads the ID
    right next to the name and uses it as the parameter value.
    Longer names are substituted first so 'Old Town Junction' beats 'Old Town'.
    Returns the original text unchanged when no stations are found.
    """
    result = text
    seen_ids: set[str] = set()
    for name in sorted(_STATION_INDEX, key=len, reverse=True):
        sid = _STATION_INDEX[name]
        if sid in seen_ids:
            continue
        pattern = re.compile(re.escape(name), re.IGNORECASE)
        if pattern.search(result):
            result = pattern.sub(f"{name} ({sid})", result)
            seen_ids.add(sid)
    return result


# ── System prompt ─────────────────────────────────────────────────────────────
###nini fix
SYSTEM_PROMPT = """You are TransitFlow, a transit assistant for a dual-network system.

Networks: City Metro MS01-MS20 (lines M1-M4) | National Rail NR01-NR10 (lines NR1-NR2)
Interchanges: Central Square (MS01) ↔ Central Station (NR01) | Old Town (MS07, metro) ↔ Old Town Junction (NR03, rail) | Ferndale (MS15, metro) ↔ Ferndale Halt (NR07, rail)
IMPORTANT: MS07 is Old Town metro station. NR03 is Old Town Junction rail station. They are different stations. When user says NR03, use NR03. When user says MS07, use MS07.
Today: {today}

LOGIN RULE: Routes, fares, schedules, and policies work WITHOUT login for all users. Only make_booking and cancel_booking need login — if the user tries to book or cancel and is not logged in, tell them to log in first. If the user IS logged in (their name and user_id appear in this prompt), never tell them to log in. Treat them as authenticated for make_booking and cancel_booking.

When DATA FROM TRANSITFLOW DATABASE is provided, use it as the only source of truth. Do not contradict it or say a route was not found if the data shows one.
For route results: list every station name in order, note any line changes, and give the total travel time.
If a tool returns found: false, no results, or an empty list, say the information was not found. Never invent stations, routes, schedules, or prices that were not in the database results.
Always reply in the same language as the user.
""".format(today=date.today().isoformat())
###nini fix end

# ── Tool definitions (sent to the LLM to decide which to call) ────────────────

TOOLS = [
    {
        ###nini fix
        "name": "check_national_rail_availability",
        "description": (
            "Check available national rail trains between two stations. "
            "Use for any question about what trains run, schedules, timetables, or availability. "
            "If user mentions a travel date, ALWAYS pass it as travel_date parameter. "
            "Returns schedules, fares, and seat availability when travel_date is provided."
),      ###nini fix end
        "parameters": {
            "origin_id":      {"type": "string", "description": "National rail station ID e.g. NR01"},
            "destination_id": {"type": "string", "description": "National rail station ID e.g. NR05"},
            "travel_date":    {"type": "string", "description": "YYYY-MM-DD (optional — omit for general info)"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "get_national_rail_fare",
        "description": "Calculate the fare for a national rail journey on a specific schedule.",
        "parameters": {
            "schedule_id":     {"type": "string", "description": "e.g. NR_SCH01"},
            "fare_class":      {"type": "string", "description": "standard or first"},
            "stops_travelled": {"type": "integer", "description": "Number of stops between origin and destination (from availability result)"},
        },
        "required": ["schedule_id", "fare_class", "stops_travelled"],
    },
    {
        "name": "check_metro_availability",
        "description": "Check available metro services between two metro stations.",
        "parameters": {
            "origin_id":      {"type": "string", "description": "Metro station ID e.g. MS01"},
            "destination_id": {"type": "string", "description": "Metro station ID e.g. MS09"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "calculate_metro_fare",
        "description": "Calculate the metro single-ticket fare for a journey.",
        "parameters": {
            "schedule_id":     {"type": "string", "description": "e.g. MS_SCH01"},
            "stops_travelled": {"type": "integer", "description": "Number of stops between origin and destination"},
        },
        "required": ["schedule_id", "stops_travelled"],
    },
    {
        "name": "get_metro_fare",
        "description": (
            "Get the metro ticket PRICE between two stations. "
            "Use ONLY for fare/price/cost questions ('how much does it cost', 'what is the fare'). "
            "Do NOT use this for route or direction questions — use find_route instead."
        ),
        "parameters": {
            "origin_id":      {"type": "string", "description": "Metro station ID e.g. MS01"},
            "destination_id": {"type": "string", "description": "Metro station ID e.g. MS09"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        "name": "get_user_bookings",
        "description": (
            "Retrieve the logged-in user's full booking history (national rail bookings + metro trips). "
            "Use whenever the user asks about their tickets, journeys, or travel history. "
            "Requires login — no parameters needed."
        ),
        "parameters": {},
        "required": [],
    },
    {      
        ###nini fix
        "name": "get_available_seats",
        "description": (
            "Show available seats on a national rail service. "
            "REQUIRED parameters: schedule_id, travel_date, AND fare_class (standard or first). "
            "Always call this before make_booking when user wants to select a specific seat."
),      ###nini fix end
        "parameters": {
            "schedule_id":  {"type": "string", "description": "e.g. NR_SCH01"},
            "travel_date":  {"type": "string", "description": "YYYY-MM-DD"},
            "fare_class":   {"type": "string", "description": "standard or first"},
        },
        "required": ["schedule_id", "travel_date", "fare_class"],
    },
    {
        #nini fix
        "name": "make_booking",
        "description": (
            "USE THIS TOOL when the user explicitly says 'book', 'reserve', 'purchase', "
            "or 'buy a ticket' and provides a schedule_id, origin, destination, date, and fare_class. "
            "REQUIRES LOGIN. Only call after the user has confirmed all booking details. "
            "Do NOT call this speculatively. Do NOT use check_national_rail_availability instead."
        ), ###nini fix end
        "parameters": {
            "schedule_id":            {"type": "string", "description": "e.g. NR_SCH01"},
            "origin_station_id":      {"type": "string", "description": "e.g. NR01"},
            "destination_station_id": {"type": "string", "description": "e.g. NR05"},
            "travel_date":            {"type": "string", "description": "YYYY-MM-DD"},
            "fare_class":             {"type": "string", "description": "standard or first"},
            ###nini fix
            "seat_id": {"type": "string", "description": "Specific seat ID (e.g. B05) or 'any' for auto-assign. DEFAULT: use 'any' if user does not specify a seat."},
            ###nini fix end
            "ticket_type":            {"type": "string", "description": "single or return (default single)"},
        },
        "required": ["schedule_id", "origin_station_id", "destination_station_id", "travel_date", "fare_class", "seat_id"],
    },
    {
        ###nini fix
        "name": "cancel_booking",
        "description": (
            "USE THIS TOOL when the user explicitly says 'cancel', 'cancel my booking', "
            "or 'I want to cancel' and provides a booking_id (format: BK-XXXXXX or BK001 etc). "
            "REQUIRES LOGIN. Do NOT use get_user_bookings instead of this tool. "
            "Only call after the user has explicitly confirmed the cancellation."
        ),
        "parameters": {
            "booking_id": {"type": "string", "description": "Booking reference e.g. BK-A1B2C3"},
        },
        "required": ["booking_id"],
    },
    {
        ###nini fix
        "name": "search_policy",
        "description": (
            "ALWAYS USE THIS TOOL for ANY question about compensation, refunds, or policies. "
            "USE THIS when user asks what they are 'entitled to', about 'compensation' for delays, "
            "or about 'policy' for luggage, bicycles, pets, food, conduct, booking rules, ticket types, "
            "child fares, group discounts, lost property, or accessibility. "
            "Do NOT use get_delay_ripple for compensation questions. "
            "Trigger words: 'compensation', 'entitled', 'refund', 'policy', 'rules', 'allowed', 'delayed'."
),      ###nini fix end
        "parameters": {
            "query": {"type": "string", "description": "Natural language question about policy"},
        },
        "required": ["query"],
    },
    # ── ADDED 2026-05-29: feedback summary tool ─────────────────────────────
    {
        "name": "get_feedback_summary",
        "description": (
            "Get passenger feedback ratings and comments. "
            "Use when the user asks about ratings, reviews, stars, feedback, "
            "or satisfaction scores. Can optionally filter by a specific booking ID."
        ),
        "parameters": {
            "booking_id": {"type": "string", "description": "Optional booking ID e.g. BK001 or MT001 to filter feedback"},
        },
        "required": [],
    },
    # ── END ADDED 2026-05-29 ────────────────────────────────────────────────
    {
        "name": "find_route",
        "description": (
            "Find the best route or path between two stations. Use for ANY question about "
            "directions, how to get from A to B, fastest route, quickest route, or shortest path. "
            "Works for metro-only, rail-only, or cross-network journeys. "
            "IMPORTANT: if the user says 'fastest', 'quickest', or 'shortest time' use optimise_by='time'. "
            "If the user says 'cheapest', 'lowest fare', or 'least expensive' use optimise_by='cost'."
        ),
        "parameters": {
            "origin_id":      {"type": "string", "description": "Station ID e.g. MS01 or NR01"},
            "destination_id": {"type": "string", "description": "Station ID e.g. MS09 or NR05"},
            "network":        {"type": "string", "description": "metro, rail, or auto (default auto — inferred from IDs)"},
            "optimise_by":    {"type": "string", "description": "time for fastest route (default), cost for cheapest route"},
        },
        "required": ["origin_id", "destination_id"],
    },
    {
        ###nini fix
        "name": "find_alternative_routes",
        "description": (
            "USE THIS TOOL when the user asks about alternative routes or paths that AVOID a specific "
            "station. Trigger words: 'closed', 'avoid', 'alternative', 'if X is closed', 'without going through'. "
            "REQUIRED: origin_id, destination_id, AND avoid_station_id. "
            "Do NOT use find_route or check_national_rail_availability for this type of question."
        ),   ###nini fix end
        "parameters": {
            "origin_id":        {"type": "string", "description": "Station ID e.g. MS01 or NR01"},
            "destination_id":   {"type": "string", "description": "Station ID e.g. MS09 or NR05"},
            "avoid_station_id": {"type": "string", "description": "Station ID to avoid e.g. MS05"},
            "network":          {"type": "string", "description": "metro, rail, or auto"},
        },
        "required": ["origin_id", "destination_id", "avoid_station_id"],
    },
    {
        "name": "get_station_connections",
        "description": (
            "Get all direct connections from a station — which stations it links to, "
            "which lines serve it, and travel times to each neighbour. "
            "Use when the user asks what stations are directly connected to a station, "
            "or which lines serve a station."
        ),
        "parameters": {
            "station_id": {"type": "string", "description": "Station ID e.g. MS01 or NR01"},
        },
        "required": ["station_id"],
    },
    {
        ###nini fix
        "name": "get_delay_ripple",
        "description": "Show which STATIONS are affected by a disruption at a specific station ID. Use ONLY when user asks which stations are impacted, NOT for compensation or refund questions.",
        ###nini fix end
        "parameters": {
            "delayed_station_id": {"type": "string", "description": "Station ID of the delayed/disrupted station e.g. NR03 or MS07"},
            "hops":               {"type": "integer", "description": "How many connections out to check (default 2)"},
        },
        "required": ["delayed_station_id"],
    },
]
#nini fix
TOOLS_SCHEMA = """\
find_route(origin_id, destination_id, optimise_by?)
check_national_rail_availability(origin_id, destination_id, travel_date?)
get_national_rail_fare(schedule_id, fare_class, stops_travelled)
check_metro_availability(origin_id, destination_id)
calculate_metro_fare(schedule_id, stops_travelled)
get_available_seats(schedule_id, travel_date, fare_class)
make_booking(schedule_id, origin_station_id, destination_station_id, travel_date, fare_class, seat_id, ticket_type?)  # USE when user says book/reserve/buy ticket
cancel_booking(booking_id)
get_user_bookings()
search_policy(query)
get_feedback_summary(booking_id?)
find_alternative_routes(origin_id, destination_id, avoid_station_id, network?)
get_station_connections(station_id)
get_delay_ripple(delayed_station_id, hops?)"""
###nini fix end

# ── Agent logic ───────────────────────────────────────────────────────────────

def _execute_tool(
    tool_name: str,
    params: dict,
    current_user_email: Optional[str] = None,
) -> str:
    """
    Execute a tool call and return the result as a JSON string.
    This is where the LLM's decision meets the actual databases.
    """
    try:
        if tool_name == "check_national_rail_availability":
            result = query_national_rail_availability(**params)

        elif tool_name == "get_national_rail_fare":
            result = query_national_rail_fare(**params)

        elif tool_name == "check_metro_availability":
            result = query_metro_schedules(
                origin_id=params["origin_id"],
                destination_id=params["destination_id"],
            )

        elif tool_name == "calculate_metro_fare":
            result = query_metro_fare(**params)

        elif tool_name == "get_metro_fare":
            schedules = query_metro_schedules(
                origin_id=params["origin_id"],
                destination_id=params["destination_id"],
            )
            if not schedules:
                result = {"error": "No metro service found between these stations."}
            else:
                sched = schedules[0]
                stops = sched.get("stops_in_order") or []
                if isinstance(stops, str):
                    import json as _json
                    stops = _json.loads(stops)
                try:
                    n_stops = stops.index(params["destination_id"]) - stops.index(params["origin_id"])
                except ValueError:
                    n_stops = 1
                fare = query_metro_fare(sched["schedule_id"], n_stops)
                result = {
                    "origin":       sched.get("origin_name", params["origin_id"]),
                    "destination":  sched.get("destination_name", params["destination_id"]),
                    "line":         sched.get("line"),
                    "schedule_id":  sched["schedule_id"],
                    "stops":        n_stops,
                    **(fare or {"error": "Fare lookup failed"}),
                }

        elif tool_name == "get_user_bookings":
            if not current_user_email:
                return json.dumps({"error": "No user is currently logged in."})
            result = query_user_bookings(current_user_email)

        elif tool_name == "get_available_seats":
            result = query_available_seats(**params)

        elif tool_name == "make_booking":
            if not current_user_email:
                return json.dumps({"error": "You must be logged in to make a booking."})
            profile = query_user_profile(current_user_email)
            if not profile:
                return json.dumps({"error": "User profile not found."})
            ok, data = execute_booking(
                user_id=profile["user_id"],
                schedule_id=params["schedule_id"],
                origin_station_id=params["origin_station_id"],
                destination_station_id=params["destination_station_id"],
                travel_date=params["travel_date"],
                fare_class=params["fare_class"],
                seat_id=params["seat_id"],
                ticket_type=params.get("ticket_type", "single"),
            )
            result = data if ok else {"error": data}

        elif tool_name == "cancel_booking":
            if not current_user_email:
                return json.dumps({"error": "You must be logged in to cancel a booking."})
            profile = query_user_profile(current_user_email)
            if not profile:
                return json.dumps({"error": "User profile not found."})
            ok, data = execute_cancellation(
                booking_id=params["booking_id"],
                user_id=profile["user_id"],
            )
            result = data if ok else {"error": data}

        elif tool_name == "search_policy":
            embedding = llm.embed(params["query"])
            docs = query_policy_vector_search(embedding)
            # ── MODIFIED 2026-05-28 ─────────────────────────────────────────
            # Increased content truncation from 800 to 2000 characters.
            # 800 chars was too short for refund policies with multiple
            # cancellation windows (RF001 has 4 windows; the 3rd and 4th
            # were being cut off, causing the LLM to miss relevant rules).
            # ────────────────────────────────────────────────────────────────
            result = [
                {
                    "title":      d["title"],
                    "category":   d["category"],
                    "content":    d["content"][:2000],
                    "similarity": round(d["similarity"], 3),
                }
                for d in docs
            ]
            # ── END MODIFIED 2026-05-28 ─────────────────────────────────────

        # ── ADDED 2026-05-29: feedback summary tool execution ────────────
        elif tool_name == "get_feedback_summary":
            result = query_feedback_summary(params.get("booking_id"))
        # ── END ADDED 2026-05-29 ────────────────────────────────────────

        elif tool_name == "find_route":
            origin_id      = params["origin_id"]
            destination_id = params["destination_id"]
            network        = params.get("network", "auto")
            optimise_by    = params.get("optimise_by", "time")

            # Detect cross-network routing (one MS, one NR)
            is_cross = (
                (origin_id.upper().startswith("MS") and destination_id.upper().startswith("NR")) or
                (origin_id.upper().startswith("NR") and destination_id.upper().startswith("MS"))
            )

            if is_cross:
                result = query_interchange_path(origin_id, destination_id)
            elif optimise_by == "cost":
                result = query_cheapest_route(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    network=network,
                )
            else:
                result = query_shortest_route(
                    origin_id=origin_id,
                    destination_id=destination_id,
                    network=network,
                )

        elif tool_name == "find_alternative_routes":
            routes = query_alternative_routes(
                origin_id=params["origin_id"],
                destination_id=params["destination_id"],
                avoid_station_id=params["avoid_station_id"],
                network=params.get("network", "auto"),
            )
            result = [{"route_number": i + 1, "legs": r} for i, r in enumerate(routes)]

        elif tool_name == "get_station_connections":
            result = query_station_connections(**params)

        elif tool_name == "get_delay_ripple":
            result = query_delay_ripple(
                delayed_station_id=params["delayed_station_id"],
                hops=params.get("hops", 2),
            )

        else:
            result = {"error": f"Unknown tool: {tool_name}"}

        return json.dumps(result, default=str)

    except Exception as e:
        return json.dumps({"error": str(e)})


def _flatten_to_text(obj, depth: int = 0) -> str:
    """Recursively convert any JSON value to indented key-value text."""
    pad = "  " * depth
    if isinstance(obj, dict):
        if not obj:
            return f"{pad}(empty)"
        lines = []
        for k, v in obj.items():
            if v is None:
                continue
            if isinstance(v, (dict, list)):
                inner = _flatten_to_text(v, depth + 1)
                if inner.strip():
                    lines.append(f"{pad}{k}:\n{inner}")
            else:
                lines.append(f"{pad}{k}: {v}")
        return "\n".join(lines) or f"{pad}(empty)"
    elif isinstance(obj, list):
        if not obj:
            return f"{pad}(no records)"
        parts = []
        for i, item in enumerate(obj, 1):
            if isinstance(item, (dict, list)):
                parts.append(f"{pad}[{i}]")
                parts.append(_flatten_to_text(item, depth + 1))
            else:
                parts.append(f"{pad}- {item}")
        return "\n".join(parts)
    else:
        return f"{pad}{obj}"


def _normalise_result(tool_name: str, result_json: str) -> str:
    """
    Convert raw tool JSON to structured readable text for the answer LLM.
    Pure Python — works for any tool output without per-tool code.
    Students never need to touch this when adding new tools.
    """
    try:
        data = json.loads(result_json)
    except json.JSONDecodeError:
        return result_json
    if isinstance(data, dict) and "error" in data:
        return f"Error: {data['error']}"
    return _flatten_to_text(data)


def _summarise_result(tool_name: str, result_json: str) -> str:
    """Raw result string shown in the debug panel only."""
    return result_json


def _parse_tool_calls(llm_response: str) -> list[dict] | None:
    """
    Parse tool call JSON from the LLM response.

    The LLM is prompted to respond ONLY with a JSON block when it wants
    to call tools. Format:
        {"tool_calls": [{"name": "...", "params": {...}}, ...]}
    """
    import re
    text = llm_response.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    # raw_decode stops after the first complete JSON object, so it handles both
    # preamble text and multiple JSON objects in one response (common on small models).
    decoder = json.JSONDecoder()
    for m in re.finditer(r'\{', text):
        try:
            data, _ = decoder.raw_decode(text, m.start())
            if "tool_calls" in data:
                return data["tool_calls"]
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return None


def run_agent(
    user_message: str,
    history: list[dict],
    debug: bool = False,
    current_user_email: Optional[str] = None,
) -> tuple:
    """
    Main agent loop.

    Args:
        user_message:       The user's latest message
        history:            Conversation history (list of {role, content} dicts)
        debug:              If True, also return internal tool call info
        current_user_email: Email of the logged-in user, or None for guests

    Returns:
        (assistant_reply, updated_history) or (assistant_reply, updated_history, debug_info)
    """
    debug_info = []

    # Build a context-aware system prompt based on login state
    if current_user_email:
        profile = query_user_profile(current_user_email)
        if profile:
            user_display = f"{profile['full_name']} (email: {current_user_email}, user_id: {profile['user_id']})"
        else:
            user_display = current_user_email
        contextual_prompt = SYSTEM_PROMPT + (
            f"\n\nLogged-in user: {user_display}. "
            "Answer personal booking queries for this user without asking for their email or ID. "
            "Use get_user_bookings() for any booking history request. "
            "Use make_booking / cancel_booking for booking and cancellation requests."
        )
    else:
        contextual_prompt = SYSTEM_PROMPT + (
            "\n\nNo user is currently logged in. "
            "If the user asks about personal bookings, history, or wants to make/cancel a booking, "
            "tell them they must log in first."
        )

    # Step 1: Ask the LLM which tools to call
    # Include recent history so the LLM can extract params from multi-turn flows.
    recent_history = history[-4:] if len(history) > 4 else history

    # Substitute station names with 'name (ID)' inline so the LLM reads the ID
    # directly next to each name and uses it as the parameter value.
    _augmented_message = _inject_station_ids(user_message)

    tool_selection_prompt = f"""Output only this JSON (no other text):
{{"tool_calls": [{{"name": "TOOL", "params": {{"KEY": "VALUE"}}}}]}}
Or if no tool needed: {{"tool_calls": []}}

STATIONS: Metro=MS01-MS20, Rail=NR01-NR10
USER: {current_user_email or "not logged in"}
get_user_bookings: call (no params) when logged-in user asks about their bookings, tickets, or travel history.
make_booking/cancel_booking: only if user is logged in.
Route/path/journey questions: use find_route. Policy questions: use search_policy.
Never use "" as a param value. Omit optional params if unknown.

TOOLS:
{TOOLS_SCHEMA}

HISTORY:
{json.dumps(recent_history, indent=None)}

USER: "{_augmented_message}"

Examples:
"fastest route MS01 to MS14" -> {{"tool_calls": [{{"name": "find_route", "params": {{"origin_id": "MS01", "destination_id": "MS14", "optimise_by": "time"}}}}]}}
"cheapest NR01 to NR05" -> {{"tool_calls": [{{"name": "find_route", "params": {{"origin_id": "NR01", "destination_id": "NR05", "optimise_by": "cost"}}}}]}}
"trains NR01 to NR03 on 2025-06-01" -> {{"tool_calls": [{{"name": "check_national_rail_availability", "params": {{"origin_id": "NR01", "destination_id": "NR03", "travel_date": "2025-06-01"}}}}]}}
"refund policy" -> {{"tool_calls": [{{"name": "search_policy", "params": {{"query": "refund policy"}}}}]}}
"hello" -> {{"tool_calls": []}}
"show my bookings" -> {{"tool_calls": [{{"name": "get_user_bookings", "params": {{}}}}]}}
"book me a seat NR01 to NR05 on 2025-06-01" -> {{"tool_calls": [{{"name": "check_national_rail_availability", "params": {{"origin_id": "NR01", "destination_id": "NR05", "travel_date": "2025-06-01"}}}}]}}

JSON:"""

    if llm.get_chat_provider() == "ollama":
        # llama3.2:1b is fine-tuned for native tool calling — far more reliable than
        # prompt-based JSON routing which produces malformed output on 1B models.
        tool_calls = llm.ollama_tool_call(
            recent_history, TOOLS, _augmented_message,
            system_prompt=(
                "You are a tool router. Call the right tool based on the user message. "
                f"Logged-in user: {current_user_email or 'none'}. "
                "My bookings/tickets/travel history → get_user_bookings (no params). "
                "Book a ticket / make a booking → check_national_rail_availability first, then make_booking. "
                "Cancel a booking → cancel_booking. "
                "Policy/rules/conduct/compensation/luggage/bicycle questions → search_policy. "
                "Route/directions/fastest/quickest/how-to-get/path questions → find_route ONLY (never get_metro_fare). "
                "Metro fare/price/cost/how-much-does-it-cost questions → get_metro_fare. "
                "Rail fare/cost/price questions → check_national_rail_availability then get_national_rail_fare. "
                "Schedule/timetable/trains/services questions → check_national_rail_availability or check_metro_availability. "
                "Only call a tool when needed. Output nothing except tool calls."
            ),
        )
        if debug:
            debug_info.append(f"**Tool selection (native):** {tool_calls}")
    else:
        selection_response = llm.chat(
            messages=[{"role": "user", "content": tool_selection_prompt}],
            system_prompt="JSON only. You are a router. Output valid JSON. No empty string param values.",
        )
        tool_calls = _parse_tool_calls(selection_response) or []
        if debug:
            debug_info.append(f"**Tool selection:** {selection_response}")

    # ── Deterministic fallbacks ────────────────────────────────────────────────
    # llama3.2:1b is unreliable for tool routing on anything beyond trivial queries.
    # Rules below cover every common query type.  Each rule only fires when the
    # correct tool is not already selected with valid required params.
    _lower = _augmented_message.lower()
    _station_ids = re.findall(r'\b(MS\d{2}|NR\d{2})\b', _augmented_message, re.IGNORECASE)
    _two_stations = len(_station_ids) >= 2

    def _tool_selected(name: str, *required_params) -> bool:
        """Return True only if tool `name` is in tool_calls with all required params set."""
        call = next((c for c in tool_calls if c.get("name") == name), None)
        if not call:
            return False
        p = call.get("params") or {}
        return all(p.get(k) for k in required_params)

    def _fallback(name: str, params: dict, reason: str):
        nonlocal tool_calls
        tool_calls = [{"name": name, "params": params}]
        if debug:
            debug_info.append(f"**Fallback:** {reason} → {name}({params})")

    # 1. Route / directions / path — also overrides wrong-tool selections
    _route_triggers = {"fastest route", "quickest route", "shortest route", "cheapest route",
                       "best route", "how to get", "directions from", "route from", "route to",
                       "get from", "travel from", "way from", "path from"}
    _is_route = (
        any(kw in _lower for kw in _route_triggers) or
        (_two_stations and "route" in _lower)
    )
    if _is_route and _two_stations \
            and not _tool_selected("find_route", "origin_id", "destination_id") \
            and not _tool_selected("find_alternative_routes", "origin_id", "destination_id", "avoid_station_id"):
        _opt = "cost" if any(kw in _lower for kw in ["cheap", "cheapest", "lowest cost"]) else "time"
        _fallback("find_route",
                  {"origin_id": _station_ids[0].upper(), "destination_id": _station_ids[1].upper(), "optimise_by": _opt},
                  "route query")

    # 2. Availability / trains / schedules between two stations
    elif not tool_calls and _two_stations:
        _avail_triggers = {"train", "trains", "service", "services", "run from", "runs from",
                           "schedule", "timetable", "available", "availability"}
        if any(kw in _lower for kw in _avail_triggers):
            o, d = _station_ids[0].upper(), _station_ids[1].upper()
            _travel_date = next(
                (w for w in _lower.split() if re.match(r'\d{4}-\d{2}-\d{2}', w)), None
            )
            _params = {"origin_id": o, "destination_id": d}
            if _travel_date:
                _params["travel_date"] = _travel_date
            _tool = "check_national_rail_availability" if o.startswith("NR") else "check_metro_availability"
            _fallback(_tool, _params, "availability query")

    # 3. Personal booking history — requires login
    if current_user_email and not tool_calls:
        _personal_triggers = {"my booking", "my ticket", "my trip", "my journey", "my history",
                               "my reservation", "show booking", "view booking", "check booking",
                               "list booking", "show my", "view my"}
        if any(kw in _lower for kw in _personal_triggers):
            _fallback("get_user_bookings", {}, "personal booking query")

    """
    ── ADDED 2026-05-28 ──────────────────────────────────────────────────────────
    4. Policy / conduct / rules — override wrong-tool selections

    Problem: The LLM routes policy questions (pets, luggage, bicycles, refunds,
    etc.) to transit tools such as check_national_rail_availability when it sees
    keywords like "national rail", returning empty results and causing the LLM
    to fabricate incorrect answers (e.g. "dogs are not allowed").

    Fix: detect policy-related keywords and force search_policy, overriding any
    wrong tool the LLM may have already selected.

    Fires when:
      (a) no tool was selected, OR
      (b) the LLM selected a non-policy tool for a clearly policy-type question
    ─────────────────────────────────────────────────────────────────────────────
    """
    # 4. Policy / conduct / rules — override wrong-tool selections
    # Fires when: (a) no tool selected, OR (b) wrong tool selected for a policy question
    _POLICY_KEYWORDS = {
        "refund", "cancel", "cancell", "policy", "policies", "rule", "rules",
        "luggage", "bag", "suitcase", "baggage",
        "bicycle", "bike", "cycling", "foldable",
        "pet", "dog", "cat", "animal", "carrier",
        "food", "drink", "alcohol", "eating", "smoking", "vaping",
        "conduct", "noise", "priority seat", "wheelchair", "accessibility",
        "delay compensation", "compensation",
        # ── MODIFIED 2026-05-29: Added standalone "child", "children", "infant" ──
        # Previously only "child fare" was listed, so "child aged 8" didn't match
        # ─────────────────────────────────────────────────────────────────────────
        "child", "children", "infant", "baby", "toddler",
        "child fare", "group discount", "group fare", "group booking",
        "ticket type", "day pass", "return ticket",
        "allowed", "permit", "permitted", "bring", "allowed to", "can i bring",
        "prohibited", "banned", "restriction",
        "seat selection", "choose a seat", "select a seat", "pick a seat",
        "pay extra", "extra fee", "extra charge", "surcharge", "additional fee",
        "booking rule", "advance booking", "how early", "how far in advance",
        "change fee", "change ticket", "modify booking",
        "payment method", "credit card", "debit card", "ewallet",
        "lost ticket", "ticket validity", "valid id",
        # ── ADDED 2026-05-29: lost property and accessibility keywords ───────
        "lost property", "lost item", "lost my", "left my", "report lost", "found item",
        "lost", "wallet", "keys", "collect it", "where can i collect",
        "hearing loop", "large print", "wheelchair space",
        # ─────────────────────────────────────────────────────────────────────
        # NOTE: feedback keywords are handled separately (Rule 5 below),
        # not in _POLICY_KEYWORDS, because feedback queries go to
        # get_feedback_summary (relational), not search_policy (vector).
    }
    _is_policy = any(kw in _lower for kw in _POLICY_KEYWORDS)
    # ── MODIFIED 2026-05-29 ──────────────────────────────────────────────────
    # Added get_metro_fare to the wrong-tool override list.
    # Previously get_metro_fare was missing, causing "drink alcohol on metro"
    # to be routed to fare lookup instead of search_policy.
    #
    # get_user_bookings is included in the override list BUT protected by
    # _is_personal_query: if the user says "my booking", "my ticket", etc.,
    # the override is skipped so personal booking queries still work.
    # This prevents "I left my phone on the metro" from calling
    # get_user_bookings (which errors when not logged in).
    # ──────────────────────────────────────────────────────────────────────────
    _personal_booking_kw = {"my booking", "my ticket", "my trip", "my journey",
                            "my history", "my reservation", "show booking",
                            "view booking", "check booking", "list booking",
                            "show my", "view my"}
    _is_personal_query = any(kw in _lower for kw in _personal_booking_kw)
    _wrong_tool_for_policy = (
        _is_policy and not _is_personal_query and tool_calls and
        tool_calls[0].get("name") in (
            "check_national_rail_availability",
            "check_metro_availability",
            "find_route",
            "get_national_rail_fare",
            "get_metro_fare",
            "calculate_metro_fare",
            "get_user_bookings",
        )
    )
    if _is_policy and (not tool_calls or _wrong_tool_for_policy):
        _fallback("search_policy", {"query": user_message}, "policy keyword detected")
    """
    ── END ADDED 2026-05-28 ──────────────────────────────────────────────────────
    """

    # ── ADDED 2026-05-29 ────────────────────────────────────────────────────────
    # 5. Feedback queries — route to get_feedback_summary (relational, not vector)
    #
    # Problem: questions like "how many 5-star ratings?" have no matching tool,
    # and the LLM may route them to search_policy or get_user_bookings.
    #
    # Fix: detect feedback-related keywords and force get_feedback_summary.
    # ── END ADDED 2026-05-29 ────────────────────────────────────────────────────
    _FEEDBACK_KEYWORDS = {"feedback", "rating", "ratings", "review", "reviews",
                          "star", "stars", "satisfaction"}
    _is_feedback = any(kw in _lower for kw in _FEEDBACK_KEYWORDS)
    if _is_feedback and (not tool_calls or tool_calls[0].get("name") != "get_feedback_summary"):
        tool_calls = [{"name": "get_feedback_summary", "params": {}}]

    # Step 2: Execute each tool call against the real databases
    tool_results = []
    for call in tool_calls:
        tool_name = call.get("name", "")
        params    = call.get("params") or call.get("parameters", {})

         ### nini fix: Skip calls with empty string values for REQUIRED params only
        _required = {
            "find_route": ["origin_id", "destination_id"],
            "find_alternative_routes": ["origin_id", "destination_id", "avoid_station_id"],
            "check_national_rail_availability": ["origin_id", "destination_id"],
            "check_metro_availability": ["origin_id", "destination_id"],
            "make_booking": ["schedule_id", "origin_station_id", "destination_station_id", "travel_date", "fare_class"],
            "cancel_booking": ["booking_id"],
            "get_available_seats": ["schedule_id", "travel_date", "fare_class"],
            "search_policy": ["query"],
        }
        _req_keys = _required.get(tool_name, list(params.keys()))
        _empty = [k for k in _req_keys if params.get(k, None) == ""]
        if _empty:
            if debug:
                debug_info.append(f"**Skipped** `{tool_name}` — empty required params: {_empty}")
            continue
        ###nini fix end

        if debug:
            debug_info.append(f"**Calling:** `{tool_name}({params})`")

        result_json = _execute_tool(tool_name, params, current_user_email)

        summary = _summarise_result(tool_name, result_json)

        if debug:
            debug_info.append(
                f"**Result (raw):** ```json\n{result_json[:300]}\n```\n"
                f"**Summary sent to LLM:** {summary}"
            )

        tool_results.append({
            "tool":    tool_name,
            "params":  params,
            "result":  result_json,
            "summary": summary,
        })

    # Step 3: Normalise raw tool results to plain English using the LLM, then
    # compose the final answer.  The normalisation call replaces hand-crafted
    # per-tool formatters: any tool a student adds works automatically.
    _DB_KEYWORDS = {"booking", "ticket", "schedule", "fare", "route", "seat",
                    "train", "metro", "journey", "trip", "history", "reservation"}
    if tool_results:
        data_block = "\n\n".join(
            f"[{tr['tool']}]\n{_normalise_result(tr['tool'], tr['result'])}"
            for tr in tool_results
        )
        if debug:
            debug_info.append(f"**Data (normalised):**\n{data_block}")
        content = (
            f"DATA FROM TRANSITFLOW DATABASE:\n{data_block}"
            f"\n\nUser asks: {user_message}"
            f"\n\nAnswer using only the data above:"
        )
    # ── MODIFIED 2026-05-28 ───────────────────────────────────────────────────
    # Last-resort policy search: instead of immediately telling the user "no
    # data found", try search_policy as a fallback. The vector similarity
    # threshold (VECTOR_SIMILARITY_THRESHOLD in .env) filters out irrelevant
    # results automatically — if nothing scores above the threshold, the list
    # comes back empty, and we fall through to the "no data" message.
    #
    # This eliminates the need to enumerate every possible policy keyword:
    # the vector DB itself decides whether the question is policy-related.
    # ───────────────────────────────────────────────────────────────────────────
    elif any(kw in user_message.lower() for kw in _DB_KEYWORDS):
        # Try search_policy as last resort before giving up
        if debug:
            debug_info.append("**Last-resort:** no tool selected — trying search_policy as fallback")
        _last_resort_json = _execute_tool(
            "search_policy", {"query": user_message}, current_user_email
        )
        import json as _json
        _last_resort_results = _json.loads(_last_resort_json)
        if isinstance(_last_resort_results, list) and len(_last_resort_results) > 0:
            # Vector search found relevant documents — use them
            tool_results.append({
                "tool": "search_policy",
                "params": {"query": user_message},
                "result": _last_resort_json,
                "summary": _last_resort_json,
            })
            data_block = "\n\n".join(
                f"[{tr['tool']}]\n{_normalise_result(tr['tool'], tr['result'])}"
                for tr in tool_results
            )
            if debug:
                debug_info.append(f"**Last-resort found {len(_last_resort_results)} docs**")
            content = (
                f"DATA FROM TRANSITFLOW DATABASE:\n{data_block}"
                f"\n\nUser asks: {user_message}"
                f"\n\nAnswer using only the data above:"
            )
        else:
            # Vector search also returned nothing — genuinely no data
            content = (
                f"User asks: {user_message}\n\n"
                "IMPORTANT: No data was retrieved from the TransitFlow database for this query. "
                "Do NOT invent any bookings, fares, schedules, seat numbers, or travel times. "
                "Tell the user no data was found."
            )
    # ── END MODIFIED 2026-05-28 ─────────────────────────────────────────────
    else:
        content = user_message

    final_messages = history + [{"role": "user", "content": content}]

    answer = llm.chat(messages=final_messages, system_prompt=contextual_prompt)

    # Update history
    updated_history = history + [
        {"role": "user",      "content": user_message},
        {"role": "assistant", "content": answer},
    ]

    if debug:
        return answer, updated_history, "\n\n".join(debug_info)
    return answer, updated_history

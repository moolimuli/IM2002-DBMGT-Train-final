# Task 6 Extension — TransitFlow Vector Search Optimisation, Feedback Query & Departure Time

## Overview

This extension improves the vector search (RAG) pipeline's accuracy, adds a **feedback
query tool** (relational), **new policy documents** (vector), and a **departure time
timetable system** (relational) that resolves the booking ambiguity problem discussed in
the course forum. All changes include dated inline comments (`# TASK 6 EXTENSION` + date annotations).

---

## Files Modified

### 1. `databases/relational/queries.py`
# TASK 6 EXTENSION

| Function | Type | Description |
|----------|------|-------------|
| `query_feedback_summary(booking_id?)` | NEW | Queries `schema1.feedback` table — returns rating distribution (count per star), average rating, total count, and latest 10 comments. Optionally filters by booking ID. JOINs `schema1.users` for commenter names. |
| `generate_departure_times(schedule_id)` | NEW | Computes all departure times from `first_train_time`, `last_train_time`, `frequency_min`. Returns sorted `["06:00","06:30",...]` list. No new table needed. |
| `query_national_rail_availability()` | MODIFIED | Response now includes `departure_times` list so the LLM can present available trains to the user. |
| `query_available_seats()` | MODIFIED | Added optional `departure_time` parameter — filters seat availability per specific train instead of sharing one seat pool across all daily trains. |
| `execute_booking()` | MODIFIED | Added optional `departure_time` parameter — validates against computed timetable, stores actual departure time instead of always defaulting to `first_train_time`. |

### 2. `skeleton/agent.py`
# TASK 6 EXTENSION

| Change | Type | Description |
|--------|------|-------------|
| `import generate_departure_times` | NEW | Import the timetable generator function |
| `import query_feedback_summary` | NEW | Import the new feedback query function |
| `get_feedback_summary` tool definition (TOOLS list) | NEW | Registers the feedback tool so the LLM can call it |
| `get_feedback_summary` in TOOLS_SCHEMA | NEW | Adds tool signature to the Gemini router |
| `elif tool_name == "get_feedback_summary"` | NEW | Wires tool execution to the query function |
| `get_available_seats` tool definition | MODIFIED | Added `departure_time` optional parameter for per-train seat queries |
| `make_booking` tool definition | MODIFIED | Added `departure_time` parameter so user can specify which train to book |
| `make_booking` execution | MODIFIED | Passes `departure_time` to `execute_booking()` |
| TOOLS_SCHEMA | MODIFIED | Updated `get_available_seats` and `make_booking` signatures with `departure_time?` |
| System prompt — BOOKING FLOW | NEW | Instructs LLM to present departure_times list and ask user which train before booking |
| Rule 8: deterministic booking override | NEW | When user message contains "book" + schedule_id + station_ids + date, forces `make_booking` with extracted params (including `departure_time`), preventing LLM from misrouting to `search_policy` |
| `_execute_tool` — departure_time extraction | NEW | Fallback regex extraction of `HH:MM` from user message when LLM omits the optional `departure_time` parameter in `make_booking` or `get_available_seats` calls |
| Rule 4: `_POLICY_KEYWORDS` | NEW | Deterministic keyword fallback — forces `search_policy` when policy-related keywords detected, overriding wrong LLM tool selections |
| Rule 4: `_wrong_tool_for_policy` | NEW | Override list for tools incorrectly selected for policy questions (e.g., `get_metro_fare` for "can I drink alcohol on metro?") |
| Rule 4: `_is_personal_query` guard | NEW | Prevents policy override from breaking personal booking queries (e.g., "show my cancelled bookings") |
| Rule 5: `_FEEDBACK_KEYWORDS` | NEW | Deterministic fallback for feedback queries — routes to `get_feedback_summary` |
| Content truncation 800 → 2000 | MODIFIED | Increased policy content shown to LLM from 800 to 2000 chars to prevent refund windows from being cut off |
| Last-resort vector search fallback | MODIFIED | When no tool matches but DB keywords present, tries `search_policy` before returning "no data found" |

### 3. `skeleton/seed_vectors.py`
# TASK 6 EXTENSION

| Change | Type | Description |
|--------|------|-------------|
| `_strip_metadata(data)` | NEW | Recursively removes `_`-prefixed keys (e.g., `_modified`) before embedding, so annotation text does not pollute the semantic vector space |
| `build_documents()` — topic-level splitting | MODIFIED | Split booking_rules and travel_policies from 5 large section-level documents into ~50 topic-level documents for focused embeddings |
| `build_documents()` — new sections | MODIFIED | Added `"lost_property"` and `"accessibility"` to the travel_policies section loop |
| `seed()` — idempotent DELETE | NEW | Deletes all existing policy_documents before re-seeding to prevent duplicates |
| Embed with stripped metadata | MODIFIED | Embedding input uses `_strip_metadata()` output; stored content retains full original text |

### 4. `train-mock-data/refund_policy.json`
*(JSON does not support comments — `# TASK 6 EXTENSION` marker cannot be added to data files; changes are documented here instead)*

| Change | Type | Description |
|--------|------|-------------|
| RF001 cancellation windows (W1–W4) | MODIFIED | Added explicit boundary examples (e.g., "cancelling 2, 3, 5, 10, or 12 hours before departure all qualify for 50% refund") to help small LLMs interpret conditions correctly |
| RF002 cancellation windows (W1–W3) | MODIFIED | Same explicit examples added for Express Service policy |

### 5. `train-mock-data/travel_policies.json`
*(JSON does not support comments — `# TASK 6 EXTENSION` marker cannot be added to data files; changes are documented here instead)*

| Change | Type | Description |
|--------|------|-------------|
| `lost_property.metro` | NEW | Lost property policy for metro: reporting, collection at MS01, 30-day retention, how to report via app |
| `lost_property.national_rail` | NEW | Lost property policy for national rail: reporting, collection at NR01, 60-day retention, liability disclaimer |
| `accessibility.metro` | NEW | Expanded metro accessibility: step-free access, lifts, audio/visual, guide dogs, 2-hour advance contact |
| `accessibility.national_rail` | NEW | Expanded national rail accessibility: wheelchair spaces (2 per carriage), hearing loops, large print, helpline |

### 6. `.env`
`# TASK 6 EXTENSION` marker added inline

| Change | Type | Description |
|--------|------|-------------|
| `VECTOR_TOP_K=3` → `VECTOR_TOP_K=5` | MODIFIED | Increased search results from 3 to 5 for broader coverage |

### 7. `skeleton/ui.py`
# TASK 6 EXTENSION

| Change | Type | Description |
|--------|------|-------------|
| `load_trip_history(current_user)` | NEW | Fetches national rail bookings and metro travels for the logged-in user via `query_user_bookings()` and displays them as formatted pandas DataFrames in two separate tables |
| `load_station_connections(station_id)` | NEW | Fetches direct Neo4j connections for a selected station via `query_station_connections()` and displays results as a formatted DataFrame |
| Tab 2: My Trip History | NEW | New Gradio tab with `gr.DataFrame` tables for `schema1.national_rail_bookings` and `schema1.metro_travels` — surfaces structured booking data outside of chat |
| Tab 3: Station Lookup | NEW | New Gradio tab with `gr.Dropdown` (all 30 stations) + `gr.DataFrame` for direct connections — bypasses LLM for deterministic station queries |
| Custom CSS theme | NEW | Replaced `gr.themes.Soft()` with `gr.themes.Base()` + custom CSS: Syne + DM Sans fonts, navy/orange colour scheme, gradient header, styled DataFrames |
| `STATION_CHOICES` | NEW | Static list mapping all 30 station IDs to display names for the Station Lookup dropdown |

Tables queried:
- `schema1.national_rail_bookings` (via `query_user_bookings`)
- `schema1.metro_travels` (via `query_user_bookings`)
- Neo4j: `MetroStation`, `NationalRailStation` nodes via `METRO_LINK`, `RAIL_LINK` (via `query_station_connections`)
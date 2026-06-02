# Task 6 Extension — TransitFlow Vector Search Optimisation & Feedback Query

## Overview

This extension improves the vector search (RAG) pipeline's accuracy and adds two new
database features: a **feedback query tool** (relational) and **new policy documents**
(vector). All changes include dated inline comments (`# TASK 6 EXTENSION` + date annotations).

---

## Files Modified

### 1. `databases/relational/queries.py`
# TASK 6 EXTENSION

| Function | Type | Description |
|----------|------|-------------|
| `query_feedback_summary(booking_id?)` | NEW | Queries `schema1.feedback` table — returns rating distribution (count per star), average rating, total count, and latest 10 comments. Optionally filters by booking ID. JOINs `schema1.users` for commenter names. |

### 2. `skeleton/agent.py`
# TASK 6 EXTENSION

| Change | Type | Description |
|--------|------|-------------|
| `import query_feedback_summary` | NEW | Import the new feedback query function |
| `get_feedback_summary` tool definition (TOOLS list) | NEW | Registers the feedback tool so the LLM can call it |
| `get_feedback_summary` in TOOLS_SCHEMA | NEW | Adds tool signature to the Gemini router |
| `elif tool_name == "get_feedback_summary"` | NEW | Wires tool execution to the query function |
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
# TASK 6 EXTENSION

| Change | Type | Description |
|--------|------|-------------|
| RF001 cancellation windows (W1–W4) | MODIFIED | Added explicit boundary examples (e.g., "cancelling 2, 3, 5, 10, or 12 hours before departure all qualify for 50% refund") to help small LLMs interpret conditions correctly |
| RF002 cancellation windows (W1–W3) | MODIFIED | Same explicit examples added for Express Service policy |

### 5. `train-mock-data/travel_policies.json`
# TASK 6 EXTENSION

| Change | Type | Description |
|--------|------|-------------|
| `lost_property.metro` | NEW | Lost property policy for metro: reporting, collection at MS01, 30-day retention, how to report via app |
| `lost_property.national_rail` | NEW | Lost property policy for national rail: reporting, collection at NR01, 60-day retention, liability disclaimer |
| `accessibility.metro` | NEW | Expanded metro accessibility: step-free access, lifts, audio/visual, guide dogs, 2-hour advance contact |
| `accessibility.national_rail` | NEW | Expanded national rail accessibility: wheelchair spaces (2 per carriage), hearing loops, large print, helpline |

### 6. `.env`
| Change | Type | Description |
|--------|------|-------------|
| `VECTOR_TOP_K=3` → `VECTOR_TOP_K=5` | MODIFIED | Increased search results from 3 to 5 for broader coverage |

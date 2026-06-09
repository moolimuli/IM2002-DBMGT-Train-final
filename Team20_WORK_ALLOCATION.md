# Work Allocation Report — [Team ID]

> **Instructions:** Complete this document as a team before or alongside your final submission.
> Submit one copy per team via EEClass. This document is shared with all markers.
> Be specific — vague entries ("we all helped") will prevent individual contribution adjustments from being applied in your favour.

---

## 1. Team Members

| Full Name | Student ID | GitHub Username | Email |
|-----------|-----------|----------------|-------|
| 余妍萱 | 113403527 | ninininiadk / 101140nnn-commits | yunini20060123@gmail.com |
| 蘇于涵 | | | |
| 吳若喬 | 113403507| PhoebeJO | phoebe011624@gmail.com |

---

## 2. Task Ownership

For each task, name the **primary owner** (the person most responsible for delivering it)
and any **supporting members** (who assisted but were not the lead). Leave the Notes column
for anything that deviates from the standard expectation (e.g., task was pair-programmed,
or reassigned mid-project).

### Code Repository

| Task | Primary Owner | Supporting Member(s) | Notes |
|------|--------------|---------------------|-------|
| **Task 1** — Relational schema design (`schema.sql`) |余妍萱 | | Fixed schema.sql CREATE INDEX syntax errors; implemented schema1/schema2 separation architecture; designed Argon2id password storage approach |
| **Task 2a** — Core availability & fare queries (`query_national_rail_availability`, `query_metro_schedules`, `query_national_rail_fare`, `query_metro_fare`) |余妍萱 | | Optimized return fields across all query functions (added station names, fare calculations, seat_availability); fixed O1 bug in execute_cancellation using actual departure_time |
| **Task 2b** — Seat & user queries (`query_available_seats`, `query_user_profile`, `query_user_bookings`, `query_payment_info`) |余妍萱 | | Optimized return fields across all query functions (added station names, fare calculations, seat_availability); fixed O1 bug in execute_cancellation using actual departure_time |
| **Task 2c** — Write operations (`execute_booking`, `execute_cancellation`) |余妍萱 | | Optimized return fields across all query functions (added station names, fare calculations, seat_availability); fixed O1 bug in execute_cancellation using actual departure_time |
| **Task 2d** — Authentication queries (`login_user`, `register_user`, `get_user_secret_question`, `verify_secret_answer`, `update_password`) |余妍萱 | | Implemented Argon2id password hashing (schema2.credentials table); created verify_password.py validation script |
| **Task 3** — PostgreSQL seeding (`seed_postgres.py`) |  |余妍萱 | Supporting contribution to seed data implementation |
| **Task 4** — Neo4j graph design & seeding (`seed_neo4j.py`, `seed.cypher`) | | | |
| **Task 5** — Neo4j query functions (`graph/queries.py`) | | | |
| **Task 6** *(if attempted)* — Optional extension |余妍萱 (UI) | |UI Enhancement: Trip History panel, Station Lookup panel, custom CSS theme |

### Design Document

| Section | Primary Author | Supporting Member(s) | Notes |
|---------|--------------|---------------------|-------|
| Section 1 — ER Diagram |余妍萱 | |Generated via dbdiagram.io |
| Section 2 — Normalisation Justification | | | |
| Section 3 — Graph Database Design Rationale | | | |
| Section 4 — Vector / RAG Design | | | |
| Section 5 — AI Tool Usage Evidence |余妍萱 | | examples : Schema Design (Argon2id), Query Writing (query_national_rail_availability), AI error correction |
| Section 6 — Reflection & Trade-offs | | | |
| Section 7 — Optional Extension *(if applicable)* |余妍萱 (7.5) | |Section 7.5: UI Enhancement design decisions and screenshots |

---

## 3. Estimated Contribution Percentages

Based on the task allocation above, what percentage of total team effort do you estimate each member contributed?
All members must sum to 100%.

| Member | Estimated % | Brief justification |
|--------|-----------|---------------------|
|余妍萱 | 33% | Task 1: schema design (schema1/schema2 separation, Argon2id architecture, fixed CREATE INDEX syntax); Task 2: all queries.py functions implementation and optimization; Task 2d: Argon2id password hashing and verify_password.py script; O1 bug fixes in execute_cancellation; agent.py tool descriptions optimization; Task 6: UI extension development; Design Doc Section 1 (ER diagram), Section 5 (AI evidence), Section 7.5 (UI enhancement design) |
|蘇于涵 | 33% | |
|吳若喬 | 33% | Task 1: schema design (schema1/schema2 separation, Argon2id architecture, fixed CREATE INDEX syntax); Task 2: all queries.py functions implementation and optimization;bug fixes in execute_cancellation; agent.py tool descriptions optimization;queries.py;seed_vectors.py;travel_policies.json;Design Doc Section 7(policies and departure time); TASK6.md;|
| **Total** | **100%** | |

---

## 4. Mid-Project Changes

If any tasks were reassigned or the original plan changed significantly, document it here.
If nothing changed, write "No changes."

| Change | Original plan | Revised plan | Reason |
|--------|--------------|-------------|--------|
| Task ownership formalised mid-project | Each member independently explored and completed a first version of multiple components before official task allocation was published | Tasks reassigned to reflect actual contributions after the official work allocation template was released by the instructor | The team began working on the project before the official allocation template was published; initial work was exploratory and collaborative, with formal ownership confirmed retrospectively |


---

## 5. Team Declaration

We confirm that this work allocation accurately reflects how responsibilities were divided within our team.

| Name | Signature / Typed name | Date |
|------|----------------------|------|
|余妍萱 |余妍萱 |2026-06-04 |
|蘇于涵 |蘇于涵 |2026-06-04 |
|吳若喬 |吳若喬 |2026-06-04 |
|
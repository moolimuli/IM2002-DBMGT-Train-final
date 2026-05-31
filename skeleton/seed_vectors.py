"""
TransitFlow — pgvector Policy Document Seeder
Run once after starting Docker:
    python skeleton/seed_vectors.py

This script:
  1. Loads policy documents directly from train-mock-data/ JSON files
  2. Embeds each document using the configured LLM provider
  3. Stores the text + vector in PostgreSQL (policy_documents table)

Note: Gemini free tier has ~1500 requests/minute — this script makes ~13 calls, well within limits.

Students: To extend the assistant's knowledge, add entries to the JSON files in
train-mock-data/ and re-run this script.
"""

import json
import os
import sys
import time

sys.path.insert(0, ".")

from skeleton.llm_provider import llm
from databases.relational.queries import store_policy_document

_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "train-mock-data")
)


def _load(filename):
    with open(os.path.join(_DATA_DIR, filename), encoding="utf-8") as f:
        return json.load(f)


def _strip_metadata(data):
    """
    ── ADDED 2026-05-29 ──────────────────────────────────────────────────────────
    Recursively remove internal metadata fields (keys starting with '_') before
    embedding, so annotation fields like '_modified' do not pollute the semantic
    vector space with non-policy content (dates, editor notes, etc.).

    Problem: fields like "_modified": "2026-05-28: Added explicit examples..."
    get embedded alongside the actual policy text, diluting the embedding and
    slightly reducing vector search precision.

    Fix: strip all keys starting with '_' at every level of the JSON tree before
    passing the content to the embedding model. The stored content (shown to the
    LLM) still includes the full original text; only the embedding input is cleaned.
    ── END ADDED 2026-05-29 ──────────────────────────────────────────────────────
    """
    if isinstance(data, dict):
        return {k: _strip_metadata(v) for k, v in data.items() if not k.startswith("_")}
    if isinstance(data, list):
        return [_strip_metadata(item) for item in data]
    return data


def _text(data):
    return json.dumps(data, indent=2, ensure_ascii=False)


def build_documents():
    docs = []

    # refund_policy.json — one document per policy entry
    for policy in _load("refund_policy.json"):
        docs.append({
            "title": policy["label"],
            "category": "refund",
            "source_file": "refund_policy.json",
            "content": _text(policy),
        })

    # ticket_types.json — one document per ticket type
    for tt in _load("ticket_types.json"):
        docs.append({
            "title": f"Ticket Type: {tt['display_name']}",
            "category": "booking",
            "source_file": "ticket_types.json",
            "content": _text(tt),
        })

    """
    ── MODIFIED 2026-05-28 ───────────────────────────────────────────────────────
    booking_rules.json & travel_policies.json — split into topic-level documents

    Problem: storing an entire network section (e.g. all national_rail policies)
    as ONE document dilutes the vector embedding. When a user asks about "dogs",
    the document matches weakly (mixed with bicycles, luggage, food, etc.) and
    the 800-char content truncation in agent.py cuts off the pets section entirely.

    Fix: create one document per TOPIC (e.g. "pets", "bicycles", "luggage") so
    each embedding is focused and the full topic content fits within truncation.
    ─────────────────────────────────────────────────────────────────────────────
    """
    # booking_rules.json — one document per TOPIC within each section
    br = _load("booking_rules.json")
    for section in ("national_rail", "metro", "general_rules"):
        if section in br:
            section_data = br[section]
            if isinstance(section_data, dict):
                for topic_key, topic_value in section_data.items():
                    docs.append({
                        "title": f"Booking Rules — {section.replace('_', ' ').title()} — {topic_key.replace('_', ' ').title()}",
                        "category": "booking",
                        "source_file": "booking_rules.json",
                        "content": _text({topic_key: topic_value}),
                    })
            else:
                docs.append({
                    "title": f"Booking Rules — {section.replace('_', ' ').title()}",
                    "category": "booking",
                    "source_file": "booking_rules.json",
                    "content": _text({section: section_data}),
                })

    # travel_policies.json — one document per TOPIC within each section
    tp = _load("travel_policies.json")
    for section in ("metro", "national_rail"):
        if section in tp:
            section_data = tp[section]
            if isinstance(section_data, dict):
                for topic_key, topic_value in section_data.items():
                    docs.append({
                        "title": f"Travel Policies — {section.replace('_', ' ').title()} — {topic_key.replace('_', ' ').title()}",
                        "category": "conduct",
                        "source_file": "travel_policies.json",
                        "content": _text({topic_key: topic_value}),
                    })
            else:
                docs.append({
                    "title": f"Travel Policies — {section.replace('_', ' ').title()}",
                    "category": "conduct",
                    "source_file": "travel_policies.json",
                    "content": _text({section: section_data}),
                })
    """
    ── END MODIFIED 2026-05-28 ───────────────────────────────────────────────────
    """

    return docs


def seed():
    """
    ── ADDED 2026-05-28 ──────────────────────────────────────────────────────────
    Clear existing documents before re-seeding to prevent duplicates.
    Previously, re-running this script doubled the document count each time.
    ─────────────────────────────────────────────────────────────────────────────
    """
    # Idempotent: delete all existing documents before re-seeding
    import psycopg2
    from skeleton.config import PG_DSN
    _conn = psycopg2.connect(PG_DSN)
    _conn.autocommit = True
    with _conn.cursor() as _cur:
        _cur.execute("DELETE FROM schema1.policy_documents")
        print("  [CLEAR] Cleared existing policy documents (idempotent re-seed)")
    _conn.close()
    """
    ── END ADDED 2026-05-28 ──────────────────────────────────────────────────────
    """

    documents = build_documents()
    print(f"  [SEED] Embedding {len(documents)} policy documents using {llm.chat_provider}...\n")

    for i, doc in enumerate(documents):
        print(f"  [{i+1}/{len(documents)}] Embedding: {doc['title']}")

        try:
            # ── MODIFIED 2026-05-29 ───────────────────────────────────────────────
            # Embed metadata-stripped content to avoid '_modified' fields polluting
            # the semantic vector. The full content (with metadata) is still stored
            # in the DB and shown to the LLM; only the embedding input is cleaned.
            # ─────────────────────────────────────────────────────────────────────
            _embed_content = _text(_strip_metadata(json.loads(doc["content"])))
            embedding = llm.embed(_embed_content)

            if len(embedding) != llm.embed_dim:
                print(f"    [WARN] Unexpected embedding dim: {len(embedding)} (expected {llm.embed_dim})")
                print(f"    Update GEMINI_EMBED_DIM or OLLAMA_EMBED_DIM in skeleton/config.py")
                sys.exit(1)

            doc_id = store_policy_document(
                title=doc["title"],
                category=doc["category"],
                content=doc["content"],
                embedding=embedding,
                source_file=doc.get("source_file", ""),
            )
            print(f"    [OK] Stored as document id={doc_id}")

        except Exception as e:
            print(f"    [FAIL] Failed: {e}")
            raise

        if llm.chat_provider == "gemini" and i < len(documents) - 1:
            time.sleep(0.5)

    print(f"\n  [DONE] All {len(documents)} policy documents embedded and stored.")
    print("   Test with a similarity search:")
    print("   >>> from skeleton.llm_provider import llm")
    print("   >>> from databases.relational.queries import query_policy_vector_search")
    print("   >>> results = query_policy_vector_search(llm.embed('can I get a refund for a delay?'))")
    print("   >>> print(results[0]['title'])")


if __name__ == "__main__":
    seed()

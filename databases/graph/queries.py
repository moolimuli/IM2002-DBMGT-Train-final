"""
TransitFlow — Neo4j Graph Database Layer
=========================================
This module handles all queries to Neo4j.

GRAPH ROLE:
  - Model the dual transit network (city metro M1–M4 + national rail NR1–NR2)
  - Find fastest routes (Dijkstra by travel_time_min via APOC)
  - Find cheapest routes (Dijkstra by fare via APOC)
  - Find alternative routes avoiding a given station
  - Find cross-network interchange paths (metro → rail or rail → metro)
  - Show delay ripple: which stations are affected within N hops

STUDENT TASK
------------
Design your graph schema (node labels, relationship types, properties)
based on the data in train-mock-data/, seed it with skeleton/seed_neo4j.py,
then implement the query_ functions below.

Functions prefixed with `query_` are called by the agent (skeleton/agent.py).
"""

from __future__ import annotations

from typing import Optional

from neo4j import GraphDatabase

from skeleton.config import NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD


def _driver():
    """Return a Neo4j driver. Caller is responsible for closing."""
    return GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))


# ── Example ───────────────────────────────────────────────────────────────────
# The block below shows the query pattern: open a session, run Cypher, return data.

def example_count_nodes() -> int:
    """Example: count all nodes currently in the graph."""
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS total")
            return result.single()["total"]

def _node_label(station_id: str) -> str:
    return "MetroStation" if station_id.startswith("MS") else "NationalRailStation"


def _path_to_legs(path) -> list[dict]:
    nodes = list(path.nodes)
    rels  = list(path.relationships)
    legs  = []
    for i, rel in enumerate(rels):
        legs.append({
            "from_id":        nodes[i]["station_id"],
            "from_name":      nodes[i]["name"],
            "to_id":          nodes[i + 1]["station_id"],
            "to_name":        nodes[i + 1]["name"],
            "relationship":   rel.type,
            "line":           rel.get("line", "interchange"),
            "travel_time_min": rel.get("travel_time_min", 5),
        })
    return legs


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """Find the fastest path between two stations using APOC Dijkstra."""
    origin_label = _node_label(origin_id)
    dest_label   = _node_label(destination_id)

    # Cross-network or same-network — let APOC traverse all relationship types
    rel_types = "METRO_LINK|RAIL_LINK|INTERCHANGE_TO"

    cypher = f"""
        MATCH (start:{origin_label} {{station_id: $origin_id}}),
              (end:{dest_label}   {{station_id: $dest_id}})
        CALL apoc.algo.dijkstra(start, end, '{rel_types}', 'travel_time_min')
        YIELD path, weight
        RETURN path, weight
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, origin_id=origin_id, dest_id=destination_id)
            record = result.single()

    if not record:
        return {"found": False, "origin_id": origin_id, "destination_id": destination_id}

    path = record["path"]
    legs = _path_to_legs(path)
    stations = [{"station_id": n["station_id"], "name": n["name"]} for n in path.nodes]
    return {
        "found":            True,
        "origin_id":        origin_id,
        "destination_id":   destination_id,
        "total_time_min":   record["weight"],
        "stations":         stations,
        "legs":             legs,
    }


# ── CHEAPEST ROUTE (Dijkstra by fare) ────────────────────────────────────────

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """Find the cheapest path by minimising estimated fare (approximate)."""
    origin_label = _node_label(origin_id)
    dest_label   = _node_label(destination_id)
    rel_types    = "METRO_LINK|RAIL_LINK|INTERCHANGE_TO"

    cypher = f"""
        MATCH (start:{origin_label} {{station_id: $origin_id}}),
              (end:{dest_label}   {{station_id: $dest_id}})
        CALL apoc.algo.dijkstra(start, end, '{rel_types}', 'travel_time_min')
        YIELD path, weight
        RETURN path, weight
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, origin_id=origin_id, dest_id=destination_id)
            record = result.single()

    if not record:
        return {"found": False, "origin_id": origin_id, "destination_id": destination_id}

    path = record["path"]
    legs = _path_to_legs(path)

    # Estimate fare: metro ~$0.80 base + $0.30/stop; rail ~$2.50 base + $1.50/stop
    total_fare = 0.0
    for leg in legs:
        if leg["relationship"] == "METRO_LINK":
            total_fare += 0.80 + 0.30
        elif leg["relationship"] == "RAIL_LINK":
            total_fare += 2.50 + 1.50
    stations = [{"station_id": n["station_id"], "name": n["name"]} for n in path.nodes]
    return {
        "found":             True,
        "origin_id":         origin_id,
        "destination_id":    destination_id,
        "total_time_min":    record["weight"],
        "estimated_fare_usd": round(total_fare, 2),
        "fare_class":        fare_class,
        "stations":          stations,
        "legs":              legs,
    }


# ── ALTERNATIVE ROUTES (avoiding a station) ───────────────────────────────────

def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[list[dict]]:
    """Find paths that avoid a specific station."""
    origin_label = _node_label(origin_id)
    dest_label   = _node_label(destination_id)

    cypher = f"""
        MATCH path = (start:{origin_label} {{station_id: $origin_id}})
                     -[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*1..20]->
                     (end:{dest_label} {{station_id: $dest_id}})
        WHERE NONE(n IN nodes(path) WHERE n.station_id = $avoid_id)
        WITH path,
             reduce(t = 0, r IN relationships(path) | t + r.travel_time_min) AS total_time
        ORDER BY total_time
        LIMIT $max_routes
        RETURN path, total_time
    """
    with _driver() as driver:
        with driver.session() as session:
            results = session.run(
                cypher,
                origin_id=origin_id, dest_id=destination_id,
                avoid_id=avoid_station_id, max_routes=max_routes,
            )
            routes = []
            for record in results:
                legs = _path_to_legs(record["path"])
                routes.append({"total_time_min": record["total_time"], "legs": legs})
    return routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """Find a path crossing the metro ↔ national rail boundary."""
    origin_label = _node_label(origin_id)
    dest_label   = _node_label(destination_id)

    cypher = f"""
        MATCH (start:{origin_label} {{station_id: $origin_id}}),
              (end:{dest_label}   {{station_id: $dest_id}})
        CALL apoc.algo.dijkstra(start, end,
             'METRO_LINK|RAIL_LINK|INTERCHANGE_TO', 'travel_time_min')
        YIELD path, weight
        RETURN path, weight
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(cypher, origin_id=origin_id, dest_id=destination_id)
            record = result.single()

    if not record:
        return {"found": False, "origin_id": origin_id, "destination_id": destination_id}

    path = record["path"]
    legs = _path_to_legs(path)
    interchange_points = [
        leg["from_id"] for leg in legs if leg["relationship"] == "INTERCHANGE_TO"
    ]
    stations = [{"station_id": n["station_id"], "name": n["name"]} for n in path.nodes]
    return {
        "found":              True,
        "origin_id":          origin_id,
        "destination_id":     destination_id,
        "total_time_min":     record["weight"],
        "interchange_points": interchange_points,
        "stations":           stations,
        "legs":               legs,
    }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """Find all stations within N hops of a disrupted station."""
    cypher = """
        MATCH (start {station_id: $station_id})
        CALL apoc.path.spanningTree(start, {
            relationshipFilter: 'METRO_LINK|RAIL_LINK|INTERCHANGE_TO',
            maxLevel: $hops
        })
        YIELD path
        WITH last(nodes(path)) AS affected, length(path) AS hops_away
        WHERE affected.station_id <> $station_id
        RETURN DISTINCT affected.station_id AS station_id,
                        affected.name       AS name,
                        affected.lines      AS lines,
                        hops_away
        ORDER BY hops_away, affected.station_id
    """
    with _driver() as driver:
        with driver.session() as session:
            results = session.run(cypher, station_id=delayed_station_id, hops=hops)
            return [
                {
                    "station_id":     r["station_id"],
                    "name":           r["name"],
                    "hops_away":      r["hops_away"],
                    "lines_affected": r["lines"],
                }
                for r in results
            ]


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """List all direct connections from a given station."""
    cypher = """
        MATCH (s {station_id: $station_id})-[r:METRO_LINK|RAIL_LINK|INTERCHANGE_TO]->(neighbour)
        RETURN neighbour.station_id AS station_id,
               neighbour.name       AS name,
               type(r)              AS relationship,
               r.line               AS line,
               r.travel_time_min    AS travel_time_min
        ORDER BY r.travel_time_min
    """
    with _driver() as driver:
        with driver.session() as session:
            results = session.run(cypher, station_id=station_id)
            return [dict(r) for r in results]

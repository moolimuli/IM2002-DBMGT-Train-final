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


def _rel_type(station_id: str) -> str:
    """Infer the relationship type from a station ID prefix."""
    return "METRO_LINK" if station_id.startswith("MS") else "RAIL_LINK"


def _parse_path(path) -> tuple[list[dict], list[dict]]:
    """Convert a Neo4j path object into (stations, legs) lists."""
    nodes = list(path.nodes)
    rels  = list(path.relationships)
    stations = [{"station_id": n["station_id"], "name": n["name"]} for n in nodes]
    legs = [
        {
            "from_id":         nodes[i]["station_id"],
            "from_name":       nodes[i]["name"],
            "to_id":           nodes[i + 1]["station_id"],
            "to_name":         nodes[i + 1]["name"],
            "line":            rels[i].get("line") or "interchange",
            "travel_time_min": rels[i].get("travel_time_min") or 0,
        }
        for i in range(len(rels))
    ]
    return stations, legs


# ── Example ───────────────────────────────────────────────────────────────────

def example_count_nodes() -> int:
    """Example: count all nodes currently in the graph."""
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("MATCH (n) RETURN count(n) AS total")
            return result.single()["total"]


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    """
    Find the fastest path between two stations, minimising total travel time.
    Uses apoc.algo.dijkstra (APOC required; enabled in docker-compose.yml).

    Args:
        origin_id:       e.g. "MS01" or "NR01"
        destination_id:  e.g. "MS09" or "NR05"
        network:         "metro", "rail", or "auto" (inferred from IDs)

    Returns:
        dict with keys: found, origin_id, destination_id,
                        total_time_min, path (list of station dicts), legs
    """
    rel_type = _rel_type(origin_id)

    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (origin) WHERE origin.station_id = $origin_id
                MATCH (dest)   WHERE dest.station_id   = $dest_id
                CALL apoc.algo.dijkstra(origin, dest, $rel_type, 'travel_time_min', 0, 50)
                YIELD path, weight
                RETURN path, weight
                LIMIT 1
                """,
                origin_id=origin_id,
                dest_id=destination_id,
                rel_type=rel_type,
            )
            record = next(iter(result), None)

            if not record or record["path"] is None:
                return {
                    "found": False,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "error": "No route found between the two stations.",
                }

            stations, legs = _parse_path(record["path"])
            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": record["weight"],
                "path": stations,
                "legs": legs,
            }


# ── CHEAPEST ROUTE (fewest stops = lowest fare) ───────────────────────────────

# Approximate per-stop fare used for estimation (graph edges carry no fare data).
_APPROX_FARE_PER_STOP_USD = 0.50

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    """
    Find the cheapest path between two stations, minimising total estimated fare.

    Since fare is proportional to stops travelled, this finds the path with the
    fewest stops (shortest hop count) and returns an approximate fare estimate.
    Exact fares are available via the relational database.

    Args:
        origin_id:       e.g. "NR01"
        destination_id:  e.g. "NR05"
        network:         "metro", "rail", or "auto"
        fare_class:      "standard" or "first" (national rail only)

    Returns:
        dict with found, total_fare_usd (approximate), stations, legs
    """
    rel_type = _rel_type(origin_id)

    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (origin) WHERE origin.station_id = $origin_id
                MATCH (dest)   WHERE dest.station_id   = $dest_id
                MATCH p = shortestPath((origin)-[:%s*]-(dest))
                RETURN p
                """ % rel_type,
                origin_id=origin_id,
                dest_id=destination_id,
            )
            record = result.single()

            if not record or record["p"] is None:
                return {
                    "found": False,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "error": "No route found between the two stations.",
                }

            stations, legs = _parse_path(record["p"])
            stops = len(legs)
            approx_fare = round(stops * _APPROX_FARE_PER_STOP_USD, 2)

            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "stops": stops,
                "total_fare_usd": approx_fare,
                "fare_note": "Approximate — based on stop count. Use check_national_rail_availability for exact fares.",
                "stations": stations,
                "legs": legs,
            }


# ── ALTERNATIVE ROUTES (avoiding a station) ───────────────────────────────────

def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[list[dict]]:
    """
    Find paths between two stations that avoid a specific intermediate station.
    Useful for routing around a delayed or closed station.

    Args:
        origin_id:         e.g. "NR01"
        destination_id:    e.g. "NR05"
        avoid_station_id:  e.g. "NR03"
        network:           "metro", "rail", or "auto"
        max_routes:        max number of alternatives to return

    Returns:
        List of routes, each route is a list of leg dicts
    """
    rel_type = _rel_type(origin_id)

    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (origin) WHERE origin.station_id = $origin_id
                MATCH (dest)   WHERE dest.station_id   = $dest_id
                MATCH path = (origin)-[:%s*1..15]->(dest)
                WHERE NOT any(n IN nodes(path)[1..-1] WHERE n.station_id = $avoid_id)
                RETURN path
                ORDER BY reduce(t = 0, r IN relationships(path) | t + r.travel_time_min)
                LIMIT 20
                """ % rel_type,
                origin_id=origin_id,
                dest_id=destination_id,
                avoid_id=avoid_station_id,
            )

            seen: set[tuple] = set()
            routes: list[list[dict]] = []
            for record in result:
                stations, legs = _parse_path(record["path"])
                ids = [s["station_id"] for s in stations]
                if len(ids) != len(set(ids)):  # skip paths with cycles
                    continue
                key = tuple(ids)
                if key not in seen:
                    seen.add(key)
                    routes.append(legs)
                if len(routes) >= max_routes:
                    break
            return routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    """
    Find a path between a metro station and a national rail station (or vice versa)
    crossing the network boundary via interchange relationships.

    Args:
        origin_id:       e.g. "MS03" (metro) or "NR05" (national rail)
        destination_id:  e.g. "NR05" (national rail) or "MS09" (metro)

    Returns:
        dict with found, stations list, interchange points, total_time_min
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (origin) WHERE origin.station_id = $origin_id
                MATCH (dest)   WHERE dest.station_id   = $dest_id
                CALL apoc.algo.dijkstra(
                    origin, dest,
                    'METRO_LINK|RAIL_LINK|INTERCHANGE_TO',
                    'travel_time_min', 0, 50
                )
                YIELD path, weight
                RETURN path, weight
                LIMIT 1
                """,
                origin_id=origin_id,
                dest_id=destination_id,
            )
            record = next(iter(result), None)

            if not record or record["path"] is None:
                return {
                    "found": False,
                    "origin_id": origin_id,
                    "destination_id": destination_id,
                    "error": "No cross-network route found between the two stations.",
                }

            stations, legs = _parse_path(record["path"])

            # Interchange points are the stations at either end of an INTERCHANGE_TO leg
            # (identified because _parse_path sets line="interchange" for those relationships)
            interchange_ids: list[str] = []
            for leg in legs:
                if leg["line"] == "interchange":
                    for sid in (leg["from_id"], leg["to_id"]):
                        if sid not in interchange_ids:
                            interchange_ids.append(sid)

            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": record["weight"],
                "interchange_points": interchange_ids,
                "stations": stations,
                "legs": legs,
            }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    """
    Find all stations within N hops of a delayed or disrupted station.
    Works on both metro and national rail networks.

    Args:
        delayed_station_id: e.g. "NR03" or "MS01"
        hops:               how many connections out to search (default 2)

    Returns:
        List of dicts: {station_id, name, hops_away, lines_affected}
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (start) WHERE start.station_id = $station_id
                CALL apoc.path.expandConfig(start, {
                    relationshipFilter: 'METRO_LINK|RAIL_LINK',
                    maxLevel: $hops,
                    minLevel: 1
                })
                YIELD path
                WITH last(nodes(path)) AS affected, length(path) AS hops_away
                WITH affected, min(hops_away) AS hops_away
                RETURN affected.station_id  AS station_id,
                       affected.name        AS name,
                       hops_away,
                       affected.lines       AS lines_affected
                ORDER BY hops_away, affected.station_id
                """,
                station_id=delayed_station_id,
                hops=hops,
            )
            return [dict(record) for record in result]


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    """
    List all direct connections from a given station.

    Args:
        station_id: e.g. "MS01" or "NR01"
    """
    with _driver() as driver:
        with driver.session() as session:
            result = session.run(
                """
                MATCH (s)-[r:METRO_LINK|RAIL_LINK]->(neighbor)
                WHERE s.station_id = $station_id
                RETURN neighbor.station_id  AS station_id,
                       neighbor.name        AS name,
                       r.line               AS line,
                       r.travel_time_min    AS travel_time_min,
                       type(r)              AS network
                ORDER BY r.line, neighbor.station_id
                """,
                station_id=station_id,
            )
            return [dict(record) for record in result]

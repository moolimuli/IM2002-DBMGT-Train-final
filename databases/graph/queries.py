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

# TODO: Implement the query_ functions below.
# ─────────────────────────────────────────────────────────────────────────────


# ── FASTEST ROUTE (Dijkstra by travel_time_min) ───────────────────────────────

def query_shortest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
) -> dict:
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("""
                MATCH (start {station_id: $origin}), (end {station_id: $dest})
                CALL apoc.algo.dijkstra(start, end, 'METRO_LINK|RAIL_LINK|INTERCHANGE_TO', 'travel_time_min')
                YIELD path, weight
                RETURN [node in nodes(path) | {
                    station_id: node.station_id,
                    name: node.name
                }] AS stations,
                weight AS total_time_min
            """, origin=origin_id, dest=destination_id)
            record = result.single()
            if not record:
                return {"found": False, "origin_id": origin_id, "destination_id": destination_id}
            return {
                "found": True,
                "origin_id": origin_id,
                "destination_id": destination_id,
                "total_time_min": record["total_time_min"],
                "path": record["stations"],
            }


# ── CHEAPEST ROUTE (Dijkstra by fare) ────────────────────────────────────────

def query_cheapest_route(
    origin_id: str,
    destination_id: str,
    network: str = "auto",
    fare_class: str = "standard",
) -> dict:
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("""
                MATCH (start {station_id: $origin}), (end {station_id: $dest})
                CALL apoc.algo.dijkstra(start, end, 'METRO_LINK|RAIL_LINK|INTERCHANGE_TO', 'travel_time_min')
                YIELD path, weight
                RETURN [node in nodes(path) | {
                    station_id: node.station_id,
                    name: node.name
                }] AS stations,
                weight AS total_time_min
            """, origin=origin_id, dest=destination_id)
            record = result.single()
            if not record:
                return {"found": False}
            return {
                "found": True,
                "stations": record["stations"],
                "total_time_min": record["total_time_min"],
            }


# ── ALTERNATIVE ROUTES (avoiding a station) ───────────────────────────────────


def query_alternative_routes(
    origin_id: str,
    destination_id: str,
    avoid_station_id: str,
    network: str = "auto",
    max_routes: int = 3,
) -> list[list[dict]]:
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("""
                MATCH (start {station_id: $origin}), (end {station_id: $dest})
                MATCH path = (start)-[:METRO_LINK|RAIL_LINK|INTERCHANGE_TO*..15]->(end)
                WHERE NONE(n IN nodes(path) WHERE n.station_id = $avoid)
                WITH path, reduce(t=0, r IN relationships(path) | t + r.travel_time_min) AS total
                ORDER BY total ASC
                LIMIT $max_routes
                RETURN [node IN nodes(path) | {
                    station_id: node.station_id,
                    name: node.name
                }] AS stations, total AS total_time_min
            """, origin=origin_id, dest=destination_id,
                avoid=avoid_station_id, max_routes=max_routes)
            routes = []
            for record in result:
                routes.append({
                    "stations": record["stations"],
                    "total_time_min": record["total_time_min"],
                })
            return routes


# ── CROSS-NETWORK INTERCHANGE PATH ───────────────────────────────────────────

def query_interchange_path(origin_id: str, destination_id: str) -> dict:
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("""
                MATCH (start {station_id: $origin}), (end {station_id: $dest})
                CALL apoc.algo.dijkstra(start, end, 'METRO_LINK|RAIL_LINK|INTERCHANGE_TO', 'travel_time_min')
                YIELD path, weight
                RETURN [node IN nodes(path) | {
                    station_id: node.station_id,
                    name: node.name
                }] AS stations,
                weight AS total_time_min
            """, origin=origin_id, dest=destination_id)
            record = result.single()
            if not record:
                return {"found": False, "origin_id": origin_id, "destination_id": destination_id}
            stations = record["stations"]
            interchanges = [s for s in stations
                           if (s["station_id"].startswith("MS") and destination_id.startswith("NR"))
                           or (s["station_id"].startswith("NR") and destination_id.startswith("MS"))]
            return {
                "found": True,
                "stations": stations,
                "interchange_points": interchanges,
                "total_time_min": record["total_time_min"],
            }


# ── DELAY RIPPLE ANALYSIS ─────────────────────────────────────────────────────

def query_delay_ripple(delayed_station_id: str, hops: int = 2) -> list[dict]:
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("""
                MATCH (start {station_id: $station_id})
                MATCH (start)-[:METRO_LINK|RAIL_LINK*1..$hops]-(affected)
                WHERE affected.station_id <> $station_id
                RETURN DISTINCT affected.station_id AS station_id,
                       affected.name AS name,
                       min(length(shortestPath((start)-[:METRO_LINK|RAIL_LINK*]-(affected)))) AS hops_away
                ORDER BY hops_away
            """, station_id=delayed_station_id, hops=hops)
            return [dict(r) for r in result]


# ── STATION CONNECTIONS ───────────────────────────────────────────────────────

def query_station_connections(station_id: str) -> list[dict]:
    with _driver() as driver:
        with driver.session() as session:
            result = session.run("""
                MATCH (s {station_id: $station_id})-[r:METRO_LINK|RAIL_LINK|INTERCHANGE_TO]->(n)
                RETURN n.station_id AS station_id,
                       n.name AS name,
                       type(r) AS relationship,
                       r.travel_time_min AS travel_time_min,
                       r.line AS line
            """, station_id=station_id)
            return [dict(r) for r in result]
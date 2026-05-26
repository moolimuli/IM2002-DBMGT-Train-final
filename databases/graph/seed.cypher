// TransitFlow — Graph Schema Constraints & Indexes
// Executed by seed_neo4j.py before data seeding

// Uniqueness constraints
CREATE CONSTRAINT IF NOT EXISTS FOR (n:MetroStation) REQUIRE n.station_id IS UNIQUE;
CREATE CONSTRAINT IF NOT EXISTS FOR (n:NationalRailStation) REQUIRE n.station_id IS UNIQUE;

// Indexes for fast lookup
CREATE INDEX IF NOT EXISTS FOR (n:MetroStation) ON (n.station_id);
CREATE INDEX IF NOT EXISTS FOR (n:NationalRailStation) ON (n.station_id);

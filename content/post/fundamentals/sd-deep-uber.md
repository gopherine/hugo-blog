---
title: "Lesson 2: Design Uber — Real-time matching, geospatial indexing, surge pricing"
author: "Atharva Pandey"
series: "System Design Deep Dives"
lesson: 2
tags:
  - fundamentals
  - system design
  - interviews
keywords:
  - Uber system design
  - ride matching algorithm
  - geospatial indexing
  - surge pricing
  - real-time location tracking
date: "2024-05-25T00:00:00.000Z"
---

Uber is one of the most instructive system design problems because it forces you to think about real-time data pipelines, spatial indexing, and latency-sensitive matching algorithms all at once. I spent a significant amount of time studying this problem specifically because the geospatial angle is something most candidates gloss over. They say "use a database with location queries" and move on. But at Uber's scale — 5 million trips per day, hundreds of thousands of concurrent drivers and riders — the geospatial indexing strategy is the entire problem.

## Requirements

**Functional requirements**
- Riders can request a ride by specifying pickup and destination
- The system matches the rider to a nearby available driver within seconds
- Matched driver receives the rider's location and trip details
- Both parties see real-time location updates on a map during the trip
- Fare is calculated based on time, distance, and demand multiplier (surge)
- Riders and drivers can rate each other after a trip

**Non-functional requirements**
- Matching latency: under 2 seconds from request to matched driver
- Location update frequency: drivers broadcast position every 4 seconds
- Availability: 99.99% for the matching service (failed matches mean lost revenue)
- Consistency: eventual is acceptable for analytics, strong required for trip state machine
- Scale: 5M trips/day → ~58 trips per second peak, higher during surge events

**Scale estimates**
- 1M active drivers on the platform globally at any given moment
- Location update every 4 seconds per driver → 250,000 location updates per second
- Matching requests at peak: ~1,000/second during surge events

## High-Level Design

Uber's architecture decomposes into four major subsystems: location ingestion, geospatial indexing, matching, and trip management.

```
Driver App → Location Service → Location Store (geospatial index)
                                        ↓
Rider App → Matching Service ← Supply Query (find nearby drivers)
                    ↓
             Trip Service → Notification Service → Driver App
                    ↓
             Pricing Service (surge calculation)
```

**Location ingestion**

Every active driver's app sends a location update (latitude, longitude, heading, speed) every 4 seconds via a persistent WebSocket connection. At 1M active drivers, that's 250,000 messages per second into the Location Service. These updates are written to an in-memory geospatial store — not to a relational database. The write throughput and the need for sub-millisecond spatial queries rule out traditional databases.

**Matching flow**

When a rider requests a ride, the Matching Service queries the geospatial index for available drivers within a configurable radius (starting at 1km, expanding if insufficient results). It ranks candidates by estimated time to pickup (ETA), not straight-line distance. The top N candidates receive an offer notification via push. The first driver to accept is assigned the trip; others receive a cancellation notification.

## Deep Dive

**Geospatial Indexing with S2 / Geohash**

The core data structure problem: given 1 million driver locations that update 250,000 times per second, how do you efficiently answer "give me all drivers within 1km of this point" in under 10ms?

The answer is spatial decomposition. Two dominant approaches are Geohash and Google's S2 library.

**Geohash** divides the Earth's surface into a grid of rectangular cells using a base-32 encoding. A longer geohash string means a smaller, more precise cell. The useful property: nearby locations share common geohash prefixes. A driver at geohash `9q8yy` and another at `9q8yz` are neighbors. You can find all drivers in adjacent cells by looking up the 8 neighboring geohash prefixes.

```go
type DriverLocation struct {
    DriverID  string
    Lat       float64
    Lng       float64
    Status    string // "available", "on_trip"
    UpdatedAt time.Time
}

// In Redis: GEOADD drivers_available <lng> <lat> <driverID>
// Query: GEORADIUS drivers_available <lng> <lat> 1 km ASC COUNT 20

func findNearbyDrivers(ctx context.Context, rdb *redis.Client, lat, lng float64, radiusKm float64) ([]string, error) {
    res, err := rdb.GeoRadius(ctx, "drivers_available",
        lng, lat,
        &redis.GeoRadiusQuery{
            Radius:    radiusKm,
            Unit:      "km",
            Sort:      "ASC",
            Count:     20,
            WithCoord: true,
            WithDist:  true,
        },
    ).Result()
    if err != nil {
        return nil, fmt.Errorf("geo radius query: %w", err)
    }
    ids := make([]string, 0, len(res))
    for _, loc := range res {
        ids = append(ids, loc.Name)
    }
    return ids, nil
}
```

Redis's `GEORADIUS` command (now `GEOSEARCH`) is backed by a sorted set where scores encode the geohash. This is exactly how Uber and similar services implement their in-memory spatial index. The entire driver location dataset fits in a few hundred MB of RAM, making in-memory Redis viable.

**S2** uses Hilbert curves to map the sphere to a one-dimensional index, with the same locality property: nearby points have nearby S2 cell IDs. It handles the distortion at poles and the international date line more gracefully than Geohash. Uber has written publicly about using S2 for their H3 hexagonal indexing system (though H3 is their own derivative).

**The Trip State Machine**

A trip is a state machine with hard consistency requirements. You cannot have a driver simultaneously on two trips, or a trip that never transitions to "completed."

```
REQUESTED → DRIVER_ASSIGNED → DRIVER_EN_ROUTE → TRIP_STARTED → COMPLETED
                                                              ↘ CANCELLED
```

Each state transition is a write to the Trip Service with optimistic locking. The matching system uses a distributed lock (Redlock pattern over Redis) to prevent two matching workers from assigning the same driver to two trips simultaneously.

**Surge Pricing**

Surge pricing is a supply/demand ratio calculation, not a fixed formula. The Pricing Service continuously aggregates:
- Number of ride requests in a geographic zone in the last 5 minutes
- Number of available drivers in that zone

When demand exceeds supply by a threshold, a multiplier applies. The zone granularity matters: too coarse and you apply surge to areas where supply is actually fine; too fine and you create pricing instability as drivers cross cell boundaries.

Uber uses their H3 hexagonal grid for surge zones. Hexagons tile the sphere without the distortion of rectangular grids and have equal-area neighbors. A surge multiplier for a zone is computed as:

```
surge_multiplier = max(1.0, demand_rate / supply_rate * base_multiplier)
```

This is cached and refreshed every 30 seconds. Showing a rider the current multiplier before they confirm the trip is a transparency requirement (and a legal one in several jurisdictions).

## Scaling Challenges

**Location update write amplification**

250,000 location updates per second is a significant write load. The architecture handles this with a tiered approach: a write-optimized message queue (Kafka) ingests all updates first. A consumer group processes them to update the Redis geospatial index. The Kafka log also feeds an analytics pipeline for historical location data used in ETA modeling and surge calculation. You write once to Kafka, fan out to multiple consumers.

**ETA calculation at scale**

Simple geospatial distance is not ETA. A driver 800 meters away on the other side of a highway interchange may have a 12-minute ETA; a driver 1.2km away on a clear road may have a 3-minute ETA. Uber uses a pre-computed routing graph updated with real-time traffic data. The matching service queries this graph for candidate drivers. At scale, querying the full routing graph for 20 drivers per matching request at 1,000 requests/second is expensive. The solution is a combination: use geospatial radius as a fast pre-filter, then compute ETA only for the top 20 candidates.

**Hotspot regions**

Major events (a concert, a sports game) create localized demand spikes. The geospatial index for a single city event area might receive 10x normal write and read traffic. Consistent hashing distributes the driver location keyspace across Redis shards, but a hotspot may saturate one shard. Mitigations: shard further by a secondary key (driver ID modulo), use read replicas for radius queries, and pre-scale ahead of known events using the event calendar.

## Interview Tips

The Uber problem has a few traps that catch even strong candidates.

**Don't say "store lat/lng in a database and run a range query."** A SQL `WHERE lat BETWEEN x AND y AND lng BETWEEN a AND b` query does not use a single index efficiently and does not give you great circle distance. The moment you say this, you've shown you haven't thought about the geospatial indexing problem. Say "Redis GEOSEARCH backed by a geohash sorted set" or explain S2/H3.

**Be explicit about the matching flow's race conditions.** Two drivers receiving an offer simultaneously, both accepting, is a real problem. Explain distributed locking or optimistic concurrency control on the driver record.

**Separate the location update pipeline from the matching query path.** High-frequency writes go through a queue; reads come from an in-memory index. Conflating these paths leads to designs that can't handle the write throughput.

**Surge pricing is not magic.** Describe it as a supply/demand ratio over a spatial zone with a refresh interval. Interviewers appreciate concreteness over hand-waving.

## Key Takeaway

Uber's hardest problem is not the matching algorithm — it's getting 250,000 location updates per second into a queryable spatial index with single-digit millisecond read latency. The solution is always a combination of: (1) a fast write path through a message queue, (2) an in-memory spatial index with geohash or H3 semantics, and (3) a query strategy that uses spatial pre-filtering before expensive ETA calculations. The trip state machine and surge pricing are complex but tractable once the spatial indexing foundation is solid.

---

**Previous:** [Lesson 1: Design YouTube](/post/fundamentals/sd-deep-youtube/) | **Up next:** [Lesson 3: Design Google Docs — Real-time collaboration, OT vs CRDT](/post/fundamentals/sd-deep-google-docs/)

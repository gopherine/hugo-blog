---
title: "Lesson 7: Shortest Path — Dijkstra in routing and network optimization"
author: "Atharva Pandey"
keywords:
  - dijkstra
  - shortest path
  - graph algorithms
  - network routing
  - weighted graphs
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 7
date: "2024-07-19T00:00:00.000Z"
---

When I joined a team building a multi-region traffic routing system, the first thing I had to understand was why the routing decisions were not always the geographically shortest path. The system was using Dijkstra's algorithm, but the edge weights were not just latency — they incorporated bandwidth cost, current utilization, failure rates, and SLA constraints. Dijkstra did not care what the weights meant. It just found the minimum cost path. That is its power.

Shortest path algorithms appear in systems that route anything: network packets, delivery vehicles, data center traffic, API gateway requests, and even database query planners. Understanding Dijkstra gives you the foundation for understanding all of them.

## How It Works

Dijkstra's algorithm finds the minimum-cost path from a source node to all other nodes in a weighted graph with non-negative edge weights. It works by:

1. Maintaining a priority queue of (cost, node) pairs, initialized with (0, source)
2. Greedily pulling the minimum-cost unvisited node from the queue
3. For each neighbor, if the path through the current node is cheaper, update the neighbor's cost and add it to the queue

```go
package main

import (
    "container/heap"
    "fmt"
    "math"
)

type Edge struct {
    To     string
    Weight float64
}

type Graph map[string][]Edge

// Item in the priority queue
type Item struct {
    node string
    cost float64
    idx  int
}

type PriorityQueue []*Item

func (pq PriorityQueue) Len() int            { return len(pq) }
func (pq PriorityQueue) Less(i, j int) bool  { return pq[i].cost < pq[j].cost }
func (pq PriorityQueue) Swap(i, j int) {
    pq[i], pq[j] = pq[j], pq[i]
    pq[i].idx = i
    pq[j].idx = j
}
func (pq *PriorityQueue) Push(x any) { item := x.(*Item); item.idx = len(*pq); *pq = append(*pq, item) }
func (pq *PriorityQueue) Pop() any   { old := *pq; n := len(old); x := old[n-1]; *pq = old[:n-1]; return x }

// Dijkstra returns shortest distances from source and the predecessor map for path reconstruction
func Dijkstra(g Graph, source string) (map[string]float64, map[string]string) {
    dist := make(map[string]float64)
    prev := make(map[string]string)
    for node := range g {
        dist[node] = math.MaxFloat64
    }
    dist[source] = 0

    pq := &PriorityQueue{{node: source, cost: 0}}
    heap.Init(pq)

    for pq.Len() > 0 {
        curr := heap.Pop(pq).(*Item)
        if curr.cost > dist[curr.node] {
            continue // stale entry
        }
        for _, edge := range g[curr.node] {
            newCost := dist[curr.node] + edge.Weight
            if newCost < dist[edge.To] {
                dist[edge.To] = newCost
                prev[edge.To] = curr.node
                heap.Push(pq, &Item{node: edge.To, cost: newCost})
            }
        }
    }
    return dist, prev
}

// ReconstructPath traces back through prev map to get the actual path
func ReconstructPath(prev map[string]string, source, target string) []string {
    var path []string
    for node := target; node != ""; node = prev[node] {
        path = append([]string{node}, path...)
        if node == source {
            break
        }
    }
    return path
}
```

The time complexity is O((V + E) log V) with a binary heap priority queue, where V is the number of vertices and E is the number of edges.

## When You Need It

Any problem that asks "what is the cheapest way to get from A to B?" maps to shortest path. "Cheapest" can be:

- **Latency**: minimum latency route between data centers
- **Cost**: cheapest API path across providers with different pricing
- **Hops**: fewest service calls in a microservice graph
- **Risk**: path that minimizes failure probability (use negative log probability as weights)
- **Combined**: weighted combination of latency, cost, and reliability

Dijkstra requires non-negative edge weights. For graphs with negative weights, use Bellman-Ford (handles negatives, O(VE)) or be aware of the restriction when modeling your problem.

```go
// Modeling a multi-datacenter routing problem
type DataCenter struct {
    Region  string
    Zone    string
}

type Link struct {
    From      string
    To        string
    LatencyMs float64
    CostPerGB float64
    Reliability float64 // 0 to 1
}

// Build a cost graph from links, weighting by composite metric
func BuildRoutingGraph(links []Link, latencyWeight, costWeight, reliabilityWeight float64) Graph {
    g := make(Graph)
    for _, l := range links {
        // Normalize and combine: lower is better
        // Use -log(reliability) to turn reliability into an additive cost
        reliabilityCost := 0.0
        if l.Reliability > 0 {
            reliabilityCost = -math.Log(l.Reliability)
        }
        weight := latencyWeight*l.LatencyMs +
            costWeight*l.CostPerGB +
            reliabilityWeight*reliabilityCost

        g[l.From] = append(g[l.From], Edge{To: l.To, Weight: weight})
    }
    return g
}
```

## Production Example

A CDN edge routing system needed to select, for each incoming request, the edge node that minimized a composite metric: latency to the user plus latency from that edge node back to origin, weighted by current load. Nodes with high load had artificially increased weights to steer traffic away.

```go
type EdgeNode struct {
    ID       string
    Region   string
    Load     float64 // 0 to 1
}

type RoutingGraph struct {
    graph     Graph
    userToEdge map[string][]Edge // user region -> edge nodes with weights
}

func (rg *RoutingGraph) BestEdgeForUser(userRegion string) (string, float64) {
    // Add a virtual "user" node connected to edge nodes by user latency
    g := make(Graph)
    for node, edges := range rg.graph {
        g[node] = edges
    }
    g["__user__"] = rg.userToEdge[userRegion]

    dist, _ := Dijkstra(g, "__user__")

    // Find the edge node with minimum cost to reach the origin ("__origin__")
    // In practice, edge nodes connect to origin; we want edge node minimizing
    // user_to_edge + edge_to_origin costs
    best := ""
    bestCost := math.MaxFloat64
    for node, cost := range dist {
        if node != "__user__" && node != "__origin__" {
            if cost < bestCost {
                bestCost = cost
                best = node
            }
        }
    }
    return best, bestCost
}

// Dynamic weight adjustment based on current load
func edgeWeight(baseLatency float64, load float64) float64 {
    // Exponential penalty for high load: at 90% load, weight doubles
    loadPenalty := math.Exp(load*2) - 1
    return baseLatency * (1 + loadPenalty)
}
```

The system recomputed the routing graph every 10 seconds using fresh load metrics. The Dijkstra computation over ~200 edge nodes took under 1ms, making it viable to run on every request if needed.

```go
// Measuring path quality over time for monitoring
type RouteMetrics struct {
    Path        []string
    TotalCost   float64
    Hops        int
    ComputedAt  time.Time
}

func (rg *RoutingGraph) ComputeWithMetrics(src, dst string) RouteMetrics {
    start := time.Now()
    dist, prev := Dijkstra(rg.graph, src)
    path := ReconstructPath(prev, src, dst)
    return RouteMetrics{
        Path:       path,
        TotalCost:  dist[dst],
        Hops:       len(path) - 1,
        ComputedAt: start,
    }
}
```

## The Tradeoffs

Dijkstra requires the full graph in memory and does not work well with very large graphs (millions of nodes). For those scales, you need hierarchical routing (pre-compute routes at a higher abstraction level), contraction hierarchies, or A* with a good heuristic.

**A*** extends Dijkstra with a heuristic function that estimates the remaining distance to the target. For geographic routing, this is usually Euclidean distance. A* explores far fewer nodes than Dijkstra when the heuristic is good, making it the standard choice for map routing.

Dijkstra does not handle dynamic graphs well. If edge weights change frequently (e.g., network congestion changes every second), you have to choose between rerunning Dijkstra frequently (expensive) or using an approximate routing table that may be slightly stale (usually acceptable).

Also: Dijkstra cannot handle negative edge weights. If you model discounts or incentives as negative costs, use Bellman-Ford or reframe the problem.

## Key Takeaway

Dijkstra's algorithm finds minimum-cost paths in weighted graphs with non-negative edge weights. In production, "cost" is whatever you define it to be — latency, dollar cost, failure risk, or a weighted combination. The algorithm itself does not care. This makes it a general tool for any routing or optimization problem that can be represented as finding the cheapest traversal through a graph. Understanding it also gives you the foundation to understand A*, network routing protocols (OSPF is essentially distributed Dijkstra), and database query planners.

---

← [Lesson 6: BFS and DFS](/post/fundamentals/algo-bfs-dfs/) | [Course Index](/post/fundamentals/) | Next: [Lesson 8: Dynamic Programming Intuition](/post/fundamentals/algo-dynamic-programming/) →

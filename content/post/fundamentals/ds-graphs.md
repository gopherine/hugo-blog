---
title: "Lesson 8: Graphs — You're solving graph problems without knowing it"
author: Atharva Pandey
series: "Data Structures for Production Engineers"
lesson: 8
keywords: [data structures, computer science, graphs, BFS, DFS, adjacency list, topology]
tags: [fundamentals, data structures]
date: '2024-07-23T00:00:00.000Z'
---

Most engineers don't think of themselves as solving graph problems. They think they're deploying microservices, resolving package dependencies, routing network traffic, or modeling social connections. But the underlying structure in all of these is a graph, and the algorithms that make those systems work — Dijkstra's, topological sort, BFS, DFS — are graph algorithms.

Once I started seeing graphs everywhere, I started solving systems problems better. Let me show you the representation, the key algorithms, and the production contexts where this thinking pays off.

## How It Actually Works

A graph is a set of nodes (vertices) and edges connecting them. Edges can be directed (one-way) or undirected (bidirectional), and weighted (with a cost) or unweighted.

There are two standard representations:

**Adjacency list**: each node maps to a list of its neighbors. O(V + E) space. Efficient for sparse graphs (most real-world graphs are sparse).

**Adjacency matrix**: a V×V boolean matrix where `matrix[i][j]` = true if there's an edge from i to j. O(V²) space. Efficient for dense graphs and fast edge-existence checks.

```go
package main

import "fmt"

// Adjacency list graph — directed, weighted
type Graph struct {
    nodes map[string][]Edge
}

type Edge struct {
    To     string
    Weight int
}

func NewGraph() *Graph {
    return &Graph{nodes: make(map[string][]Edge)}
}

func (g *Graph) AddEdge(from, to string, weight int) {
    g.nodes[from] = append(g.nodes[from], Edge{to, weight})
}

func (g *Graph) Neighbors(node string) []Edge {
    return g.nodes[node]
}

// BFS — shortest path in unweighted graph / level-order traversal
func (g *Graph) BFS(start string) []string {
    visited := make(map[string]bool)
    order := []string{}
    queue := []string{start}
    visited[start] = true

    for len(queue) > 0 {
        node := queue[0]
        queue = queue[1:]
        order = append(order, node)

        for _, edge := range g.Neighbors(node) {
            if !visited[edge.To] {
                visited[edge.To] = true
                queue = append(queue, edge.To)
            }
        }
    }
    return order
}

func main() {
    g := NewGraph()
    g.AddEdge("api", "auth", 1)
    g.AddEdge("api", "db", 2)
    g.AddEdge("auth", "cache", 1)
    g.AddEdge("db", "cache", 1)

    fmt.Println("BFS from api:", g.BFS("api"))
}
```

## When to Use It

Graph problems appear constantly in infrastructure:

- **Service dependencies**: can I deploy service A without breaking service B? (topological sort, cycle detection)
- **Network routing**: what's the shortest path from this edge node to that data center? (Dijkstra's, Bellman-Ford)
- **Social graphs**: friend-of-friend recommendations (BFS)
- **Package management**: resolving dependency order (topological sort on a DAG)
- **Kubernetes scheduling**: which node can accommodate this pod given resource constraints? (graph matching)
- **Circuit breaker cascade analysis**: which services will fail if service X goes down? (DFS reachability)

If you have entities and relationships between them, you have a graph. The question is whether you recognize it.

## Production Example

Topological sort is essential for any system that needs to execute tasks in dependency order. Kubernetes controllers use this. CI/CD pipelines use this. Terraform uses this for its apply order.

```go
package main

import (
    "fmt"
)

type DAG struct {
    nodes    map[string]bool
    edges    map[string][]string
    inDegree map[string]int
}

func NewDAG() *DAG {
    return &DAG{
        nodes:    make(map[string]bool),
        edges:    make(map[string][]string),
        inDegree: make(map[string]int),
    }
}

func (d *DAG) AddNode(node string) {
    if !d.nodes[node] {
        d.nodes[node] = true
        d.inDegree[node] = 0
    }
}

func (d *DAG) AddEdge(from, to string) {
    d.AddNode(from)
    d.AddNode(to)
    d.edges[from] = append(d.edges[from], to)
    d.inDegree[to]++
}

// TopoSort returns nodes in dependency order (Kahn's algorithm)
// Returns error if a cycle is detected
func (d *DAG) TopoSort() ([]string, error) {
    // Start with all nodes that have no incoming edges
    queue := []string{}
    for node := range d.nodes {
        if d.inDegree[node] == 0 {
            queue = append(queue, node)
        }
    }

    result := []string{}
    inDegree := make(map[string]int)
    for k, v := range d.inDegree {
        inDegree[k] = v
    }

    for len(queue) > 0 {
        node := queue[0]
        queue = queue[1:]
        result = append(result, node)

        for _, dep := range d.edges[node] {
            inDegree[dep]--
            if inDegree[dep] == 0 {
                queue = append(queue, dep)
            }
        }
    }

    if len(result) != len(d.nodes) {
        return nil, fmt.Errorf("cycle detected: cannot topologically sort")
    }
    return result, nil
}

func main() {
    // Infrastructure deployment order
    dag := NewDAG()
    dag.AddEdge("vpc", "subnets")
    dag.AddEdge("subnets", "security-groups")
    dag.AddEdge("security-groups", "rds")
    dag.AddEdge("security-groups", "redis")
    dag.AddEdge("rds", "app-server")
    dag.AddEdge("redis", "app-server")
    dag.AddEdge("app-server", "load-balancer")

    order, err := dag.TopoSort()
    if err != nil {
        fmt.Println("Error:", err)
        return
    }

    fmt.Println("Deployment order:")
    for i, resource := range order {
        fmt.Printf("  %d. %s\n", i+1, resource)
    }
}
```

Now let's look at Dijkstra's shortest path — the algorithm behind every routing system from BGP to Google Maps:

```go
package main

import (
    "container/heap"
    "fmt"
    "math"
)

type Item struct {
    node string
    dist int
    idx  int
}

type PriorityQueue []*Item

func (pq PriorityQueue) Len() int            { return len(pq) }
func (pq PriorityQueue) Less(i, j int) bool  { return pq[i].dist < pq[j].dist }
func (pq PriorityQueue) Swap(i, j int) {
    pq[i], pq[j] = pq[j], pq[i]
    pq[i].idx, pq[j].idx = i, j
}
func (pq *PriorityQueue) Push(x interface{}) {
    item := x.(*Item)
    item.idx = len(*pq)
    *pq = append(*pq, item)
}
func (pq *PriorityQueue) Pop() interface{} {
    old := *pq
    n := len(old)
    item := old[n-1]
    *pq = old[:n-1]
    return item
}

func Dijkstra(graph map[string][]Edge, start string) map[string]int {
    dist := make(map[string]int)
    for node := range graph {
        dist[node] = math.MaxInt32
    }
    dist[start] = 0

    pq := &PriorityQueue{{node: start, dist: 0}}
    heap.Init(pq)

    for pq.Len() > 0 {
        curr := heap.Pop(pq).(*Item)
        if curr.dist > dist[curr.node] {
            continue // stale entry
        }
        for _, edge := range graph[curr.node] {
            newDist := dist[curr.node] + edge.Weight
            if newDist < dist[edge.To] {
                dist[edge.To] = newDist
                heap.Push(pq, &Item{node: edge.To, dist: newDist})
            }
        }
    }
    return dist
}

type Edge struct {
    To     string
    Weight int
}

func main() {
    // Network latency graph (ms)
    graph := map[string][]Edge{
        "us-east-1": {{"us-west-2", 70}, {"eu-west-1", 80}},
        "us-west-2": {{"us-east-1", 70}, {"ap-northeast-1", 130}},
        "eu-west-1": {{"us-east-1", 80}, {"ap-southeast-1", 160}},
        "ap-northeast-1": {{"us-west-2", 130}, {"ap-southeast-1", 70}},
        "ap-southeast-1": {{"eu-west-1", 160}, {"ap-northeast-1", 70}},
    }

    distances := Dijkstra(graph, "us-east-1")
    fmt.Println("Minimum latency from us-east-1:")
    for region, ms := range distances {
        fmt.Printf("  -> %s: %dms\n", region, ms)
    }
}
```

## The Tradeoffs

**Representation choice matters.** Adjacency lists are O(V + E) space and fast for edge iteration. Adjacency matrices are O(V²) but support O(1) edge-existence checks. For sparse graphs (most real-world networks), adjacency lists win. For complete graphs (all pairs connected), matrices can be better.

**Cycle detection is critical.** In dependency graphs, cycles mean deadlock or infinite loops. Kahn's algorithm (topological sort) naturally detects cycles — if the output length is less than the number of nodes, a cycle exists. DFS-based detection uses a "currently visiting" set alongside a "visited" set.

**Scale limits everything.** BFS and Dijkstra's are O(V + E log V) with a binary heap. For social graphs with billions of nodes and edges, these algorithms don't run in memory on one machine. Production systems partition graphs, use approximate algorithms, or precompute answers (read: that's a distributed systems problem, not a data structures problem).

**Negative edges break Dijkstra's.** If your edge weights can be negative, Dijkstra's produces incorrect results. Use Bellman-Ford (O(VE)) or restructure your problem to avoid negative weights.

## Key Takeaway

Graphs are not exotic algorithmic territory — they're the structure underlying microservice dependencies, infrastructure orchestration, network routing, and organizational hierarchies. Learning to recognize when your problem is a graph problem, and which graph algorithm applies, is a force multiplier. Topological sort resolves ordering, BFS finds shortest unweighted paths, Dijkstra's finds shortest weighted paths, and DFS detects cycles and explores connected components. Each one is a tool for a specific production scenario.

---

*Previous: [Lesson 7: Heaps and Priority Queues — Scheduling, top-K, and rate limiters](/post/fundamentals/ds-heaps/)*

*Next: [Lesson 9: Tries — Prefix matching, autocomplete, routing tables](/post/fundamentals/ds-tries/)*

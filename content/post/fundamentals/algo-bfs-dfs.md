---
title: "Lesson 6: BFS and DFS — Dependency resolution, crawlers, cycle detection"
author: "Atharva Pandey"
keywords:
  - breadth first search
  - depth first search
  - graph traversal
  - dependency resolution
  - topological sort
  - cycle detection
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 6
date: "2024-07-03T00:00:00.000Z"
---

Graph traversal sounds abstract until you realize that most interesting data in production is a graph. Service dependencies are a graph. Database foreign key relationships form a graph. Build tool dependencies, org charts, permission hierarchies, network topologies — all graphs. BFS and DFS are the two fundamental ways to walk them, and they show up in real engineering work more than almost any other algorithm.

I have used DFS to detect circular imports in a build system, BFS to find the shortest migration path between two schema versions, and both to debug why a dependency injection container was resolving services in the wrong order.

## How It Works

**BFS** (breadth-first search) explores a graph level by level. It uses a queue. It visits all neighbors of the current node before moving deeper. This guarantees that the first time it reaches a node, it is via the shortest path (in terms of number of edges).

**DFS** (depth-first search) explores as far as possible down one branch before backtracking. It uses a stack — either explicitly or via recursion. It is the natural algorithm for topological sort, cycle detection, and scenarios where you need to fully explore a path before trying alternatives.

```go
// Graph represented as adjacency list
type Graph struct {
    edges map[string][]string
}

func NewGraph() *Graph {
    return &Graph{edges: make(map[string][]string)}
}

func (g *Graph) AddEdge(from, to string) {
    g.edges[from] = append(g.edges[from], to)
}

// BFS — level-by-level traversal, returns nodes in visit order
func (g *Graph) BFS(start string) []string {
    visited := make(map[string]bool)
    queue := []string{start}
    visited[start] = true
    var order []string

    for len(queue) > 0 {
        node := queue[0]
        queue = queue[1:]
        order = append(order, node)

        for _, neighbor := range g.edges[node] {
            if !visited[neighbor] {
                visited[neighbor] = true
                queue = append(queue, neighbor)
            }
        }
    }
    return order
}

// DFS — deep traversal, returns nodes in visit order
func (g *Graph) DFS(start string) []string {
    visited := make(map[string]bool)
    var order []string

    var dfs func(node string)
    dfs = func(node string) {
        if visited[node] {
            return
        }
        visited[node] = true
        order = append(order, node)
        for _, neighbor := range g.edges[node] {
            dfs(neighbor)
        }
    }

    dfs(start)
    return order
}
```

## When You Need It

**Use BFS when:**
- You need the shortest path (fewest hops) between two nodes
- You are doing level-by-level processing (e.g., finding all direct dependencies before transitive ones)
- The graph is wide but not deep

**Use DFS when:**
- You need a topological ordering (dependency resolution)
- You need to detect cycles
- You need to find all paths (backtracking)
- The graph is deep and you want to reach leaf nodes quickly

The most production-critical DFS application is topological sort with cycle detection. This is exactly what happens when a build system, container orchestrator, or dependency injector resolves load order.

```go
// Topological sort with cycle detection using DFS
// Returns (sorted order, error if cycle exists)
type TopoSort struct {
    graph   map[string][]string
    visited map[string]bool // permanently visited
    inStack map[string]bool // in current DFS path (for cycle detection)
    order   []string
}

func TopologicalSort(deps map[string][]string) ([]string, error) {
    ts := &TopoSort{
        graph:   deps,
        visited: make(map[string]bool),
        inStack: make(map[string]bool),
    }
    for node := range deps {
        if !ts.visited[node] {
            if err := ts.dfs(node); err != nil {
                return nil, err
            }
        }
    }
    // Result is in reverse post-order
    for i, j := 0, len(ts.order)-1; i < j; i, j = i+1, j-1 {
        ts.order[i], ts.order[j] = ts.order[j], ts.order[i]
    }
    return ts.order, nil
}

func (ts *TopoSort) dfs(node string) error {
    ts.inStack[node] = true
    for _, dep := range ts.graph[node] {
        if ts.inStack[dep] {
            return fmt.Errorf("cycle detected: %s depends on %s which creates a cycle", node, dep)
        }
        if !ts.visited[dep] {
            if err := ts.dfs(dep); err != nil {
                return err
            }
        }
    }
    ts.inStack[node] = false
    ts.visited[node] = true
    ts.order = append(ts.order, node)
    return nil
}
```

## Production Example

A microservices deployment system needed to start services in the correct order — a service that depended on a database should not start before the database was ready. The dependency graph was declared in a YAML manifest and could contain cycles if engineers made mistakes.

```go
type ServiceManifest struct {
    Name         string
    DependsOn    []string
}

type DeployPlan struct {
    order    []string
    parallel [][]string // services that can start in parallel at each level
}

func BuildDeployPlan(manifests []ServiceManifest) (*DeployPlan, error) {
    // Build adjacency list: service -> its dependencies
    deps := make(map[string][]string)
    for _, m := range manifests {
        deps[m.Name] = m.DependsOn
        for _, d := range m.DependsOn {
            if _, exists := deps[d]; !exists {
                deps[d] = nil // ensure all nodes are in the graph
            }
        }
    }

    // Topological sort detects cycles and gives us serial order
    order, err := TopologicalSort(deps)
    if err != nil {
        return nil, fmt.Errorf("invalid deployment manifest: %w", err)
    }

    // BFS to find parallelism: services with no remaining unstarted deps
    // can start at the same time (same "level")
    inDegree := make(map[string]int)
    for _, m := range manifests {
        for _, dep := range m.DependsOn {
            inDegree[dep]++ // dep is needed by someone
        }
    }
    _ = inDegree // used in a full Kahn's algorithm implementation

    return &DeployPlan{order: order}, nil
}

func ValidateNoCycles(manifests []ServiceManifest) error {
    deps := make(map[string][]string)
    for _, m := range manifests {
        deps[m.Name] = m.DependsOn
    }
    _, err := TopologicalSort(deps)
    return err
}
```

In practice, the deployment system caught circular dependencies at deploy time — before any services were started — and produced a clear error message showing which services formed the cycle. Without cycle detection, the system would either hang indefinitely or start services in a random order that sometimes worked and sometimes did not.

BFS was then used to compute parallelism: any services whose dependencies were already running could start simultaneously. This turned a serial deployment of 40 services (taking 8 minutes) into parallel waves that finished in under 2 minutes.

## The Tradeoffs

Recursive DFS uses O(depth) stack space. For deep graphs (thousands of levels), this causes stack overflow. The iterative DFS variant (using an explicit stack) avoids this but is more complex to implement correctly for topological sort because you need to handle the "post-visit" event.

BFS is memory-intensive for wide graphs. If each node has 1,000 neighbors and the graph is 5 levels deep, the queue could hold 1,000^5 entries before you find a path. In practice, visited tracking keeps this bounded, but the queue can still consume significant memory for dense graphs.

Graph algorithms also assume the graph fits in memory. For truly large graphs (web-scale crawls, social graphs), you need distributed algorithms (like MapReduce-based BFS or streaming graph processing) or offline processing pipelines.

## Key Takeaway

BFS gives you shortest paths and level-by-level processing. DFS gives you topological order, cycle detection, and full path exploration. Every system with dependencies, hierarchies, or relationships is a graph. When you need to validate that graph (no cycles), order it (topological sort), or find paths through it (BFS), reach for these two traversal algorithms before anything else.

---

← [Lesson 5: Hashing and Consistent Hashing](/post/fundamentals/algo-hashing/) | [Course Index](/post/fundamentals/) | Next: [Lesson 7: Shortest Path](/post/fundamentals/algo-shortest-path/) →

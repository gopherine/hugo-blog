---
title: "Lesson 2: Deployments and Scaling — Rolling updates, HPA, VPA"
author: Atharva Pandey
keywords:
  - Kubernetes
  - Deployments
  - rolling updates
  - Horizontal Pod Autoscaler
  - HPA
  - VPA
  - Vertical Pod Autoscaler
  - autoscaling
tags:
  - fundamentals
  - kubernetes
  - devops
series: "Kubernetes Patterns"
lesson: 2
date: '2024-08-07T00:00:00.000Z'
---

The week before a major product launch, our Kubernetes cluster started evicting pods. Traffic was spiking as we ran load tests, but instead of scaling up, the HPA was oscillating — scaling up, then the new pods were getting OOMKilled, then scaled down, then up again. Pods were in CrashLoopBackOff, the HPA metrics were lagging behind the actual load, and I was watching health check failures cascade. It turned out our resource requests were wildly inaccurate — set once during initial deployment and never updated as the service's actual usage changed. That day I learned that Deployments and autoscaling aren't fire-and-forget configurations.

## The Pattern

Kubernetes Deployments manage the desired state of a set of pods. You declare "I want 3 replicas of this image running with these resource requirements," and the Deployment controller ensures that state is maintained. Rolling updates, rollbacks, and scaling are all Deployment operations.

The scaling layer sits on top: the Horizontal Pod Autoscaler (HPA) adjusts the number of replicas based on observed metrics. The Vertical Pod Autoscaler (VPA) adjusts the resource requests of existing pods based on observed usage. Cluster Autoscaler sits one level higher and adds or removes nodes based on whether pods can be scheduled.

Understanding how these three interact — and where they conflict — is the key to a well-tuned cluster.

## How It Works

A Deployment spec has two critical sections: the pod template (which defines what runs) and the update strategy (which defines how updates are rolled out).

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api-server
spec:
  replicas: 3
  selector:
    matchLabels:
      app: api-server
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1        # allow 1 extra pod above desired (so 4 total during update)
      maxUnavailable: 0  # never go below desired count (zero-downtime rollout)
  template:
    metadata:
      labels:
        app: api-server
    spec:
      containers:
        - name: api
          image: mycompany/api:v2.3.1
          resources:
            requests:
              cpu: "250m"
              memory: "256Mi"
            limits:
              cpu: "1000m"
              memory: "512Mi"
          readinessProbe:
            httpGet:
              path: /healthz
              port: 8080
            initialDelaySeconds: 5
            periodSeconds: 5
```

With `maxSurge: 1` and `maxUnavailable: 0`, a rolling update with 3 replicas proceeds as: spin up one new pod (now 4 total), wait for it to pass readiness checks, terminate one old pod (back to 3), repeat. The readiness probe is what prevents traffic from being sent to a pod that isn't ready yet — this is not optional for zero-downtime deployments.

**Horizontal Pod Autoscaler** watches a metric and adjusts `spec.replicas` on the Deployment:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: api-server-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api-server
  minReplicas: 2
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 60   # scale up when average CPU > 60% of request
    - type: Resource
      resource:
        name: memory
        target:
          type: AverageValue
          averageValue: "400Mi"
  behavior:
    scaleUp:
      stabilizationWindowSeconds: 30     # wait 30s before scaling up again
    scaleDown:
      stabilizationWindowSeconds: 300    # wait 5 min before scaling down
```

The stabilization windows are critical. Without a scale-down window, the HPA aggressively removes pods the moment CPU drops, which then causes a spike when the next burst of traffic arrives, which causes scale-up, then scale-down — oscillation. Five minutes is a conservative but stable scale-down window for most HTTP services.

**Vertical Pod Autoscaler** in its three modes:

```yaml
apiVersion: autoscaling.k8s.io/v1
kind: VerticalPodAutoscaler
metadata:
  name: api-server-vpa
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: api-server
  updatePolicy:
    updateMode: "Off"      # "Off" = recommendation only, no auto-apply
                           # "Initial" = apply on pod creation only
                           # "Auto" = evict and recreate pods to apply changes
  resourcePolicy:
    containerPolicies:
      - containerName: api
        minAllowed:
          cpu: "100m"
          memory: "128Mi"
        maxAllowed:
          cpu: "2000m"
          memory: "2Gi"
```

I run VPA in `Off` mode first and watch the recommendations for a week before switching to `Initial`. VPA in `Auto` mode evicts pods to apply resource changes, which can be disruptive if you don't have enough replicas to absorb the evictions gracefully.

## Production Example

The interaction between HPA and VPA is where most teams get confused. They are **not designed to run on the same metric at the same time**. If HPA is scaling based on CPU utilization percentage (a relative metric — percentage of request), and VPA is simultaneously changing the CPU request value, the HPA target moves under the HPA controller's feet. A pod with a CPU request of 250m at 60% utilization looks different to HPA than the same pod after VPA changes the request to 400m.

The safe combinations:
- HPA on CPU/memory utilization percentage + VPA in `Off` mode (VPA for recommendations only)
- HPA on custom metrics (e.g., requests per second) + VPA on CPU/memory in `Auto` mode
- Only HPA, with accurately manually-set resource requests
- Only VPA (for batch workloads with irregular resource needs)

For our API service, the pattern that works: HPA on requests-per-second (a custom metric via KEDA), VPA in `Off` mode with weekly reviews of recommendations, manually updating resource requests during each service's planned maintenance window.

## The Gotchas

**Resource requests are a scheduling hint AND an autoscaler input**: If your CPU request is too low, your pod gets scheduled alongside many others on a node, fights for CPU, and is slow. If it's too high, the HPA thinks you have lots of headroom and doesn't scale when you should. Accurate requests are the foundation everything else builds on. Use VPA recommendations to calibrate.

**Pod Disruption Budgets for HPA scale-down**: When HPA scales down, it terminates pods. If it terminates too many simultaneously, you lose availability. Set a PodDisruptionBudget to constrain how many pods can be unavailable at once:

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: api-server-pdb
spec:
  minAvailable: 2        # or use maxUnavailable: 1
  selector:
    matchLabels:
      app: api-server
```

**Metrics lag**: HPA polls metrics every 15 seconds by default, and the metrics themselves are aggregated over a 60-second window by default in the metrics server. So the HPA is potentially reacting to 75-second-old data. For bursty workloads, this lag means you'll always be behind the traffic spike. Solutions: pre-scale based on time-of-day using KEDA's CronScaler, or use predictive scaling based on historical traffic patterns.

**OOMKilled pods during rolling updates**: During a rolling update, you briefly have more pods than usual (due to `maxSurge`). If your node is near memory capacity, the surge pods can trigger the OOM killer. Always leave headroom on nodes — don't pack them to 90% memory utilization in steady state if you rely on rolling updates.

## Key Takeaway

Deployments are Kubernetes' mechanism for declarative pod management, and rolling updates with proper readiness probes are how you achieve zero-downtime deployments. HPA scales replicas based on observed metrics — tune the stabilization windows to prevent oscillation. VPA tunes resource requests based on actual usage — use it for recommendations at first, auto-apply once you trust it. The most common mistake is inaccurate resource requests: too low causes CPU throttling and poor HPA behavior, too high wastes money and masks scaling problems. Don't combine HPA on CPU/memory percentage with VPA in Auto mode — they fight each other. Use custom metrics (via KEDA) or dedicate each scaler to a different axis.

---

**Previous:** [Lesson 1: Pod Design Patterns — Sidecar, ambassador, adapter](/post/fundamentals/k8s-pod-patterns/)
**Next:** [Lesson 3: ConfigMaps and Secrets — Configuration without rebuilding](/post/fundamentals/k8s-config-secrets/)

---
title: "Lesson 4: Operators and CRDs — Extending Kubernetes with your own resources"
author: Atharva Pandey
keywords:
  - Kubernetes
  - Operators
  - CRDs
  - Custom Resource Definitions
  - controller pattern
  - reconciliation loop
tags:
  - fundamentals
  - kubernetes
  - devops
series: "Kubernetes Patterns"
lesson: 4
date: '2025-01-03T00:00:00.000Z'
---

I used to manage PostgreSQL on Kubernetes with a collection of shell scripts and Helm hooks. Provisioning a new database instance meant running a script that created a StatefulSet, a Service, a ConfigMap, a Secret, and set up replication. Failover meant manually triggering another script. Backups were a cron job that required careful coordination with the stateful set. Every operational task was a script that a human had to run, remember, and maintain. Then I discovered the Zalando Postgres Operator, and I understood what the Operator pattern is actually for: encoding human operational knowledge into the Kubernetes control plane itself.

## The Pattern

An Operator is a Kubernetes controller that manages a custom resource. A Custom Resource Definition (CRD) teaches Kubernetes about a new object type. An Operator watches that object type and reconciles the actual state of the world toward the desired state expressed in the object.

The key insight is that the Kubernetes control loop — watch, compare, act — is a general mechanism. The built-in controllers (Deployment controller, ReplicaSet controller, StatefulSet controller) use it. You can write your own controller that uses the same loop to manage anything: a PostgreSQL cluster, a Kafka topic, a TLS certificate, a user account in an external system. The Operator pattern is the Kubernetes way of building automation for stateful, complex workloads.

## How It Works

First, you define the CRD — the schema for your custom resource:

```yaml
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: postgresclusters.db.mycompany.com
spec:
  group: db.mycompany.com
  names:
    kind: PostgresCluster
    plural: postgresclusters
    singular: postgrescluster
    shortNames: ["pgc"]
  scope: Namespaced
  versions:
    - name: v1alpha1
      served: true
      storage: true
      schema:
        openAPIV3Schema:
          type: object
          properties:
            spec:
              type: object
              required: ["version", "replicas"]
              properties:
                version:
                  type: string
                  enum: ["14", "15", "16"]
                replicas:
                  type: integer
                  minimum: 1
                  maximum: 7
                storageGB:
                  type: integer
                  default: 20
            status:
              type: object
              properties:
                phase:
                  type: string
                primaryEndpoint:
                  type: string
                readyReplicas:
                  type: integer
```

Once the CRD is installed, users can create `PostgresCluster` resources:

```yaml
apiVersion: db.mycompany.com/v1alpha1
kind: PostgresCluster
metadata:
  name: orders-db
  namespace: production
spec:
  version: "15"
  replicas: 3
  storageGB: 100
```

The Operator watches for `PostgresCluster` objects and reconciles the actual state. In Go using controller-runtime:

```go
import (
    "context"
    "sigs.k8s.io/controller-runtime/pkg/client"
    "sigs.k8s.io/controller-runtime/pkg/reconcile"
)

type PostgresClusterReconciler struct {
    client.Client
}

func (r *PostgresClusterReconciler) Reconcile(ctx context.Context, req reconcile.Request) (reconcile.Result, error) {
    // 1. Fetch the current state of the custom resource
    cluster := &dbv1alpha1.PostgresCluster{}
    if err := r.Get(ctx, req.NamespacedName, cluster); err != nil {
        return reconcile.Result{}, client.IgnoreNotFound(err)
    }

    // 2. Determine what should exist
    desiredStatefulSet := buildStatefulSet(cluster)
    desiredService := buildService(cluster)

    // 3. Create or update to match desired state
    if err := r.createOrUpdate(ctx, desiredStatefulSet); err != nil {
        return reconcile.Result{}, err
    }
    if err := r.createOrUpdate(ctx, desiredService); err != nil {
        return reconcile.Result{}, err
    }

    // 4. Update status
    cluster.Status.Phase = "Running"
    cluster.Status.ReadyReplicas = int32(cluster.Spec.Replicas)
    r.Status().Update(ctx, cluster)

    // 5. Requeue after 30s for drift detection
    return reconcile.Result{RequeueAfter: 30 * time.Second}, nil
}
```

The reconcile loop is idempotent by design — it can be called any number of times and must produce the same result. It compares the desired state (the spec) to the actual state (what actually exists in the cluster and the external world) and takes actions to close the gap.

## Production Example

The Cert-Manager operator is one of the best examples of the Operator pattern done right. It introduces three CRDs: `Certificate`, `Issuer`, and `ClusterIssuer`. When you create a `Certificate` resource, the Cert-Manager Operator:

1. Validates the certificate spec
2. Creates an `Order` and `Challenge` for the ACME protocol if using Let's Encrypt
3. Creates an HTTP-01 or DNS-01 challenge to prove domain ownership
4. Requests the certificate from the CA
5. Stores the issued certificate and private key in a Kubernetes Secret
6. Monitors the certificate's expiry and automatically renews it 30 days before expiration
7. Updates the Secret with the renewed certificate, triggering any pods that mount it to pick up the new cert

All of this operational logic — what a human admin would do manually — is encoded in the Operator. The user experience is:

```yaml
apiVersion: cert-manager.io/v1
kind: Certificate
metadata:
  name: api-tls
spec:
  secretName: api-tls-cert
  issuerRef:
    name: letsencrypt-prod
    kind: ClusterIssuer
  dnsNames:
    - api.mycompany.com
```

Apply this and walk away. Cert-Manager handles everything else, indefinitely. That is the Operator promise: operational tasks that were scripts become declarative API objects.

## The Gotchas

**Reconciler must be idempotent**: The reconcile function can be called multiple times for the same resource — on startup (to sync all existing resources), on any change to the resource, on any change to owned child resources, and on the requeue timer. If your reconciler isn't idempotent (creates a new child resource instead of checking if it already exists), you get duplicate objects piling up. Always use `CreateOrUpdate`, not `Create`.

**Status subresource and update conflicts**: Updating both `spec` and `status` in the same call causes conflicts because they are separate subresources. Update `status` using `r.Status().Update()`, not `r.Update()`. And use `ResourceVersion` for optimistic concurrency — if someone else updated the resource between your Get and your Update, retry the reconcile.

**CRD versioning and conversion**: Once you ship a CRD version and users have resources using it, you can't just remove fields or change field semantics. Kubernetes CRD versioning requires a `ConversionWebhook` to convert between old and new versions if you need to make breaking changes. Plan your schema carefully before v1.

**Leader election for the Operator itself**: If you run multiple replicas of your Operator for high availability, only one should be reconciling at a time (otherwise they race each other). Use the built-in leader election in controller-runtime:

```go
mgr, _ := ctrl.NewManager(ctrl.GetConfigOrDie(), ctrl.Options{
    LeaderElection:          true,
    LeaderElectionID:        "my-operator-leader",
    LeaderElectionNamespace: "kube-system",
})
```

## Key Takeaway

Operators are how you encode operational knowledge into Kubernetes. CRDs define the vocabulary; Operators implement the operational logic as a control loop. The reconcile loop — fetch desired state, compare to actual state, take action to close the gap — is the core pattern, and idempotency is the core requirement. The Operator pattern is appropriate for stateful workloads with non-trivial operational logic: databases, message brokers, certificate management, ML model serving. It's overkill for simple stateless services. When you see a Helm chart that runs a dozen post-install hooks to do setup and configuration, that's a workload that might benefit from an Operator.

---

**Previous:** [Lesson 3: ConfigMaps and Secrets — Configuration without rebuilding](/post/fundamentals/k8s-config-secrets/)
**Next:** [Lesson 5: Debugging in Kubernetes — kubectl tricks that save hours](/post/fundamentals/k8s-debugging/)

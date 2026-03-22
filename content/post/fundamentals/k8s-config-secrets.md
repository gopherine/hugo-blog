---
title: "Lesson 3: ConfigMaps and Secrets — Configuration without rebuilding"
author: Atharva Pandey
keywords:
  - Kubernetes
  - ConfigMaps
  - Secrets
  - configuration management
  - secret management
  - Vault
  - external secrets
tags:
  - fundamentals
  - kubernetes
  - devops
series: "Kubernetes Patterns"
lesson: 3
date: '2024-10-16T00:00:00.000Z'
---

We had a bug where the same Docker image was running in staging and production but behaving differently. I spent an hour diffing the code before I checked the environment variables. The staging pod had `CACHE_TTL=60` and the production pod had `CACHE_TTL=3600`. The image was identical. The behavior was totally different. That kind of environment-specific configuration — the values that shouldn't be baked into the image — is exactly what ConfigMaps and Secrets are for. But they have subtleties that bite you if you treat them as simple key-value stores.

## The Pattern

The principle is the Twelve-Factor App's "store config in the environment" factor applied to Kubernetes: separate configuration from code. The image is immutable and environment-agnostic. The configuration is environment-specific and managed separately from the image lifecycle.

ConfigMaps hold non-sensitive configuration: feature flags, environment-specific URLs, tuning parameters, config file content. Secrets hold sensitive configuration: passwords, API keys, TLS certificates, database connection strings. The API is nearly identical, but the security properties differ significantly (or should, with proper setup).

## How It Works

**ConfigMaps** can be consumed as environment variables, command-line arguments, or mounted as files:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: api-config
  namespace: production
data:
  APP_ENV: "production"
  LOG_LEVEL: "info"
  CACHE_TTL: "3600"
  # can also store entire config files
  app.yaml: |
    server:
      port: 8080
      timeout: 30s
    database:
      max_connections: 25
      idle_timeout: 5m
```

Consuming as environment variables — flat and simple:

```yaml
spec:
  containers:
    - name: api
      image: mycompany/api:v2.3.1
      envFrom:
        - configMapRef:
            name: api-config       # all keys become env vars
      env:
        - name: CACHE_TTL          # or reference individual keys
          valueFrom:
            configMapKeyRef:
              name: api-config
              key: CACHE_TTL
```

Consuming as a mounted file — useful for config files your app reads from disk:

```yaml
      volumeMounts:
        - name: config-volume
          mountPath: /etc/api/
  volumes:
    - name: config-volume
      configMap:
        name: api-config
        items:
          - key: app.yaml
            path: app.yaml        # mounts at /etc/api/app.yaml
```

**Secrets** have the same API shape but with base64-encoded values and stricter access control:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: api-secrets
  namespace: production
type: Opaque
data:
  DB_PASSWORD: cGFzc3dvcmQxMjM=   # base64("password123")
  API_KEY: c3VwZXJzZWNyZXQ=
```

Important: base64 is **encoding**, not encryption. Anyone who can `kubectl get secret api-secrets -o yaml` gets the values in plain text. Kubernetes stores secrets in etcd, and by default etcd stores them unencrypted. For real secret management, you need either **etcd encryption at rest** enabled on the cluster, or an external secret management system.

**External Secrets Operator** with HashiCorp Vault is the production pattern:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: api-secrets
spec:
  refreshInterval: 1h
  secretStoreRef:
    name: vault-backend
    kind: ClusterSecretStore
  target:
    name: api-secrets          # creates a regular Kubernetes Secret with this name
    creationPolicy: Owner
  data:
    - secretKey: DB_PASSWORD   # key in the K8s Secret
      remoteRef:
        key: secret/production/api    # path in Vault
        property: db_password         # field in Vault secret
    - secretKey: API_KEY
      remoteRef:
        key: secret/production/api
        property: api_key
```

The External Secrets Operator syncs values from Vault (or AWS Secrets Manager, GCP Secret Manager, Azure Key Vault) into Kubernetes Secrets on a schedule. Your pods consume a regular Kubernetes Secret, but the actual values live in and are managed by Vault. Rotation happens in Vault; the operator syncs the updated value to Kubernetes; on the next pod restart or config reload, pods get the new value.

## Production Example

One of the most valuable features for ConfigMaps is **live config updates** via volume mounts. When a ConfigMap is mounted as a volume (not consumed as environment variables), Kubernetes updates the mounted files automatically when the ConfigMap changes — with a propagation delay of roughly 60 seconds (governed by the `kubelet` sync period and ConfigMap cache TTL).

This means you can change a feature flag in a ConfigMap and have running pods pick it up without a restart, as long as your application watches the config file for changes:

```go
// Example: watching a config file for changes using fsnotify
import "github.com/fsnotify/fsnotify"

func watchConfig(path string, reload func()) {
    watcher, _ := fsnotify.NewWatcher()
    watcher.Add(path)
    go func() {
        for {
            select {
            case event := <-watcher.Events:
                if event.Op&fsnotify.Write == fsnotify.Write {
                    reload()
                }
            }
        }
    }()
}
```

This hot-reload pattern is how Prometheus picks up new scrape configs, how Nginx picks up new virtual host configs, and how many feature flag systems propagate changes. It's only possible because the config is external to the image.

For Secrets mounted as volumes, the same propagation mechanism applies — but beware: many applications read secrets at startup and cache them in memory. A Secret rotation in Kubernetes propagates to the mounted file, but the running process still has the old value in memory. You either need an application-level secret reload mechanism or a rolling restart after rotation.

## The Gotchas

**Environment variables are not updated live**: If you consume a ConfigMap via `envFrom` or `env.valueFrom`, the values are injected at pod startup. Changing the ConfigMap does nothing to running pods — you need a rolling restart. Only volume-mounted ConfigMaps get live updates. This surprises people who change a ConfigMap and expect running pods to pick up the change.

**Secret size limit**: Kubernetes Secrets have a 1 MiB size limit (this is the etcd object size limit). TLS certificates, JWKs, and large CA bundles can hit this. For large secrets, store them externally (Vault, S3) and reference them via ExternalSecret or init container pull.

**Namespace scoping**: ConfigMaps and Secrets are namespaced resources. A pod can only reference a ConfigMap or Secret in the same namespace. If you want to share configuration across namespaces, you need to either replicate the ConfigMap (using a tool like Reflector) or pull from an external source.

**RBAC for Secrets**: By default, any service account in the namespace can read any Secret in the namespace. Lock this down explicitly. Use RBAC to grant secret access to only the service accounts that need specific secrets:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: api-secrets-reader
rules:
  - apiGroups: [""]
    resources: ["secrets"]
    resourceNames: ["api-secrets"]   # only this specific secret
    verbs: ["get"]
```

## Key Takeaway

ConfigMaps and Secrets are the Kubernetes mechanism for separating configuration from code — the twelve-factor principle applied to container infrastructure. ConfigMaps are for non-sensitive configuration; Secrets are for sensitive values, but native Kubernetes Secrets are only base64-encoded, not encrypted by default. For real secret security, use etcd encryption at rest plus an external secret manager (Vault, AWS Secrets Manager) via External Secrets Operator. Use volume mounts when you want live updates without pod restarts; use environment variables when you prefer simplicity and can tolerate rolling restarts on config changes. Lock down Secret RBAC to the specific service accounts that need access to specific secrets.

---

**Previous:** [Lesson 2: Deployments and Scaling — Rolling updates, HPA, VPA](/post/fundamentals/k8s-deployments/)
**Next:** [Lesson 4: Operators and CRDs — Extending Kubernetes with your own resources](/post/fundamentals/k8s-operators/)

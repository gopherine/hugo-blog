---
title: "Lesson 2: Feature Stores — Why feature engineering is 80% of ML"
author: "Atharva Pandey"
series: "ML System Design"
lesson: 2
tags:
  - fundamentals
  - ML
  - system design
  - AI
keywords:
  - feature store design
  - training serving skew
  - Feast feature store
  - online offline feature store
  - ML feature engineering
date: "2024-08-02T00:00:00.000Z"
---

There is a saying in machine learning that is so universally acknowledged it has become a cliché: 80% of the work in any ML project is feature engineering. I spent a long time thinking this referred to the cognitive labor — the domain expertise required to craft meaningful features. It does, in part. But the deeper meaning is operational. The 80% is not just about *what* features to build; it's about *how* to compute them consistently, store them efficiently, retrieve them with sub-millisecond latency at serving time, and keep them synchronized between the training pipeline and the production system.

Feature stores are the infrastructure that manages all of this. Before I understood feature stores, I had the same bug in three separate ML projects: the model performed beautifully in offline evaluation and poorly in production. In every case, the root cause was training-serving skew — the features computed in training were subtly different from the features computed at serving time. A feature store is the structural solution to that problem.

## The ML Pipeline

Feature engineering spans the entire ML pipeline, touching three critical boundaries:

1. **Training time**: raw data → feature computation → stored training dataset
2. **Serving time**: raw request → feature lookup → model input → prediction
3. **Monitoring**: features in serving must match features in training; drift in either degrades the model

The feature store sits at the center of all three. It provides:
- A **registry** (what features exist, how they're defined, who owns them)
- An **offline store** (historical feature values for training and batch prediction)
- An **online store** (low-latency feature serving for real-time inference)
- A **transformation layer** (the logic to go from raw data to feature values, applied consistently everywhere)

## Architecture

```
                     Raw Data (events, DB snapshots)
                              ↓
                    Feature Computation Jobs
                     (Spark / Flink / dbt)
                       ↙             ↘
              Offline Store         Online Store
           (Parquet / S3 /       (Redis / DynamoDB /
              BigQuery)            Cassandra)
                  ↓                      ↓
           Training Jobs          Serving Layer
         (reads historical      (reads latest value
          feature snapshots)      in < 5ms)
```

The offline store is optimized for high-throughput batch reads. Training jobs read millions of (entity, timestamp) pairs and join them against historical feature values. The critical requirement here is **point-in-time correctness**: when training a model to predict whether a user will churn on day T, the training row must use only features that were available before day T. Using features that leaked information from after day T is called **temporal leakage**, and it produces models that perform better in offline evaluation than in production — a classic and costly mistake.

```go
// Point-in-time feature retrieval for training dataset construction
type HistoricalFeatureRequest struct {
    Entity     string     // e.g., "user_id"
    FeatureRefs []string  // e.g., ["user_stats.purchase_count_7d", "user_stats.session_count_30d"]
    Timestamps  []TrainingRow // each row has an entity value and a label timestamp
}

type TrainingRow struct {
    EntityValue string
    LabelTime   time.Time
}

// The offline store returns the feature value as it was at label_time,
// not the current value — this is point-in-time correctness
func (fs *FeatureStore) GetHistoricalFeatures(
    ctx context.Context,
    req HistoricalFeatureRequest,
) ([]FeatureVector, error) {
    // Implementation joins entity-timestamp pairs against
    // the time-partitioned feature history table
    return fs.offlineStore.PointInTimeJoin(ctx, req.Entity, req.FeatureRefs, req.Timestamps)
}
```

The online store is optimized for single-entity lookups at millisecond latency. When a user makes a request and the model needs their "purchase_count_7d" feature, the serving layer does a key-value lookup in Redis: `GET feature:user_stats:purchase_count_7d:{user_id}`. This must return in under 5ms for the overall inference latency budget to be met.

**The materialization pipeline**

Features don't automatically appear in the online store. A **materialization job** runs on a schedule (hourly, daily, or streaming in near-real-time) and copies the latest computed feature values from the offline store to the online store. The online store is essentially a low-latency cache of the offline store's most recent state.

```go
type MaterializationJob struct {
    FeatureView   string
    StartTime     time.Time
    EndTime       time.Time
    Entities      []string // nil means all entities
}

func (m *Materializer) Run(ctx context.Context, job MaterializationJob) error {
    // Read latest feature values from offline store
    rows, err := m.offlineStore.ReadLatest(ctx, job.FeatureView, job.StartTime, job.EndTime)
    if err != nil {
        return fmt.Errorf("reading from offline store: %w", err)
    }

    // Batch write to online store (Redis pipeline)
    pipe := m.onlineStore.Pipeline()
    for _, row := range rows {
        key := fmt.Sprintf("feature:%s:%s", job.FeatureView, row.EntityKey)
        pipe.Set(ctx, key, row.Value, featureTTL)
    }
    _, err = pipe.Exec(ctx)
    return err
}
```

## Production Challenges

**Training-serving skew**

This is the primary enemy. It occurs when the feature computation logic at serving time diverges from the computation logic used during training. Common causes:

- The training pipeline and serving code are maintained separately and drift apart over time
- The training pipeline uses a SQL query while the serving code reimplements the same logic in Python or Go (with subtle differences)
- The training pipeline uses the full historical dataset to compute statistics (like mean and stddev for normalization), but the serving code recomputes them on a rolling window

The solution: a single feature computation definition that is executed by both the training pipeline and the serving pipeline. In Feast (the open-source feature store), this is called a Feature View — a declarative definition of what the feature is and how it's computed, which the framework applies consistently in both contexts.

**Feature freshness vs. latency**

Some features need to be fresh to be useful. A fraud detection model cares about "number of transactions in the last 5 minutes" — a value that is stale after 5 minutes is useless. Other features are stable enough that daily materialization is fine: "total lifetime transactions" for a user changes slowly.

Feature freshness requirements drive the materialization cadence and the infrastructure choices:

- **Daily batch features**: computed by a nightly Spark job, materialized to the online store once per day. Simple and cheap.
- **Near-real-time features**: computed by a streaming job (Flink, Kafka Streams) that processes events as they arrive and writes directly to the online store. Complex, but necessary for features with sub-minute freshness requirements.
- **On-demand features**: computed in real time at serving time from the raw request inputs. No storage required, but adds latency to the inference path.

**Feature reuse and governance**

Without a feature store, teams rebuild the same features independently. Team A computes "user_purchase_count_30d" for their recommendation model. Team B computes the same metric for their churn prediction model. They differ subtly in how they handle returns, cancelled orders, and test accounts. Both models work differently in ways that are hard to debug.

A feature store's registry solves this by making features discoverable and shared. When Team B searches for purchase features, they find Team A's definition, use it, and both models agree on what "purchase_count_30d" means. This is the governance value of a feature store, separate from the technical architecture.

**The cold start problem for new entities**

When a new user signs up, there is no feature history. What does the model use for "purchase_count_30d"? Options:
- A global default value (mean across all users)
- A segment default based on registration attributes (new users from organic search vs. paid ads may have different behavioral priors)
- A separate "new user" model that uses only features available at signup

Feature stores need to handle null / missing feature values gracefully, and the handling must be consistent between training (where new users were presumably also handled with defaults) and serving.

## Interview Tips

Feature store questions appear in ML system design interviews when the role involves building or scaling production ML systems. The key concepts to communicate:

**Name the offline/online split explicitly.** Many candidates describe a database and leave it there. Articulate why you need two stores: the offline store for high-throughput historical reads during training, the online store for low-latency single-entity reads during serving.

**Explain point-in-time correctness.** This is the differentiating concept. Candidates who understand it signal real production experience. Say "temporal leakage" and explain why point-in-time joins are required for training data construction.

**Describe the materialization pipeline.** How do features get from the offline store to the online store? What cadence? What happens if materialization is delayed — how stale are the features serving production requests?

**Talk about training-serving skew as the root motivation.** Don't describe a feature store as "a database for features." Describe it as the system that ensures training and serving see identical feature values, which is the actual problem it solves.

## Key Takeaway

A feature store is the infrastructure answer to a fundamental ML system problem: how do you ensure that the features your model was trained on are the same features it sees at inference time, now and six months from now, regardless of who wrote the training code and who wrote the serving code? The offline/online split exists because training and serving have opposing performance requirements — high throughput vs. low latency. Point-in-time correctness is non-negotiable for training data that avoids temporal leakage. And the materialization pipeline is the bridge that keeps both stores synchronized. Feature engineering being "80% of ML" is as much about this operational infrastructure as it is about domain knowledge.

---

**Previous:** [Lesson 1: ML Pipelines](/post/fundamentals/ml-pipelines/) | **Up next:** [Lesson 3: Model Serving — Latency, batching, A/B testing in production](/post/fundamentals/ml-model-serving/)

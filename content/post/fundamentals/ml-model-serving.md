---
title: "Lesson 3: Model Serving — Latency, batching, A/B testing in production"
author: "Atharva Pandey"
series: "ML System Design"
lesson: 3
tags:
  - fundamentals
  - ML
  - system design
  - AI
keywords:
  - model serving
  - ML inference
  - A/B testing ML
  - dynamic batching
  - model deployment production
date: "2024-10-14T00:00:00.000Z"
---

Training a model is satisfying. Deploying it to serve real traffic is humbling. I remember the first time I pushed a model to production and watched the p99 latency hover at 800ms on what was supposed to be a "fast" model. The benchmark had shown 12ms inference time. What happened? The benchmark ran the model on pre-loaded batches; production served one request at a time, loaded the model fresh on cold starts, and had no GPU batching. The gap between "the model is accurate" and "the model is fast enough to be useful" is where model serving engineering lives.

This lesson covers the architecture of a production model serving system: how requests flow in, how inference is optimized, how you safely roll out new model versions, and how A/B testing works in the presence of ML models where the "change" you're testing is a continuous function, not a binary feature flag.

## The ML Pipeline

Model serving is the final stage of the ML pipeline — the point where the model's learned function is applied to live data. But it's also the beginning of the feedback loop: predictions lead to user actions, user actions generate new training data, and the training pipeline uses that data to improve the model. The serving layer must support this feedback loop by logging prediction inputs, predictions, and (eventually) outcomes.

```
Incoming Request
      ↓
Feature Retrieval (from Feature Store online layer)
      ↓
Preprocessing (normalization, tokenization, etc.)
      ↓
Model Inference (GPU / CPU)
      ↓
Postprocessing (thresholds, calibration, formatting)
      ↓
Response + Prediction Log
```

## Architecture

**Model servers**

A model server is a service that exposes inference over HTTP/gRPC and manages model loading, versioning, and hardware resources. The two most common production choices are TensorFlow Serving and Triton Inference Server (NVIDIA). Both support:

- Multiple concurrent model versions (for A/B testing and staged rollouts)
- Dynamic batching: aggregating multiple requests into a single batch inference call
- Hardware resource management: pinning models to specific GPUs, managing memory

For custom serving infrastructure (common in organizations that want more control), the pattern is a Go or Python HTTP service that loads the model into memory at startup and handles inference requests:

```go
type ModelServer struct {
    model     InferenceModel
    featureStore FeatureStoreClient
    predLog   PredictionLogger
}

func (s *ModelServer) Predict(ctx context.Context, req PredictRequest) (*PredictResponse, error) {
    // Step 1: fetch features
    features, err := s.featureStore.Get(ctx, req.EntityID, modelFeatureRefs)
    if err != nil {
        return nil, fmt.Errorf("feature retrieval: %w", err)
    }

    // Step 2: preprocess
    input, err := preprocess(features, req.ContextFeatures)
    if err != nil {
        return nil, fmt.Errorf("preprocessing: %w", err)
    }

    // Step 3: inference
    output, err := s.model.Infer(ctx, input)
    if err != nil {
        return nil, fmt.Errorf("inference: %w", err)
    }

    // Step 4: postprocess
    score := sigmoid(output.RawScore)
    label := classifyByThreshold(score, modelThreshold)

    // Step 5: log prediction for training feedback loop
    _ = s.predLog.Log(ctx, PredictionRecord{
        RequestID: req.RequestID,
        EntityID:  req.EntityID,
        Features:  features,
        Score:     score,
        Label:     label,
        ModelVersion: s.model.Version(),
        ServedAt:  time.Now(),
    })

    return &PredictResponse{Score: score, Label: label}, nil
}
```

**Dynamic batching**

GPUs achieve peak throughput when computing on large batches — the matrix multiplications at the heart of neural networks are embarrassingly parallelizable across batch dimensions. Serving requests one at a time wastes GPU capacity. Dynamic batching solves this: the server holds incoming requests in a buffer for a short window (say, 5ms), then groups them into a single batch inference call.

The tradeoff: batching reduces GPU cost but adds latency (every request waits up to the batch window). For latency-sensitive applications (sub-50ms requirement), batch windows must be tiny (1–5ms). For throughput-optimized applications (offline scoring pipelines), batch windows can be larger.

```go
type BatchingServer struct {
    model      InferenceModel
    batchSize  int
    windowMs   int
    reqCh      chan batchedRequest
}

type batchedRequest struct {
    input  []float32
    respCh chan batchedResponse
}

func (b *BatchingServer) processBatches(ctx context.Context) {
    ticker := time.NewTicker(time.Duration(b.windowMs) * time.Millisecond)
    defer ticker.Stop()

    var pending []batchedRequest
    for {
        select {
        case req := <-b.reqCh:
            pending = append(pending, req)
            if len(pending) >= b.batchSize {
                b.flush(ctx, pending)
                pending = pending[:0]
            }
        case <-ticker.C:
            if len(pending) > 0 {
                b.flush(ctx, pending)
                pending = pending[:0]
            }
        case <-ctx.Done():
            return
        }
    }
}

func (b *BatchingServer) flush(ctx context.Context, batch []batchedRequest) {
    inputs := make([][]float32, len(batch))
    for i, r := range batch {
        inputs[i] = r.input
    }
    outputs, err := b.model.BatchInfer(ctx, inputs)
    for i, r := range batch {
        r.respCh <- batchedResponse{output: outputs[i], err: err}
    }
}
```

**Model caching and cold starts**

Loading a large model from disk into memory takes time — from hundreds of milliseconds to several seconds for multi-gigabyte models. In serverless or auto-scaling environments, new instances spin up to handle traffic spikes and face a cold start penalty. Mitigations:
- Keep warm instances running even at low traffic (costly but effective)
- Pre-load models at container startup, before the instance is added to the load balancer rotation
- Use model weights stored in fast local NVMe storage (or memory-mapped files) rather than object storage

## Production Challenges

**Latency budget decomposition**

"The model must respond in under 100ms" is a budget that must be allocated across multiple stages. A realistic budget for a real-time recommendation system:

- Network (request in): 5ms
- Feature retrieval (Redis): 5ms
- Preprocessing: 3ms
- Inference (GPU): 20ms
- Postprocessing: 2ms
- Network (response out): 5ms
- **Total: 40ms — within budget with 60ms headroom for variance**

If the inference alone takes 80ms, the SLA is dead before you start. Profiling and allocating the latency budget before choosing infrastructure is necessary, not optional.

**Model versioning and rollout**

You don't push a new model to 100% of traffic immediately. The standard rollout sequence:

1. **Shadow mode**: run the new model in parallel with the production model, log its predictions, but serve only the production model's results. Validate that the new model's predictions are reasonable.
2. **Canary**: route 1–5% of traffic to the new model. Monitor error rates, latency, and any downstream business metrics that respond quickly (click-through rate, add-to-cart rate).
3. **A/B test**: gradually increase the new model's traffic share, with statistical significance gates before each increment.
4. **Full rollout**: once confidence is established, route 100% of traffic to the new model.

Shadow mode is particularly valuable for catching cases where the new model behaves very differently on production traffic compared to the evaluation dataset — a sign of dataset drift or a subtle bug.

**A/B testing ML models**

A/B testing a model is more complex than A/B testing a UI change. A UI change is binary: user sees old button or new button. A model change is continuous: every user gets a different score from the old model vs. the new model, and the downstream effect (did they convert? did they churn?) has high variance and takes time to observe.

Key considerations for ML A/B tests:

- **Assignment consistency**: a user assigned to the "new model" bucket should stay there for the duration of the experiment. If they see different models across sessions, their behavior is contaminated by both models, making attribution impossible.
- **Metric sensitivity**: define the primary metric before the experiment starts. Common trap: running the experiment for two days, not seeing significance, extending it, and eventually finding a metric that's significant. This is p-hacking.
- **Network effects**: if your model affects user-generated content (e.g., a content ranking model affects what posts users see, which affects engagement, which creates new content), treatment and control groups are not independent. The standard IID assumption for A/B tests is violated.

```go
// Deterministic experiment assignment using a hash
func assignExperiment(userID string, experimentID string, buckets []Bucket) Bucket {
    // Deterministic hash ensures the same user always gets the same bucket
    h := fnv.New32a()
    _, _ = h.Write([]byte(userID + ":" + experimentID))
    hash := h.Sum32()

    // Weighted bucket selection
    totalWeight := 0
    for _, b := range buckets {
        totalWeight += b.Weight
    }
    slot := int(hash % uint32(totalWeight))
    cumulative := 0
    for _, b := range buckets {
        cumulative += b.Weight
        if slot < cumulative {
            return b
        }
    }
    return buckets[len(buckets)-1]
}
```

**Monitoring: data drift and concept drift**

After deployment, two types of drift can degrade a model:

- **Data drift**: the distribution of input features shifts. If a recommendation model was trained when 60% of users were on mobile and the split becomes 80% mobile, features correlated with device type will be misaligned.
- **Concept drift**: the relationship between features and the target label changes. A fraud model trained on historical fraud patterns may miss a new fraud scheme that looks nothing like historical data.

Both require monitoring the feature distribution over time (compared to the training distribution) and monitoring prediction score distributions (a shift in the distribution of scores is often the first signal that something has changed). Model performance metrics (precision, recall) require labeled data, which has a feedback delay — you don't know if a prediction was right until the outcome is observed, which may be days or weeks later.

## Interview Tips

Model serving questions usually come up in the context of "design a real-time recommendation system" or "design a fraud detection system." The serving layer is one component among several. Tips for handling it well:

**Lead with the latency budget.** State your total latency budget and allocate it across feature retrieval, inference, and network. This immediately demonstrates that you think about serving as an engineering problem with constraints, not just "deploy the model."

**Explain dynamic batching.** It's not obvious to non-ML candidates why you'd deliberately batch requests and add latency. Explaining GPU utilization and the throughput/latency tradeoff shows depth.

**Describe shadow mode for model rollout.** Most candidates say "use a canary deployment." Shadow mode is the step before the canary — safer, and it shows you understand the unique risk profile of ML models (behavioral changes are subtle and not immediately visible in error rates).

**Distinguish data drift from concept drift.** Both cause model degradation, but they require different responses — data drift might be addressed by feature normalization adjustments, while concept drift requires retraining on recent data.

## Key Takeaway

Model serving is where ML meets distributed systems at their most demanding intersection. The inference engine must deliver sub-100ms responses while retrieving features, computing predictions, and logging everything for the feedback loop. Dynamic batching squeezes GPU efficiency without violating SLAs. Rollout strategies — shadow mode, canary, A/B test — acknowledge that a new model version is a code change with statistical uncertainty, not just a binary right/wrong. And monitoring for data and concept drift is what keeps a deployed model from decaying silently over the months after it ships. The model being accurate is the beginning, not the end.

---

**Previous:** [Lesson 2: Feature Stores](/post/fundamentals/ml-feature-stores/) | **Up next:** [Lesson 4: Recommendation Systems — Collaborative filtering, embeddings, and the cold start problem](/post/fundamentals/ml-recommendations/)

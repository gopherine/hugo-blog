---
title: "Lesson 1: ML Pipelines — From raw data to deployed model"
author: "Atharva Pandey"
series: "ML System Design"
lesson: 1
tags:
  - fundamentals
  - ML
  - system design
  - AI
keywords:
  - ML pipeline design
  - data ingestion
  - model training pipeline
  - MLOps
  - feature engineering pipeline
date: "2024-06-02T00:00:00.000Z"
---

The first time I built a "production ML pipeline," it was a cron job that ran a Python script, trained a model, and saved the artifact to disk. It worked, until it didn't. The model silently degraded over three weeks because the training data schema changed and nobody noticed. There were no tests, no validation, no monitoring. That experience, embarrassing as it was, taught me more about ML system design than any paper on model architecture.

An ML pipeline is not a model. The model is perhaps 5% of the work. The remaining 95% is the infrastructure that makes the model trainable, reproducible, monitorable, and deployable — consistently. This lesson walks through what a production ML pipeline actually looks like, end to end.

## The ML Pipeline

A production ML pipeline has six stages that must function as a cohesive system, not six separate scripts.

```
Raw Data Sources
      ↓
Data Ingestion & Validation
      ↓
Feature Engineering
      ↓
Model Training
      ↓
Model Evaluation & Validation
      ↓
Model Serving (Deployment)
      ↓
Monitoring & Retraining Trigger
```

Each stage has its own failure modes, and a failure at any stage can corrupt all downstream stages silently. Let's walk through each.

**Stage 1: Data Ingestion and Validation**

Raw data arrives from multiple sources: event streams (user clicks, transactions), database snapshots, third-party APIs, manually labeled datasets. The ingestion layer must:

- Pull or receive this data on a schedule or trigger
- Validate schema: are all expected columns present? Are data types correct?
- Validate distributions: are feature distributions within expected bounds? Has the click-through rate dropped from 3% to 0.01%? (This is a signal the data pipeline upstream broke, not that users stopped clicking.)
- Log data quality metrics and fail loudly when thresholds are violated

```go
type DataValidationResult struct {
    TableName       string
    RowCount        int64
    SchemaErrors    []string
    StatsViolations []StatViolation
    PassedAt        time.Time
}

type StatViolation struct {
    Column    string
    Metric    string // "mean", "null_fraction", "unique_count"
    Expected  float64
    Actual    float64
    Threshold float64
}

func validateDataset(ds Dataset, schema Schema, stats BaselineStats) DataValidationResult {
    result := DataValidationResult{TableName: ds.Name}

    // Schema check
    for _, col := range schema.RequiredColumns {
        if !ds.HasColumn(col) {
            result.SchemaErrors = append(result.SchemaErrors,
                fmt.Sprintf("missing required column: %s", col))
        }
    }

    // Distribution check
    for col, baseline := range stats.Columns {
        actual := ds.ColumnStats(col)
        if math.Abs(actual.NullFraction-baseline.NullFraction) > 0.05 {
            result.StatsViolations = append(result.StatsViolations, StatViolation{
                Column:    col,
                Metric:    "null_fraction",
                Expected:  baseline.NullFraction,
                Actual:    actual.NullFraction,
                Threshold: 0.05,
            })
        }
    }
    return result
}
```

**Stage 2: Feature Engineering**

Raw data is almost never in the right form for a model. Feature engineering transforms it into a numerical representation a model can learn from. Common transformations: normalization, log transforms, one-hot encoding, date/time feature extraction (day of week, hour of day), text tokenization, embedding lookups.

The critical operational concern: the same transformations applied at training time must be applied identically at serving time. A mismatch between training and serving feature computation is one of the most common production ML bugs, and one of the hardest to catch because the model will still make predictions — they'll just be wrong in subtle ways.

Feature stores (the subject of Lesson 2) exist specifically to solve this training-serving skew problem.

**Stage 3: Model Training**

Training is the most familiar part. But at scale, several operational concerns arise:

- **Reproducibility**: given the same training data and hyperparameters, you should get the same model. This requires fixing random seeds, pinning library versions, and logging every input to the training run.
- **Experiment tracking**: log hyperparameters, training metrics, validation metrics, and the resulting model artifact for every run. Tools like MLflow or Weights & Biases serve this purpose. Without experiment tracking, you're flying blind when trying to reproduce a good result from two months ago.
- **Distributed training**: for large models or large datasets, training needs to run across multiple machines. Data parallelism (splitting the dataset across workers, each with a full model copy) is the most common approach. Each worker computes gradients on its shard; gradients are aggregated via AllReduce or a parameter server, and the model is updated.

**Stage 4: Model Evaluation and Validation**

A model that trains to 95% accuracy on training data and 60% on validation data is overfit and shouldn't be deployed. Evaluation gates are automated checks that must pass before a model artifact advances to deployment:

- Validation set metrics must exceed a minimum threshold
- Performance on known-difficult slices (rare classes, edge cases) must exceed per-slice thresholds
- Model size and inference latency must be within budget
- No regression versus the current production model on a holdout set

Treating model evaluation as an automated gate — not a human eyeball check — is essential for a pipeline that deploys more than once a month.

**Stage 5: Serving**

The model artifact (weights plus inference code) is deployed to a serving layer. This is covered in depth in Lesson 3. The key constraint linking training to serving: the inference code at serving time must apply features identically to how the training pipeline applied them. If training normalized a column by subtracting the mean of the training set, serving must subtract that same training-set mean — not recompute it on live data.

## Architecture

A production ML pipeline is typically implemented as a **DAG (Directed Acyclic Graph)** of tasks, orchestrated by a workflow engine. The workflow engine handles scheduling, dependency management, retry logic, and failure alerting.

```
[Ingest Raw Data] → [Validate Schema] → [Compute Features] → [Split Train/Val/Test]
                                                                       ↓
                                                              [Train Model] → [Evaluate]
                                                                                  ↓
                                                                        [Pass Gates?]
                                                                         ↙        ↘
                                                                 [Deploy]       [Alert & Halt]
```

Popular orchestration tools: Apache Airflow (the original, still widely used), Prefect (more Pythonic API), Kubeflow Pipelines (Kubernetes-native), Metaflow (Netflix, excellent for data science teams). For simpler pipelines, even a well-structured set of Go or Python scripts with a CI/CD trigger suffices.

The key architectural principle: **each stage is idempotent**. Running a stage twice with the same inputs produces the same output. This allows safe retries on failure without corrupting downstream stages.

```go
// Example: a training pipeline stage expressed as a Go struct
type PipelineStage struct {
    Name     string
    InputKey string  // content-addressed storage key for inputs
    Fn       func(ctx context.Context, inputs Artifacts) (Artifacts, error)
}

// Idempotency via content-addressable artifact keys
func (p *Pipeline) Run(ctx context.Context) error {
    for _, stage := range p.Stages {
        outputKey := hashInputs(stage.InputKey, stage.Name)
        if p.artifactStore.Exists(outputKey) {
            p.logger.Infof("stage %s already complete, skipping", stage.Name)
            continue
        }
        inputs, err := p.artifactStore.Load(stage.InputKey)
        if err != nil {
            return fmt.Errorf("loading inputs for %s: %w", stage.Name, err)
        }
        outputs, err := stage.Fn(ctx, inputs)
        if err != nil {
            return fmt.Errorf("running stage %s: %w", stage.Name, err)
        }
        if err := p.artifactStore.Save(outputKey, outputs); err != nil {
            return fmt.Errorf("saving outputs for %s: %w", stage.Name, err)
        }
    }
    return nil
}
```

## Production Challenges

**Data freshness vs. training cost**

Training more frequently keeps the model current but is expensive. Training less frequently is cheap but risks staleness (data drift degrades model quality). The right cadence depends on the problem: a fraud detection model might need hourly retraining because fraud patterns change within hours; a product recommendation model might be fine with weekly retraining.

**Backfills and historical data**

When you add a new feature, you often need to backfill historical training data with that feature's values. If the feature didn't exist historically, you need to either compute a reasonable proxy or accept that early training data won't have it. Documenting what features are available from what date is essential operational hygiene.

**Pipeline monitoring**

The pipeline itself needs monitoring, independent of the model's serving-time performance. How long does each stage take? Has the training data volume dropped unexpectedly? Is the model evaluation job failing silently? Pipeline health dashboards are not glamorous, but they're what prevent silent degradation from going undetected for weeks.

## Interview Tips

ML pipeline questions in system design interviews are less about model architecture and more about operational engineering. Interviewers want to see:

**You understand training-serving skew.** Explicitly state that the feature computation at serving time must use the exact same logic and statistics (means, vocabulary, etc.) as at training time. Propose a feature store or a shared feature computation library as the solution.

**You think about data validation.** Most candidates go straight from "get data" to "train model." Insert a validation step. Mention that distribution shifts in input data are as important to detect as model performance degradation.

**You know what an orchestration DAG does.** Don't describe a pipeline as "a script that runs sequentially." Describe it as a DAG of tasks with explicit dependencies, retries, and alerting.

**You treat model deployment as a gate, not a merge.** Automated evaluation gates before deployment are industry standard. Describe what metrics gate deployment, not just that you "evaluate the model."

## Key Takeaway

An ML pipeline is infrastructure for decision-making under uncertainty about data and model quality. The model is the small moving part in the middle; the valuable engineering is everything around it — the data validation that catches silent corruption, the experiment tracking that makes results reproducible, the evaluation gates that prevent bad models from reaching production, and the monitoring that detects drift before business metrics suffer. Every ML engineer who has maintained a production pipeline for more than six months has a story about the day the pipeline broke silently. Building the pipeline so that it fails loudly is the primary design goal.

---

**Up next:** [Lesson 2: Feature Stores — Why feature engineering is 80% of ML](/post/fundamentals/ml-feature-stores/)

---
title: "Lesson 8: ML Data Pipelines — polars and processing at speed"
author: Atharva Pandey
series: "Rust and AI/LLM Integration"
lesson: 8
keywords: [Rust, rust, AI, LLM, polars, data pipeline, ETL, DataFrame, preprocessing, ML]
tags: [Rust tutorial, rust, ai, llm]
date: '2025-08-30T14:15:00.000Z'
---

Last quarter I inherited a Python data pipeline that prepared training data for our recommendation model. It processed 50 million rows. Took 3 hours. Used 64GB of RAM. Everyone accepted this as normal — "big data is slow." I rewrote it in Rust with polars. Same data. 4 minutes. 6GB of RAM. My teammates thought I was lying until they ran it themselves.

Polars isn't just "pandas but faster." It's a fundamentally different approach to DataFrame operations — lazy evaluation, query optimization, true parallelism, and a Rust-native API that makes data pipeline code genuinely pleasant to write.

## Why polars Over pandas?

I'm not dunking on pandas here — I've written thousands of lines of pandas code and it's fine for exploration. But for production pipelines:

- **Memory**: pandas copies data constantly. `df["col"] = df["col"].apply(...)` creates a full copy. Polars operates on Arrow-backed columnar memory with zero-copy operations where possible.
- **Speed**: Polars auto-parallelizes operations across all CPU cores. pandas is single-threaded unless you bolt on Dask or Modin.
- **Correctness**: Polars' lazy API catches type errors before execution. You don't find out your column doesn't exist halfway through a 3-hour job.
- **Streaming**: Polars can process datasets larger than RAM using its streaming engine. pandas just crashes with `MemoryError`.

## Getting Started

```toml
[package]
name = "ml-pipeline"
version = "0.1.0"
edition = "2021"

[dependencies]
polars = { version = "0.44", features = ["lazy", "csv", "parquet", "json", "strings", "temporal", "dtype-struct", "streaming"] }
```

The feature flags matter. Polars is modular — you only compile what you use. For ML pipelines, you'll typically want all of these.

## Loading Data

Polars reads CSV, Parquet, JSON, and IPC (Arrow) formats. Parquet is almost always the right choice for ML data:

```rust
use polars::prelude::*;

fn load_data() -> Result<LazyFrame, PolarsError> {
    // CSV — scan without loading into memory
    let csv_lf = LazyCsvReader::new("data/raw/users.csv")
        .with_has_header(true)
        .with_infer_schema_length(Some(1000))
        .finish()?;

    // Parquet — columnar, compressed, fast
    let parquet_lf = LazyFrame::scan_parquet(
        "data/raw/events.parquet",
        ScanArgsParquet::default(),
    )?;

    // Multiple Parquet files (common with partitioned data)
    let multi_lf = LazyFrame::scan_parquet(
        "data/raw/events/*.parquet",
        ScanArgsParquet::default(),
    )?;

    Ok(parquet_lf)
}
```

Notice `LazyFrame` — not `DataFrame`. This is crucial. With lazy evaluation, polars builds a query plan instead of immediately executing operations. The optimizer then fuses operations, pushes predicates down, and eliminates unnecessary work. You write readable code and get optimized execution automatically.

## A Real ML Pipeline

Let's build a complete pipeline for preparing recommendation model features. We'll take raw user events and produce training-ready feature vectors:

```rust
use polars::prelude::*;

pub fn build_features() -> Result<DataFrame, PolarsError> {
    // Load raw events
    let events = LazyCsvReader::new("data/events.csv")
        .with_has_header(true)
        .finish()?;

    // Load product catalog
    let products = LazyCsvReader::new("data/products.csv")
        .with_has_header(true)
        .finish()?;

    // Step 1: Clean and filter events
    let clean_events = events
        .filter(col("event_type").is_in(lit(Series::new(
            PlSmallStr::from_static("types"),
            &["view", "click", "purchase", "add_to_cart"],
        ))))
        .filter(col("timestamp").gt(lit("2024-01-01")))
        .with_column(
            col("timestamp")
                .str()
                .to_datetime(
                    Some(TimeUnit::Milliseconds),
                    None,
                    StrptimeOptions::default(),
                    lit("raise"),
                )
                .alias("event_time"),
        );

    // Step 2: User-level aggregations
    let user_features = clean_events
        .clone()
        .group_by([col("user_id")])
        .agg([
            col("event_type").count().alias("total_events"),
            col("event_type")
                .filter(col("event_type").eq(lit("purchase")))
                .count()
                .alias("purchase_count"),
            col("event_type")
                .filter(col("event_type").eq(lit("view")))
                .count()
                .alias("view_count"),
            col("product_id").n_unique().alias("unique_products"),
            col("event_time").max().alias("last_active"),
            col("event_time").min().alias("first_active"),
        ])
        .with_columns([
            // Conversion rate
            (col("purchase_count").cast(DataType::Float64)
                / col("view_count").cast(DataType::Float64))
            .alias("conversion_rate"),
            // Days active
            ((col("last_active") - col("first_active"))
                .dt()
                .total_days())
            .alias("days_active"),
        ]);

    // Step 3: Product interaction features
    let product_features = clean_events
        .clone()
        .group_by([col("user_id"), col("product_id")])
        .agg([
            col("event_type").count().alias("interaction_count"),
            col("event_type")
                .filter(col("event_type").eq(lit("purchase")))
                .count()
                .alias("product_purchases"),
        ]);

    // Step 4: Join with product metadata
    let enriched = product_features.join(
        products,
        [col("product_id")],
        [col("product_id")],
        JoinArgs::new(JoinType::Left),
    );

    // Step 5: Category-level features per user
    let category_features = enriched
        .group_by([col("user_id")])
        .agg([
            col("category").n_unique().alias("categories_browsed"),
            col("price")
                .mean()
                .alias("avg_price_interacted"),
            col("price")
                .filter(col("product_purchases").gt(lit(0)))
                .mean()
                .alias("avg_price_purchased"),
        ]);

    // Step 6: Final feature table
    let final_features = user_features
        .join(
            category_features,
            [col("user_id")],
            [col("user_id")],
            JoinArgs::new(JoinType::Left),
        )
        .with_columns([
            // Fill nulls with sensible defaults
            col("conversion_rate").fill_null(lit(0.0)),
            col("avg_price_interacted").fill_null(lit(0.0)),
            col("avg_price_purchased").fill_null(lit(0.0)),
            col("categories_browsed").fill_null(lit(0)),
        ])
        .select([
            col("user_id"),
            col("total_events"),
            col("purchase_count"),
            col("view_count"),
            col("unique_products"),
            col("conversion_rate"),
            col("days_active"),
            col("categories_browsed"),
            col("avg_price_interacted"),
            col("avg_price_purchased"),
        ]);

    // Execute the lazy query and collect results
    final_features.collect()
}
```

That entire pipeline — filtering, aggregating, joining, feature engineering — is defined as a lazy computation graph. When we call `.collect()`, polars optimizes the entire graph at once. It might reorder joins, push filters into the scan, or parallelize independent aggregations. We wrote clear, step-by-step code; polars figures out the fastest execution strategy.

## Text Preprocessing for NLP

ML pipelines for language models need text preprocessing. Here's a pipeline that cleans and tokenizes text data:

```rust
pub fn preprocess_text_data() -> Result<DataFrame, PolarsError> {
    let raw = LazyCsvReader::new("data/reviews.csv")
        .with_has_header(true)
        .finish()?;

    let processed = raw
        // Basic text cleaning
        .with_columns([
            col("review_text")
                .str()
                .to_lowercase()
                .str()
                .replace_all(lit(r#"<[^>]+>"#), lit(""), true)  // Strip HTML
                .str()
                .replace_all(lit(r#"[^\w\s]"#), lit(" "), true) // Remove punctuation
                .str()
                .replace_all(lit(r#"\s+"#), lit(" "), true)     // Normalize whitespace
                .str()
                .strip_chars(lit(" "))
                .alias("clean_text"),
        ])
        // Calculate text statistics
        .with_columns([
            col("clean_text")
                .str()
                .count_matches(lit(r"\S+"), true)
                .alias("word_count"),
            col("clean_text")
                .str()
                .len_chars()
                .alias("char_count"),
        ])
        // Filter out very short or very long reviews
        .filter(
            col("word_count")
                .gt(lit(5))
                .and(col("word_count").lt(lit(1000))),
        )
        // Create label from rating
        .with_column(
            when(col("rating").gt_eq(lit(4)))
                .then(lit("positive"))
                .when(col("rating").lt_eq(lit(2)))
                .then(lit("negative"))
                .otherwise(lit("neutral"))
                .alias("sentiment_label"),
        );

    processed.collect()
}
```

## Train/Test Splitting

Every ML pipeline needs to split data properly:

```rust
use rand::seq::SliceRandom;
use rand::SeedableRng;

pub fn train_test_split(
    df: &DataFrame,
    test_ratio: f64,
    seed: u64,
) -> Result<(DataFrame, DataFrame), PolarsError> {
    let n = df.height();
    let test_size = (n as f64 * test_ratio).round() as usize;

    let mut indices: Vec<usize> = (0..n).collect();
    let mut rng = rand::rngs::StdRng::seed_from_u64(seed);
    indices.shuffle(&mut rng);

    let test_indices = &indices[..test_size];
    let train_indices = &indices[test_size..];

    let train_idx = IdxCa::new(
        PlSmallStr::from_static("idx"),
        train_indices.iter().map(|&i| i as u32).collect::<Vec<_>>(),
    );
    let test_idx = IdxCa::new(
        PlSmallStr::from_static("idx"),
        test_indices.iter().map(|&i| i as u32).collect::<Vec<_>>(),
    );

    let train = df.take(&train_idx)?;
    let test = df.take(&test_idx)?;

    Ok((train, test))
}

/// Stratified split — maintains label distribution
pub fn stratified_split(
    df: &DataFrame,
    label_col: &str,
    test_ratio: f64,
    seed: u64,
) -> Result<(DataFrame, DataFrame), PolarsError> {
    let labels = df.column(label_col)?;
    let unique_labels = labels.unique()?;

    let mut train_frames = Vec::new();
    let mut test_frames = Vec::new();

    for i in 0..unique_labels.len() {
        let label = unique_labels.get(i)?;
        let mask = labels.equal(&label)?;
        let subset = df.filter(&mask)?;

        let (train, test) = train_test_split(&subset, test_ratio, seed + i as u64)?;
        train_frames.push(train);
        test_frames.push(test);
    }

    // Concatenate all label subsets
    let refs: Vec<&DataFrame> = train_frames.iter().collect();
    let train = polars::functions::concat_df_horizontal(&refs)?;

    let refs: Vec<&DataFrame> = test_frames.iter().collect();
    let test = polars::functions::concat_df_horizontal(&refs)?;

    Ok((train, test))
}
```

## Feature Normalization

Models care about feature scales. Here's z-score normalization and min-max scaling:

```rust
pub fn normalize_features(
    df: &DataFrame,
    feature_cols: &[&str],
    method: NormMethod,
) -> Result<(DataFrame, NormParams), PolarsError> {
    let mut result = df.clone();
    let mut params = NormParams::new();

    for &col_name in feature_cols {
        let series = df.column(col_name)?;
        let values = series.f64()?;

        match method {
            NormMethod::ZScore => {
                let mean = values.mean().unwrap_or(0.0);
                let std = values.std(1).unwrap_or(1.0);
                let std = if std == 0.0 { 1.0 } else { std };

                let normalized = ((series.cast(&DataType::Float64)? - mean) / std)?;
                result.replace(col_name, normalized)?;
                params.insert(col_name.to_string(), NormParam::ZScore { mean, std });
            }
            NormMethod::MinMax => {
                let min = values.min().unwrap_or(0.0);
                let max = values.max().unwrap_or(1.0);
                let range = max - min;
                let range = if range == 0.0 { 1.0 } else { range };

                let normalized = ((series.cast(&DataType::Float64)? - min) / range)?;
                result.replace(col_name, normalized)?;
                params.insert(col_name.to_string(), NormParam::MinMax { min, max });
            }
        }
    }

    Ok((result, params))
}

pub enum NormMethod {
    ZScore,
    MinMax,
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub enum NormParam {
    ZScore { mean: f64, std: f64 },
    MinMax { min: f64, max: f64 },
}

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct NormParams(std::collections::HashMap<String, NormParam>);

impl NormParams {
    fn new() -> Self {
        Self(std::collections::HashMap::new())
    }

    fn insert(&mut self, key: String, value: NormParam) {
        self.0.insert(key, value);
    }

    /// Save params so we can apply the same normalization at inference time
    pub fn save(&self, path: &std::path::Path) -> Result<(), std::io::Error> {
        let json = serde_json::to_string_pretty(self)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))?;
        std::fs::write(path, json)
    }

    pub fn load(path: &std::path::Path) -> Result<Self, std::io::Error> {
        let json = std::fs::read_to_string(path)?;
        serde_json::from_str(&json)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::Other, e))
    }
}
```

Saving normalization parameters is something a surprising number of pipelines get wrong. If you normalize training data but then use different statistics at inference time, your model's predictions are garbage. Always serialize these params alongside the model.

## Streaming for Large Datasets

When data doesn't fit in RAM, polars' streaming engine processes it in chunks:

```rust
pub fn process_large_dataset() -> Result<(), PolarsError> {
    let lf = LazyFrame::scan_parquet(
        "data/huge_dataset/*.parquet",
        ScanArgsParquet::default(),
    )?;

    // This entire pipeline executes in streaming mode
    let result = lf
        .filter(col("valid").eq(lit(true)))
        .group_by([col("category")])
        .agg([
            col("value").mean().alias("avg_value"),
            col("value").std(1).alias("std_value"),
            col("id").count().alias("count"),
        ])
        .sort(["count"], SortMultipleOptions::default().with_order_descending(true))
        .collect()?;

    // Write results
    let mut file = std::fs::File::create("data/output/category_stats.parquet")?;
    ParquetWriter::new(&mut file)
        .with_compression(ParquetCompression::Snappy)
        .finish(&mut result.clone())?;

    eprintln!("Processed {} categories", result.height());
    Ok(())
}
```

Polars decides internally how to chunk the data. You don't manage chunk sizes or worry about merging partial results — the engine handles it. Your code looks identical to the non-streaming version.

## Converting to Model Inputs

The final step is converting a DataFrame into the format your model expects. For ONNX models from Lesson 7:

```rust
use ndarray::Array2;

pub fn dataframe_to_ndarray(
    df: &DataFrame,
    feature_cols: &[&str],
) -> Result<Array2<f32>, PolarsError> {
    let n_rows = df.height();
    let n_cols = feature_cols.len();

    let mut data = Vec::with_capacity(n_rows * n_cols);

    for &col_name in feature_cols {
        let series = df.column(col_name)?.cast(&DataType::Float32)?;
        let ca = series.f32()?;

        for value in ca.into_iter() {
            data.push(value.unwrap_or(0.0));
        }
    }

    // polars is column-major, ndarray expects row-major for most models
    let col_major = Array2::from_shape_vec((n_cols, n_rows), data).unwrap();
    Ok(col_major.t().to_owned())
}
```

## The Complete Pipeline

Here's how everything fits together:

```rust
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let start = std::time::Instant::now();

    // Step 1: Build features
    eprintln!("Building features...");
    let features = build_features()?;
    eprintln!("  {} rows, {} columns", features.height(), features.width());

    // Step 2: Normalize
    let feature_cols = &[
        "total_events",
        "purchase_count",
        "conversion_rate",
        "days_active",
        "avg_price_interacted",
    ];
    let (normalized, norm_params) = normalize_features(
        &features,
        feature_cols,
        NormMethod::ZScore,
    )?;
    norm_params.save(std::path::Path::new("data/output/norm_params.json"))?;

    // Step 3: Split
    let (train, test) = train_test_split(&normalized, 0.2, 42)?;
    eprintln!("  Train: {} rows, Test: {} rows", train.height(), test.height());

    // Step 4: Convert to model inputs
    let train_array = dataframe_to_ndarray(&train, feature_cols)?;
    let test_array = dataframe_to_ndarray(&test, feature_cols)?;
    eprintln!("  Train array: {:?}", train_array.shape());
    eprintln!("  Test array:  {:?}", test_array.shape());

    // Step 5: Save processed data
    let mut train_file = std::fs::File::create("data/output/train.parquet")?;
    ParquetWriter::new(&mut train_file).finish(&mut train.clone())?;

    let mut test_file = std::fs::File::create("data/output/test.parquet")?;
    ParquetWriter::new(&mut test_file).finish(&mut test.clone())?;

    let elapsed = start.elapsed();
    eprintln!("\nPipeline completed in {elapsed:?}");

    Ok(())
}
```

Clean, fast, and type-safe from raw data to model-ready features. No surprise `NaN` propagation, no silent type coercions, no OOM crashes on large datasets.

## Series Wrap-Up

That's a wrap on the Rust and AI/LLM Integration course. We've covered the full stack:

1. **LLM API Clients** — Type-safe HTTP clients with retry logic
2. **Streaming** — SSE parsing, backpressure, WebSocket support
3. **Tool Calling** — Trait-based tools with schema generation
4. **Embeddings** — Vector search and RAG pipelines
5. **Agent Architectures** — ReAct, plan-execute, state machines
6. **MCP Servers** — Building standardized AI tool interfaces
7. **On-Device Inference** — ONNX Runtime and candle for local models
8. **ML Pipelines** — Data processing with polars

The theme throughout has been the same: Rust's type system catches bugs that would bite you in production with other languages. When you're dealing with AI systems that cost real money per API call, that have non-deterministic behavior, and that interact with external services — type safety isn't a nice-to-have. It's the difference between a prototype and a system you can actually trust.

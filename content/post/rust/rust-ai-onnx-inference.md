---
title: "Lesson 7: On-Device Inference — ONNX Runtime and candle"
author: Atharva Pandey
series: "Rust and AI/LLM Integration"
lesson: 7
keywords: [Rust, rust, AI, LLM, ONNX, candle, inference, local, on-device, huggingface]
tags: [Rust tutorial, rust, ai, llm]
date: '2025-08-26T07:49:00.000Z'
---

I run a sentiment analysis model on every support ticket that comes in. At first I used the OpenAI API — about 2 cents per ticket. Sounds cheap until you do the math: 10,000 tickets a day, $200/day, $6,000/month. For a model that classifies text into "positive," "negative," and "neutral."

Switched to a local ONNX model running on a $50/month VM. Same accuracy. Latency dropped from 300ms to 8ms. Cost dropped to roughly zero. Not every task needs GPT-4 — and Rust is arguably the best language for running models locally because you get C++ performance without the C++ pain.

## Two Paths to Local Inference

There are two main approaches for running ML models in Rust:

1. **ONNX Runtime** — Run any model exported to the ONNX format. Supports models from PyTorch, TensorFlow, scikit-learn, you name it. Battle-tested in production at Microsoft-scale.
2. **candle** — HuggingFace's pure-Rust ML framework. Natively loads HuggingFace models, supports GPU acceleration, and doesn't require any C/C++ runtime.

They serve different purposes. ONNX Runtime is for deploying pre-trained models. candle is for when you want more control over the inference pipeline, or when you're working with models from the HuggingFace ecosystem directly.

## ONNX Runtime in Rust

Let's start with ONNX Runtime. You'll need the `ort` crate, which provides safe Rust bindings:

```toml
[package]
name = "local-inference"
version = "0.1.0"
edition = "2021"

[dependencies]
ort = { version = "2", features = ["load-dynamic"] }
ndarray = "0.16"
tokenizers = "0.20"
```

The `load-dynamic` feature means `ort` loads the ONNX Runtime shared library at runtime instead of statically linking it. This keeps compile times reasonable and lets you swap the runtime version without rebuilding.

### Loading and Running a Model

```rust
use ort::{Session, Value as OrtValue};
use ndarray::{Array2, CowArray};
use std::path::Path;

pub struct OnnxModel {
    session: Session,
}

impl OnnxModel {
    pub fn load(model_path: &Path) -> Result<Self, ort::Error> {
        let session = Session::builder()?
            .with_optimization_level(ort::GraphOptimizationLevel::Level3)?
            .with_intra_threads(4)?
            .commit_from_file(model_path)?;

        eprintln!("Model loaded: {:?}", model_path);
        eprintln!("  Inputs:");
        for input in session.inputs.iter() {
            eprintln!("    {}: {:?}", input.name, input.input_type);
        }
        eprintln!("  Outputs:");
        for output in session.outputs.iter() {
            eprintln!("    {}: {:?}", output.name, output.output_type);
        }

        Ok(Self { session })
    }

    pub fn run_inference(
        &self,
        input_ids: &[i64],
        attention_mask: &[i64],
    ) -> Result<Vec<f32>, ort::Error> {
        let seq_len = input_ids.len();

        // Create 2D arrays with batch dimension
        let input_ids_array = Array2::from_shape_vec(
            (1, seq_len),
            input_ids.to_vec(),
        ).unwrap();

        let attention_mask_array = Array2::from_shape_vec(
            (1, seq_len),
            attention_mask.to_vec(),
        ).unwrap();

        let outputs = self.session.run(
            ort::inputs![
                "input_ids" => input_ids_array,
                "attention_mask" => attention_mask_array,
            ]?
        )?;

        // Extract the output tensor
        let output = outputs[0]
            .try_extract_tensor::<f32>()?;

        Ok(output.as_slice().unwrap().to_vec())
    }
}
```

### Text Classification Pipeline

Now let's build a proper text classification pipeline that handles tokenization:

```rust
use tokenizers::Tokenizer;

pub struct TextClassifier {
    model: OnnxModel,
    tokenizer: Tokenizer,
    labels: Vec<String>,
    max_length: usize,
}

impl TextClassifier {
    pub fn load(
        model_path: &Path,
        tokenizer_path: &Path,
        labels: Vec<String>,
    ) -> Result<Self, Box<dyn std::error::Error>> {
        let model = OnnxModel::load(model_path)?;
        let tokenizer = Tokenizer::from_file(tokenizer_path)
            .map_err(|e| format!("Tokenizer load failed: {e}"))?;

        Ok(Self {
            model,
            tokenizer,
            labels,
            max_length: 512,
        })
    }

    pub fn classify(&self, text: &str) -> Result<Classification, Box<dyn std::error::Error>> {
        // Tokenize
        let encoding = self
            .tokenizer
            .encode(text, true)
            .map_err(|e| format!("Tokenization failed: {e}"))?;

        let mut input_ids: Vec<i64> = encoding
            .get_ids()
            .iter()
            .map(|&id| id as i64)
            .collect();
        let mut attention_mask: Vec<i64> = encoding
            .get_attention_mask()
            .iter()
            .map(|&m| m as i64)
            .collect();

        // Truncate if needed
        if input_ids.len() > self.max_length {
            input_ids.truncate(self.max_length);
            attention_mask.truncate(self.max_length);
        }

        // Run inference
        let logits = self.model.run_inference(&input_ids, &attention_mask)?;

        // Apply softmax
        let probabilities = softmax(&logits);

        // Find the best label
        let (best_idx, best_prob) = probabilities
            .iter()
            .enumerate()
            .max_by(|(_, a), (_, b)| a.partial_cmp(b).unwrap())
            .unwrap();

        Ok(Classification {
            label: self.labels[best_idx].clone(),
            confidence: *best_prob,
            all_scores: self
                .labels
                .iter()
                .zip(probabilities.iter())
                .map(|(l, &p)| (l.clone(), p))
                .collect(),
        })
    }
}

#[derive(Debug)]
pub struct Classification {
    pub label: String,
    pub confidence: f32,
    pub all_scores: Vec<(String, f32)>,
}

fn softmax(logits: &[f32]) -> Vec<f32> {
    let max = logits.iter().cloned().fold(f32::NEG_INFINITY, f32::max);
    let exps: Vec<f32> = logits.iter().map(|&x| (x - max).exp()).collect();
    let sum: f32 = exps.iter().sum();
    exps.iter().map(|&e| e / sum).collect()
}
```

### Batch Inference

Processing one text at a time wastes GPU/CPU cycles. Batch inference is dramatically faster:

```rust
impl TextClassifier {
    pub fn classify_batch(
        &self,
        texts: &[&str],
    ) -> Result<Vec<Classification>, Box<dyn std::error::Error>> {
        if texts.is_empty() {
            return Ok(Vec::new());
        }

        // Tokenize all texts
        let encodings: Vec<_> = texts
            .iter()
            .map(|text| self.tokenizer.encode(*text, true))
            .collect::<Result<Vec<_>, _>>()
            .map_err(|e| format!("Tokenization failed: {e}"))?;

        // Find max length for padding
        let max_len = encodings
            .iter()
            .map(|e| e.get_ids().len().min(self.max_length))
            .max()
            .unwrap_or(0);

        let batch_size = texts.len();

        // Build padded batch tensors
        let mut all_input_ids = vec![0i64; batch_size * max_len];
        let mut all_attention_mask = vec![0i64; batch_size * max_len];

        for (i, encoding) in encodings.iter().enumerate() {
            let ids = encoding.get_ids();
            let mask = encoding.get_attention_mask();
            let len = ids.len().min(max_len);

            for j in 0..len {
                all_input_ids[i * max_len + j] = ids[j] as i64;
                all_attention_mask[i * max_len + j] = mask[j] as i64;
            }
        }

        let input_ids_array = ndarray::Array2::from_shape_vec(
            (batch_size, max_len),
            all_input_ids,
        ).unwrap();

        let attention_array = ndarray::Array2::from_shape_vec(
            (batch_size, max_len),
            all_attention_mask,
        ).unwrap();

        let outputs = self.model.session.run(
            ort::inputs![
                "input_ids" => input_ids_array,
                "attention_mask" => attention_array,
            ]?
        )?;

        let output_tensor = outputs[0].try_extract_tensor::<f32>()?;
        let num_labels = self.labels.len();

        let results: Vec<Classification> = (0..batch_size)
            .map(|i| {
                let start = i * num_labels;
                let logits = &output_tensor.as_slice().unwrap()[start..start + num_labels];
                let probs = softmax(logits);

                let (best_idx, best_prob) = probs
                    .iter()
                    .enumerate()
                    .max_by(|(_, a), (_, b)| a.partial_cmp(b).unwrap())
                    .unwrap();

                Classification {
                    label: self.labels[best_idx].clone(),
                    confidence: *best_prob,
                    all_scores: self
                        .labels
                        .iter()
                        .zip(probs.iter())
                        .map(|(l, &p)| (l.clone(), p))
                        .collect(),
                }
            })
            .collect();

        Ok(results)
    }
}
```

Batching 32 texts together instead of processing them one by one can be 10-20x faster, depending on the model and hardware. The overhead is padding shorter sequences — but the parallelism gains far outweigh that cost.

## candle: Pure-Rust ML

candle is HuggingFace's answer to "what if PyTorch, but Rust?" It's a tensor computation library that can load and run transformer models directly:

```toml
[dependencies]
candle-core = "0.8"
candle-nn = "0.8"
candle-transformers = "0.8"
hf-hub = "0.3"
```

### Loading an Embedding Model With candle

```rust
use candle_core::{Device, Tensor, DType};
use candle_nn::VarBuilder;
use candle_transformers::models::bert::{BertModel, Config as BertConfig};
use hf_hub::{api::sync::Api, Repo, RepoType};

pub struct LocalEmbeddingModel {
    model: BertModel,
    tokenizer: Tokenizer,
    device: Device,
}

impl LocalEmbeddingModel {
    pub fn load(model_id: &str) -> Result<Self, Box<dyn std::error::Error>> {
        let device = Device::Cpu; // Use Device::cuda_if_available(0)? for GPU

        let api = Api::new()?;
        let repo = api.repo(Repo::new(model_id.to_string(), RepoType::Model));

        // Download model files
        let config_path = repo.get("config.json")?;
        let weights_path = repo.get("model.safetensors")?;
        let tokenizer_path = repo.get("tokenizer.json")?;

        // Load config
        let config_str = std::fs::read_to_string(&config_path)?;
        let config: BertConfig = serde_json::from_str(&config_str)?;

        // Load weights
        let vb = unsafe {
            VarBuilder::from_mmaped_safetensors(
                &[weights_path],
                DType::F32,
                &device,
            )?
        };

        let model = BertModel::load(vb, &config)?;
        let tokenizer = Tokenizer::from_file(&tokenizer_path)
            .map_err(|e| format!("Failed to load tokenizer: {e}"))?;

        Ok(Self {
            model,
            tokenizer,
            device,
        })
    }

    pub fn embed(&self, text: &str) -> Result<Vec<f32>, Box<dyn std::error::Error>> {
        let encoding = self
            .tokenizer
            .encode(text, true)
            .map_err(|e| format!("Tokenization failed: {e}"))?;

        let input_ids = Tensor::new(
            encoding.get_ids().to_vec(),
            &self.device,
        )?.unsqueeze(0)?;

        let token_type_ids = input_ids.zeros_like()?;

        let embeddings = self.model.forward(&input_ids, &token_type_ids, None)?;

        // Mean pooling over the sequence dimension
        let (_, seq_len, _) = embeddings.dims3()?;
        let sum = embeddings.sum(1)?;
        let mean = (sum / seq_len as f64)?;

        let result = mean.squeeze(0)?.to_vec1::<f32>()?;
        Ok(result)
    }
}
```

The big win with candle: no Python, no C++ runtime, no ONNX conversion step. You point it at a HuggingFace model ID and it downloads, loads, and runs it. The `hf-hub` crate handles caching and downloading transparently.

### Sentence Similarity

With a local embedding model, you can build a similarity engine that runs entirely offline:

```rust
impl LocalEmbeddingModel {
    pub fn similarity(&self, text_a: &str, text_b: &str) -> Result<f32, Box<dyn std::error::Error>> {
        let emb_a = self.embed(text_a)?;
        let emb_b = self.embed(text_b)?;

        Ok(cosine_similarity(&emb_a, &emb_b))
    }

    pub fn find_most_similar(
        &self,
        query: &str,
        candidates: &[&str],
    ) -> Result<Vec<(usize, f32)>, Box<dyn std::error::Error>> {
        let query_emb = self.embed(query)?;

        let mut scores: Vec<(usize, f32)> = candidates
            .iter()
            .enumerate()
            .map(|(i, text)| {
                let emb = self.embed(text).unwrap();
                (i, cosine_similarity(&query_emb, &emb))
            })
            .collect();

        scores.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap());
        Ok(scores)
    }
}

fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let dot: f32 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    let norm_a: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let norm_b: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm_a * norm_b == 0.0 {
        0.0
    } else {
        dot / (norm_a * norm_b)
    }
}
```

## Benchmarking: API vs. Local

Here's actual timing data from my setup — a MacBook with M2 and a linux server with a T4 GPU:

```rust
use std::time::Instant;

fn benchmark_classifier(classifier: &TextClassifier) {
    let texts: Vec<&str> = vec![
        "This product is absolutely fantastic, I love it!",
        "Terrible experience, would not recommend to anyone.",
        "It's okay, nothing special but gets the job done.",
        "The customer service was incredibly helpful and responsive.",
        "Waste of money, broke after two days of normal use.",
    ];

    // Warmup
    for text in &texts {
        let _ = classifier.classify(text);
    }

    // Single inference
    let start = Instant::now();
    for _ in 0..100 {
        for text in &texts {
            let _ = classifier.classify(text);
        }
    }
    let elapsed = start.elapsed();
    let per_text = elapsed / (100 * texts.len() as u32);
    eprintln!("Single inference: {per_text:?} per text");

    // Batch inference
    let start = Instant::now();
    for _ in 0..100 {
        let _ = classifier.classify_batch(&texts);
    }
    let elapsed = start.elapsed();
    let per_text = elapsed / (100 * texts.len() as u32);
    eprintln!("Batch inference:  {per_text:?} per text");
}
```

Typical results I've seen:

| Method | Latency per text | Cost per 1M texts |
|--------|-----------------|-------------------|
| OpenAI API | 200-500ms | ~$200 |
| ONNX (CPU, single) | 8-15ms | ~$50 (compute) |
| ONNX (CPU, batch 32) | 1-3ms | ~$50 (compute) |
| candle (CPU) | 10-20ms | ~$50 (compute) |
| ONNX (GPU) | 0.5-2ms | ~$100 (GPU VM) |

For high-volume classification and embedding tasks, local inference wins hands down. For complex reasoning and generation? You still want a large model behind an API. Pick the right tool for the job.

## What's Next

We've covered running models locally — both the battle-tested ONNX path and the pure-Rust candle path. In the final lesson, we'll build ML data pipelines using polars. Because before you can run inference, you need clean, processed data — and Rust with polars is absurdly fast at data transformation.

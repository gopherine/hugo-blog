---
title: "Lesson 8: PyO3 — Rust extensions for Python"
author: Atharva Pandey
series: "Rust Unsafe & FFI"
lesson: 8
keywords: [Rust, rust, unsafe, FFI, PyO3, Python, maturin, pyo3, native extension]
tags: [Rust tutorial, rust, unsafe, ffi]
date: '2025-06-28T08:14:00.000Z'
---

I had a Python service that processed 2 million JSON records daily. Profiling showed 80% of the time was spent in one function — a custom similarity scoring algorithm. I rewrote that single function in Rust with PyO3. Same API, same tests, same deployment. Processing time dropped from 47 minutes to 90 seconds. The Python team didn't have to learn Rust, didn't have to change their imports, didn't even notice — they just saw their pipeline get 30x faster.

That's the pitch for PyO3: write the hot path in Rust, call it from Python like any other module.

## Getting Started with maturin

PyO3 is the Rust-to-Python bridge. `maturin` is the build tool that packages your Rust code as a Python wheel. Together, they handle all the complexity of CPython's C API.

```bash
# Install maturin
pip install maturin

# Create a new project
mkdir rust-python-demo && cd rust-python-demo
maturin init --bindings pyo3
```

This generates:

```toml
# Cargo.toml
[package]
name = "rust_python_demo"
version = "0.1.0"
edition = "2021"

[lib]
name = "rust_python_demo"
crate-type = ["cdylib"]

[dependencies]
pyo3 = { version = "0.23", features = ["extension-module"] }
```

```rust
// src/lib.rs
use pyo3::prelude::*;

#[pyfunction]
fn sum_as_string(a: usize, b: usize) -> PyResult<String> {
    Ok((a + b).to_string())
}

#[pymodule]
fn rust_python_demo(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(sum_as_string, m)?)?;
    Ok(())
}
```

Build and install:

```bash
# Development build (fast, unoptimized)
maturin develop

# Production build
maturin build --release

# Now in Python:
# >>> import rust_python_demo
# >>> rust_python_demo.sum_as_string(5, 3)
# '8'
```

That's it. No header files, no manual type conversion, no shared library paths. PyO3 and maturin handle all the CPython ABI details.

## Exposing Functions

PyO3's `#[pyfunction]` macro makes any Rust function callable from Python. Type conversion happens automatically for common types:

```rust
use pyo3::prelude::*;

/// Calculate the nth Fibonacci number.
/// Raises ValueError if n > 186 (would overflow u128).
#[pyfunction]
fn fibonacci(n: u32) -> PyResult<u128> {
    if n > 186 {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "n too large, would overflow"
        ));
    }

    let mut a: u128 = 0;
    let mut b: u128 = 1;
    for _ in 0..n {
        let temp = b;
        b = a + b;
        a = temp;
    }
    Ok(a)
}

/// Count occurrences of a byte in a bytes object.
#[pyfunction]
fn count_byte(data: &[u8], target: u8) -> usize {
    data.iter().filter(|&&b| b == target).count()
}

/// Process a list of floats — return sum and mean.
#[pyfunction]
fn stats(values: Vec<f64>) -> PyResult<(f64, f64)> {
    if values.is_empty() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            "empty list"
        ));
    }
    let sum: f64 = values.iter().sum();
    let mean = sum / values.len() as f64;
    Ok((sum, mean))
}

/// Filter strings by length — demonstrates Vec<String> conversion.
#[pyfunction]
fn filter_by_length(strings: Vec<String>, min_len: usize) -> Vec<String> {
    strings.into_iter().filter(|s| s.len() >= min_len).collect()
}

#[pymodule]
fn rust_python_demo(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(fibonacci, m)?)?;
    m.add_function(wrap_pyfunction!(count_byte, m)?)?;
    m.add_function(wrap_pyfunction!(stats, m)?)?;
    m.add_function(wrap_pyfunction!(filter_by_length, m)?)?;
    Ok(())
}
```

```python
# Usage from Python
import rust_python_demo as rp

print(rp.fibonacci(50))          # 12586269025
print(rp.count_byte(b"hello", ord('l')))  # 2
print(rp.stats([1.0, 2.0, 3.0]))  # (6.0, 2.0)
print(rp.filter_by_length(["hi", "hello", "hey"], 3))  # ['hello', 'hey']
```

The type mapping works both ways. Python integers become Rust integers (with overflow checking), Python strings become `String` or `&str`, Python lists become `Vec<T>`, and so on.

## Exposing Classes

This is where PyO3 really shines. You can expose full Rust structs as Python classes with methods, properties, and dunder methods:

```rust
use pyo3::prelude::*;
use std::collections::HashMap;

/// A fast word frequency counter implemented in Rust.
#[pyclass]
struct WordCounter {
    counts: HashMap<String, usize>,
    total_words: usize,
}

#[pymethods]
impl WordCounter {
    /// Create a new empty WordCounter.
    #[new]
    fn new() -> Self {
        WordCounter {
            counts: HashMap::new(),
            total_words: 0,
        }
    }

    /// Add text to the counter. Words are split on whitespace.
    fn add_text(&mut self, text: &str) {
        for word in text.split_whitespace() {
            let normalized = word.to_lowercase();
            // Strip basic punctuation
            let cleaned: String = normalized
                .chars()
                .filter(|c| c.is_alphanumeric())
                .collect();

            if !cleaned.is_empty() {
                *self.counts.entry(cleaned).or_insert(0) += 1;
                self.total_words += 1;
            }
        }
    }

    /// Get the count for a specific word.
    fn count(&self, word: &str) -> usize {
        let normalized = word.to_lowercase();
        *self.counts.get(&normalized).unwrap_or(&0)
    }

    /// Get the top N most frequent words.
    fn top_n(&self, n: usize) -> Vec<(String, usize)> {
        let mut pairs: Vec<_> = self.counts.iter()
            .map(|(k, v)| (k.clone(), *v))
            .collect();
        pairs.sort_by(|a, b| b.1.cmp(&a.1));
        pairs.truncate(n);
        pairs
    }

    /// Total number of words processed.
    #[getter]
    fn total_words(&self) -> usize {
        self.total_words
    }

    /// Number of unique words.
    #[getter]
    fn unique_words(&self) -> usize {
        self.counts.len()
    }

    /// Python __len__ — return unique word count.
    fn __len__(&self) -> usize {
        self.counts.len()
    }

    /// Python __contains__ — support 'word' in counter syntax.
    fn __contains__(&self, word: &str) -> bool {
        self.counts.contains_key(&word.to_lowercase())
    }

    /// Python __repr__
    fn __repr__(&self) -> String {
        format!(
            "WordCounter(total={}, unique={})",
            self.total_words,
            self.counts.len()
        )
    }
}
```

```python
# Python usage — feels completely native
from rust_python_demo import WordCounter

counter = WordCounter()
counter.add_text("the quick brown fox jumps over the lazy dog")
counter.add_text("the fox is quick and the dog is lazy")

print(counter.total_words)     # 18
print(counter.unique_words)    # 10
print(counter.count("the"))    # 4
print(counter.top_n(3))        # [('the', 4), ('quick', 2), ('fox', 2)]
print("fox" in counter)        # True
print(len(counter))            # 10
print(repr(counter))           # WordCounter(total=18, unique=10)
```

## Error Handling

Python exceptions map cleanly to `PyResult<T>`. You can raise any standard Python exception:

```rust
use pyo3::prelude::*;
use pyo3::exceptions::{PyValueError, PyIOError, PyTypeError, PyRuntimeError};

#[pyfunction]
fn parse_positive_int(s: &str) -> PyResult<u64> {
    let value: u64 = s.parse().map_err(|e| {
        PyValueError::new_err(format!("Cannot parse '{}' as positive integer: {}", s, e))
    })?;

    if value == 0 {
        return Err(PyValueError::new_err("Value must be positive, got 0"));
    }

    Ok(value)
}

/// Custom exception type
pyo3::create_exception!(rust_python_demo, ProcessingError, pyo3::exceptions::PyException);

#[pyfunction]
fn process_data(data: Vec<f64>) -> PyResult<Vec<f64>> {
    if data.is_empty() {
        return Err(ProcessingError::new_err("Cannot process empty data"));
    }

    let mean = data.iter().sum::<f64>() / data.len() as f64;

    // Normalize
    Ok(data.iter().map(|x| x - mean).collect())
}

#[pymodule]
fn rust_python_demo(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("ProcessingError", m.py().get_type::<ProcessingError>())?;
    m.add_function(wrap_pyfunction!(parse_positive_int, m)?)?;
    m.add_function(wrap_pyfunction!(process_data, m)?)?;
    Ok(())
}
```

```python
from rust_python_demo import parse_positive_int, process_data, ProcessingError

try:
    parse_positive_int("abc")
except ValueError as e:
    print(f"Caught: {e}")  # Caught: Cannot parse 'abc' as positive integer: ...

try:
    process_data([])
except ProcessingError as e:
    print(f"Caught: {e}")  # Caught: Cannot process empty data
```

## Working with NumPy

For numerical computing, PyO3 integrates with NumPy through the `numpy` crate. This is where you get the big performance wins — operating on NumPy arrays without copying data:

```toml
# Cargo.toml
[dependencies]
pyo3 = { version = "0.23", features = ["extension-module"] }
numpy = "0.23"
```

```rust
use numpy::{PyArray1, PyReadonlyArray1, PyReadonlyArray2, PyArray2};
use pyo3::prelude::*;

/// Element-wise square root, operating on the NumPy array directly.
/// No data copying — we read from the input and write to a new array.
#[pyfunction]
fn fast_sqrt<'py>(
    py: Python<'py>,
    input: PyReadonlyArray1<'py, f64>,
) -> Bound<'py, PyArray1<f64>> {
    let input = input.as_slice().unwrap();
    let output: Vec<f64> = input.iter().map(|x| x.sqrt()).collect();
    PyArray1::from_vec(py, output)
}

/// Compute the dot product of two vectors.
/// Zero-copy: reads directly from NumPy's memory.
#[pyfunction]
fn dot_product(a: PyReadonlyArray1<'_, f64>, b: PyReadonlyArray1<'_, f64>) -> PyResult<f64> {
    let a = a.as_slice().unwrap();
    let b = b.as_slice().unwrap();

    if a.len() != b.len() {
        return Err(pyo3::exceptions::PyValueError::new_err(
            format!("Shape mismatch: {} vs {}", a.len(), b.len())
        ));
    }

    Ok(a.iter().zip(b.iter()).map(|(x, y)| x * y).sum())
}

/// Row-wise normalization of a 2D array.
#[pyfunction]
fn normalize_rows<'py>(
    py: Python<'py>,
    input: PyReadonlyArray2<'py, f64>,
) -> PyResult<Bound<'py, PyArray2<f64>>> {
    let shape = input.shape();
    let rows = shape[0];
    let cols = shape[1];

    let input = input.as_slice().unwrap();
    let mut output = vec![0.0f64; rows * cols];

    for r in 0..rows {
        let row_start = r * cols;
        let row_end = row_start + cols;
        let row = &input[row_start..row_end];

        let norm: f64 = row.iter().map(|x| x * x).sum::<f64>().sqrt();

        if norm > f64::EPSILON {
            for c in 0..cols {
                output[row_start + c] = row[c] / norm;
            }
        }
    }

    Ok(PyArray2::from_vec2(
        py,
        &(0..rows)
            .map(|r| output[r * cols..(r + 1) * cols].to_vec())
            .collect::<Vec<_>>(),
    )?)
}
```

```python
import numpy as np
from rust_python_demo import fast_sqrt, dot_product, normalize_rows

# Element-wise sqrt
arr = np.array([1.0, 4.0, 9.0, 16.0])
result = fast_sqrt(arr)
print(result)  # [1. 2. 3. 4.]

# Dot product
a = np.array([1.0, 2.0, 3.0])
b = np.array([4.0, 5.0, 6.0])
print(dot_product(a, b))  # 32.0

# Row normalization
matrix = np.array([[3.0, 4.0], [1.0, 0.0]])
normed = normalize_rows(matrix)
print(normed)  # [[0.6, 0.8], [1.0, 0.0]]
```

The key here is `PyReadonlyArray1` — it gives you a zero-copy slice view into NumPy's memory. No copying a million-element array just to read it.

## Releasing the GIL

Python's Global Interpreter Lock (GIL) means only one thread runs Python code at a time. When your Rust code doesn't need Python objects, you can release the GIL to enable true parallelism:

```rust
use pyo3::prelude::*;

/// CPU-intensive computation that releases the GIL.
#[pyfunction]
fn parallel_sum(data: Vec<f64>) -> f64 {
    // Release the GIL — other Python threads can run while
    // this Rust code executes.
    Python::with_gil(|py| {
        py.allow_threads(|| {
            // Pure Rust computation — no Python objects touched
            data.iter().sum()
        })
    })
}

/// Even better: release the GIL and use Rayon for parallel iteration.
/// Add rayon = "1" to Cargo.toml
#[pyfunction]
fn parallel_map_square(py: Python<'_>, data: Vec<f64>) -> Vec<f64> {
    py.allow_threads(|| {
        use rayon::prelude::*;
        data.par_iter().map(|x| x * x).collect()
    })
}
```

This is a massive win. Python's threading is limited by the GIL, but `allow_threads` lets your Rust code use all available cores. Combine this with Rayon and you get parallel computation that's impossible in pure Python.

## A Real-World Example: Text Similarity

Let me show you something close to what I built for that JSON processing pipeline:

```rust
use pyo3::prelude::*;
use std::collections::HashMap;

#[pyclass]
struct TextSimilarity {
    idf_cache: HashMap<String, f64>,
    doc_count: usize,
}

#[pymethods]
impl TextSimilarity {
    #[new]
    fn new() -> Self {
        TextSimilarity {
            idf_cache: HashMap::new(),
            doc_count: 0,
        }
    }

    /// Build IDF (inverse document frequency) from a corpus.
    fn fit(&mut self, documents: Vec<String>) {
        self.doc_count = documents.len();
        let mut doc_freq: HashMap<String, usize> = HashMap::new();

        for doc in &documents {
            let unique_words: std::collections::HashSet<&str> =
                doc.split_whitespace().collect();
            for word in unique_words {
                *doc_freq.entry(word.to_lowercase()).or_insert(0) += 1;
            }
        }

        self.idf_cache = doc_freq
            .into_iter()
            .map(|(word, freq)| {
                let idf = ((self.doc_count as f64) / (1.0 + freq as f64)).ln() + 1.0;
                (word, idf)
            })
            .collect();
    }

    /// Compute TF-IDF cosine similarity between two texts.
    /// Releases the GIL for the computation.
    fn similarity(&self, py: Python<'_>, a: &str, b: &str) -> f64 {
        let idf = &self.idf_cache;

        py.allow_threads(|| {
            let vec_a = Self::tfidf_vector(a, idf);
            let vec_b = Self::tfidf_vector(b, idf);
            Self::cosine_similarity(&vec_a, &vec_b)
        })
    }

    /// Batch similarity — compare one query against many documents.
    fn batch_similarity(
        &self,
        py: Python<'_>,
        query: &str,
        documents: Vec<String>,
    ) -> Vec<f64> {
        let idf = &self.idf_cache;

        py.allow_threads(|| {
            let query_vec = Self::tfidf_vector(query, idf);
            documents
                .iter()
                .map(|doc| {
                    let doc_vec = Self::tfidf_vector(doc, idf);
                    Self::cosine_similarity(&query_vec, &doc_vec)
                })
                .collect()
        })
    }
}

impl TextSimilarity {
    fn tfidf_vector(text: &str, idf: &HashMap<String, f64>) -> HashMap<String, f64> {
        let words: Vec<String> = text.split_whitespace()
            .map(|w| w.to_lowercase())
            .collect();
        let total = words.len() as f64;

        let mut tf: HashMap<String, f64> = HashMap::new();
        for word in &words {
            *tf.entry(word.clone()).or_insert(0.0) += 1.0 / total;
        }

        tf.into_iter()
            .map(|(word, freq)| {
                let idf_val = idf.get(&word).copied().unwrap_or(1.0);
                (word, freq * idf_val)
            })
            .collect()
    }

    fn cosine_similarity(a: &HashMap<String, f64>, b: &HashMap<String, f64>) -> f64 {
        let dot: f64 = a.iter()
            .filter_map(|(k, v)| b.get(k).map(|bv| v * bv))
            .sum();

        let norm_a: f64 = a.values().map(|v| v * v).sum::<f64>().sqrt();
        let norm_b: f64 = b.values().map(|v| v * v).sum::<f64>().sqrt();

        if norm_a < f64::EPSILON || norm_b < f64::EPSILON {
            0.0
        } else {
            dot / (norm_a * norm_b)
        }
    }
}
```

```python
from rust_python_demo import TextSimilarity

sim = TextSimilarity()

# Fit on a corpus
corpus = [
    "the cat sat on the mat",
    "the dog chased the cat",
    "the bird flew over the house",
    "a fish swam in the pond",
]
sim.fit(corpus)

# Compare texts
score = sim.similarity("the cat is on the mat", "the cat sat on the mat")
print(f"Similarity: {score:.4f}")  # High similarity

# Batch comparison
query = "cat and dog"
scores = sim.batch_similarity(query, corpus)
for doc, score in zip(corpus, scores):
    print(f"  {score:.4f} - {doc}")
```

## Testing PyO3 Modules

You can test from both sides:

```rust
// Rust-side tests (unit tests for internal logic)
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cosine_similarity_identical() {
        let mut a = HashMap::new();
        a.insert("hello".into(), 1.0);
        a.insert("world".into(), 2.0);

        let sim = TextSimilarity::cosine_similarity(&a, &a);
        assert!((sim - 1.0).abs() < 1e-10);
    }

    #[test]
    fn test_cosine_similarity_orthogonal() {
        let mut a = HashMap::new();
        a.insert("hello".into(), 1.0);

        let mut b = HashMap::new();
        b.insert("world".into(), 1.0);

        let sim = TextSimilarity::cosine_similarity(&a, &b);
        assert!(sim.abs() < 1e-10);
    }
}
```

```python
# Python-side tests (pytest)
import pytest
from rust_python_demo import WordCounter, fibonacci

def test_fibonacci():
    assert fibonacci(0) == 0
    assert fibonacci(1) == 1
    assert fibonacci(10) == 55

def test_fibonacci_overflow():
    with pytest.raises(ValueError):
        fibonacci(200)

def test_word_counter():
    wc = WordCounter()
    wc.add_text("hello world hello")
    assert wc.count("hello") == 2
    assert wc.count("world") == 1
    assert wc.total_words == 3
    assert "hello" in wc
    assert "missing" not in wc
```

## Performance Tip: Avoid Unnecessary Copies

The biggest performance pitfall with PyO3 is copying data across the boundary. Every `Vec<String>` argument copies all the strings from Python to Rust. For large datasets, this kills your speedup.

Strategies:
- Use `&[u8]` or `PyReadonlyArray` for zero-copy access
- Process data in batches instead of element-by-element
- For very large datasets, accept a file path and read directly in Rust
- Use `#[pyclass]` to keep data on the Rust side between calls

```rust
/// BAD: Copies entire list from Python to Rust
#[pyfunction]
fn slow_sum(values: Vec<f64>) -> f64 {
    values.iter().sum()
}

/// GOOD: Zero-copy access to NumPy array
#[pyfunction]
fn fast_sum(values: PyReadonlyArray1<'_, f64>) -> f64 {
    values.as_slice().unwrap().iter().sum()
}
```

The difference is significant — for a 10-million-element array, `Vec<f64>` copies 80MB of data. `PyReadonlyArray1` copies zero bytes.

PyO3 turns Rust into the best-kept performance secret in the Python ecosystem. Next up: we do the same thing for Node.js with napi-rs.

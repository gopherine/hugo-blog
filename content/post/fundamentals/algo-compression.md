---
title: "Lesson 12: Compression Basics — Why gzip works and entropy matters"
author: "Atharva Pandey"
keywords:
  - compression
  - gzip
  - entropy
  - huffman coding
  - LZ77
  - zstd
  - production engineering
tags:
  - fundamentals
  - algorithms
series: "Algorithms That Matter in Production"
lesson: 12
date: "2024-10-03T00:00:00.000Z"
---

Every senior engineer I know makes compression decisions regularly: should this API response be gzip-compressed? Should logs be stored compressed? What compression level? Which algorithm? I made these decisions for years based on "gzip is standard, use it" without understanding why it worked or when something else might be better.

Understanding the fundamentals of how compression works — entropy, Huffman coding, and LZ77 — changed how I think about data formats, wire protocols, and storage costs. It also helped me understand why some data compresses well and some data does not, which is critical for capacity planning.

## How It Works

Compression exploits two types of redundancy:

1. **Statistical redundancy**: some symbols appear more often than others. Encode frequent symbols with fewer bits.
2. **Repetition**: the same sequences appear multiple times. Encode them as references to earlier occurrences.

**Huffman coding** handles statistical redundancy. Assign shorter bit codes to more frequent symbols and longer codes to less frequent ones. This is the entropy encoding stage of gzip, zstd, and brotli.

**LZ77** (and its variants LZ78, LZW, LZSS) handle repetition. As you scan through the data, maintain a "window" of recently seen data. When you encounter a sequence you have seen before, output a (offset, length) pair instead of the raw bytes.

gzip = LZ77 (called DEFLATE) + Huffman coding. The two stages compose: LZ77 removes repetition first, then Huffman coding compresses what remains statistically.

```go
package main

import (
    "bytes"
    "compress/gzip"
    "compress/zlib"
    "fmt"
    "io"
)

// Gzip compression with configurable level
func gzipCompress(data []byte, level int) ([]byte, error) {
    var buf bytes.Buffer
    w, err := gzip.NewWriterLevel(&buf, level)
    if err != nil {
        return nil, fmt.Errorf("creating gzip writer: %w", err)
    }
    if _, err := w.Write(data); err != nil {
        return nil, fmt.Errorf("writing compressed data: %w", err)
    }
    if err := w.Close(); err != nil {
        return nil, fmt.Errorf("closing gzip writer: %w", err)
    }
    return buf.Bytes(), nil
}

func gzipDecompress(data []byte) ([]byte, error) {
    r, err := gzip.NewReader(bytes.NewReader(data))
    if err != nil {
        return nil, fmt.Errorf("creating gzip reader: %w", err)
    }
    defer r.Close()
    return io.ReadAll(r)
}

// Demonstrate compression ratio difference by content type
func compressionRatio(data []byte) float64 {
    compressed, err := gzipCompress(data, gzip.DefaultCompression)
    if err != nil {
        return 1.0
    }
    return float64(len(compressed)) / float64(len(data))
}
```

**Entropy** is the theoretical minimum bits per symbol, given the symbol frequencies. A file with completely random data has entropy close to 8 bits per byte — no compression is possible. A file with highly repetitive patterns (like a JSON response with many repeated field names) might have entropy of 2–3 bits per byte, meaning compression can reduce it to 25–37% of the original size.

```go
import "math"

// Estimate entropy in bits per byte for a byte sequence
func entropy(data []byte) float64 {
    freq := make(map[byte]int)
    for _, b := range data {
        freq[b]++
    }
    n := float64(len(data))
    var h float64
    for _, count := range freq {
        p := float64(count) / n
        h -= p * math.Log2(p)
    }
    return h // bits per symbol; max is 8 for fully random bytes
}
```

## When You Need It

Understanding entropy tells you when compression will help:

- **JSON/XML API responses**: high redundancy (repeated field names, whitespace, common values). Typically compress 70–90%. Always worth compressing for large payloads.
- **Log files**: moderate-to-high redundancy (repeated timestamps, hostnames, log levels). 60–80% compression typical.
- **Images (JPEG, PNG)**: already compressed. Running gzip on a JPEG may actually increase its size. Never double-compress.
- **Random binary data** (encrypted data, already-compressed data): close to maximum entropy. Compression adds overhead without size reduction.
- **CSV/TSV data**: high redundancy in column names and common values. 70–85% compression typical.

```go
// Practical: HTTP response compression middleware
import (
    "net/http"
    "strings"
    "compress/gzip"
)

type gzipResponseWriter struct {
    http.ResponseWriter
    writer *gzip.Writer
}

func (g *gzipResponseWriter) Write(b []byte) (int, error) {
    return g.writer.Write(b)
}

// Only compress content types that benefit from it
func shouldCompress(contentType string) bool {
    compressible := []string{
        "application/json",
        "text/",
        "application/javascript",
        "application/xml",
    }
    for _, prefix := range compressible {
        if strings.HasPrefix(contentType, prefix) {
            return true
        }
    }
    return false
}

func GzipMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        if !strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
            next.ServeHTTP(w, r)
            return
        }
        gz, err := gzip.NewWriterLevel(w, gzip.BestSpeed) // BestSpeed for APIs
        if err != nil {
            next.ServeHTTP(w, r)
            return
        }
        defer gz.Close()
        w.Header().Set("Content-Encoding", "gzip")
        w.Header().Del("Content-Length") // Length changes after compression
        next.ServeHTTP(&gzipResponseWriter{ResponseWriter: w, writer: gz}, r)
    })
}
```

## Production Example

A data pipeline archived ~500GB of JSON event logs per day to object storage. The first version stored raw JSON. Storage costs were growing faster than event volume because each event's JSON structure had significant overhead — field names repeated in every record.

The optimization had three parts:

1. Compress with zstd instead of gzip (better ratio and faster for this workload)
2. Use a columnar-friendly batch format (write all events of the same type together, improving LZ77's window hit rate)
3. Choose compression level based on the access pattern (hot data: fast compression; archive data: maximum compression)

```go
import "github.com/klauspost/compress/zstd"

// Zstd encoder/decoder — faster than gzip, better ratio for most data
type EventArchiver struct {
    encoder *zstd.Encoder
    decoder *zstd.Decoder
}

func NewEventArchiver() (*EventArchiver, error) {
    // SpeedBetterCompression balances ratio vs CPU for archive workloads
    enc, err := zstd.NewWriter(nil, zstd.WithEncoderLevel(zstd.SpeedBetterCompression))
    if err != nil {
        return nil, err
    }
    dec, err := zstd.NewReader(nil)
    if err != nil {
        return nil, err
    }
    return &EventArchiver{encoder: enc, decoder: dec}, nil
}

func (a *EventArchiver) CompressBatch(events [][]byte) []byte {
    // Concatenate all events — repeated structure across events
    // helps LZ77 find longer matches
    var combined []byte
    for _, e := range events {
        combined = append(combined, e...)
        combined = append(combined, '\n')
    }
    return a.encoder.EncodeAll(combined, nil)
}

// Estimate whether data is worth compressing before spending CPU on it
func isCompressibleData(sample []byte) bool {
    if len(sample) < 256 {
        return true // small data: just compress, overhead is minimal
    }
    e := entropy(sample)
    // If entropy > 7.5 bits/byte, the data is likely already compressed or random
    return e < 7.5
}
```

Results: zstd compressed the JSON events to ~12% of original size (vs gzip's ~18%). Batching similar events improved the ratio further because the compressor's sliding window could find matches across event boundaries. Annual storage cost dropped by $180,000.

The access pattern insight: hot data (last 7 days) was compressed with `SpeedDefault` (faster decompression for queries). Archive data (older) used `SpeedBetterCompression`. The ratio difference was 15–20%, and queries on hot data were 3x faster to decompress.

## The Tradeoffs

Compression trades CPU for storage/bandwidth. For latency-sensitive paths, the CPU cost of compression can exceed the time saved by smaller payloads. Measure: at what payload size does gzip compression help vs. hurt total response time?

A rough rule: for small payloads (< 1KB), compression overhead often exceeds the savings. For large payloads (> 10KB), compression almost always wins. The 1–10KB range depends on your hardware and compression level.

Compression level matters. gzip level 1 (BestSpeed) is 5–10x faster than level 9 (BestCompression) with typically only 10–20% worse ratio. For real-time APIs, level 1 is usually the right choice. For archival storage, level 9 or zstd's maximum pays off over months of reduced storage costs.

Dictionary compression (zstd trained dictionaries) can dramatically improve ratios for small, similar payloads (like JSON API responses). By training a dictionary on a corpus of typical responses, the compressor can reference common patterns without encoding them from scratch. Ratios of 10:1 on 500-byte JSON payloads are achievable, compared to 2:1 with standard zstd.

## Key Takeaway

Compression works by exploiting two types of redundancy: statistical (Huffman) and repetition (LZ77). Entropy tells you the theoretical limit — data close to maximum entropy (random or already compressed) cannot be compressed further. In production, compress JSON and text aggressively (gzip or zstd), never double-compress already-compressed data, and choose compression level based on whether you are optimizing for speed (APIs) or ratio (archive storage). Understanding the fundamentals lets you make these decisions with confidence rather than guessing.

---

← [Lesson 11: String Algorithms](/post/fundamentals/algo-string/) | [Course Index](/post/fundamentals/) | Next: [Lesson 13: Cryptographic Primitives](/post/fundamentals/algo-crypto/) →

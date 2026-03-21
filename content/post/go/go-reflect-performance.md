---
title: 'Lesson 2: Performance Costs — reflect.ValueOf is not free'
author: Atharva Pandey
series: "Go Reflection & Metaprogramming"
lesson: 2
keywords:
  - Go
  - golang
  - reflection
  - performance
  - benchmarks
  - reflect.ValueOf
  - optimization
tags:
  - Go tutorial
  - golang
  - reflection
date: '2024-08-15T00:00:00.000Z'
---

Every time I see reflection in a hot path, I know there is a performance conversation waiting to happen. Reflection in Go is not arbitrarily slow — it is predictably and measurably slow in specific ways. Understanding those costs, benchmarking them in your context, and knowing the standard mitigation patterns (primarily caching) lets you use reflection where it belongs without making your application noticeably slower.

The performance story of reflection has two parts: the cost of type inspection (getting a `reflect.Type`, reading fields, checking kinds) and the cost of value operations (getting a `reflect.Value`, reading or setting field values, calling methods). Type inspection is expensive the first time and cheap if cached. Value operations are expensive every time and there is no way to avoid that cost except to reduce how often you do them.

## The Problem

The classic performance mistake is running reflection inside a hot loop without caching:

```go
// WRONG — reflects on the struct type on every single call
func marshalRecord(record interface{}) (map[string]interface{}, error) {
    v := reflect.ValueOf(record)
    if v.Kind() == reflect.Ptr {
        v = v.Elem()
    }
    t := v.Type()

    result := make(map[string]interface{}, t.NumField())
    for i := 0; i < t.NumField(); i++ {
        field := t.Field(i)
        tag := field.Tag.Get("json")
        if tag == "-" {
            continue
        }
        name := strings.Split(tag, ",")[0]
        if name == "" {
            name = field.Name
        }
        result[name] = v.Field(i).Interface()
    }
    return result, nil
}
```

This function re-derives the type, re-reads all field tags, and re-parses all tag names on every call, even when it processes the same struct type thousands of times. For a struct with ten fields, each call does at least ten `Field()` calls plus ten tag string parses.

Let me show what the actual benchmark numbers look like:

```go
// benchmark_test.go
type TestRecord struct {
    ID        int    `json:"id"`
    Name      string `json:"name"`
    Email     string `json:"email"`
    CreatedAt string `json:"created_at"`
}

var record = TestRecord{1, "Atharva", "a@example.com", "2024-01-01"}

func BenchmarkMarshalNaive(b *testing.B) {
    for i := 0; i < b.N; i++ {
        marshalRecord(record)
    }
}

// Typical result: ~800ns/op with significant allocation
// BenchmarkMarshalNaive-8   1500000   801 ns/op   320 B/op   12 allocs/op
```

## The Idiomatic Way

The mitigation is to cache the expensive type inspection and reuse it:

```go
// RIGHT — reflect on the type once, cache the field layout
type fieldInfo struct {
    index int
    name  string
}

var fieldCache sync.Map // map[reflect.Type][]fieldInfo

func getFieldInfo(t reflect.Type) []fieldInfo {
    if t.Kind() == reflect.Ptr {
        t = t.Elem()
    }
    if cached, ok := fieldCache.Load(t); ok {
        return cached.([]fieldInfo)
    }

    fields := make([]fieldInfo, 0, t.NumField())
    for i := 0; i < t.NumField(); i++ {
        f := t.Field(i)
        tag := f.Tag.Get("json")
        if tag == "-" {
            continue
        }
        name := strings.Split(tag, ",")[0]
        if name == "" {
            name = f.Name
        }
        fields = append(fields, fieldInfo{index: i, name: name})
    }

    fieldCache.Store(t, fields)
    return fields
}

func marshalRecordCached(record interface{}) (map[string]interface{}, error) {
    v := reflect.ValueOf(record)
    if v.Kind() == reflect.Ptr {
        v = v.Elem()
    }
    // Type inspection is cached — only value operations happen per call
    fields := getFieldInfo(v.Type())

    result := make(map[string]interface{}, len(fields))
    for _, f := range fields {
        result[f.name] = v.Field(f.index).Interface()
    }
    return result, nil
}
```

The benchmark comparison is significant:

```go
func BenchmarkMarshalCached(b *testing.B) {
    // First call populates the cache
    marshalRecordCached(record)
    b.ResetTimer()

    for i := 0; i < b.N; i++ {
        marshalRecordCached(record)
    }
}

// BenchmarkMarshalCached-8   4000000   298 ns/op   192 B/op   5 allocs/op
// ~2.7x faster, fewer allocations
```

The remaining overhead is `v.Field(i).Interface()` — reading each field value through the reflect machinery. That cost cannot be avoided when you need to read field values dynamically. But it can be further reduced with `unsafe.Pointer` for extremely hot paths (the approach `encoding/json`'s fast path uses), at the cost of much more complex code.

For the truly critical path, the right answer is often to avoid runtime reflection entirely and generate code instead:

```go
// Generated code for TestRecord — zero reflection overhead
func marshalTestRecord(r TestRecord) map[string]interface{} {
    return map[string]interface{}{
        "id":         r.ID,
        "name":       r.Name,
        "email":      r.Email,
        "created_at": r.CreatedAt,
    }
}

// BenchmarkMarshalGenerated-8   20000000   58 ns/op   128 B/op   1 allocs/op
// ~14x faster than naive reflection, ~5x faster than cached reflection
```

Code generation is covered in the final lesson of this course. The point here is that there is a performance spectrum: naive reflection (worst), cached reflection (acceptable for most cases), and generated code (best, at the cost of tooling complexity).

## In The Wild

A data export service I worked on used reflection to serialize database rows to CSV. It was fast enough in development, where export jobs ran infrequently. In production, export jobs ran constantly, and profiling revealed that `reflect.ValueOf` and `Field.Interface()` were consuming over 40% of CPU time on the export worker nodes.

The investigation started with `pprof`:

```bash
go tool pprof -http=:6060 http://worker:6061/debug/pprof/profile?seconds=30
```

The flame graph showed `reflect.(*Value).Field` and `reflect.valueInterface` dominating the hot path. The fix was two-pronged: add caching for the field layout (immediate 3x speedup) and generate typed marshalers for the ten most common export types (additional 4x speedup for those types).

```go
// Before caching: profiler showed ~42% time in reflect operations
// After caching:  profiler showed ~15% time in reflect operations
// After codegen for top types: profiler showed ~4% time in reflect operations
```

The remaining 4% was for user-defined custom export types that the code generator did not know about — those still used cached reflection, which was acceptable for their lower volume.

## The Gotchas

**`sync.Map` vs. `map` with `sync.RWMutex` for caches.** `sync.Map` is optimized for read-heavy workloads where keys are written once and read many times — exactly the pattern for type caches. For caches where entries are frequently added and removed, a `map` with `sync.RWMutex` may be faster.

**`reflect.Value.Interface()` allocates.** Every call to `.Interface()` converts a `reflect.Value` to an `interface{}`, which usually involves a heap allocation. If you are doing this inside a tight loop, profile to see whether the allocation is the bottleneck, and consider using `unsafe` for the innermost operations if it is.

**Reflection ignores inlining.** The Go compiler inlines small functions aggressively, which is a major source of performance in normal Go code. Reflect calls are not inlined. The gap between reflected and direct field access is almost entirely due to this: the compiler can often eliminate the direct access entirely after inlining.

## Key Takeaway

The performance cost of reflection is real but manageable if you respect two rules: cache type inspection results and avoid reflection in the hottest loops of your application. The cache pattern — reflect once per type, store field layouts in a `sync.Map`, reuse on every subsequent call — captures most of the benefit of code generation with a fraction of the complexity. When caching is not enough, code generation is the next step. Profile before optimizing, benchmark your changes, and do not accept reflection overhead on critical paths just because it is "how the framework works" — it is usually possible to push reflection to initialization time and keep hot paths fast.

---

← [Lesson 1: When Reflection Is Justified](/post/go/go-reflect-when-justified) | [Course Index](/series/go-reflection-metaprogramming) | [Next → Lesson 3: Struct Tags](/post/go/go-reflect-struct-tags)

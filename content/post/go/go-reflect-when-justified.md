---
title: 'Lesson 1: When Reflection Is Justified — The last resort that sometimes is the only resort'
author: Atharva Pandey
series: "Go Reflection & Metaprogramming"
lesson: 1
keywords:
  - Go
  - golang
  - reflection
  - reflect
  - metaprogramming
  - when to use reflection
tags:
  - Go tutorial
  - golang
  - reflection
date: '2024-06-28T00:00:00.000Z'
---

Go's `reflect` package carries a reputation: "don't use it unless you have to." That advice is correct but incomplete. Understanding *when* you actually have to use it — and when you are just reaching for it out of habit from dynamic languages — is the difference between reflection that pulls its weight and reflection that creates unmaintainable, slow, panic-prone code that confuses every future reader of the codebase.

Reflection lets Go inspect and manipulate values, types, and struct fields at runtime without static type information. It is the mechanism behind `encoding/json`, `database/sql`'s row scanning, struct validators, ORM frameworks, and dependency injection containers. It is also the mechanism behind a lot of code that should have just used a map or an interface.

## The Problem

The most common unjustified use of reflection is avoiding a type switch:

```go
// WRONG — reflection to do what a type switch does cleanly
func describe(i interface{}) string {
    v := reflect.ValueOf(i)
    switch v.Kind() {
    case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
        return fmt.Sprintf("integer: %d", v.Int())
    case reflect.String:
        return fmt.Sprintf("string: %q", v.String())
    case reflect.Bool:
        return fmt.Sprintf("bool: %v", v.Bool())
    }
    return "unknown"
}

// RIGHT — just use a type switch
func describe(i interface{}) string {
    switch v := i.(type) {
    case int:
        return fmt.Sprintf("integer: %d", v)
    case string:
        return fmt.Sprintf("string: %q", v)
    case bool:
        return fmt.Sprintf("bool: %v", v)
    }
    return "unknown"
}
```

The reflection version is slower, harder to read, and does not even handle all int types correctly. The type switch version is idiomatic Go.

The second unjustified use is generic behavior that generics now handle:

```go
// WRONG — reflection to operate on slices of any type (pre-generics approach)
func Map(slice interface{}, fn interface{}) interface{} {
    sliceVal := reflect.ValueOf(slice)
    fnVal := reflect.ValueOf(fn)
    result := reflect.MakeSlice(sliceVal.Type(), sliceVal.Len(), sliceVal.Len())
    for i := 0; i < sliceVal.Len(); i++ {
        out := fnVal.Call([]reflect.Value{sliceVal.Index(i)})
        result.Index(i).Set(out[0])
    }
    return result.Interface()
}

// RIGHT — use generics
func Map[T, U any](slice []T, fn func(T) U) []U {
    result := make([]U, len(slice))
    for i, v := range slice {
        result[i] = fn(v)
    }
    return result
}
```

The generic version is type-safe, readable, and faster by an order of magnitude.

## The Idiomatic Way

Reflection is justified in three specific situations: working with types you genuinely cannot know at compile time, reading struct field metadata (tags), and implementing frameworks where the caller provides arbitrary structs.

```go
// JUSTIFIED — reading struct tags at startup for ORM field mapping
// (this is what sql scanners, json encoders, etc. do internally)
func mapStructFields(t reflect.Type) map[string]int {
    if t.Kind() == reflect.Ptr {
        t = t.Elem()
    }
    fields := make(map[string]int)
    for i := 0; i < t.NumField(); i++ {
        f := t.Field(i)
        tag := f.Tag.Get("db")
        if tag == "" || tag == "-" {
            continue
        }
        // Parse tag value, ignoring options like `db:"name,omitempty"`
        name := strings.Split(tag, ",")[0]
        fields[name] = i
    }
    return fields
}
```

This is the foundational pattern used by every Go ORM: at startup (or on first use), reflect on the struct type once, build a mapping of column names to field indices, and cache it. All subsequent use of that mapping is fast because the reflection cost is paid once.

```go
// JUSTIFIED — filling arbitrary struct fields from a map of values
// (the core of sql.Scan, json.Unmarshal, etc.)
func fillStruct(dest interface{}, values map[string]interface{}) error {
    v := reflect.ValueOf(dest)
    if v.Kind() != reflect.Ptr || v.IsNil() {
        return errors.New("dest must be a non-nil pointer to a struct")
    }
    v = v.Elem()
    if v.Kind() != reflect.Struct {
        return errors.New("dest must point to a struct")
    }
    t := v.Type()
    for i := 0; i < t.NumField(); i++ {
        field := t.Field(i)
        tag := field.Tag.Get("fill")
        if tag == "" {
            continue
        }
        val, ok := values[tag]
        if !ok {
            continue
        }
        fv := v.Field(i)
        if !fv.CanSet() {
            continue
        }
        fv.Set(reflect.ValueOf(val).Convert(fv.Type()))
    }
    return nil
}
```

This kind of code is not pleasant to write, but there is no alternative when the caller passes an arbitrary struct that you need to populate. `encoding/json`, `database/sql`, form decoders, and config loaders all contain some version of this pattern.

## In The Wild

The reflection-justified work I have done most is building a lightweight test assertion library that provides useful diff output for arbitrary struct types. Without reflection, you can only compare types you know about at compile time. With it, you can recursively compare any two values:

```go
// Simplified deep-equal with path tracking for useful error messages
func deepEqual(path string, a, b reflect.Value) []string {
    if a.Type() != b.Type() {
        return []string{fmt.Sprintf("%s: type mismatch: %v vs %v", path, a.Type(), b.Type())}
    }

    switch a.Kind() {
    case reflect.Struct:
        var diffs []string
        for i := 0; i < a.NumField(); i++ {
            fieldName := a.Type().Field(i).Name
            diffs = append(diffs, deepEqual(path+"."+fieldName, a.Field(i), b.Field(i))...)
        }
        return diffs
    case reflect.Slice:
        if a.Len() != b.Len() {
            return []string{fmt.Sprintf("%s: length mismatch: %d vs %d", path, a.Len(), b.Len())}
        }
        var diffs []string
        for i := 0; i < a.Len(); i++ {
            diffs = append(diffs, deepEqual(fmt.Sprintf("%s[%d]", path, i), a.Index(i), b.Index(i))...)
        }
        return diffs
    default:
        if !reflect.DeepEqual(a.Interface(), b.Interface()) {
            return []string{fmt.Sprintf("%s: %v != %v", path, a.Interface(), b.Interface())}
        }
        return nil
    }
}
```

This is the kind of code that reflection enables and that has no reasonable alternative. You cannot write a general-purpose deep comparison function without it — you would have to special-case every type in your codebase.

## The Gotchas

**Reflection panics at runtime, not compile time.** A wrong type assertion or calling a method on a nil value panics. Every `reflect` operation that can fail should be guarded with kind checks. Compile-time safety is the main thing you are giving up when you use reflection, so be explicit about validating inputs.

**Reflection on unexported fields fails silently or panics.** `v.CanSet()` returns false for unexported fields, and `v.Interface()` panics on unexported fields. Always check before operating.

**Reflection and generics are not the same trade-off.** Generics solve the "operate on slices of any type" problem with full compile-time safety. Reflection solves the "operate on a struct whose fields I don't know" problem at runtime. They address different scenarios. After Go 1.18, many reflection uses for "generic algorithms" should be replaced with actual generics.

## Key Takeaway

Reflection is justified when you are genuinely operating on types that are unknown at compile time — when the caller can pass any struct and you need to inspect its fields, fill its values, or compare it to another struct. It is not justified for avoiding type switches, for generic algorithms (use generics), or for "maybe I'll need this flexibility later." The cost is real: slower execution, runtime panics instead of compile-time errors, and code that is harder to read and debug. When reflection is the right tool, use it carefully, cache its results, document what you expect, and validate inputs before operating on them.

---

[Course Index](/series/go-reflection-metaprogramming) | [Next → Lesson 2: Performance Costs](/post/go/go-reflect-performance)

---
title: 'Lesson 4: Building a Serializer — encoding/json under the hood'
author: Atharva Pandey
series: "Go Reflection & Metaprogramming"
lesson: 4
keywords:
  - Go
  - golang
  - reflection
  - serializer
  - encoding/json
  - marshaling
  - unmarshaling
tags:
  - Go tutorial
  - golang
  - reflection
date: '2024-11-28T00:00:00.000Z'
---

`encoding/json` is Go's most-used package and one of its most instructive implementations. Under the hood it is almost entirely reflection: it inspects struct field types, reads `json` tags, handles pointer dereferences, recurses into nested structs, and handles special cases like `time.Time` and `json.Marshaler` interface implementations. Building a simplified version of a struct serializer from scratch is the best way to understand both how reflection works in practice and why `encoding/json` makes the design choices it does.

This lesson builds a minimal but functional struct-to-JSON serializer — not for production use, but as a learning vehicle. The concepts apply directly to building any tag-driven framework: config loaders, form parsers, database row mappers, and custom serialization formats.

## The Problem

The naive approach to struct serialization is fine for types you know:

```go
// WRONG — works only for types known at compile time
type User struct {
    ID   int
    Name string
}

func marshalUser(u User) string {
    return fmt.Sprintf(`{"id":%d,"name":%q}`, u.ID, u.Name)
}
```

This does not generalize. You cannot write `marshalUser` for a type you do not know. When your API needs to serialize ten different response types, you have ten hand-written marshaling functions with no shared logic, and adding a field to any struct means updating its marshaling function manually.

The second problem is the "any interface" approach that loses all type information:

```go
// WRONG — accepts anything but provides no guarantees
func marshal(v interface{}) ([]byte, error) {
    // now what? we know nothing about v's structure
}
```

This is where reflection enters: it lets you recover type information at runtime and use it to drive the serialization.

## The Idiomatic Way

A working serializer proceeds in three steps: reflect on the value to determine its kind, dispatch to a kind-specific serializer, and handle special cases (pointers, nil values, custom marshalers).

```go
package simplejson

import (
    "bytes"
    "fmt"
    "reflect"
    "strings"
    "sync"
)

// structFields is a cached layout for a struct type.
type structField struct {
    index     int
    name      string
    omitempty bool
}

var cache sync.Map // map[reflect.Type][]structField

func getStructFields(t reflect.Type) []structField {
    if v, ok := cache.Load(t); ok {
        return v.([]structField)
    }
    fields := buildFieldList(t)
    cache.Store(t, fields)
    return fields
}

func buildFieldList(t reflect.Type) []structField {
    var fields []structField
    for i := 0; i < t.NumField(); i++ {
        f := t.Field(i)
        if !f.IsExported() {
            continue
        }
        tag := f.Tag.Get("json")
        if tag == "-" {
            continue
        }
        name, options, _ := strings.Cut(tag, ",")
        if name == "" {
            name = f.Name
        }
        fields = append(fields, structField{
            index:     i,
            name:      name,
            omitempty: strings.Contains(options, "omitempty"),
        })
    }
    return fields
}
```

The marshaling function dispatches on kind:

```go
func Marshal(v interface{}) ([]byte, error) {
    if v == nil {
        return []byte("null"), nil
    }
    var buf bytes.Buffer
    if err := marshalValue(&buf, reflect.ValueOf(v)); err != nil {
        return nil, err
    }
    return buf.Bytes(), nil
}

func marshalValue(buf *bytes.Buffer, v reflect.Value) error {
    // Dereference pointers
    for v.Kind() == reflect.Ptr {
        if v.IsNil() {
            buf.WriteString("null")
            return nil
        }
        v = v.Elem()
    }

    switch v.Kind() {
    case reflect.Struct:
        return marshalStruct(buf, v)
    case reflect.Slice, reflect.Array:
        return marshalSlice(buf, v)
    case reflect.Map:
        return marshalMap(buf, v)
    case reflect.String:
        buf.WriteString(fmt.Sprintf("%q", v.String()))
    case reflect.Int, reflect.Int8, reflect.Int16, reflect.Int32, reflect.Int64:
        buf.WriteString(fmt.Sprintf("%d", v.Int()))
    case reflect.Float32, reflect.Float64:
        buf.WriteString(fmt.Sprintf("%g", v.Float()))
    case reflect.Bool:
        if v.Bool() {
            buf.WriteString("true")
        } else {
            buf.WriteString("false")
        }
    case reflect.Interface:
        if v.IsNil() {
            buf.WriteString("null")
            return nil
        }
        return marshalValue(buf, v.Elem())
    default:
        return fmt.Errorf("unsupported kind: %s", v.Kind())
    }
    return nil
}
```

The struct marshaling uses the cached field layout:

```go
func marshalStruct(buf *bytes.Buffer, v reflect.Value) error {
    t := v.Type()
    fields := getStructFields(t)

    buf.WriteByte('{')
    first := true
    for _, f := range fields {
        fv := v.Field(f.index)

        // omitempty: skip zero values
        if f.omitempty && fv.IsZero() {
            continue
        }

        if !first {
            buf.WriteByte(',')
        }
        first = false

        buf.WriteString(fmt.Sprintf("%q:", f.name))
        if err := marshalValue(buf, fv); err != nil {
            return fmt.Errorf("field %s: %w", f.name, err)
        }
    }
    buf.WriteByte('}')
    return nil
}

func marshalSlice(buf *bytes.Buffer, v reflect.Value) error {
    if v.IsNil() {
        buf.WriteString("null")
        return nil
    }
    buf.WriteByte('[')
    for i := 0; i < v.Len(); i++ {
        if i > 0 {
            buf.WriteByte(',')
        }
        if err := marshalValue(buf, v.Index(i)); err != nil {
            return err
        }
    }
    buf.WriteByte(']')
    return nil
}
```

## In The Wild

After building this serializer, the design choices in `encoding/json` start to make sense. The real `encoding/json` package caches encoders per type — not just field lists, but full function values that encode each type without re-checking the kind each time. This is the "type-specific codec" pattern:

```go
// How encoding/json actually works (simplified):
// For each type, it builds an encoder function once and caches it.
type encoderFunc func(e *encodeState, v reflect.Value, opts encOpts)

var encoderCache sync.Map // map[reflect.Type]encoderFunc

func typeEncoder(t reflect.Type) encoderFunc {
    if fi, ok := encoderCache.Load(t); ok {
        return fi.(encoderFunc)
    }
    // Build the encoder based on t.Kind()
    f := newTypeEncoder(t)
    encoderCache.Store(t, f)
    return f
}
```

By caching a function (not just a field list), `encoding/json` avoids the kind switch on every marshal call. The first call builds the optimal encoder for that type; subsequent calls call it directly. This is why `encoding/json` is much faster than a naive reflection loop even though it is "just reflection."

I applied the same pattern when building a custom binary serializer for an internal RPC system. The first call to `Encode(v)` inspected `v`'s type, built an encoder function, and cached it. Every subsequent call for that type was a direct function call that avoided the reflect dispatch:

```go
type encodeFn func(buf *bytes.Buffer, v reflect.Value) error

var fnCache sync.Map

func getEncoder(t reflect.Type) encodeFn {
    if fn, ok := fnCache.Load(t); ok {
        return fn.(encodeFn)
    }
    fn := buildEncoder(t) // does the kind-switch and field-analysis once
    fnCache.Store(t, fn)
    return fn
}
```

Throughput improved 4x over the naive version because the per-call cost was reduced from "reflect on the struct" to "call a cached function."

## The Gotchas

**Circular references cause infinite recursion.** If struct A contains a pointer to struct B, which contains a pointer back to A, a naive serializer will recurse until it stack-overflows. `encoding/json` tracks visited pointer addresses to detect cycles and returns an error. Always handle this in production serializers.

**Interface fields need special handling.** When a field is of interface type, `v.Field(i).Kind()` returns `reflect.Interface`, not the kind of the underlying value. You must call `v.Field(i).Elem()` to get the underlying value — but only after checking `IsNil()`, because an unset interface value has no element to `Elem()` from.

**`reflect.Value.IsZero()` is your friend for omitempty.** It correctly handles zero values for all kinds — zero int, empty string, nil pointer, nil slice, nil map. Before Go 1.13, you had to write kind-specific zero checks manually.

## Key Takeaway

Building a serializer from scratch is the best way to internalize reflection because serializers use every significant reflection operation: kind dispatch, field enumeration, tag parsing, pointer dereferencing, nil checks, and recursive descent into nested types. The implementation patterns here — cache the field layout, cache encoder functions per type, handle pointers and nil before dispatching on kind — are exactly what `encoding/json` does internally. Once you understand these patterns, you can both use the standard library more effectively (knowing why custom `MarshalJSON` implementations are called) and build your own frameworks that follow the same design principles.

---

← [Lesson 3: Struct Tags](/post/go/go-reflect-struct-tags) | [Course Index](/series/go-reflection-metaprogramming) | [Next → Lesson 5: Validation Frameworks](/post/go/go-reflect-validation)

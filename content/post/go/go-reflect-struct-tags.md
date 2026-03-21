---
title: 'Lesson 3: Struct Tags — Metadata your compiler ignores but your framework reads'
author: Atharva Pandey
series: "Go Reflection & Metaprogramming"
lesson: 3
keywords:
  - Go
  - golang
  - reflection
  - struct tags
  - metadata
  - json tags
  - db tags
tags:
  - Go tutorial
  - golang
  - reflection
date: '2024-10-08T00:00:00.000Z'
---

Struct tags are one of Go's most practically useful metaprogramming tools and one of its least formally documented features. The syntax is simple — a raw string literal after the field type in a struct — but the convention built on top of it powers nearly every serialization library, ORM, validator, and configuration loader in the ecosystem. Understanding how tags work at the reflection level demystifies why `encoding/json` knows to use `omitempty`, why `database/sql` scanners can map column names to struct fields, and how you can build your own tag-driven behavior.

Struct tags are not special syntax from the compiler's perspective. They are raw string literals attached to struct fields that the compiler stores in type information and the `reflect` package makes readable at runtime. The compiler never reads them. It is entirely convention — a string format that frameworks agree on and parse themselves.

## The Problem

Without struct tags, any system that needs to map a Go struct to an external representation has two choices: use the Go field names directly (which forces callers to use Go naming conventions in their JSON, database columns, or config files) or require explicit registration of each field (which is tedious and error-prone at scale):

```go
// WITHOUT tags — JSON field names are Go field names
type User struct {
    UserID    int
    FirstName string
    CreatedAt time.Time
}
// Produces: {"UserID": 1, "FirstName": "Atharva", "CreatedAt": "..."}
// Not great for an API that should use camelCase or snake_case
```

Requiring explicit registration is even worse:

```go
// WITHOUT tags — manual registration, tedious and fragile
type User struct {
    UserID    int
    FirstName string
    CreatedAt time.Time
}

func init() {
    json.RegisterField(User{}, "UserID", "user_id")
    json.RegisterField(User{}, "FirstName", "first_name")
    // ...
}
```

This is maintenance overhead that scales linearly with field count and breaks silently when fields are renamed.

## The Idiomatic Way

Struct tags solve both problems: they keep metadata co-located with the field it describes, and they are parseable by any framework using `reflect.StructField.Tag.Get(key)`.

```go
// RIGHT — tags control serialization, DB mapping, and validation in one declaration
type User struct {
    UserID    int       `json:"user_id"           db:"user_id"    validate:"required"`
    FirstName string    `json:"first_name"         db:"first_name" validate:"required,min=1,max=100"`
    LastName  string    `json:"last_name"          db:"last_name"  validate:"required"`
    Email     string    `json:"email"              db:"email"      validate:"required,email"`
    CreatedAt time.Time `json:"created_at"         db:"created_at"`
    Password  string    `json:"-"                  db:"password_hash"`
}
```

Reading tags at runtime is a few lines of reflection:

```go
func printFieldTags(v interface{}) {
    t := reflect.TypeOf(v)
    if t.Kind() == reflect.Ptr {
        t = t.Elem()
    }
    for i := 0; i < t.NumField(); i++ {
        field := t.Field(i)
        jsonTag := field.Tag.Get("json")
        dbTag := field.Tag.Get("db")
        validateTag := field.Tag.Get("validate")
        fmt.Printf("Field: %-12s json:%-20s db:%-15s validate:%s\n",
            field.Name, jsonTag, dbTag, validateTag)
    }
}
```

Building a custom tag parser follows the same pattern the standard library uses. Here is a minimal implementation of tag parsing that handles the `name,option1,option2` format:

```go
// tagOptions mirrors how encoding/json parses its tags
type tagOptions struct {
    name      string
    omitempty bool
    inline    bool
}

func parseTag(tag string) tagOptions {
    if tag == "" {
        return tagOptions{}
    }
    parts := strings.Split(tag, ",")
    opts := tagOptions{name: parts[0]}
    for _, opt := range parts[1:] {
        switch opt {
        case "omitempty":
            opts.omitempty = true
        case "inline":
            opts.inline = true
        }
    }
    return opts
}

// Usage:
// field.Tag.Get("json") might return "user_id,omitempty"
// parseTag("user_id,omitempty") returns tagOptions{name:"user_id", omitempty:true}
```

## In The Wild

The most powerful use of struct tags I have built was a configuration loader that populated a struct from both environment variables and a YAML file, with validation, using tags to declare all three sources in one place:

```go
type ServerConfig struct {
    Host     string `env:"SERVER_HOST"     yaml:"host"     validate:"required"`
    Port     int    `env:"SERVER_PORT"     yaml:"port"     validate:"required,min=1,max=65535"`
    TLSCert  string `env:"TLS_CERT_PATH"   yaml:"tls_cert" validate:"omitempty,filepath"`
    TLSKey   string `env:"TLS_KEY_PATH"    yaml:"tls_key"  validate:"omitempty,filepath"`
    Timeout  string `env:"SERVER_TIMEOUT"  yaml:"timeout"  validate:"omitempty,duration"`
}
```

The loader read the `yaml` tag to populate from the file, then overrode with the `env` tag value if the environment variable was set, then ran the `validate` tag rules. All three behaviors were driven by the same struct declaration:

```go
func loadConfig(path string, dest interface{}) error {
    // 1. Load from YAML file using yaml tags
    if err := loadYAML(path, dest); err != nil {
        return err
    }
    // 2. Override with environment variables using env tags
    if err := overrideFromEnv(dest); err != nil {
        return err
    }
    // 3. Validate using validate tags
    return validate(dest)
}

func overrideFromEnv(dest interface{}) error {
    v := reflect.ValueOf(dest).Elem()
    t := v.Type()
    for i := 0; i < t.NumField(); i++ {
        field := t.Field(i)
        envKey := field.Tag.Get("env")
        if envKey == "" {
            continue
        }
        envVal := os.Getenv(envKey)
        if envVal == "" {
            continue
        }
        fv := v.Field(i)
        if !fv.CanSet() {
            continue
        }
        // Convert string env value to the field's actual type
        converted, err := convertString(envVal, field.Type)
        if err != nil {
            return fmt.Errorf("field %s: %w", field.Name, err)
        }
        fv.Set(converted)
    }
    return nil
}
```

The entire configuration system was about 150 lines. No external config library. No code generation. The struct tags were the documentation and the specification simultaneously.

## The Gotchas

**Tag syntax errors are not compile-time errors.** If you write `json:"name omitempty"` (space instead of comma), the compiler accepts it but `encoding/json` sees the entire string `"name omitempty"` as the field name. Use `go vet` — it has a `structtag` check that catches malformed tags.

**Tag key names are case-sensitive and conventionally lowercase.** `json`, `yaml`, `db`, `validate`, `env` are all lowercase. Using `JSON` or `DB` will silently fail to match because `tag.Get("json")` is an exact string match.

**Tags do not survive JSON round-trips.** Tags are compile-time metadata. If you serialize a struct to JSON and deserialize it back, the tags are not part of the JSON — they are properties of the Go type. A dynamically created struct (using `reflect.StructOf`) can have tags, but creating them requires constructing `reflect.StructTag` values manually.

**`reflect.StructField.IsExported()` before operating.** Unexported fields cannot have their values read or set through reflection. Always check `field.IsExported()` before attempting to access the value of a tagged field.

## Key Takeaway

Struct tags are Go's mechanism for attaching declarative metadata to type fields in a way that frameworks can read without you having to write registration code. The convention is simple: key-value pairs separated by spaces, each value a quoted string, parsed by `reflect.StructField.Tag.Get`. Used well, tags let you declare serialization format, database mapping, validation rules, and environment variable bindings all in one struct definition that is easy to read, easy to update, and impossible to get out of sync with the field it annotates. The frameworks that power most of Go's ecosystem — `encoding/json`, `sqlx`, `validator`, `viper` — all rely on this mechanism. Understanding it at the reflection level lets you build your own tools with the same ergonomics.

---

← [Lesson 2: Performance Costs](/post/go/go-reflect-performance) | [Course Index](/series/go-reflection-metaprogramming) | [Next → Lesson 4: Building a Serializer](/post/go/go-reflect-serializer)

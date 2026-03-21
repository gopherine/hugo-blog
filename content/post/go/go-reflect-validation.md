---
title: 'Lesson 5: Validation Frameworks — Reflect once, validate everywhere'
author: Atharva Pandey
series: "Go Reflection & Metaprogramming"
lesson: 5
keywords:
  - Go
  - golang
  - reflection
  - validation
  - struct validation
  - go-playground/validator
  - input validation
tags:
  - Go tutorial
  - golang
  - reflection
date: '2025-02-10T00:00:00.000Z'
---

Input validation is one of those problems that looks solved until you realize you are writing the same type of check — not nil, min length, valid email format, required when other field is set — dozens of times across dozens of handlers. Centralizing these rules without reflection means either a massive switch statement or hand-writing a validation function for every struct in your application. Reflection makes it possible to declare validation rules once, at the struct, and enforce them automatically everywhere that struct is validated.

This is the exact design behind `github.com/go-playground/validator`, which is used in millions of Go services. Understanding how it works — and how to build a minimal version of the same pattern — makes you both a better user of the library and better equipped to write your own constraint systems.

## The Problem

Hand-written validation duplicates constraint logic across every function that uses a type:

```go
// WRONG — validation is duplicated and disconnected from the type definition
type CreateUserRequest struct {
    Name  string
    Email string
    Age   int
}

func handleCreateUser(req CreateUserRequest) error {
    if req.Name == "" {
        return errors.New("name is required")
    }
    if len(req.Name) > 100 {
        return errors.New("name must be 100 characters or fewer")
    }
    if req.Email == "" {
        return errors.New("email is required")
    }
    if !isValidEmail(req.Email) {
        return errors.New("email is not valid")
    }
    if req.Age < 13 {
        return errors.New("age must be at least 13")
    }
    if req.Age > 120 {
        return errors.New("age must be 120 or fewer")
    }
    return createUser(req)
}
```

Every handler that receives a `CreateUserRequest` duplicates this logic or calls the same imperative validation function. When the validation rules change — "email must be from an approved domain" — you update multiple places. When you add a field, you remember (or forget) to add its validation.

## The Idiomatic Way

Tag-driven validation co-locates the rules with the type:

```go
// RIGHT — rules live with the type, validation is centralized
type CreateUserRequest struct {
    Name  string `validate:"required,min=1,max=100"`
    Email string `validate:"required,email"`
    Age   int    `validate:"required,min=13,max=120"`
}

// One line validates the entire struct against all declared rules
if err := validate.Struct(req); err != nil {
    return err
}
```

Building a minimal validator from scratch shows how the pattern works:

```go
package minivalidate

import (
    "fmt"
    "reflect"
    "strconv"
    "strings"
)

type ValidationError struct {
    Field   string
    Tag     string
    Message string
}

func (e *ValidationError) Error() string {
    return fmt.Sprintf("field '%s' failed '%s': %s", e.Field, e.Tag, e.Message)
}

type ValidationErrors []*ValidationError

func (errs ValidationErrors) Error() string {
    msgs := make([]string, len(errs))
    for i, e := range errs {
        msgs[i] = e.Error()
    }
    return strings.Join(msgs, "; ")
}

func Struct(s interface{}) error {
    v := reflect.ValueOf(s)
    if v.Kind() == reflect.Ptr {
        if v.IsNil() {
            return fmt.Errorf("cannot validate nil pointer")
        }
        v = v.Elem()
    }
    if v.Kind() != reflect.Struct {
        return fmt.Errorf("expected struct, got %s", v.Kind())
    }

    t := v.Type()
    var errs ValidationErrors

    for i := 0; i < t.NumField(); i++ {
        field := t.Field(i)
        if !field.IsExported() {
            continue
        }
        tag := field.Tag.Get("validate")
        if tag == "" || tag == "-" {
            continue
        }

        fv := v.Field(i)
        for _, rule := range strings.Split(tag, ",") {
            if err := applyRule(rule, field.Name, fv); err != nil {
                errs = append(errs, err)
            }
        }
    }

    if len(errs) > 0 {
        return errs
    }
    return nil
}

func applyRule(rule, fieldName string, v reflect.Value) *ValidationError {
    // Split "min=5" into ["min", "5"]
    parts := strings.SplitN(rule, "=", 2)
    tag := parts[0]
    var param string
    if len(parts) == 2 {
        param = parts[1]
    }

    switch tag {
    case "required":
        if v.IsZero() {
            return &ValidationError{Field: fieldName, Tag: "required", Message: "is required"}
        }
    case "min":
        n, _ := strconv.ParseInt(param, 10, 64)
        switch v.Kind() {
        case reflect.String:
            if int64(len(v.String())) < n {
                return &ValidationError{Field: fieldName, Tag: "min",
                    Message: fmt.Sprintf("must be at least %d characters", n)}
            }
        case reflect.Int, reflect.Int64:
            if v.Int() < n {
                return &ValidationError{Field: fieldName, Tag: "min",
                    Message: fmt.Sprintf("must be at least %d", n)}
            }
        }
    case "max":
        n, _ := strconv.ParseInt(param, 10, 64)
        switch v.Kind() {
        case reflect.String:
            if int64(len(v.String())) > n {
                return &ValidationError{Field: fieldName, Tag: "max",
                    Message: fmt.Sprintf("must be at most %d characters", n)}
            }
        case reflect.Int, reflect.Int64:
            if v.Int() > n {
                return &ValidationError{Field: fieldName, Tag: "max",
                    Message: fmt.Sprintf("must be at most %d", n)}
            }
        }
    case "email":
        if !strings.Contains(v.String(), "@") {
            return &ValidationError{Field: fieldName, Tag: "email", Message: "must be a valid email"}
        }
    }
    return nil
}
```

This validator is about 90 lines and handles the most common rules. Production libraries like `go-playground/validator` add cross-field validation, custom rule registration, locale-specific messages, and nested struct validation — but the core mechanism is exactly this reflection loop over struct fields, tag parsing, and per-field rule application.

## In The Wild

The integration of `go-playground/validator` with HTTP handlers is where tag-driven validation pays off most visibly. The pattern I use across services is a middleware-friendly `BindAndValidate` helper:

```go
var validate = validator.New(validator.WithRequiredStructEnabled())

func init() {
    // Register a custom tag name function to use json tag names in error messages
    validate.RegisterTagNameFunc(func(fld reflect.StructField) string {
        name := strings.SplitN(fld.Tag.Get("json"), ",", 2)[0]
        if name == "-" {
            return ""
        }
        return name
    })
}

func BindAndValidate(r *http.Request, dest interface{}) error {
    if err := json.NewDecoder(r.Body).Decode(dest); err != nil {
        return fmt.Errorf("decoding request body: %w", err)
    }
    if err := validate.Struct(dest); err != nil {
        var validationErrs validator.ValidationErrors
        if errors.As(err, &validationErrs) {
            // Convert to a map of field -> message for API responses
            errMap := make(map[string]string, len(validationErrs))
            for _, e := range validationErrs {
                errMap[e.Field()] = e.Translate(nil)
            }
            return &ValidationError{Fields: errMap}
        }
        return err
    }
    return nil
}
```

Every handler becomes one call:

```go
func handleCreateUser(w http.ResponseWriter, r *http.Request) {
    var req CreateUserRequest
    if err := BindAndValidate(r, &req); err != nil {
        writeValidationError(w, err)
        return
    }
    // req is guaranteed to be valid here
    user, err := svc.CreateUser(r.Context(), req)
    // ...
}
```

The validation rules are defined once on `CreateUserRequest`. They are enforced in every handler that calls `BindAndValidate`. When the product team says "email must be from an approved domain," the change is one new custom rule registration and one tag update — not a hunt through every handler.

## The Gotchas

**Validator error messages reference Go field names by default.** Use `RegisterTagNameFunc` to map to JSON names (as shown above) so your API error messages say `"email"` instead of `"Email"`. This is one of the first things to configure when adding `go-playground/validator` to an API.

**Cross-field validation needs custom validators or `dive`.** The `validate:"required_with=OtherField"` family of rules handles the "required when another field is set" case. For truly custom logic, register a custom validation function with `validate.RegisterValidation`. This covers cases like "end date must be after start date."

**Validate at the boundary, not deep in business logic.** Validation belongs at the HTTP handler level (or the CLI parsing level), not inside domain functions. Domain functions should receive already-validated inputs and panic or return programmer-facing errors when they receive impossible inputs. The validation layer is about user input; domain errors are about logic.

## Key Takeaway

Tag-driven validation is one of the most cost-effective applications of reflection in Go. The reflect cost happens once per struct type (on the first validation call); every subsequent call works from the cached field and rule list. The payoff is that validation rules live exactly where the type is defined, cannot drift out of sync with it, and are enforced automatically everywhere the type is validated. `go-playground/validator` is the production choice for most services. Understanding how it works — the reflection loop, the tag parsing, the per-rule dispatch — lets you use it confidently, extend it with custom rules, and build similar patterns for your own frameworks.

---

← [Lesson 4: Building a Serializer](/post/go/go-reflect-serializer) | [Course Index](/series/go-reflection-metaprogramming) | [Next → Lesson 6: Avoiding Reflection](/post/go/go-reflect-avoiding)

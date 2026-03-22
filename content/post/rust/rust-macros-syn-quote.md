---
title: "Lesson 10: syn and quote — Parsing and generating tokens"
author: Atharva Pandey
series: "Rust Macros & Metaprogramming"
lesson: 10
keywords: [Rust, rust, macros, syn, quote, proc-macro, token stream, parsing, code generation]
tags: [Rust tutorial, rust, macros, metaprogramming]
date: '2025-02-27T10:20:00.000Z'
---

Every time I write a proc macro without `syn` and `quote`, I regret it within twenty minutes. Raw `TokenStream` manipulation is like writing HTML by concatenating strings — technically possible, practically unbearable. These two crates are the reason Rust's proc macro ecosystem works at all. They handle the two hardest parts — parsing Rust syntax into a usable data structure, and generating valid Rust code from a template — so you can focus on the actual logic of your macro.

## The Two Halves of Every Proc Macro

Every procedural macro does two things:

1. **Parse** the input `TokenStream` into structured data
2. **Generate** an output `TokenStream` from that data

`syn` handles step 1. `quote` handles step 2. And `proc-macro2` bridges them together, providing types that work both in the compiler context and in unit tests.

```
Input TokenStream → [syn parses] → Syntax Tree → [your logic] → Modified Tree → [quote generates] → Output TokenStream
```

## syn: Parsing Rust

### DeriveInput — The Starting Point

For derive macros, `syn::DeriveInput` represents the parsed struct/enum:

```rust
use syn::{parse_macro_input, DeriveInput};

#[proc_macro_derive(MyDerive)]
pub fn my_derive(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);

    // input.ident — the struct/enum name
    // input.generics — generic parameters
    // input.data — the fields/variants
    // input.attrs — attributes on the item
    // input.vis — visibility (pub, pub(crate), etc.)

    todo!()
}
```

### Navigating Data

`DeriveInput.data` is an enum with three variants:

```rust
use syn::{Data, Fields, FieldsNamed, Field};

match &input.data {
    Data::Struct(data_struct) => {
        match &data_struct.fields {
            Fields::Named(FieldsNamed { named, .. }) => {
                // Regular struct: struct Foo { bar: i32 }
                for field in named {
                    let name = field.ident.as_ref().unwrap(); // field name
                    let ty = &field.ty;                        // field type
                    let attrs = &field.attrs;                  // field attributes
                    let vis = &field.vis;                      // field visibility
                }
            }
            Fields::Unnamed(fields) => {
                // Tuple struct: struct Foo(i32, String)
                for (i, field) in fields.unnamed.iter().enumerate() {
                    let ty = &field.ty;
                    // fields accessed by index: self.0, self.1, ...
                }
            }
            Fields::Unit => {
                // Unit struct: struct Foo;
            }
        }
    }
    Data::Enum(data_enum) => {
        for variant in &data_enum.variants {
            let variant_name = &variant.ident;
            // variant.fields — same as struct fields
            // variant.discriminant — explicit discriminant like Foo = 1
        }
    }
    Data::Union(_) => {
        // Unions — rare, usually just error out
    }
}
```

### Parsing Types

`syn::Type` represents any Rust type. The most common variants:

```rust
use syn::Type;

fn analyze_type(ty: &Type) {
    match ty {
        Type::Path(type_path) => {
            // Named types: i32, String, Vec<T>, std::io::Error
            let segments = &type_path.path.segments;
            let last_segment = segments.last().unwrap();
            let type_name = last_segment.ident.to_string();

            // Check for generic arguments
            match &last_segment.arguments {
                syn::PathArguments::None => {
                    // Simple type: i32, String
                }
                syn::PathArguments::AngleBracketed(args) => {
                    // Generic type: Vec<T>, HashMap<K, V>
                    for arg in &args.args {
                        // process generic arguments
                    }
                }
                _ => {}
            }
        }
        Type::Reference(type_ref) => {
            // Reference types: &str, &mut Vec<i32>
            let _mutability = type_ref.mutability;
            let _inner = &type_ref.elem;
        }
        _ => {
            // Many other variants: Tuple, Array, Slice, etc.
        }
    }
}
```

A common task: checking if a type is `Option<T>` and extracting the inner type:

```rust
fn extract_option_inner(ty: &Type) -> Option<&Type> {
    if let Type::Path(type_path) = ty {
        if let Some(segment) = type_path.path.segments.last() {
            if segment.ident == "Option" {
                if let syn::PathArguments::AngleBracketed(args) = &segment.arguments {
                    if let Some(syn::GenericArgument::Type(inner)) = args.args.first() {
                        return Some(inner);
                    }
                }
            }
        }
    }
    None
}
```

This pattern shows up everywhere — in builder macros (to make `Option` fields optional), in serialization macros (to handle `#[serde(skip_serializing_if)]`), and in validation macros (to differentiate required vs. optional fields).

### Parsing Attributes

Field-level attributes like `#[serde(rename = "name")]` or `#[builder(default)]` are stored in each field's `attrs` vector:

```rust
use syn::{Attribute, Meta, Expr, Lit};

fn parse_field_attributes(attrs: &[Attribute]) -> Option<String> {
    for attr in attrs {
        if attr.path().is_ident("my_attr") {
            // Parse the attribute's content
            match &attr.meta {
                Meta::Path(_) => {
                    // #[my_attr] — no arguments
                    return Some("flag".to_string());
                }
                Meta::NameValue(nv) => {
                    // #[my_attr = "value"]
                    if let Expr::Lit(expr_lit) = &nv.value {
                        if let Lit::Str(s) = &expr_lit.lit {
                            return Some(s.value());
                        }
                    }
                }
                Meta::List(list) => {
                    // #[my_attr(key = "value", flag)]
                    // Parse the token stream inside the parentheses
                    let _ = list.tokens.clone(); // process further
                }
            }
        }
    }
    None
}
```

For more structured attribute parsing, `syn` provides the `parse_nested_meta` method:

```rust
fn parse_builder_attr(attr: &Attribute) -> syn::Result<Option<String>> {
    let mut default_value = None;

    attr.parse_nested_meta(|meta| {
        if meta.path.is_ident("default") {
            let value = meta.value()?;
            let s: LitStr = value.parse()?;
            default_value = Some(s.value());
            Ok(())
        } else {
            Err(meta.error("unsupported attribute"))
        }
    })?;

    Ok(default_value)
}
```

### Custom Parse Implementations

For function-like macros, you define your own syntax by implementing `Parse`:

```rust
use syn::parse::{Parse, ParseStream};
use syn::{Ident, Token, LitStr, LitInt};

struct KeyValue {
    key: Ident,
    value: LitStr,
}

impl Parse for KeyValue {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        let key: Ident = input.parse()?;
        input.parse::<Token![:]>()?;   // expect a colon
        let value: LitStr = input.parse()?;
        Ok(KeyValue { key, value })
    }
}
```

The `ParseStream` type gives you methods for inspecting and consuming tokens:

- `input.parse::<T>()` — consume and parse the next tokens as type `T`
- `input.peek(Token![,])` — check the next token without consuming
- `input.is_empty()` — check if there's more input
- `input.lookahead1()` — peek with error reporting
- `input.parse::<Token![=>]>()` — consume a specific punctuation token

## quote: Generating Code

### Basic Interpolation

`quote!` uses `#variable` syntax to interpolate values:

```rust
use quote::quote;

let name = format_ident!("MyStruct");
let field_name = format_ident!("count");
let field_type = quote! { i32 };

let expanded = quote! {
    struct #name {
        #field_name: #field_type,
    }
};

// Generates:
// struct MyStruct {
//     count: i32,
// }
```

### Repetition

`quote!` supports repetition with `#( ... )*` syntax, mirroring `macro_rules!`:

```rust
let field_names = vec![
    format_ident!("x"),
    format_ident!("y"),
    format_ident!("z"),
];
let field_types = vec![
    quote! { f64 },
    quote! { f64 },
    quote! { f64 },
];

let expanded = quote! {
    struct Point {
        #(#field_names: #field_types,)*
    }
};

// Generates:
// struct Point {
//     x: f64,
//     y: f64,
//     z: f64,
// }
```

Multiple variables inside the same `#( ... )*` must have the same number of elements. They're zipped together.

### Nested Repetition

```rust
let struct_names = vec![format_ident!("A"), format_ident!("B")];
let all_fields: Vec<Vec<proc_macro2::TokenStream>> = vec![
    vec![quote! { x: i32 }, quote! { y: i32 }],
    vec![quote! { name: String }],
];

// You can't directly do nested #( #( ... )* )* in quote
// Instead, pre-generate the inner repetitions:
let struct_defs: Vec<_> = struct_names.iter().zip(all_fields.iter()).map(|(name, fields)| {
    quote! {
        struct #name {
            #(#fields,)*
        }
    }
}).collect();

let expanded = quote! {
    #(#struct_defs)*
};
```

### format_ident!

Creates new identifiers from format strings:

```rust
use quote::format_ident;

let base = "User";
let builder = format_ident!("{}Builder", base);     // UserBuilder
let getter = format_ident!("get_{}", "name");        // get_name
let private = format_ident!("__{}_internal", "calc"); // __calc_internal
```

You can also use an existing `Ident` and preserve its span (for better error messages):

```rust
let original: &Ident = &input.ident;
let builder = format_ident!("{}Builder", original);
// builder has the same span as original
```

### Conditional Code Generation

`quote!` works with regular Rust control flow:

```rust
let has_debug = true;
let debug_impl = if has_debug {
    Some(quote! {
        impl std::fmt::Debug for #name {
            fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                write!(f, "{}(...)", stringify!(#name))
            }
        }
    })
} else {
    None
};

let expanded = quote! {
    struct #name { /* ... */ }
    #debug_impl  // if None, nothing is emitted
};
```

`Option<TokenStream>` interpolates as nothing when `None` and as the contained tokens when `Some`. This is incredibly useful for conditional feature generation.

## Putting It Together: A Complete Example

Let's build a `#[derive(Validate)]` macro that generates validation based on field attributes:

```rust
// validate_macros/src/lib.rs
use proc_macro::TokenStream;
use quote::quote;
use syn::{
    parse_macro_input, DeriveInput, Data, Fields,
    Attribute, Expr, Lit, Meta,
};

struct FieldValidation {
    field_name: syn::Ident,
    min_len: Option<usize>,
    max_len: Option<usize>,
    non_empty: bool,
}

fn parse_validate_attr(attrs: &[Attribute]) -> FieldValidation {
    let mut validation = FieldValidation {
        field_name: syn::Ident::new("_", proc_macro2::Span::call_site()),
        min_len: None,
        max_len: None,
        non_empty: false,
    };

    for attr in attrs {
        if !attr.path().is_ident("validate") {
            continue;
        }

        let _ = attr.parse_nested_meta(|meta| {
            if meta.path.is_ident("non_empty") {
                validation.non_empty = true;
            } else if meta.path.is_ident("min_len") {
                let value = meta.value()?;
                let lit: syn::LitInt = value.parse()?;
                validation.min_len = Some(lit.base10_parse()?);
            } else if meta.path.is_ident("max_len") {
                let value = meta.value()?;
                let lit: syn::LitInt = value.parse()?;
                validation.max_len = Some(lit.base10_parse()?);
            }
            Ok(())
        });
    }

    validation
}

#[proc_macro_derive(Validate, attributes(validate))]
pub fn derive_validate(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);
    let name = &input.ident;

    let fields = match &input.data {
        Data::Struct(data) => match &data.fields {
            Fields::Named(fields) => &fields.named,
            _ => {
                return syn::Error::new_spanned(
                    name,
                    "Validate only supports structs with named fields",
                ).to_compile_error().into();
            }
        },
        _ => {
            return syn::Error::new_spanned(
                name,
                "Validate can only be used on structs",
            ).to_compile_error().into();
        }
    };

    let checks: Vec<_> = fields.iter().filter_map(|f| {
        let field_name = f.ident.as_ref().unwrap();
        let mut validation = parse_validate_attr(&f.attrs);
        validation.field_name = field_name.clone();

        let field_str = field_name.to_string();
        let mut field_checks = Vec::new();

        if validation.non_empty {
            field_checks.push(quote! {
                if self.#field_name.is_empty() {
                    errors.push(format!("{} must not be empty", #field_str));
                }
            });
        }

        if let Some(min) = validation.min_len {
            field_checks.push(quote! {
                if self.#field_name.len() < #min {
                    errors.push(format!("{} must be at least {} characters", #field_str, #min));
                }
            });
        }

        if let Some(max) = validation.max_len {
            field_checks.push(quote! {
                if self.#field_name.len() > #max {
                    errors.push(format!("{} must be at most {} characters", #field_str, #max));
                }
            });
        }

        if field_checks.is_empty() {
            None
        } else {
            Some(quote! { #(#field_checks)* })
        }
    }).collect();

    let (impl_generics, ty_generics, where_clause) = input.generics.split_for_impl();

    let expanded = quote! {
        impl #impl_generics #name #ty_generics #where_clause {
            pub fn validate(&self) -> ::std::result::Result<(), ::std::vec::Vec<::std::string::String>> {
                let mut errors = ::std::vec::Vec::new();
                #(#checks)*
                if errors.is_empty() {
                    ::std::result::Result::Ok(())
                } else {
                    ::std::result::Result::Err(errors)
                }
            }
        }
    };

    TokenStream::from(expanded)
}
```

Usage:

```rust
use validate_macros::Validate;

#[derive(Debug, Validate)]
struct Registration {
    #[validate(non_empty, min_len = 3, max_len = 20)]
    username: String,

    #[validate(non_empty, min_len = 5)]
    password: String,

    #[validate(non_empty)]
    email: String,

    age: u32,  // no validation
}

fn main() {
    let reg = Registration {
        username: "ab".to_string(),
        password: "1234".to_string(),
        email: String::new(),
        age: 25,
    };

    match reg.validate() {
        Ok(()) => println!("valid!"),
        Err(errors) => {
            for e in &errors {
                println!("  - {}", e);
            }
        }
    }
    // - username must be at least 3 characters
    // - password must be at least 5 characters
    // - email must not be empty
}
```

This macro demonstrates the full pipeline:
1. Parse the struct with `syn::DeriveInput`
2. Extract field-level attributes with custom parsing
3. Generate validation code conditionally (only for annotated fields)
4. Produce clean error messages with proper spans
5. Handle generics correctly with `split_for_impl()`

## proc-macro2: The Bridge

You'll notice `proc_macro2::TokenStream` and `proc_macro2::Span` appearing in proc macro code. Why two `TokenStream` types?

- `proc_macro::TokenStream` — the compiler's type, only available inside proc macro functions
- `proc_macro2::TokenStream` — a "portable" version that works anywhere, including unit tests

`syn` and `quote` use `proc_macro2` types internally. The conversion between the two is automatic:

```rust
use proc_macro::TokenStream;   // compiler type
use proc_macro2::TokenStream as TokenStream2; // portable type

#[proc_macro_derive(MyDerive)]
pub fn my_derive(input: TokenStream) -> TokenStream {
    // parse_macro_input! converts proc_macro → proc_macro2 internally
    let input = parse_macro_input!(input as DeriveInput);

    // quote! produces proc_macro2::TokenStream
    let expanded: TokenStream2 = quote! { /* ... */ };

    // TokenStream::from converts proc_macro2 → proc_macro
    TokenStream::from(expanded)
}
```

In unit tests, you work with `proc_macro2` directly:

```rust
#[cfg(test)]
mod tests {
    use quote::quote;
    use syn::parse2;

    #[test]
    fn test_parsing() {
        let tokens = quote! { struct Foo { x: i32 } };
        let input: syn::DeriveInput = parse2(tokens).unwrap();
        assert_eq!(input.ident.to_string(), "Foo");
    }
}
```

## Performance Considerations

`syn` with `features = ["full"]` adds significant compile time. If you only need to parse derive inputs and not arbitrary Rust syntax, use `features = ["derive"]` instead:

```toml
[dependencies]
syn = { version = "2", features = ["derive"] }
```

The feature flags control which parts of the Rust grammar `syn` can parse:

- `derive` — `DeriveInput`, basic types, attributes
- `full` — everything: expressions, statements, patterns, items
- `parsing` — the `Parse` trait and parsing infrastructure
- `printing` — the `ToTokens` trait
- `extra-traits` — `Debug`, `Eq`, `Hash` for syntax tree types

For derive macros, `derive` plus `parsing` is usually enough. Only reach for `full` if you're parsing function bodies or arbitrary expressions.

These two crates are the foundation of Rust's entire proc macro ecosystem. Every derive macro you've ever used — `Debug`, `Serialize`, `Clone` — builds on `syn` and `quote`. Understanding them deeply is what separates someone who copies macro boilerplate from someone who builds macros that save their team hundreds of hours.

Next lesson: we'll look at how the most popular Rust crates — serde, clap, sqlx — use macros under the hood.

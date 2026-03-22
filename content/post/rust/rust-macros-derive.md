---
title: "Lesson 7: Derive Macros — Custom #[derive()]"
author: Atharva Pandey
series: "Rust Macros & Metaprogramming"
lesson: 7
keywords: [Rust, rust, macros, derive macros, proc-macro, syn, quote, builder pattern]
tags: [Rust tutorial, rust, macros, metaprogramming]
date: '2025-02-19T13:55:00.000Z'
---

The first derive macro I shipped to production generated about 200 lines of boilerplate per struct. We had 47 structs. That's 9,400 lines of code I didn't have to write, test, or maintain. Every time someone added a field to a struct, the derive macro picked it up automatically. No manual updates, no forgotten implementations, no "oh I changed the struct but forgot to update the builder" bugs. Derive macros are the highest-leverage tool in Rust's macro system, and once you build one, you'll find excuses to build more.

## How Derive Macros Work

When you write `#[derive(MyTrait)]`, the compiler finds the proc macro function registered for `MyTrait`, passes it the entire struct or enum definition as a `TokenStream`, and appends whatever `TokenStream` the macro returns to the module.

The critical point: **derive macros only add code.** They cannot modify the original struct. The struct definition passes through unchanged. Your macro's output appears *after* the struct, as if you'd typed it by hand.

This means derive macros are perfect for implementing traits. You inspect the struct's fields, generate an `impl` block, and the compiler handles the rest.

## Setting Up the Project

We need two crates. Here's the structure:

```
derive_demo/
├── Cargo.toml          # workspace root
├── src/
│   └── main.rs         # consumer
└── derive_demo_macros/
    ├── Cargo.toml      # proc-macro crate
    └── src/
        └── lib.rs      # macro implementation
```

Workspace `Cargo.toml`:

```toml
[workspace]
members = [".", "derive_demo_macros"]
resolver = "2"

[package]
name = "derive_demo"
version = "0.1.0"
edition = "2021"

[dependencies]
derive_demo_macros = { path = "./derive_demo_macros" }
```

Macro crate `derive_demo_macros/Cargo.toml`:

```toml
[package]
name = "derive_demo_macros"
version = "0.1.0"
edition = "2021"

[lib]
proc-macro = true

[dependencies]
syn = { version = "2", features = ["full"] }
quote = "1"
proc-macro2 = "1"
```

## Building a Describe Derive Macro

Let's build something practical: a `#[derive(Describe)]` macro that generates a method returning a human-readable description of the struct's fields and their types.

First, the macro implementation:

```rust
// derive_demo_macros/src/lib.rs
use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, DeriveInput, Data, Fields};

#[proc_macro_derive(Describe)]
pub fn derive_describe(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);
    let name = &input.ident;

    let description = match &input.data {
        Data::Struct(data_struct) => {
            match &data_struct.fields {
                Fields::Named(fields) => {
                    let field_descriptions: Vec<String> = fields.named.iter().map(|f| {
                        let field_name = f.ident.as_ref().unwrap().to_string();
                        let field_type = quote!(#(f.ty)).to_string();
                        // Clean up the type string
                        let field_type = field_type
                            .replace(' ', "");
                        format!("  {}: {}", field_name, field_type)
                    }).collect();

                    let struct_name = name.to_string();
                    let field_count = field_descriptions.len();
                    let fields_str = field_descriptions.join("\n");

                    format!(
                        "{} ({} fields):\n{}",
                        struct_name, field_count, fields_str
                    )
                }
                _ => format!("{} (tuple or unit struct)", name),
            }
        }
        Data::Enum(_) => format!("{} (enum)", name),
        Data::Union(_) => format!("{} (union)", name),
    };

    let expanded = quote! {
        impl #name {
            pub fn describe() -> &'static str {
                #description
            }
        }
    };

    TokenStream::from(expanded)
}
```

Wait — that type stringification is clunky. Let me show a cleaner approach that works with `quote` properly:

```rust
// derive_demo_macros/src/lib.rs
use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, DeriveInput, Data, Fields};

#[proc_macro_derive(Describe)]
pub fn derive_describe(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);
    let name = &input.ident;
    let name_str = name.to_string();

    let field_info = match &input.data {
        Data::Struct(data) => match &data.fields {
            Fields::Named(fields) => {
                let infos: Vec<_> = fields.named.iter().map(|f| {
                    let fname = f.ident.as_ref().unwrap().to_string();
                    let ftype = format!("{}", quote!(#(& f.ty)));
                    format!("  {}: {}", fname, ftype.replace(' ', ""))
                }).collect();
                infos.join(", ")
            }
            _ => String::from("(unnamed fields)"),
        },
        _ => String::from("(not a struct)"),
    };

    let desc = format!("{}: [{}]", name_str, field_info);

    let expanded = quote! {
        impl #name {
            pub fn describe() -> &'static str {
                #desc
            }
        }
    };

    TokenStream::from(expanded)
}
```

Actually, let me stop overcomplicating the type printing and give you a version that's clean and actually compiles without issues:

```rust
// derive_demo_macros/src/lib.rs
use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, DeriveInput, Data, Fields};

#[proc_macro_derive(Describe)]
pub fn derive_describe(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);
    let name = &input.ident;

    let field_names: Vec<String> = match &input.data {
        Data::Struct(data) => match &data.fields {
            Fields::Named(fields) => {
                fields.named.iter().map(|f| {
                    f.ident.as_ref().unwrap().to_string()
                }).collect()
            }
            _ => vec![],
        },
        _ => vec![],
    };

    let field_count = field_names.len();
    let name_str = name.to_string();
    let fields_str = field_names.join(", ");

    let expanded = quote! {
        impl #name {
            pub fn describe() -> String {
                format!(
                    "{} has {} field(s): {}",
                    #name_str,
                    #field_count,
                    #fields_str,
                )
            }
        }
    };

    TokenStream::from(expanded)
}
```

Using it:

```rust
// src/main.rs
use derive_demo_macros::Describe;

#[derive(Describe)]
struct User {
    name: String,
    email: String,
    age: u32,
}

#[derive(Describe)]
struct Config {
    host: String,
    port: u16,
    debug: bool,
    max_connections: usize,
}

fn main() {
    println!("{}", User::describe());
    // User has 3 field(s): name, email, age

    println!("{}", Config::describe());
    // Config has 4 field(s): host, port, debug, max_connections
}
```

## Building a Builder Derive Macro

Now let's build something genuinely useful: `#[derive(Builder)]` that generates the builder pattern automatically.

Given this:

```rust
#[derive(Builder)]
struct Config {
    host: String,
    port: u16,
    debug: bool,
}
```

We want to generate:

```rust
struct ConfigBuilder {
    host: Option<String>,
    port: Option<u16>,
    debug: Option<bool>,
}

impl ConfigBuilder {
    fn host(mut self, value: String) -> Self {
        self.host = Some(value);
        self
    }
    fn port(mut self, value: u16) -> Self {
        self.port = Some(value);
        self
    }
    fn debug(mut self, value: bool) -> Self {
        self.debug = Some(value);
        self
    }
    fn build(self) -> Result<Config, String> {
        Ok(Config {
            host: self.host.ok_or("host is required")?,
            port: self.port.ok_or("port is required")?,
            debug: self.debug.ok_or("debug is required")?,
        })
    }
}

impl Config {
    fn builder() -> ConfigBuilder {
        ConfigBuilder {
            host: None,
            port: None,
            debug: None,
        }
    }
}
```

Here's the macro:

```rust
// derive_demo_macros/src/lib.rs
use proc_macro::TokenStream;
use quote::{quote, format_ident};
use syn::{parse_macro_input, DeriveInput, Data, Fields};

#[proc_macro_derive(Builder)]
pub fn derive_builder(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);
    let name = &input.ident;
    let builder_name = format_ident!("{}Builder", name);

    let fields = match &input.data {
        Data::Struct(data) => match &data.fields {
            Fields::Named(fields) => &fields.named,
            _ => panic!("Builder only supports structs with named fields"),
        },
        _ => panic!("Builder only supports structs"),
    };

    // Generate Option-wrapped fields for the builder struct
    let builder_fields = fields.iter().map(|f| {
        let name = &f.ident;
        let ty = &f.ty;
        quote! { #name: Option<#ty> }
    });

    // Generate setter methods
    let setters = fields.iter().map(|f| {
        let name = &f.ident;
        let ty = &f.ty;
        quote! {
            pub fn #name(mut self, value: #ty) -> Self {
                self.#name = Some(value);
                self
            }
        }
    });

    // Generate the build method's field extractions
    let build_fields = fields.iter().map(|f| {
        let name = &f.ident;
        let err = format!("{} is required", name.as_ref().unwrap());
        quote! {
            #name: self.#name.ok_or(#err)?
        }
    });

    // Generate None initializers
    let none_fields = fields.iter().map(|f| {
        let name = &f.ident;
        quote! { #name: None }
    });

    let expanded = quote! {
        pub struct #builder_name {
            #(#builder_fields,)*
        }

        impl #builder_name {
            #(#setters)*

            pub fn build(self) -> Result<#name, String> {
                Ok(#name {
                    #(#build_fields,)*
                })
            }
        }

        impl #name {
            pub fn builder() -> #builder_name {
                #builder_name {
                    #(#none_fields,)*
                }
            }
        }
    };

    TokenStream::from(expanded)
}
```

Using it:

```rust
use derive_demo_macros::Builder;

#[derive(Debug, Builder)]
struct Config {
    host: String,
    port: u16,
    debug: bool,
}

fn main() {
    let config = Config::builder()
        .host("localhost".to_string())
        .port(8080)
        .debug(true)
        .build()
        .expect("failed to build config");

    println!("{:?}", config);
    // Config { host: "localhost", port: 8080, debug: true }

    // Missing a field:
    let result = Config::builder()
        .host("localhost".to_string())
        .build();

    println!("{:?}", result);
    // Err("port is required")
}
```

The key techniques here:

- **`format_ident!`** creates new identifiers by string formatting. `format_ident!("{}Builder", name)` takes `Config` and produces `ConfigBuilder`. This is exactly what `macro_rules!` can't do.
- **`#name`** in `quote!` interpolates a variable — like `$name` in `macro_rules!` but more ergonomic.
- **`#(#iter,)*`** repeats for each item in an iterator, with commas between them.

## Handling Generics

Real-world derive macros need to handle generic structs. `syn` provides the tools:

```rust
#[proc_macro_derive(PrintType)]
pub fn derive_print_type(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);
    let name = &input.ident;
    let name_str = name.to_string();

    // Extract generics
    let (impl_generics, ty_generics, where_clause) = input.generics.split_for_impl();

    let expanded = quote! {
        impl #impl_generics #name #ty_generics #where_clause {
            pub fn print_type(&self) {
                println!("Type: {}", #name_str);
            }
        }
    };

    TokenStream::from(expanded)
}
```

The `split_for_impl()` method splits generics into three parts:
- `impl_generics` — the `<T: Clone>` part after `impl`
- `ty_generics` — the `<T>` part after the type name
- `where_clause` — any `where` bounds

This handles `struct Wrapper<T: Clone>` correctly, generating `impl<T: Clone> Wrapper<T>`.

## Helper Attributes

Derive macros can define helper attributes — attributes that are valid only on fields within the derived struct:

```rust
#[proc_macro_derive(Builder, attributes(builder))]
pub fn derive_builder(input: TokenStream) -> TokenStream {
    // Now fields can have #[builder(...)] attributes
    // ...
}
```

Usage:

```rust
#[derive(Builder)]
struct Config {
    host: String,
    #[builder(default = "8080")]
    port: u16,
    #[builder(default = "false")]
    debug: bool,
}
```

Parsing these attributes requires digging into `syn`'s `Attribute` type, which we'll cover properly in the `syn` and `quote` lesson. The key idea: you declare the attribute name in the `attributes(...)` list on the derive macro, and then parse it from each field's `attrs` vector.

## Error Handling in Derive Macros

Don't panic in proc macros. Use `syn::Error` to generate proper compiler errors with good spans:

```rust
use syn::Error;

#[proc_macro_derive(MyDerive)]
pub fn derive_my(input: TokenStream) -> TokenStream {
    let input = parse_macro_input!(input as DeriveInput);

    match &input.data {
        Data::Struct(data) => match &data.fields {
            Fields::Named(fields) => {
                // proceed
                let name = &input.ident;
                let expanded = quote! {
                    impl #name {
                        pub fn hello() {
                            println!("hello from derive");
                        }
                    }
                };
                TokenStream::from(expanded)
            }
            _ => {
                let err = Error::new_spanned(
                    &input.ident,
                    "MyDerive only supports structs with named fields"
                );
                TokenStream::from(err.to_compile_error())
            }
        },
        _ => {
            let err = Error::new_spanned(
                &input.ident,
                "MyDerive can only be used on structs"
            );
            TokenStream::from(err.to_compile_error())
        }
    }
}
```

`Error::new_spanned` attaches the error to a specific syntax element. The compiler then underlines exactly the right piece of code in the error message, instead of pointing at the whole macro invocation. This is the difference between a frustrating macro and a professional one.

## Testing Derive Macros

You can test proc macros with regular integration tests:

```rust
// tests/derive_test.rs
use derive_demo_macros::Builder;

#[derive(Debug, Builder)]
struct TestStruct {
    name: String,
    count: usize,
}

#[test]
fn test_builder_success() {
    let result = TestStruct::builder()
        .name("test".to_string())
        .count(42)
        .build();

    assert!(result.is_ok());
    let val = result.unwrap();
    assert_eq!(val.name, "test");
    assert_eq!(val.count, 42);
}

#[test]
fn test_builder_missing_field() {
    let result = TestStruct::builder()
        .name("test".to_string())
        .build();

    assert!(result.is_err());
}
```

For testing that the macro *rejects* invalid input (like being applied to an enum), use `trybuild`:

```toml
[dev-dependencies]
trybuild = "1"
```

```rust
#[test]
fn test_compile_failures() {
    let t = trybuild::TestCases::new();
    t.compile_fail("tests/ui/*.rs");
}
```

Create test cases in `tests/ui/` that should fail to compile, and `trybuild` verifies they fail with the expected error messages.

Next lesson: attribute macros. Same proc macro infrastructure, different registration, and the ability to transform (not just augment) the code they're attached to.

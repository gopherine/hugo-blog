---
title: "Lesson 8: Attribute Macros — #[my_attr] in practice"
author: Atharva Pandey
series: "Rust Macros & Metaprogramming"
lesson: 8
keywords: [Rust, rust, macros, attribute macros, proc-macro, web frameworks, instrumentation]
tags: [Rust tutorial, rust, macros, metaprogramming]
date: '2025-02-22T07:15:00.000Z'
---

I was reviewing a codebase that used Actix Web and kept seeing `#[get("/users")]` on handler functions. I knew it was a macro, but I didn't understand the mechanics — how does an attribute on a function transform the function? Where does the routing registration happen? When I finally built my own attribute macro, the whole system clicked. Attribute macros aren't magic. They're just functions that receive code and return different code.

## Derive vs. Attribute Macros

The distinction is simple but important.

**Derive macros** see the item and *add* code alongside it. The original item passes through unchanged. You can't modify a struct's fields or rename a function with a derive macro.

**Attribute macros** *replace* the annotated item entirely. They receive the item's tokens and must return the full replacement. This means they can modify, wrap, augment, or completely replace the original code.

This is what makes attribute macros so powerful for frameworks. An `#[route]` macro can wrap your handler function in routing logic. A `#[test]` macro can transform your function into a test harness. A `#[trace]` macro can inject timing code at the beginning and end.

## The Function Signature

Attribute macros take two `TokenStream` arguments:

```rust
#[proc_macro_attribute]
pub fn my_attribute(attr: TokenStream, item: TokenStream) -> TokenStream {
    // attr: the tokens inside the attribute's parentheses
    // item: the annotated item (function, struct, etc.)
    // return: the replacement code
    item // pass through unchanged
}
```

For `#[my_attribute(arg1, arg2)]`:
- `attr` contains `arg1, arg2`
- `item` contains the full function/struct/impl definition

For `#[my_attribute]` (no arguments):
- `attr` is empty
- `item` contains the annotated item

## A Timing Attribute Macro

Let's build `#[timed]` — an attribute that wraps a function to print how long it took to execute.

```rust
// timing_macros/src/lib.rs
use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, ItemFn};

#[proc_macro_attribute]
pub fn timed(_attr: TokenStream, item: TokenStream) -> TokenStream {
    let input_fn = parse_macro_input!(item as ItemFn);

    let fn_name = &input_fn.sig.ident;
    let fn_name_str = fn_name.to_string();
    let fn_block = &input_fn.block;
    let fn_sig = &input_fn.sig;
    let fn_vis = &input_fn.vis;
    let fn_attrs = &input_fn.attrs;

    let expanded = quote! {
        #(#fn_attrs)*
        #fn_vis #fn_sig {
            let __start = ::std::time::Instant::now();
            let __result = (|| #fn_block)();
            let __elapsed = __start.elapsed();
            eprintln!("[timed] {} took {:?}", #fn_name_str, __elapsed);
            __result
        }
    };

    TokenStream::from(expanded)
}
```

Usage:

```rust
use timing_macros::timed;

#[timed]
fn compute_fibonacci(n: u64) -> u64 {
    if n <= 1 {
        return n;
    }
    let mut a = 0u64;
    let mut b = 1u64;
    for _ in 2..=n {
        let temp = a + b;
        a = b;
        b = temp;
    }
    b
}

#[timed]
fn process_data(data: &[i32]) -> i32 {
    data.iter().filter(|&&x| x > 0).sum()
}

fn main() {
    let fib = compute_fibonacci(50);
    println!("fib(50) = {}", fib);
    // [timed] compute_fibonacci took 125ns

    let total = process_data(&[1, -2, 3, -4, 5]);
    println!("total = {}", total);
    // [timed] process_data took 84ns
}
```

The trick: we wrap the original function body in a closure `(|| #fn_block)()` so it can return a value. The timing code surrounds the closure call. The function's signature, visibility, and attributes are preserved.

## Handling Attribute Arguments

Let's extend `#[timed]` to accept a label:

```rust
// timing_macros/src/lib.rs
use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, ItemFn, LitStr};
use syn::parse::{Parse, ParseStream};

struct TimedArgs {
    label: Option<String>,
}

impl Parse for TimedArgs {
    fn parse(input: ParseStream) -> syn::Result<Self> {
        if input.is_empty() {
            return Ok(TimedArgs { label: None });
        }
        let lit: LitStr = input.parse()?;
        Ok(TimedArgs { label: Some(lit.value()) })
    }
}

#[proc_macro_attribute]
pub fn timed(attr: TokenStream, item: TokenStream) -> TokenStream {
    let args = parse_macro_input!(attr as TimedArgs);
    let input_fn = parse_macro_input!(item as ItemFn);

    let fn_name = &input_fn.sig.ident;
    let label = args.label.unwrap_or_else(|| fn_name.to_string());
    let fn_block = &input_fn.block;
    let fn_sig = &input_fn.sig;
    let fn_vis = &input_fn.vis;
    let fn_attrs = &input_fn.attrs;

    let expanded = quote! {
        #(#fn_attrs)*
        #fn_vis #fn_sig {
            let __start = ::std::time::Instant::now();
            let __result = (|| #fn_block)();
            let __elapsed = __start.elapsed();
            eprintln!("[timed] {} took {:?}", #label, __elapsed);
            __result
        }
    };

    TokenStream::from(expanded)
}
```

Now both forms work:

```rust
#[timed]                          // uses function name as label
fn foo() { /* ... */ }

#[timed("database query")]        // custom label
fn fetch_users() { /* ... */ }
```

The `Parse` trait implementation is how `syn` handles custom parsing. You define what tokens you expect and `syn` does the heavy lifting.

## Building a Route Attribute

Here's a simplified version of what web frameworks do. We'll create `#[get("/path")]` and `#[post("/path")]`:

```rust
// route_macros/src/lib.rs
use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, ItemFn, LitStr};

#[proc_macro_attribute]
pub fn get(attr: TokenStream, item: TokenStream) -> TokenStream {
    let path = parse_macro_input!(attr as LitStr);
    let input_fn = parse_macro_input!(item as ItemFn);
    generate_route("GET", &path, &input_fn)
}

#[proc_macro_attribute]
pub fn post(attr: TokenStream, item: TokenStream) -> TokenStream {
    let path = parse_macro_input!(attr as LitStr);
    let input_fn = parse_macro_input!(item as ItemFn);
    generate_route("POST", &path, &input_fn)
}

fn generate_route(method: &str, path: &LitStr, input_fn: &ItemFn) -> TokenStream {
    let fn_name = &input_fn.sig.ident;
    let fn_name_str = fn_name.to_string();
    let path_str = path.value();
    let fn_vis = &input_fn.vis;
    let fn_sig = &input_fn.sig;
    let fn_block = &input_fn.block;
    let fn_attrs = &input_fn.attrs;

    let register_fn = syn::Ident::new(
        &format!("__register_{}", fn_name),
        fn_name.span(),
    );

    let expanded = quote! {
        #(#fn_attrs)*
        #fn_vis #fn_sig #fn_block

        #[allow(non_snake_case)]
        fn #register_fn() -> (&'static str, &'static str, &'static str) {
            (#method, #path_str, #fn_name_str)
        }
    };

    TokenStream::from(expanded)
}
```

Usage:

```rust
use route_macros::{get, post};

#[get("/users")]
fn list_users() -> String {
    "user list".to_string()
}

#[post("/users")]
fn create_user() -> String {
    "user created".to_string()
}

fn main() {
    // In a real framework, these registration functions would
    // be collected and used to build a router
    let route1 = __register_list_users();
    let route2 = __register_create_user();
    println!("{:?}", route1);  // ("GET", "/users", "list_users")
    println!("{:?}", route2);  // ("POST", "/users", "create_user")
}
```

Real frameworks like Actix do something more sophisticated — they generate wrapper functions that handle request parsing, response serialization, and error conversion. But the core mechanism is exactly this: an attribute macro that emits the original function plus registration code.

## Attribute Macros on Structs

Attributes aren't limited to functions. You can put them on structs, enums, or any item:

```rust
// validated_macros/src/lib.rs
use proc_macro::TokenStream;
use quote::quote;
use syn::{parse_macro_input, DeriveInput, Data, Fields};

#[proc_macro_attribute]
pub fn validated(_attr: TokenStream, item: TokenStream) -> TokenStream {
    let input = parse_macro_input!(item as DeriveInput);
    let name = &input.ident;
    let vis = &input.vis;
    let attrs = &input.attrs;
    let generics = &input.generics;

    let fields = match &input.data {
        Data::Struct(data) => match &data.fields {
            Fields::Named(fields) => &fields.named,
            _ => panic!("validated only works on structs with named fields"),
        },
        _ => panic!("validated only works on structs"),
    };

    let field_defs = fields.iter().map(|f| {
        let name = &f.ident;
        let ty = &f.ty;
        let attrs = &f.attrs;
        quote! { #(#attrs)* #name: #ty }
    });

    let field_checks = fields.iter().filter_map(|f| {
        let name = &f.ident;
        let ty = &f.ty;
        let ty_str = quote!(#ty).to_string();

        if ty_str.contains("String") || ty_str.contains("& str") {
            let err_msg = format!("{} cannot be empty", name.as_ref().unwrap());
            Some(quote! {
                if self.#name.is_empty() {
                    errors.push(#err_msg.to_string());
                }
            })
        } else {
            None
        }
    });

    let (impl_generics, ty_generics, where_clause) = generics.split_for_impl();

    let expanded = quote! {
        #(#attrs)*
        #vis struct #name #generics {
            #(#field_defs,)*
        }

        impl #impl_generics #name #ty_generics #where_clause {
            pub fn validate(&self) -> Result<(), Vec<String>> {
                let mut errors = Vec::new();
                #(#field_checks)*
                if errors.is_empty() {
                    Ok(())
                } else {
                    Err(errors)
                }
            }
        }
    };

    TokenStream::from(expanded)
}
```

Usage:

```rust
use validated_macros::validated;

#[validated]
#[derive(Debug)]
struct Registration {
    username: String,
    email: String,
    age: u32,
}

fn main() {
    let reg = Registration {
        username: String::new(),
        email: "test@test.com".to_string(),
        age: 25,
    };

    match reg.validate() {
        Ok(()) => println!("valid"),
        Err(errors) => {
            for e in errors {
                println!("error: {}", e);
            }
        }
    }
    // error: username cannot be empty
}
```

Notice how the attribute macro re-emits the struct definition (preserving its attributes like `#[derive(Debug)]`) and adds a `validate()` method. If we'd used a derive macro instead, we'd get the `validate()` method but couldn't modify the struct itself. Here, we *could* modify the struct if we wanted — adding fields, changing types, wrapping in another struct.

## Async Function Support

Wrapping async functions requires extra care because the function body is an `async` block:

```rust
#[proc_macro_attribute]
pub fn timed_async(_attr: TokenStream, item: TokenStream) -> TokenStream {
    let input_fn = parse_macro_input!(item as ItemFn);

    let fn_name = &input_fn.sig.ident;
    let fn_name_str = fn_name.to_string();
    let fn_block = &input_fn.block;
    let fn_sig = &input_fn.sig;
    let fn_vis = &input_fn.vis;
    let fn_attrs = &input_fn.attrs;
    let is_async = input_fn.sig.asyncness.is_some();

    let expanded = if is_async {
        quote! {
            #(#fn_attrs)*
            #fn_vis #fn_sig {
                let __start = ::std::time::Instant::now();
                let __result = async move #fn_block.await;
                let __elapsed = __start.elapsed();
                eprintln!("[timed] {} took {:?}", #fn_name_str, __elapsed);
                __result
            }
        }
    } else {
        quote! {
            #(#fn_attrs)*
            #fn_vis #fn_sig {
                let __start = ::std::time::Instant::now();
                let __result = (|| #fn_block)();
                let __elapsed = __start.elapsed();
                eprintln!("[timed] {} took {:?}", #fn_name_str, __elapsed);
                __result
            }
        }
    };

    TokenStream::from(expanded)
}
```

We check `input_fn.sig.asyncness` and generate different wrapping code. For async functions, we wrap in an `async move` block and `.await` it.

## Error Handling

Same as derive macros — use `syn::Error` for user-friendly compiler errors:

```rust
#[proc_macro_attribute]
pub fn my_attr(attr: TokenStream, item: TokenStream) -> TokenStream {
    let input_fn = match syn::parse::<ItemFn>(item.clone()) {
        Ok(f) => f,
        Err(_) => {
            return syn::Error::new(
                proc_macro2::Span::call_site(),
                "my_attr can only be applied to functions"
            ).to_compile_error().into();
        }
    };

    // ... rest of implementation

    TokenStream::from(quote! { /* ... */ })
}
```

The `.clone()` on `item` is important — `parse` consumes the `TokenStream`, and if parsing fails, you might want the original tokens for error reporting.

## Composing Multiple Attributes

One thing that trips people up: when multiple attribute macros are stacked, they apply from **bottom to top** (innermost first):

```rust
#[outer_attr]    // runs second, receives the output of inner_attr
#[inner_attr]    // runs first, receives the original function
fn my_function() {
    // ...
}
```

This ordering matters when your macros interact. If `inner_attr` wraps the function and changes its signature, `outer_attr` needs to handle the wrapped version, not the original.

In practice, keep attribute macros independent. If they need to interact, document the expected ordering clearly.

## When to Use Attribute Macros vs. Derive Macros

Use **derive macros** when:
- You're implementing a trait
- You don't need to modify the original item
- The macro applies to structs/enums

Use **attribute macros** when:
- You need to modify or wrap the original item
- You're adding behavior to functions
- You want to transform code, not just augment it
- You need to process attribute arguments

The frameworks you use daily — `tokio::main`, `actix_web::get`, `tracing::instrument` — are all attribute macros. They wrap your code in a way that derive macros fundamentally cannot.

Next lesson: function-like procedural macros. The most flexible kind, where the input doesn't even need to be valid Rust syntax.

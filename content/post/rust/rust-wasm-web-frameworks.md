---
title: "Lesson 4: Leptos, Yew, Dioxus — Full-stack Rust"
author: Atharva Pandey
series: "Rust WebAssembly"
lesson: 4
keywords: [Rust, rust, WebAssembly, WASM, Leptos, Yew, Dioxus, frontend framework, reactive, SSR]
tags: [Rust tutorial, rust, webassembly, wasm]
date: '2025-07-07T09:17:33.000Z'
---

After building that todo list with raw `web-sys` in Lesson 3, I think we can all agree: manually managing DOM nodes, closures wrapped in `Rc<RefCell<Option<Closure<dyn FnMut()>>>>`, and string-based style attributes isn't how anyone wants to build a real application. That's where Rust frontend frameworks come in. And we've got three serious contenders — each with a different philosophy about how to build web UIs.

I've built side projects with all three. Let me tell you what actually matters when choosing between them.

## The Big Three

| | **Leptos** | **Yew** | **Dioxus** |
|---|---|---|---|
| **Reactive model** | Fine-grained signals | Virtual DOM | Virtual DOM |
| **Syntax** | RSX macros | `html!` macro | RSX macros |
| **SSR** | First-class | Supported | Supported |
| **Target** | Web-first | Web-first | Multi-platform |
| **Maturity** | Newer, fast-moving | Most mature | Growing rapidly |
| **Inspiration** | SolidJS | React (class era) | React (hooks era) |

Let's build the same component in each one to see how they feel.

## Leptos: Signals and Fine-Grained Reactivity

Leptos is my current favorite, and I'll tell you why: it doesn't use a virtual DOM. Instead, it uses fine-grained reactive signals — when a piece of state changes, only the DOM nodes that depend on it get updated. No diffing, no reconciliation. This is the same approach SolidJS uses, and it produces genuinely faster updates.

```bash
cargo install trunk  # Build tool for Rust WASM apps
cargo init leptos-demo
cd leptos-demo
```

```toml
# Cargo.toml
[package]
name = "leptos-demo"
version = "0.1.0"
edition = "2021"

[dependencies]
leptos = { version = "0.7", features = ["csr"] }  # client-side rendering
console_error_panic_hook = "0.1"
```

```rust
// src/main.rs
use leptos::prelude::*;

fn main() {
    console_error_panic_hook::set_once();
    mount_to_body(App);
}

#[component]
fn App() -> impl IntoView {
    let (todos, set_todos) = signal(vec![
        ("Learn Rust".to_string(), false),
        ("Build with WASM".to_string(), false),
    ]);
    let (input_value, set_input_value) = signal(String::new());

    let add_todo = move |_| {
        let text = input_value.get();
        if !text.is_empty() {
            set_todos.update(|todos| {
                todos.push((text, false));
            });
            set_input_value.set(String::new());
        }
    };

    let toggle_todo = move |index: usize| {
        set_todos.update(|todos| {
            if let Some(todo) = todos.get_mut(index) {
                todo.1 = !todo.1;
            }
        });
    };

    let remove_todo = move |index: usize| {
        set_todos.update(|todos| {
            todos.remove(index);
        });
    };

    let remaining = move || {
        todos.get().iter().filter(|(_, done)| !done).count()
    };

    view! {
        <div style="max-width: 500px; margin: 2rem auto; font-family: system-ui;">
            <h1>"Leptos Todo"</h1>
            <div style="display: flex; gap: 8px; margin-bottom: 1rem;">
                <input
                    type="text"
                    placeholder="What needs doing?"
                    style="flex: 1; padding: 8px; font-size: 16px;"
                    prop:value=input_value
                    on:input=move |ev| {
                        set_input_value.set(event_target_value(&ev));
                    }
                    on:keypress=move |ev| {
                        if ev.key() == "Enter" {
                            add_todo(ev);
                        }
                    }
                />
                <button
                    style="padding: 8px 16px; background: #2ecc71; color: white; border: none; cursor: pointer;"
                    on:click=add_todo
                >
                    "Add"
                </button>
            </div>

            <For
                each=move || {
                    todos.get().into_iter().enumerate().collect::<Vec<_>>()
                }
                key=|(i, _)| *i
                children=move |(index, (text, completed))| {
                    view! {
                        <div style="padding: 8px; margin: 4px 0; display: flex; align-items: center; gap: 8px;">
                            <input
                                type="checkbox"
                                prop:checked=completed
                                on:change=move |_| toggle_todo(index)
                            />
                            <span style:text-decoration=move || {
                                if completed { "line-through" } else { "none" }
                            }>
                                {text.clone()}
                            </span>
                            <button
                                style="margin-left: auto; background: #e74c3c; color: white; border: none; padding: 4px 8px; cursor: pointer;"
                                on:click=move |_| remove_todo(index)
                            >
                                "x"
                            </button>
                        </div>
                    }
                }
            />

            <p style="color: #666; margin-top: 1rem;">
                {remaining} " items remaining"
            </p>
        </div>
    }
}
```

Compare that to the raw `web-sys` version from Lesson 3. Night and day. The `view!` macro gives you JSX-like syntax, signals give you reactivity, and `#[component]` gives you composability.

Build and serve:

```bash
trunk serve
```

### What I like about Leptos

- Fine-grained reactivity means fewer unnecessary re-renders
- `view!` macro checks your HTML at compile time
- First-class server-side rendering — Leptos can render on the server and hydrate on the client
- Server functions let you call backend code from components with `#[server]`
- Active development with a responsive maintainer (Greg Johnston)

### What's rough

- The ecosystem is younger — fewer third-party components
- The API has gone through breaking changes (0.5 to 0.6 to 0.7 were all significant)
- Signal semantics can be confusing — `get()` vs `with()` vs just calling the signal as a function

## Yew: The Veteran

Yew is the oldest Rust frontend framework. It uses a virtual DOM model inspired by React. If you've written React class components, Yew's model will feel immediately familiar.

```toml
# Cargo.toml
[package]
name = "yew-demo"
version = "0.1.0"
edition = "2021"

[dependencies]
yew = { version = "0.21", features = ["csr"] }
```

```rust
use yew::prelude::*;

#[derive(Clone, PartialEq)]
struct Todo {
    text: String,
    completed: bool,
}

#[function_component(App)]
fn app() -> Html {
    let todos = use_state(|| vec![
        Todo { text: "Learn Rust".into(), completed: false },
        Todo { text: "Build with WASM".into(), completed: false },
    ]);
    let input_value = use_state(String::new);

    let on_input = {
        let input_value = input_value.clone();
        Callback::from(move |e: InputEvent| {
            let input: web_sys::HtmlInputElement = e.target_unchecked_into();
            input_value.set(input.value());
        })
    };

    let on_add = {
        let todos = todos.clone();
        let input_value = input_value.clone();
        Callback::from(move |_: MouseEvent| {
            let text = (*input_value).clone();
            if !text.is_empty() {
                let mut new_todos = (*todos).clone();
                new_todos.push(Todo { text, completed: false });
                todos.set(new_todos);
                input_value.set(String::new());
            }
        })
    };

    let on_toggle = {
        let todos = todos.clone();
        Callback::from(move |index: usize| {
            let mut new_todos = (*todos).clone();
            if let Some(todo) = new_todos.get_mut(index) {
                todo.completed = !todo.completed;
            }
            todos.set(new_todos);
        })
    };

    let on_remove = {
        let todos = todos.clone();
        Callback::from(move |index: usize| {
            let mut new_todos = (*todos).clone();
            new_todos.remove(index);
            todos.set(new_todos);
        })
    };

    let remaining = todos.iter().filter(|t| !t.completed).count();

    html! {
        <div style="max-width: 500px; margin: 2rem auto; font-family: system-ui;">
            <h1>{"Yew Todo"}</h1>
            <div style="display: flex; gap: 8px; margin-bottom: 1rem;">
                <input
                    type="text"
                    placeholder="What needs doing?"
                    style="flex: 1; padding: 8px; font-size: 16px;"
                    value={(*input_value).clone()}
                    oninput={on_input}
                />
                <button
                    style="padding: 8px 16px; background: #2ecc71; color: white; border: none; cursor: pointer;"
                    onclick={on_add}
                >
                    {"Add"}
                </button>
            </div>
            {for todos.iter().enumerate().map(|(i, todo)| {
                let toggle = on_toggle.reform(move |_: MouseEvent| i);
                let remove = on_remove.reform(move |_: MouseEvent| i);
                let style = if todo.completed {
                    "padding: 8px; margin: 4px 0; display: flex; align-items: center; gap: 8px; text-decoration: line-through; opacity: 0.6;"
                } else {
                    "padding: 8px; margin: 4px 0; display: flex; align-items: center; gap: 8px;"
                };

                html! {
                    <div style={style}>
                        <input
                            type="checkbox"
                            checked={todo.completed}
                            onclick={toggle}
                        />
                        <span style="flex: 1;">{&todo.text}</span>
                        <button
                            style="background: #e74c3c; color: white; border: none; padding: 4px 8px; cursor: pointer;"
                            onclick={remove}
                        >
                            {"x"}
                        </button>
                    </div>
                }
            })}
            <p style="color: #666; margin-top: 1rem;">
                {format!("{} items remaining", remaining)}
            </p>
        </div>
    }
}

fn main() {
    yew::Renderer::<App>::new().render();
}
```

### What I like about Yew

- The most mature Rust frontend framework — been around since 2018
- Large ecosystem of community components
- The `html!` macro is solid and familiar
- Good documentation and lots of examples

### What's rough

- Virtual DOM means diffing overhead — not as fast as Leptos for fine-grained updates
- Lots of `.clone()` calls for state management (notice all those clones above)
- The `Callback` system is verbose
- SSR support exists but isn't as polished as Leptos

## Dioxus: The Multi-Platform Play

Dioxus is the newest of the three, but it's aiming for something bigger: write your app once, deploy to web, desktop (via WebView or WGPU), mobile, and terminal. The API is heavily inspired by React hooks.

```toml
# Cargo.toml
[package]
name = "dioxus-demo"
version = "0.1.0"
edition = "2021"

[dependencies]
dioxus = { version = "0.6", features = ["web"] }
```

```rust
use dioxus::prelude::*;

fn main() {
    dioxus::launch(App);
}

#[component]
fn App() -> Element {
    let mut todos = use_signal(|| vec![
        ("Learn Rust".to_string(), false),
        ("Build with WASM".to_string(), false),
    ]);
    let mut input_value = use_signal(String::new);

    let add_todo = move |_| {
        let text = input_value();
        if !text.is_empty() {
            todos.write().push((text, false));
            input_value.set(String::new());
        }
    };

    rsx! {
        div {
            style: "max-width: 500px; margin: 2rem auto; font-family: system-ui;",
            h1 { "Dioxus Todo" }

            div {
                style: "display: flex; gap: 8px; margin-bottom: 1rem;",
                input {
                    r#type: "text",
                    placeholder: "What needs doing?",
                    style: "flex: 1; padding: 8px; font-size: 16px;",
                    value: "{input_value}",
                    oninput: move |evt| input_value.set(evt.value()),
                    onkeypress: move |evt| {
                        if evt.key() == Key::Enter {
                            add_todo(evt);
                        }
                    },
                }
                button {
                    style: "padding: 8px 16px; background: #2ecc71; color: white; border: none; cursor: pointer;",
                    onclick: add_todo,
                    "Add"
                }
            }

            for (index, (text, completed)) in todos().iter().enumerate() {
                div {
                    style: if *completed {
                        "padding: 8px; margin: 4px 0; display: flex; align-items: center; gap: 8px; text-decoration: line-through; opacity: 0.6;"
                    } else {
                        "padding: 8px; margin: 4px 0; display: flex; align-items: center; gap: 8px;"
                    },
                    input {
                        r#type: "checkbox",
                        checked: *completed,
                        onchange: move |_| {
                            todos.write()[index].1 = !todos()[index].1;
                        },
                    }
                    span { style: "flex: 1;", "{text}" }
                    button {
                        style: "background: #e74c3c; color: white; border: none; padding: 4px 8px; cursor: pointer;",
                        onclick: move |_| { todos.write().remove(index); },
                        "x"
                    }
                }
            }

            p {
                style: "color: #666; margin-top: 1rem;",
                "{todos().iter().filter(|(_, done)| !done).count()} items remaining"
            }
        }
    }
}
```

### What I like about Dioxus

- The RSX syntax is clean — closer to Rust than HTML
- Multi-platform deployment from a single codebase (web, desktop, mobile)
- Signals are ergonomic — `use_signal` is straightforward
- Hot reload works well during development
- Good developer experience overall

### What's rough

- Younger ecosystem — fewer battle-tested production deployments
- Desktop rendering via WebView can feel non-native
- API changes between versions (0.4 to 0.5 to 0.6 had breaking changes)
- The multi-platform promise means web-specific optimizations sometimes lag

## Which One Should You Actually Pick?

I'll be opinionated because that's what you're here for.

**Pick Leptos if** you're building a web application and care about performance. Its fine-grained reactivity model produces genuinely faster updates than virtual DOM approaches. The server function integration is excellent for full-stack apps. If you're coming from SolidJS or want the most "Rust-native" feeling, Leptos is your framework.

**Pick Yew if** your team needs stability and documentation. It's been around the longest, has the most community resources, and is the least likely to break your code with a new release. If you're migrating a React application to Rust, Yew's mental model will be the most familiar.

**Pick Dioxus if** you want to target more than just the browser. A desktop app and a web app from the same codebase is genuinely compelling. Also a good choice if you're coming from React hooks — the API is very similar. The development experience (hot reload, error messages) is arguably the best of the three.

**Pick raw web-sys if** you're building a canvas-based application, a library, or something that doesn't need a component model. Games, visualizations, and embedded widgets are often better without a framework.

## Full-Stack Architecture with Leptos

Since Leptos is my pick for most web projects, let me show a slightly more realistic setup — a full-stack app with server functions:

```rust
use leptos::prelude::*;

// This function runs on the server
#[server(GetTodos)]
pub async fn get_todos() -> Result<Vec<(String, bool)>, ServerFnError> {
    // In real code: query a database
    Ok(vec![
        ("Server-rendered todo".to_string(), false),
        ("Hydrated on client".to_string(), true),
    ])
}

#[server(AddTodo)]
pub async fn add_todo(text: String) -> Result<(), ServerFnError> {
    // In real code: insert into database
    println!("Adding todo: {}", text);
    Ok(())
}

#[component]
fn App() -> impl IntoView {
    let todos = Resource::new(|| (), |_| get_todos());

    view! {
        <Suspense fallback=move || view! { <p>"Loading..."</p> }>
            {move || todos.get().map(|result| match result {
                Ok(todos) => view! {
                    <TodoList initial_todos=todos />
                }.into_any(),
                Err(e) => view! {
                    <p>"Error: " {e.to_string()}</p>
                }.into_any(),
            })}
        </Suspense>
    }
}

#[component]
fn TodoList(initial_todos: Vec<(String, bool)>) -> impl IntoView {
    let (todos, set_todos) = signal(initial_todos);

    let add = Action::new(move |text: &String| {
        let text = text.clone();
        async move {
            if let Err(e) = add_todo(text.clone()).await {
                web_sys::console::error_1(
                    &format!("Failed to add: {}", e).into()
                );
            }
        }
    });

    view! {
        <div>
            <h2>"Todos (" {move || todos.get().len()} ")"</h2>
            // ... render todos
        </div>
    }
}
```

The `#[server]` macro is the magic. It generates both a server-side handler and a client-side function that makes an HTTP request to it. During SSR, the function calls run directly. On the client, they become fetch requests. Same code, both contexts.

## Binary Size Comparison

Real numbers from equivalent todo apps (release builds with `opt-level = "z"` and `wasm-opt`):

| Framework | WASM Size (gzipped) |
|---|---|
| Raw web-sys | ~35KB |
| Leptos (CSR) | ~90KB |
| Yew (CSR) | ~110KB |
| Dioxus (web) | ~100KB |
| React (for comparison) | ~45KB + your code |

None of these are deal-breakers. A 100KB gzipped WASM binary loads in milliseconds on any reasonable connection. But if you're building an embedded widget that needs to be tiny, raw `web-sys` or a minimal Leptos component is the way to go.

## Build Tooling

All three frameworks use **Trunk** as their primary build tool for client-side apps:

```bash
# Install
cargo install trunk

# Development server with hot reload
trunk serve

# Production build
trunk build --release
```

For full-stack Leptos, there's also `cargo-leptos`:

```bash
cargo install cargo-leptos
cargo leptos watch  # Development
cargo leptos build --release  # Production
```

Trunk handles WASM compilation, asset bundling, and generating the HTML shell. It's the `vite` of the Rust WASM world — simple config, just works.

## My Recommendation

If you're starting a new Rust WASM project today, try Leptos first. The fine-grained reactivity model is the right approach for web UIs (React is literally moving toward a similar model with their compiler). The server function story is compelling for full-stack apps. And the community, while smaller than React's, is active and helpful.

But honestly? All three frameworks are production-capable. The Rust WASM frontend ecosystem has matured enormously in the last two years. The bottleneck isn't the framework — it's whether your team can be productive in Rust, and whether WASM makes sense for your use case.

Next up, we're digging into WASM performance — specifically, when it actually beats JavaScript and when it doesn't. The answer might surprise you.

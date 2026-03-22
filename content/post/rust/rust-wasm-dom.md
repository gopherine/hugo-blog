---
title: "Lesson 3: Manipulating the DOM from Rust — Web without JavaScript"
author: Atharva Pandey
series: "Rust WebAssembly"
lesson: 3
keywords: [Rust, rust, WebAssembly, WASM, web-sys, DOM manipulation, event handling, canvas]
tags: [Rust tutorial, rust, webassembly, wasm]
date: '2025-07-05T11:33:47.000Z'
---

There's something deeply satisfying about writing `document.create_element("div")` in Rust and watching it actually work in a browser. It's also, if I'm being honest, kind of painful — because `web-sys` wraps every single Web API call in `Result` types, and you end up with `.unwrap()` chains that would make any Rustacean cringe. But once you build the right abstractions on top, it becomes surprisingly pleasant. Let me show you how I got there.

## The web-sys Crate

`web-sys` provides Rust bindings to the entire Web API surface — every DOM method, every event type, every CSS property. It's auto-generated from the WebIDL specifications that browsers implement, which means it's comprehensive but raw.

Here's the setup:

```toml
[package]
name = "rust-dom"
version = "0.1.0"
edition = "2021"

[lib]
crate-type = ["cdylib", "rlib"]

[dependencies]
wasm-bindgen = "0.2"
js-sys = "0.3"

[dependencies.web-sys]
version = "0.3"
features = [
    "Document",
    "Element",
    "HtmlElement",
    "HtmlInputElement",
    "HtmlCanvasElement",
    "CanvasRenderingContext2d",
    "Window",
    "Node",
    "Text",
    "Event",
    "MouseEvent",
    "KeyboardEvent",
    "InputEvent",
    "EventTarget",
    "CssStyleDeclaration",
    "DomRect",
    "console",
]
```

Notice the `features` list. `web-sys` uses Cargo features to keep binary size down — you only compile bindings for the APIs you actually use. Forget a feature? You'll get a compile error that says the type doesn't exist. It's annoying the first time, but it's the right design decision.

## Hello, DOM

Let's start simple — create some elements and put them on the page:

```rust
use wasm_bindgen::prelude::*;
use web_sys::{Document, Element, HtmlElement};

fn get_document() -> Document {
    web_sys::window()
        .expect("no global window")
        .document()
        .expect("no document on window")
}

fn create_element(doc: &Document, tag: &str) -> Element {
    doc.create_element(tag).expect("failed to create element")
}

#[wasm_bindgen(start)]
pub fn main() -> Result<(), JsValue> {
    let document = get_document();
    let body = document.body().expect("no body");

    // Create a container div
    let container = create_element(&document, "div");
    container.set_id("app");
    container.set_attribute("style",
        "max-width: 600px; margin: 2rem auto; font-family: system-ui;"
    )?;

    // Create a heading
    let heading = create_element(&document, "h1");
    heading.set_text_content(Some("Built with Rust"));

    // Create a paragraph
    let paragraph = create_element(&document, "p");
    paragraph.set_text_content(Some(
        "This entire page was built from Rust. No JavaScript."
    ));

    // Assemble the DOM tree
    container.append_child(&heading)?;
    container.append_child(&paragraph)?;
    body.append_child(&container)?;

    Ok(())
}
```

Every method that can fail returns `Result<_, JsValue>`. In DOM manipulation, failures are rare (you'd have to pass invalid element names or operate on detached nodes), so `.expect()` or `?` is fine. But the type system forces you to acknowledge it.

## Event Handling

This is where things get interesting — and where closures from Lesson 2 become essential:

```rust
use wasm_bindgen::prelude::*;
use wasm_bindgen::JsCast;
use web_sys::{Document, HtmlInputElement, Event};
use std::cell::RefCell;
use std::rc::Rc;

#[wasm_bindgen(start)]
pub fn main() -> Result<(), JsValue> {
    console_error_panic_hook::set_once();
    let document = get_document();
    let body = document.body().expect("no body");

    // Create an input field
    let input = document
        .create_element("input")?
        .dyn_into::<HtmlInputElement>()?;
    input.set_type("text");
    input.set_placeholder("Type something...");
    input.set_attribute("style", "padding: 8px; font-size: 16px; width: 300px;")?;

    // Create a display element
    let display = document.create_element("p")?;
    display.set_text_content(Some("You typed: "));
    display.set_attribute("style", "font-size: 18px; color: #333;")?;

    // Clone for the closure
    let display_clone = display.clone();

    // Character count
    let count_el = document.create_element("p")?;
    count_el.set_attribute("style", "color: #666;")?;
    let count_clone = count_el.clone();

    // Set up the event listener
    let closure = Closure::wrap(Box::new(move |event: Event| {
        let target = event
            .target()
            .expect("event should have a target")
            .dyn_into::<HtmlInputElement>()
            .expect("target should be an input");

        let value = target.value();
        display_clone.set_text_content(
            Some(&format!("You typed: {}", value))
        );
        count_clone.set_text_content(
            Some(&format!("{} characters", value.len()))
        );
    }) as Box<dyn FnMut(Event)>);

    input.add_event_listener_with_callback(
        "input",
        closure.as_ref().unchecked_ref(),
    )?;

    // Keep the closure alive for the lifetime of the page
    closure.forget();

    body.append_child(&input)?;
    body.append_child(&display)?;
    body.append_child(&count_el)?;

    Ok(())
}

fn get_document() -> Document {
    web_sys::window().unwrap().document().unwrap()
}
```

Notice `dyn_into::<HtmlInputElement>()` — that's a downcast. `web-sys` returns generic `EventTarget` or `Element` types, and you cast them to specific types to access type-specific methods. The `JsCast` trait provides `dyn_into()` (checked) and `unchecked_ref()` (unchecked) for this.

## Building a Real Component: Todo List

Enough toy examples. Let's build a todo list that actually works — with add, toggle, and delete:

```rust
use wasm_bindgen::prelude::*;
use wasm_bindgen::JsCast;
use web_sys::*;
use std::cell::RefCell;
use std::rc::Rc;

struct Todo {
    id: u32,
    text: String,
    completed: bool,
}

struct App {
    todos: Vec<Todo>,
    next_id: u32,
}

impl App {
    fn new() -> Self {
        App {
            todos: Vec::new(),
            next_id: 0,
        }
    }

    fn add(&mut self, text: String) {
        self.todos.push(Todo {
            id: self.next_id,
            text,
            completed: false,
        });
        self.next_id += 1;
    }

    fn toggle(&mut self, id: u32) {
        if let Some(todo) = self.todos.iter_mut().find(|t| t.id == id) {
            todo.completed = !todo.completed;
        }
    }

    fn remove(&mut self, id: u32) {
        self.todos.retain(|t| t.id != id);
    }
}

fn render(app: &App, document: &Document, container: &Element) -> Result<(), JsValue> {
    // Clear existing content
    container.set_text_content(Some(""));

    for todo in &app.todos {
        let item = document.create_element("div")?;
        let style = if todo.completed {
            "padding: 8px; margin: 4px 0; display: flex; align-items: center; gap: 8px; text-decoration: line-through; opacity: 0.6;"
        } else {
            "padding: 8px; margin: 4px 0; display: flex; align-items: center; gap: 8px;"
        };
        item.set_attribute("style", style)?;

        let checkbox = document
            .create_element("input")?
            .dyn_into::<HtmlInputElement>()?;
        checkbox.set_type("checkbox");
        checkbox.set_checked(todo.completed);
        checkbox.set_attribute("data-id", &todo.id.to_string())?;

        let label = document.create_element("span")?;
        label.set_text_content(Some(&todo.text));
        label.set_attribute("style", "flex: 1;")?;

        let delete_btn = document.create_element("button")?;
        delete_btn.set_text_content(Some("x"));
        delete_btn.set_attribute("data-id", &todo.id.to_string())?;
        delete_btn.set_attribute("style",
            "background: #e74c3c; color: white; border: none; padding: 4px 8px; cursor: pointer; border-radius: 4px;"
        )?;

        item.append_child(&checkbox)?;
        item.append_child(&label)?;
        item.append_child(&delete_btn)?;
        container.append_child(&item)?;
    }

    Ok(())
}

#[wasm_bindgen(start)]
pub fn main() -> Result<(), JsValue> {
    console_error_panic_hook::set_once();

    let document = get_document();
    let body = document.body().expect("no body");

    let app = Rc::new(RefCell::new(App::new()));

    // Build the UI shell
    let wrapper = document.create_element("div")?;
    wrapper.set_attribute("style",
        "max-width: 500px; margin: 2rem auto; font-family: system-ui;"
    )?;

    let title = document.create_element("h1")?;
    title.set_text_content(Some("Rust Todo"));

    let form_row = document.create_element("div")?;
    form_row.set_attribute("style", "display: flex; gap: 8px; margin-bottom: 1rem;")?;

    let input = document
        .create_element("input")?
        .dyn_into::<HtmlInputElement>()?;
    input.set_type("text");
    input.set_placeholder("What needs doing?");
    input.set_attribute("style", "flex: 1; padding: 8px; font-size: 16px;")?;

    let add_btn = document.create_element("button")?;
    add_btn.set_text_content(Some("Add"));
    add_btn.set_attribute("style",
        "padding: 8px 16px; background: #2ecc71; color: white; border: none; cursor: pointer; font-size: 16px; border-radius: 4px;"
    )?;

    let todo_container = document.create_element("div")?;
    todo_container.set_id("todos");

    // Wire up the add button
    {
        let app = app.clone();
        let input = input.clone();
        let document = document.clone();
        let container = todo_container.clone();

        let closure = Closure::wrap(Box::new(move |_: Event| {
            let text = input.value();
            if !text.is_empty() {
                app.borrow_mut().add(text);
                input.set_value("");
                render(&app.borrow(), &document, &container).unwrap();
            }
        }) as Box<dyn FnMut(Event)>);

        add_btn.add_event_listener_with_callback(
            "click",
            closure.as_ref().unchecked_ref(),
        )?;
        closure.forget();
    }

    // Wire up keyboard shortcut (Enter to add)
    {
        let app = app.clone();
        let input_ref = input.clone();
        let document = document.clone();
        let container = todo_container.clone();

        let closure = Closure::wrap(Box::new(move |event: KeyboardEvent| {
            if event.key() == "Enter" {
                let text = input_ref.value();
                if !text.is_empty() {
                    app.borrow_mut().add(text);
                    input_ref.set_value("");
                    render(&app.borrow(), &document, &container).unwrap();
                }
            }
        }) as Box<dyn FnMut(KeyboardEvent)>);

        input.add_event_listener_with_callback(
            "keypress",
            closure.as_ref().unchecked_ref(),
        )?;
        closure.forget();
    }

    // Wire up toggle and delete via event delegation
    {
        let app = app.clone();
        let document = document.clone();
        let container = todo_container.clone();

        let closure = Closure::wrap(Box::new(move |event: Event| {
            let target: Element = event
                .target()
                .unwrap()
                .dyn_into()
                .unwrap();

            if let Ok(id_str) = target.get_attribute("data-id")
                .ok_or(())
                .and_then(|s| Ok(s))
            {
                if let Ok(id) = id_str.parse::<u32>() {
                    let tag = target.tag_name();
                    if tag == "INPUT" {
                        app.borrow_mut().toggle(id);
                    } else if tag == "BUTTON" {
                        app.borrow_mut().remove(id);
                    }
                    render(&app.borrow(), &document, &container).unwrap();
                }
            }
        }) as Box<dyn FnMut(Event)>);

        todo_container.add_event_listener_with_callback(
            "click",
            closure.as_ref().unchecked_ref(),
        )?;
        closure.forget();
    }

    // Assemble
    form_row.append_child(&input)?;
    form_row.append_child(&add_btn)?;
    wrapper.append_child(&title)?;
    wrapper.append_child(&form_row)?;
    wrapper.append_child(&todo_container)?;
    body.append_child(&wrapper)?;

    Ok(())
}

fn get_document() -> Document {
    web_sys::window().unwrap().document().unwrap()
}
```

It works. It's functional. And it's also... a lot of code for a todo list, right? That's the honest truth about raw `web-sys` DOM manipulation. Every element creation is explicit. Every event handler requires a closure wrapped in `Rc<RefCell<>>`. Every style is a string attribute.

This is exactly why frameworks like Leptos and Yew exist. We'll get to those in Lesson 4.

## Canvas: Where Rust WASM Shines

Raw DOM manipulation is verbose, but canvas work is where Rust really earns its keep. Here's a particle system:

```rust
use wasm_bindgen::prelude::*;
use wasm_bindgen::JsCast;
use web_sys::*;
use std::cell::RefCell;
use std::rc::Rc;

struct Particle {
    x: f64,
    y: f64,
    vx: f64,
    vy: f64,
    radius: f64,
    color: String,
    life: f64,
}

struct ParticleSystem {
    particles: Vec<Particle>,
    width: f64,
    height: f64,
}

impl ParticleSystem {
    fn new(width: f64, height: f64) -> Self {
        ParticleSystem {
            particles: Vec::with_capacity(1000),
            width,
            height,
        }
    }

    fn spawn(&mut self, x: f64, y: f64, count: usize) {
        for _ in 0..count {
            let angle = js_sys::Math::random() * std::f64::consts::TAU;
            let speed = js_sys::Math::random() * 3.0 + 1.0;
            let hue = (js_sys::Math::random() * 60.0 + 10.0) as u32; // warm colors

            self.particles.push(Particle {
                x,
                y,
                vx: angle.cos() * speed,
                vy: angle.sin() * speed - 2.0, // slight upward bias
                radius: js_sys::Math::random() * 3.0 + 1.0,
                color: format!("hsl({}, 100%, 60%)", hue),
                life: 1.0,
            });
        }
    }

    fn update(&mut self) {
        for p in &mut self.particles {
            p.x += p.vx;
            p.y += p.vy;
            p.vy += 0.05; // gravity
            p.life -= 0.015;
            p.radius *= 0.99;
        }

        self.particles.retain(|p| p.life > 0.0);
    }

    fn draw(&self, ctx: &CanvasRenderingContext2d) {
        ctx.clear_rect(0.0, 0.0, self.width, self.height);

        for p in &self.particles {
            ctx.set_global_alpha(p.life);
            ctx.set_fill_style_str(&p.color);
            ctx.begin_path();
            ctx.arc(p.x, p.y, p.radius, 0.0, std::f64::consts::TAU)
                .unwrap();
            ctx.fill();
        }

        ctx.set_global_alpha(1.0);

        // Draw particle count
        ctx.set_fill_style_str("#fff");
        ctx.set_font("14px monospace");
        ctx.fill_text(
            &format!("Particles: {}", self.particles.len()),
            10.0,
            20.0,
        )
        .unwrap();
    }
}

#[wasm_bindgen(start)]
pub fn main() -> Result<(), JsValue> {
    console_error_panic_hook::set_once();

    let document = web_sys::window().unwrap().document().unwrap();
    let body = document.body().unwrap();

    let canvas = document
        .create_element("canvas")?
        .dyn_into::<HtmlCanvasElement>()?;
    canvas.set_width(800);
    canvas.set_height(600);
    canvas.set_attribute("style",
        "background: #1a1a2e; display: block; margin: 0 auto; cursor: crosshair;"
    )?;

    let ctx = canvas
        .get_context("2d")?
        .unwrap()
        .dyn_into::<CanvasRenderingContext2d>()?;

    body.append_child(&canvas)?;

    let system = Rc::new(RefCell::new(ParticleSystem::new(800.0, 600.0)));

    // Mouse click spawns particles
    {
        let system = system.clone();
        let closure = Closure::wrap(Box::new(move |event: MouseEvent| {
            let x = event.offset_x() as f64;
            let y = event.offset_y() as f64;
            system.borrow_mut().spawn(x, y, 30);
        }) as Box<dyn FnMut(MouseEvent)>);

        canvas.add_event_listener_with_callback(
            "click",
            closure.as_ref().unchecked_ref(),
        )?;
        closure.forget();
    }

    // Animation loop using requestAnimationFrame
    {
        let system = system.clone();
        let f: Rc<RefCell<Option<Closure<dyn FnMut()>>>> =
            Rc::new(RefCell::new(None));
        let g = f.clone();

        *g.borrow_mut() = Some(Closure::wrap(Box::new(move || {
            system.borrow_mut().update();
            system.borrow().draw(&ctx);

            // Schedule next frame
            web_sys::window()
                .unwrap()
                .request_animation_frame(
                    f.borrow().as_ref().unwrap().as_ref().unchecked_ref(),
                )
                .unwrap();
        }) as Box<dyn FnMut()>));

        web_sys::window()
            .unwrap()
            .request_animation_frame(
                g.borrow().as_ref().unwrap().as_ref().unchecked_ref(),
            )?;
    }

    Ok(())
}
```

This runs at 60fps with hundreds of particles. The physics simulation — the position updates, the gravity, the lifecycle management — all runs in WASM. The only JS interaction is the canvas draw calls and `requestAnimationFrame`. That's the sweet spot for Rust WASM: do the heavy computation in Rust, use the browser APIs for rendering.

## The requestAnimationFrame Pattern

That `Rc<RefCell<Option<Closure>>>` monstrosity for `requestAnimationFrame` deserves explanation, because you'll use it constantly.

The problem: `requestAnimationFrame` takes a callback. That callback needs to call `requestAnimationFrame` again with itself. In Rust, that's a self-referential structure — the closure needs to own a reference to itself. We can't do that directly, so we use the double-Rc trick:

```rust
// The pattern, distilled:
let f: Rc<RefCell<Option<Closure<dyn FnMut()>>>> = Rc::new(RefCell::new(None));
let g = f.clone(); // g is used to set the closure, f is captured by it

*g.borrow_mut() = Some(Closure::wrap(Box::new(move || {
    // Your animation logic here

    // Request next frame using the captured Rc
    window().request_animation_frame(
        f.borrow().as_ref().unwrap().as_ref().unchecked_ref()
    ).unwrap();
}) as Box<dyn FnMut()>));

// Start the loop
window().request_animation_frame(
    g.borrow().as_ref().unwrap().as_ref().unchecked_ref()
).unwrap();
```

Is it pretty? No. Does it work? Perfectly. Is there a better way? Use `gloo-timers` or a framework. But understanding the raw pattern helps when things go wrong.

## Fetch from Rust

You can make HTTP requests without touching JavaScript:

```rust
use wasm_bindgen::prelude::*;
use wasm_bindgen_futures::JsFuture;
use web_sys::{Request, RequestInit, RequestMode, Response};

pub async fn fetch_json(url: &str) -> Result<JsValue, JsValue> {
    let mut opts = RequestInit::new();
    opts.method("GET");
    opts.mode(RequestMode::Cors);

    let request = Request::new_with_str_and_init(url, &opts)?;
    request.headers().set("Accept", "application/json")?;

    let window = web_sys::window().unwrap();
    let resp_value = JsFuture::from(window.fetch_with_request(&request)).await?;
    let resp: Response = resp_value.dyn_into()?;

    if !resp.ok() {
        return Err(JsValue::from_str(
            &format!("HTTP {}: {}", resp.status(), resp.status_text())
        ));
    }

    let json = JsFuture::from(resp.json()?).await?;
    Ok(json)
}

#[wasm_bindgen]
pub async fn load_data() -> Result<JsValue, JsValue> {
    let data = fetch_json("https://jsonplaceholder.typicode.com/todos/1").await?;
    web_sys::console::log_1(&data);
    Ok(data)
}
```

Add to `Cargo.toml`:

```toml
[dependencies]
wasm-bindgen-futures = "0.4"
```

`wasm-bindgen-futures` bridges Rust futures and JavaScript Promises. `JsFuture::from()` converts a JS `Promise` into a Rust `Future` you can `.await`. Going the other direction, `#[wasm_bindgen]` on an `async fn` automatically wraps the return in a Promise for JavaScript.

## The Honest Assessment

Writing raw DOM code with `web-sys` is educational and sometimes necessary. It's the right choice when:

- You're building a canvas-heavy application (games, visualizations, editors)
- You need surgical control over exactly which Web APIs you call
- You're writing a library that other Rust WASM code will use
- You want to understand what frameworks do under the hood

It's the wrong choice when:

- You're building a UI-heavy application with lots of components
- You want reactivity and state management
- Your team needs to be productive quickly
- You're doing the kind of work React/Vue/Svelte excel at

In the next lesson, we're looking at Leptos, Yew, and Dioxus — frameworks that take all this manual DOM work and give you a component model, reactive state, and routing. They're built on everything we've covered so far, but they make it actually pleasant to build full applications.

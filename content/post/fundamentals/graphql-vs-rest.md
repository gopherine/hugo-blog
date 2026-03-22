---
title: "Lesson 1: GraphQL vs REST — When GraphQL wins and when it doesn't"
author: Atharva Pandey
keywords:
  - GraphQL vs REST
  - API design
  - GraphQL tradeoffs
  - REST API
  - API architecture
  - backend engineering
tags:
  - fundamentals
  - GraphQL
  - API
series: "GraphQL in Practice"
lesson: 1
date: '2024-06-06T00:00:00.000Z'
---

I spent about six months being wrong about GraphQL. My first exposure to it was a rewrite pitch from a frontend engineer who was tired of making five REST calls to assemble one screen. "GraphQL solves this," they said, and the demo looked compelling. A single query, exactly the fields you need, one round trip. I was sold before thinking carefully about what we were buying.

The rewrite happened. It took eight months instead of four. Some of the problems we were trying to solve got better. Others got worse. Looking back, the mistake wasn't choosing GraphQL — it was not being rigorous about where GraphQL actually wins and where it doesn't before committing.

## The Real Problem REST Solves Poorly

REST is a resource-oriented model. You have endpoints that map to nouns: `/users`, `/posts`, `/comments`. That works cleanly when your clients need whole resources. It breaks down when clients need compositions — when a single UI screen needs fields from three different resources, some nested, some conditional, and the exact fields depend on which user is viewing it.

This is the overfetching and underfetching problem. Overfetching: your `/users` endpoint returns 40 fields and the mobile client needs 4. Underfetching: to show a user profile with their recent posts and comment counts, you need three requests that can't be parallelized because each depends on the previous response.

These are real problems, especially in mobile contexts where bandwidth is limited and round-trip latency is painful. GraphQL was explicitly designed to solve them. Facebook created it for their mobile apps in 2012 before open-sourcing it in 2015.

## Where GraphQL Actually Wins

**Rapid frontend iteration.** When a product team is moving fast and the shape of data on every screen is changing weekly, GraphQL shines. Frontend engineers can adjust their queries without waiting for backend endpoint changes. No coordination, no versioning, no "can you add that field to the response?" tickets.

**Multiple clients with different data needs.** A native mobile app, a web app, and a third-party partner integration all have different data requirements. With REST, you end up either building client-specific endpoints (a `/mobile/users` that returns trimmed fields), returning everything and accepting the waste, or building a Backend for Frontend (BFF) layer. GraphQL lets all three clients specify exactly what they need from a single schema.

**Complex, interconnected data.** If your data is naturally a graph — users have posts, posts have comments, comments have authors who might be the same user — GraphQL's nested query syntax mirrors that structure directly. The query shape looks like the data shape. This is genuinely more expressive than `?include=posts.comments.author`.

**Explorability and self-documentation.** GraphQL schemas are introspectable. Tools like GraphiQL and Apollo Studio give you a live, always-up-to-date documentation interface. New engineers can explore the API interactively. This is not a toy feature — on large teams, it meaningfully reduces the "what fields does this endpoint actually return?" overhead.

## Where GraphQL Doesn't Win

This is the list I wish someone had handed me before that rewrite.

**Simple CRUD APIs with stable endpoints.** If your API is "create a widget, read a widget, update a widget, delete a widget" and the clients are not particularly varied, REST is simpler. You get HTTP caching, straightforward auth at the route level, and standard tooling everywhere. GraphQL adds schema management overhead and a learning curve for no real gain.

**File uploads.** GraphQL over HTTP does not handle multipart file uploads elegantly. There are workarounds (the `graphql-multipart-request-spec`), but they are awkward. REST handles this with a simple `multipart/form-data` POST. If your API handles significant file I/O, mixing REST and GraphQL is often the pragmatic answer.

**HTTP caching.** REST gets browser and CDN caching almost for free. GET `/posts/123` can be cached at every level. GraphQL queries are typically POSTs with query bodies, which are opaque to caches. You can work around this with persisted queries and GET-based query execution, but it requires extra setup and discipline. If you're serving a content-heavy API to many clients, this matters.

**Performance visibility.** With REST, a slow endpoint is obvious from your observability tooling — `GET /recommendations` is slow. With GraphQL, every request hits the same endpoint. Determining which queries are expensive requires additional query-aware instrumentation. This is solvable, but it's work you have to do rather than work that's built in.

**Authorization complexity.** In REST, you can put authorization at the route level. A user cannot hit `/admin/users` without the right role — one check, clearly located. In GraphQL, authorization typically lives in resolvers, which means it's distributed across the schema. If you're not disciplined about it, it's easy to accidentally expose fields to users who shouldn't see them. Field-level authorization libraries (like graphql-shield) help, but they add a layer that REST doesn't need.

## The N+1 Problem

I'd be doing you a disservice if I didn't mention this early. It's the most common GraphQL performance pitfall and it will hurt you in production if you're not prepared for it.

Consider a query that fetches a list of posts, each with their author. Naively implemented, your resolvers will load the list of posts (1 query), then load the author for each post one at a time (N queries). With 100 posts, that's 101 database queries per request.

This doesn't happen with REST because you're writing the data-fetching logic yourself, so you naturally think about it. With GraphQL, the resolver model hides it — you implement a post resolver and an author resolver separately, and the N+1 emerges from how they compose.

The standard solution is DataLoader, a batching utility that groups all the author lookups in a single tick and executes them as one query. It works well and it's the expected pattern in any production GraphQL server. The lesson is to be aware of it before you have 1000 posts and a 4-second response time.

## Making the Decision

The question I now ask before recommending GraphQL: is the client/server relationship the main source of complexity here, or is it the business logic?

If the complexity is in composing data for varied clients — mobile, web, partners — GraphQL pays dividends. If the complexity is in the domain logic itself — pricing rules, authorization hierarchies, workflow state machines — GraphQL is largely neutral and the extra surface area is overhead.

Teams with a high frontend-to-backend engineer ratio tend to benefit most from GraphQL. Teams with a single client and stable data needs tend to benefit least.

There's also a team capability dimension. GraphQL requires everyone touching the API to understand schema design, resolver patterns, and tooling like DataLoader. REST is simpler to onboard. If your team is growing rapidly with engineers who are new to the stack, that onboarding tax is real.

The next lesson digs into schema design — types, queries, mutations, and subscriptions — where most of the interesting and consequential GraphQL decisions live.

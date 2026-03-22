---
title: "Lesson 3: GraphQL in Go — gqlgen, resolvers, and DataLoader for N+1"
author: Atharva Pandey
keywords:
  - GraphQL Go
  - gqlgen
  - DataLoader Go
  - N+1 problem GraphQL
  - GraphQL resolvers
  - Go API development
tags:
  - fundamentals
  - GraphQL
  - API
series: "GraphQL in Practice"
lesson: 3
date: '2024-12-07T00:00:00.000Z'
---

Go is not the most common language for GraphQL tutorials. Most of the ecosystem documentation assumes you're working in JavaScript or TypeScript, and a lot of the tooling is designed around Node's event loop model. But Go is an excellent choice for a GraphQL server — statically typed, fast, and with gqlgen you get one of the cleanest schema-first GraphQL implementations I've used in any language.

This lesson is about making it work in production: generating a working server from a schema, implementing resolvers correctly, and fixing the N+1 problem with DataLoader before it shows up in your latency graphs.

## Why gqlgen

There are a handful of Go GraphQL libraries. The main options are `graphql-go/graphql`, `graph-gophers/graphql-go`, and `99designs/gqlgen`. I use gqlgen for new projects and recommend it without much hesitation.

The reason is code generation. gqlgen takes your SDL schema, generates Go interfaces for your resolvers, and generates all the plumbing to connect query execution to those interfaces. You implement the generated interfaces. The schema and the code are kept in sync by the generator, not by developer discipline.

The alternative libraries are resolver-model APIs where you register resolver functions manually. These work fine, but you lose the compile-time guarantee that your resolvers match your schema. In a schema-first workflow, gqlgen's generated interfaces make it impossible to ship a mismatch between schema and implementation. The compiler tells you before the tests do.

## Setting Up a gqlgen Project

Start with the schema. Create a `schema.graphqls` file in your project root:

```graphql
type Query {
  post(id: ID!): Post
  posts(first: Int, after: String): PostConnection!
}

type Mutation {
  createPost(input: CreatePostInput!): CreatePostPayload!
}

type Post {
  id: ID!
  title: String!
  body: String!
  author: User!
  createdAt: String!
}

type User {
  id: ID!
  name: String!
  email: String!
}

type PostConnection {
  edges: [PostEdge!]!
  pageInfo: PageInfo!
}

type PostEdge {
  cursor: String!
  node: Post!
}

type PageInfo {
  hasNextPage: Boolean!
  endCursor: String
}

input CreatePostInput {
  title: String!
  body: String!
}

type CreatePostPayload {
  post: Post
  errors: [UserError!]
}

type UserError {
  field: String!
  message: String!
}
```

Then initialize gqlgen:

```bash
go get github.com/99designs/gqlgen
go run github.com/99designs/gqlgen init
```

This generates `graph/resolver.go`, `graph/schema.resolvers.go`, and `server.go`. The resolver file is yours to implement. The schema resolvers file is generated and contains stubs for every resolver the schema requires.

## Implementing Resolvers

The generated `schema.resolvers.go` will contain stubs like:

```go
func (r *queryResolver) Post(ctx context.Context, id string) (*model.Post, error) {
    panic(fmt.Errorf("not implemented"))
}

func (r *postResolver) Author(ctx context.Context, obj *model.Post) (*model.User, error) {
    panic(fmt.Errorf("not implemented"))
}
```

Implement the query resolver by fetching from your data layer:

```go
func (r *queryResolver) Post(ctx context.Context, id string) (*model.Post, error) {
    post, err := r.db.GetPost(ctx, id)
    if err != nil {
        if errors.Is(err, db.ErrNotFound) {
            return nil, nil // GraphQL convention: return nil, nil for not found
        }
        return nil, fmt.Errorf("fetching post: %w", err)
    }
    return post, nil
}
```

The field resolver for `Author` is where the N+1 problem lives. The naive implementation:

```go
func (r *postResolver) Author(ctx context.Context, obj *model.Post) (*model.User, error) {
    return r.db.GetUser(ctx, obj.AuthorID) // Called once per post
}
```

If you're rendering 50 posts and each calls `GetUser`, that's 50 individual user queries. With 200 posts it's 200. This is not hypothetical degradation — I've seen response times go from 200ms to 8 seconds when this pattern hits a busy table without caching.

## DataLoader: Batching the N+1

DataLoader solves N+1 by coalescing multiple loads that happen within the same event loop tick (or in Go's case, within the same goroutine scheduling window) into a single batched call.

The Go implementation is `graph-gophers/dataloader` or `vikstrous/dataloadgen`. I prefer `dataloadgen` because it's generic and provides type safety.

The pattern:

```go
// Create a loader that batches user lookups
userLoader := dataloadgen.NewLoader(func(ctx context.Context, ids []string) ([]*model.User, []error) {
    users, err := r.db.GetUsersByIDs(ctx, ids)
    if err != nil {
        // Return the error for every ID
        errs := make([]error, len(ids))
        for i := range errs {
            errs[i] = err
        }
        return nil, errs
    }

    // Map results back to input order
    userByID := make(map[string]*model.User, len(users))
    for _, u := range users {
        userByID[u.ID] = u
    }
    result := make([]*model.User, len(ids))
    for i, id := range ids {
        result[i] = userByID[id]
    }
    return result, nil
}, dataloadgen.WithWait(2*time.Millisecond))
```

The loader is request-scoped — create a new one per request and attach it to the context. Then your field resolver becomes:

```go
func (r *postResolver) Author(ctx context.Context, obj *model.Post) (*model.User, error) {
    return loaders.FromContext(ctx).UserLoader.Load(ctx, obj.AuthorID)
}
```

Now, when a query requests 50 posts and their authors, all 50 `Load` calls happen within the same resolver execution window. DataLoader batches them into one `GetUsersByIDs` call. One query instead of 50. The latency difference in production is dramatic.

The `WithWait` duration (2ms in the example) is the window DataLoader waits for additional loads to coalesce before dispatching the batch. Too high and you add latency to every request. Too low and you miss loads that happen a tick later. Two milliseconds is usually a safe default; profile your specific workload.

## Middleware and Context

GraphQL middleware in gqlgen is implemented via the `graphql.FieldMiddleware` interface or, more commonly, the `extension` system. For cross-cutting concerns like authentication, request logging, and complexity limits, I use handler-level middleware rather than resolver-level.

Authentication is typically handled at the HTTP handler level. Parse the JWT or session token, put the authenticated user in the context, and read it in resolvers that require auth:

```go
func AuthMiddleware(next http.Handler) http.Handler {
    return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        token := r.Header.Get("Authorization")
        if token != "" {
            user, err := validateToken(token)
            if err == nil {
                r = r.WithContext(context.WithValue(r.Context(), userContextKey, user))
            }
        }
        next.ServeHTTP(w, r)
    })
}
```

In resolvers:

```go
func requireAuth(ctx context.Context) (*model.User, error) {
    user, ok := ctx.Value(userContextKey).(*model.User)
    if !ok || user == nil {
        return nil, &gqlerror.Error{
            Message:    "authentication required",
            Extensions: map[string]interface{}{"code": "UNAUTHENTICATED"},
        }
    }
    return user, nil
}
```

## Query Complexity Limits

Without complexity limits, a malicious or careless client can send a deeply nested query that triggers exponential resolver calls. gqlgen has built-in complexity limit support:

```go
srv := handler.NewDefaultServer(generated.NewExecutableSchema(generated.Config{
    Resolvers: &graph.Resolver{},
}))
srv.Use(extension.FixedComplexityLimit(100))
```

You can also define custom complexity calculations per field if the default (each field = 1) doesn't capture the actual cost of expensive resolvers.

For production systems, also add query depth limits and a timeout on the request context. A 5-second timeout and a depth limit of 10 catch most abusive patterns without impacting legitimate clients.

## Error Handling

GraphQL has its own error model — errors are returned alongside data in the response, not as HTTP error codes. gqlgen handles this through `gqlerror.Error`. Use error extensions to include machine-readable codes:

```go
return nil, &gqlerror.Error{
    Message: "post not found",
    Extensions: map[string]interface{}{
        "code": "NOT_FOUND",
        "id":   id,
    },
}
```

Domain errors (not found, validation failed, unauthorized) should be returned as GraphQL errors with meaningful codes. Infrastructure errors (database timeout, unexpected nil) should be logged with full context and returned to the client as a generic "internal server error" — never expose internal details in error messages.

## A Note on Testing

Resolver testing in gqlgen works best through the full HTTP stack, not by calling resolver functions directly. Write tests that POST GraphQL queries to your server handler and assert on the JSON response. This catches schema-resolver mismatches, middleware behavior, and serialization issues that unit tests on individual resolvers would miss.

```go
func TestGetPost(t *testing.T) {
    srv := newTestServer(t)
    query := `{ "query": "{ post(id: \"1\") { id title author { name } } }" }`
    resp := postGraphQL(t, srv, query)
    require.Equal(t, http.StatusOK, resp.Code)
    // assert on resp.Body JSON
}
```

---

🎓 Course Complete! You've finished **GraphQL in Practice**. From understanding when GraphQL is the right choice, through deliberate schema design, to a production-ready Go implementation with batching and complexity limits — you have the full picture. The N+1 problem will not surprise you in production.

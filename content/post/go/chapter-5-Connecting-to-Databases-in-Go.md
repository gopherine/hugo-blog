---
title: 'Chapter 5: Connecting to Databases in Go'
author: Atharva Pandey
keywords:
  - sqlc and postgres
  - golang migrate and sqlc
  - connect database with sqlc
  - database connectivity with sqlc
  - Go
tags:
  - postgres
  - sqlc
  - go
date: 2024-03-11T18:30:00.000Z
---

When building a web application, connecting to a database is a fundamental step. In this guide, we'll explore how to effectively connect to a database using Go, focusing on tools like golang-migrate, sqlc, and godotenv for environment management, alongside the Echo framework to set up a basic application.

## Setting Up Your Environment

Before diving into the code, ensure your environment is ready. You'll need Go installed on your system. The steps vary slightly between Windows, macOS, and Ubuntu, but generally, you can download the installer or package from the Go official website and follow the provided instructions.

For managing databases, docker-compose is an efficient tool, especially for local development. Install Docker and Docker Compose by following the instructions on the Docker website.

## Project Initialization

Start by creating a new directory for your project and initializing a new Go module:

```shell
mkdir vasavigo && cd vasavigo
go mod init github.com/atharva/vasavigo
```

Then, add the necessary dependencies to your go.mod file by running:\\

```shell
go get github.com/jackc/pgx/v5
go get github.com/labstack/echo/v4
go get github.com/joho/godotenv
```

## Database Setup with Docker Compose

For local development, use docker-compose to spin up a PostgreSQL container. Create a docker-compose.yml file with the following content:\\

```yaml
version: '3.9'

services:
  postgres:
    image: postgres:14-alpine
    ports:
      - 5432:5432
    volumes:
      - ./postgres_data:/var/lib/postgresql/data
    environment:
      - POSTGRES_PASSWORD=secret
      - POSTGRES_USER=root
      - POSTGRES_DB=postgres

```

Run docker-compose up -d to start the PostgreSQL container.

## Managing Environment Variables with godotenv

Storing configuration in environment variables is a best practice. godotenv allows you to load env variables from a .env file. Create a .env file at the root of your project with the following content:\\

```
DATABASE_URL=postgres://root:secret@localhost:5432/postgres?sslmode=disable
```

In your main.go, initialize godotenv to load these variables:\\

```go
import (
    "github.com/joho/godotenv"
    // other imports
)

func init() {
    if err := godotenv.Load(); err != nil {
        log.Fatal("Error loading .env file")
    }
}

```

## Connecting to the Database with pgx

Use the pgx library to connect to PostgreSQL. Modify your main.go to include the database connection logic:\\

```go
conn, err := pgx.Connect(ctx, os.Getenv("DATABASE_URL"))
if err != nil {
    log.Fatalf("Unable to connect to database: %v\n", err)
}
defer conn.Close(ctx)

```

## Generating Database Code with sqlc

sqlc generates type-safe code from SQL. Define your queries in .sql files inside the repo/queries directory and your schema in the repo/migrations directory. Use sqlc.yaml to configure sqlc, then run sqlc generate to produce the Go code for interacting with the database.

In the sqlc.yaml file, you define the SQL engine (PostgreSQL in this case), the locations of your queries and migrations, and the output package for the generated code:

```yaml
version: "2"
sql:
  - engine: "postgresql"
    # queries refers to where our manually created queries located
    queries: "repo/queries" # will refer a directory
    # schema refers to where our schema definitions located
    schema: "repo/migrations" # will refer a directory
    gen:
      go:
        package: "repo"
        sql_package: "pgx/v5"
        out: "repo"

```

\
With this configuration, sqlc looks in ./repo/queries for your SQL query files and in ./repo/migrations for your schema definitions.

Your SQL query file (test.sql) contains named queries that sqlc uses to generate Go methods. For example:

```sql
-- name: CreateTest :one
INSERT INTO test (name, bio)
VALUES ($1, $2)
RETURNING id, name, bio;

```

This CreateTest query inserts a new record into the test table and returns the inserted data. sqlc generates a Go method with a signature similar to CreateTest(ctx context.Context, arg CreateTestParams) (Test, error), which you can then call in your service layer.

## Database Migrations with Golang Migrate

golang-migrate is a tool for handling database migrations. To install it, follow the instructions on their [GitHub page](https://github.com/golang-migrate/migrate).

Once installed, create a migrations directory in your project to store your migration files. Use the migrate command to create new migrations:\\

```shell
migrate create -ext sql -dir ./repo/migrations -seq create_tests
```

This command will generate two files: one for the up migration and one for the down migration. Fill these files with your SQL statements to create and drop the tests table. In your repo/migrations directory, you'll have SQL files defining the schema for your test table. The .up.sql file contains the SQL commands to create the table and any indexes:\\

```sql
CREATE TABLE test (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    bio TEXT
);

CREATE INDEX IF NOT EXISTS idx_test_id ON test(id);

```

The corresponding .down.sql file contains the commands to undo these operations, typically dropping the table and indexes:\\

```sql
DROP INDEX IF EXISTS idx_test_id;
DROP TABLE IF EXISTS test;

```

These migration files are used by the golang-migrate tool to set up and tear down the database schema during development and deployment processes.

By integrating these elements—golang-migrate for database migrations, sqlc for generating type-safe database code, godotenv for environment management, and the Echo framework for HTTP routing and middleware—you create a robust foundation for developing web applications in Go with clean architecture, separating concerns and making your codebase more maintainable and scalable.

## Service Layer: Accessing Database Actions

The service layer in a Go application acts as a bridge between the HTTP handlers (controllers) and the database (repository). It encapsulates the business logic and calls the necessary repository functions to interact with the database.

In the provided example, the service layer defines an interface TestService with methods like  CreateTest, and possibly others for CRUD operations on the test table. The implementation of these methods uses the methods generated by sqlc based on your SQL queries to interact with the database.

For instance, the CreateTest method in the service layer might look something like this:\\

```go
func (s *Services) CreateTest(name, bio string) (Test, error) {
    // Use the CreateTest query generated by sqlc
    test, err := s.DB.CreateTest(s.ctx, db.CreateTestParams{
        Name: name,
        Bio:  bio,
    })
    if err != nil {
        return nil, err
    }
    return test, nil
}

```

## Setting Up the Echo Framework

Echo is a high-performance web framework for Go. In your main.go, set up basic routes and start the Echo server:\\

```go
e:= echo.New()

// Define routes
e.GET("/create", testController.CreateTest)

// Start server
e.Logger.Fatal(e.Start(":1323"))

```

\
Putting It All Together
-----------------------

With the database, migrations, environment variables, and web server set up, your application is ready to run. Start the Echo server, and you'll be able to interact with your PostgreSQL database through the defined routes.

This guide provides a solid foundation for building web applications in Go with database connectivity. Each tool—golang-migrate, sqlc, godotenv, and the Echo framework—plays a crucial role in the development process, making your application robust, maintainable, and scalable.

Remember, this is just a starting point. As your application grows, you'll likely introduce more tools and practices to enhance its functionality and performance. Happy coding!

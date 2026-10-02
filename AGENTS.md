# AGENTS.md

## Tooling

- **Hugo** builds the site. There are no JS build deps — CSS is processed by Hugo's asset pipeline.
- `bun` is only a convenience script runner (`bun run dev`); plain `hugo` works everywhere.

## Commands

- `bun run dev` (or `hugo server -D --renderToMemory`) — dev server at http://localhost:1313
- `bun run build` (or `hugo --minify`) — production build into `public/`

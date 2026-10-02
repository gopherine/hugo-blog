# AGENTS.md

## Tooling

- Use **bun** for this repo, never npm or yarn. Install with `bun install`, run scripts with `bun run <script>` (`bun run dev`, `bun run build`).
- Hugo builds the site; bun handles the JS/CSS toolchain (Tailwind + PostCSS).

## Commands

- `bun run dev` — local dev server at http://localhost:1313
- `bun run build` — production build into `public/`

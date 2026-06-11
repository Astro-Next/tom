# Templates

`tom init` creates three project files that agents read before doing work. These files start as scaffolds with placeholder content — fill them in to give agents the context they need.

## CLAUDE.md

Agent instructions. Every Claude Code subprocess reads this file automatically.

```markdown
# Agent Instructions

Instructions for AI agents working on this project.

## Project Overview
<!-- Describe what this project does, its purpose, and key technologies. -->

## Build & Test
<!-- Commands agents need to run:
- Build: `npm run build`
- Test: `npm test`
- Lint: `npm run lint`
-->

## Key Files
<!-- Important entry points and files agents should know about:
- `src/index.ts` — main entry point
- `src/config.ts` — configuration
-->

## Project Context
- `docs/index.md` — project knowledge and documentation
- `CONVENTIONS.md` — coding patterns and standards
```

**What to fill in:**
- **Project Overview** — what the project does, the language and framework, anything an agent needs to orient itself.
- **Build & Test** — the exact commands to build, test, and lint. Agents run these after writing code. If your project uses a different toolchain, replace the examples.
- **Key Files** — entry points, config files, and any files agents should read before making changes.
- **Project Context** — already filled in. These point agents to the other project files.

## CONVENTIONS.md

Coding standards. Dev agents follow these when writing code, review agents check against them.

```markdown
# Conventions
<!-- Add conventions as the project evolves. Examples:
## Naming
- Components: PascalCase
- Functions: camelCase
- Files: kebab-case

## Patterns
- Use existing utilities from `src/utils/` before creating new ones
- Error handling: use `Result` type, not try/catch

## Testing
- Co-locate tests: `foo.ts` → `foo.test.ts`
- Use integration tests for API endpoints
-->
```

**What to fill in:**
- **Naming** — casing rules for files, functions, classes, variables, whatever your project uses.
- **Patterns** — preferred idioms, error handling style, how to structure modules, what to reuse.
- **Testing** — where tests live, what kind of tests to write, coverage expectations.

Start small. Add conventions as the project evolves and as you notice agents making choices you'd correct.

## docs/index.md

Project knowledge base. Agents read this to understand the domain, architecture, and key decisions.

```markdown
<!-- # Project Knowledge

Entry point for project documentation. Organize knowledge into subdirectories by topic:

Example:
docs/
  index.md          — this file, links to all topics
  architecture/     — system design and key decisions
  api/              — endpoints and data models
  guides/           — setup, workflows, how-tos

## Architecture
- [auth design](arch/auth.md) — OAuth2 flow, token storage, session management
- [login process](arch/login-flow.md) — step-by-step login sequence and error handling

## API
- [users API](api/users.md) — user CRUD endpoints and permissions

## Guides
- [local setup](guides/setup.md) — dev environment, dependencies, first run
-->
```

**What to fill in:**
- Uncomment the content and replace the examples with your project's actual docs.
- Link to architecture docs, API references, setup guides — anything that helps an agent understand the codebase.
- This file is the entry point. Agents start here and follow links to learn more.

## .tom/settings.json

The default configuration file. See [Configuration](configuration.md) for the full schema and field reference.

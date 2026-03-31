# Design Documents

Design documents are stored in `design-docs/` with a 7-digit zero-padded sequential number prefix.

## Format

```
design-docs/{NNNNNNN}-{slug}/design-doc.md
```

Example: `design-docs/0000001-a2a-registry-broker/design-doc.md`

## Rules

- Always check the latest number before creating a new design document
- Increment by 1 from the highest existing number
- Use 7-digit zero-padding (e.g., `0000001`, `0000002`, `0000003`)
- The slug should be a kebab-case short description of the feature

## How to find the next number

Look at existing directories in `design-docs/` and use the next sequential number.

## Implementation Order

When implementing a design document, ALWAYS update documentation FIRST before writing any code.

The first implementation step in every design document must be:
- Update `ARCHITECTURE.md` with the new feature's architecture
- Update `docs/` directory with usage and configuration details
- Update relevant skill documentation
- Update README if needed
- Update project rules if needed

Only after documentation is complete should code implementation begin.

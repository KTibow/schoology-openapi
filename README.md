# schoology-openapi

An [OpenAPI 3.1](https://spec.openapis.org/oas/v3.1.0) description of the
**Schoology REST API** (`https://api.schoology.com/v1`), reconstructed from the
[official developer documentation](https://developers.schoology.com/api/)
(mirrored as [schoology-docs-md](https://kendell.dev/schoology-docs-md/)) and
checked against a real account.

- **Reference:** <https://kendell.dev/schoology-openapi/>
- **The spec:** <https://kendell.dev/schoology-openapi/openapi.json> — one
  self-contained document, no external `$ref`s. Fetch it and parse it; that is
  the intended interface, and everything worth knowing is in it.

Two builds ship. Use the first unless you are validating responses:

| File | `additionalProperties` | For |
| --- | --- | --- |
| `openapi.json` | closed on requests, open on responses | clients, codegen, publishing |
| `openapi.strict.json` | closed everywhere | linting and response verification |

## Development

```sh
pnpm install
pnpm run lint            # build + strictness rules + Spectral
pnpm run verify          # request fixtures, plus live responses if probe/ exists
pnpm run site:build      # -> site/dist
```

`spec/` is the source of truth: `openapi.yaml` (root document and tags),
`paths/`, and `components/`. `scripts/build.py` bundles it into `dist/`;
`lint_spec.py` enforces the house rules that Spectral cannot.
`.github/workflows/deploy.yml` builds, lints, verifies and deploys to Pages on
push to `main`.

### Live probing (optional)

`scripts/probe*.py` read a live account to check response shapes against the
schemas. They need credentials in `.env` (gitignored):

```
SC_KEY_75=<consumer key>
SC_SECRET_75=<consumer secret>
TOKEN_KEY=<access token key>
TOKEN_SECRET=<access token secret>
```

They only ever issue GETs, and they write responses to `probe/` (also
gitignored). Keep it that way: nothing derived from a real account — ids
included — belongs in a commit. `spec/examples.json` is committed but
generated, taking only key names and JSON types from `probe/` and every scalar
from a fixed fictional dictionary.

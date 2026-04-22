# Build & deploy

Build:

```bash
cd docs-site
pip install -r requirements.txt
mkdocs serve         # http://localhost:8000
mkdocs build         # static files in site/
```

Deploy options:

1. **Railway static service** — new service pointing at `docs-site/site/`, domain `docs.telecomtowerpower.com.br`.
2. **GitHub Pages** — push `site/` to `gh-pages` branch or use `mkdocs gh-deploy`.
3. **Caddy reverse proxy** — serve `site/` from the existing Caddy container under `/docs/`.

## Adding pages

1. Create `docs-site/docs/<path>/file.md` (Portuguese original).
2. Create mirror at `docs-site/docs/en/<path>/file.md`.
3. Add the PT path to `nav:` in `mkdocs.yml`; English nav inherits the same structure via `mkdocs-static-i18n`.

## CI

Add to `.github/workflows/docs.yml`:

```yaml
name: docs
on:
  push:
    branches: [main]
    paths: ["docs-site/**"]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install -r docs-site/requirements.txt
      - run: cd docs-site && mkdocs gh-deploy --force
```

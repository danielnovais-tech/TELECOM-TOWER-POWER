# Contributing to TELECOM-TOWER-POWER

Thank you for your interest in TELECOM-TOWER-POWER. This repository is
**proprietary** (see [LICENSE.md](LICENSE.md)); contributions are welcome but
are accepted only under the terms below.

## 1. Before you contribute

- Open an issue describing the change **before** writing non-trivial code.
  Drive-by PRs that conflict with the product roadmap will be closed.
- Security issues MUST NOT be filed as public issues. Follow
  [SECURITY.md](SECURITY.md).
- Do not include third-party code with incompatible licenses (GPL, AGPL,
  SSPL, CC-BY-NC, or any "no commercial use" clause). Apache-2.0, MIT, BSD,
  ISC, MPL-2.0 and Python-PSF are acceptable subject to review.

## 2. Developer Certificate of Origin (DCO)

By contributing, you certify the [Developer Certificate of Origin
1.1](https://developercertificate.org/). Every commit MUST be signed off:

```
git commit -s -m "fix(coverage): correct fresnel zone clearance edge case"
```

This adds a `Signed-off-by: Your Name <you@example.com>` trailer asserting
that you have the right to submit the contribution under the project's
license.

CI rejects PRs containing commits without `Signed-off-by`.

## 3. Licensing of contributions

By submitting a contribution, you agree that:

1. The copyright holder of TELECOM-TOWER-POWER may distribute your
   contribution under the **TELECOM-TOWER-POWER Proprietary Business
   License** ([LICENSE.md](LICENSE.md)) and, after the Change Date defined
   therein, under **Apache-2.0**.
2. You grant the copyright holder a perpetual, worldwide, non-exclusive,
   royalty-free, irrevocable license to your contribution under any license
   the copyright holder chooses to apply to the project, including the right
   to relicense.
3. You represent that the contribution is your original work, OR that you
   have the right to submit it (e.g. your employer has authorized the
   contribution), and that the contribution does not knowingly infringe any
   third-party patent, copyright, trademark, or trade secret.
4. You explicitly grant a patent license under any patents you own that are
   necessarily infringed by your contribution, on the same terms as
   Apache-2.0 §3.

For substantial contributions (≥ 50 lines of non-trivial code, new modules,
new endpoints, model changes, or anything touching `bedrock_service.py`,
`coverage_*.py`, `models.py`, ANATEL loaders, or licensing-related files),
the copyright holder may additionally require a signed Contributor License
Agreement (CLA). You will be contacted on the PR if so.

## 4. Code style and quality

- Python 3.10. Run `make lint` and `make test` before opening a PR.
- New endpoints require: OpenAPI annotations, a test in `tests/`, an entry
  in audit logging, and rate-limit consideration.
- Database changes require an Alembic migration; do not edit historical
  migrations.
- Model artefact changes (`coverage_model.npz`, training pipeline) require
  benchmark numbers vs. the Hata baseline in the PR description.

## 5. SPDX headers

New source files MUST include an SPDX header in the first 5 lines:

```python
# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
```

Files copied from upstream Apache-2.0/MIT/BSD projects must keep their
original headers and be listed in `NOTICE`.

## 6. What we will not accept

- Changes that re-license the project or any subset of files without the
  copyright holder's written approval.
- Inclusion of OpenCellID raw rows, geocoding caches from third-party
  providers, ANATEL bulk dumps, or any material whose redistribution would
  violate [LICENSE-DATA.md](LICENSE-DATA.md).
- Hard-coded credentials, sample API keys, customer data, or PII in tests
  or fixtures.
- Disabling CI checks, security scanners, or license scanners to make a PR
  pass.

## 7. Review and merge

- At least one approving review from a maintainer is required.
- The maintainer merges; contributors do not self-merge.
- Squash-merge is the default; commit message MUST retain `Signed-off-by`.

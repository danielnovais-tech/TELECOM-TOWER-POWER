# LICENSE-DATA — Data, Models and Derived Artefacts

Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER").
All rights reserved.

This document governs **data assets, trained models and derived artefacts**
shipped with or produced by this repository. It is **separate from**
[LICENSE.md](LICENSE.md), which covers source code only. In case of conflict
between this document and an upstream license of a third-party dataset, the
upstream license prevails for the upstream portion.

---

## 1. Scope

The following artefacts are governed by this document:

| Artefact | Path / Identifier | Origin |
|---|---|---|
| Trained coverage model (weights) | `coverage_model.npz` | Internally trained |
| Geocoding cache | `geocode_cache_br.json` | Derived from third-party geocoder |
| Curated tower dataset (loaded into Postgres) | output of `load_anatel.py`, `snap_anatel.py`, `load_opencellid.py`, `load_brazil_towers.py`, `refresh_brazil_towers.py` | ANATEL + OpenCellID + IBGE |
| Brazilian municipalities reference | `municipios_brasileiros.csv` | IBGE |
| Bedrock prompt catalogues / quotas | `bedrock_quotas.json`, prompt strings in `bedrock_service.py` | Internal |
| Sample/test data | `sample_batch_test.csv`, `sample_receivers.csv`, `report.csv` | Internal, synthetic |
| SRTM elevation tiles consumed at runtime | `srtm_data/*.hgt` | NASA SRTM (public domain, US) |

This list is illustrative; any binary model weights, cached lookups,
embeddings, indexes or curated datasets produced by the code in this
repository are covered.

## 2. Third-party data — pass-through obligations

Some artefacts are derivatives of upstream datasets. The licensee MUST comply
with the upstream license for those portions:

- **ANATEL (estações licenciadas, Mosaico)** — public data from the Brazilian
  regulator. Attribution required: *"Source: ANATEL — Sistema Mosaico"*.
  The curated/cleaned form bundled here is a derivative work of the
  TELECOM-TOWER-POWER project; redistribution of the cleaned form is governed
  by Section 4 below.
- **OpenCellID** — distributed under **CC-BY-SA 4.0**. Any redistribution of
  raw OpenCellID-derived rows MUST preserve attribution and remain under
  CC-BY-SA 4.0. The trained model `coverage_model.npz` is **not** considered
  a redistribution of the OpenCellID dataset: it consumes aggregated/derived
  features and statistics, not reproducible per-row data.
- **IBGE (`municipios_brasileiros.csv`)** — open data with attribution
  ("Fonte: IBGE").
- **NASA SRTM** — US public domain. Attribution recommended.
- **Geocoding cache (`geocode_cache_br.json`)** — subject to the Terms of
  Service of the upstream geocoding provider. The licensee is responsible for
  ensuring that any local mirror, cache duration and downstream use comply
  with that provider's TOS. The cache MUST NOT be redistributed as a
  standalone dataset.

## 3. Permissions

Without a separate signed Data Agreement, the licensee MAY:

- Read, load and query these artefacts **locally** for evaluation and
  non-production POC purposes, consistent with [LICENSE.md](LICENSE.md).
- Use the artefacts as inputs to the licensee's own internal analysis,
  provided the **outputs** are not redistributed publicly in a form that
  reconstructs the curated datasets or model weights.

## 4. Restrictions

The licensee MUST NOT, without prior written authorization from the
copyright holder:

- Redistribute `coverage_model.npz` or any successor model weight artefact,
  in whole or in part, in any form (binary, serialized, transformed,
  quantized, distilled).
- Extract, reconstruct or attempt to reconstruct model weights, training
  data or training hyperparameters via API probing, output sampling,
  membership-inference, or any other technique.
- Use outputs of the model or of `/bedrock/*` endpoints to **train, fine-tune,
  distill or evaluate** a competing model.
- Redistribute the curated tower dataset as a standalone dataset (CSV, JSON,
  Parquet, GeoJSON, MBTiles, vector tiles, or any equivalent), except where
  explicitly required by an upstream license (e.g. CC-BY-SA portions of
  OpenCellID, isolated and clearly attributed).
- Republish the geocoding cache (`geocode_cache_br.json`).
- Bundle these artefacts into a third-party product, dataset, model hub
  upload, or commercial dataset offering.

## 5. Production use of the model

Production use of `coverage_model.npz` (defined as: any use that supports
revenue-generating activity, customer-facing decisioning, or external
publication of model outputs) requires a signed License Agreement. The
TELECOM-TOWER-POWER hosted SaaS includes such rights for subscribers under
the applicable Order Form.

## 6. LGPD / personal data

The artefacts in this document are intended to contain **no personal data**
(`dados pessoais` per Art. 5 LGPD). Tower coordinates, ANATEL station IDs and
operator names are not personal data. If the licensee enriches these
artefacts with personal data of receivers, end-users or third parties, the
licensee acts as **controlador** for that personal data and is solely
responsible for LGPD compliance, including legal basis, data-subject rights
and incident notification to ANPD.

The TELECOM-TOWER-POWER hosted service acts as **operador** for personal
data submitted by customers via the API, under the terms of the applicable
Data Processing Addendum (DPA).

## 7. No warranty

Data and model artefacts are provided **as is**, without warranty of
accuracy, completeness, fitness for any particular purpose, regulatory
adequacy (including but not limited to ANATEL licensing decisions), or
absence of error. RF-engineering decisions, site-acquisition decisions,
and regulatory filings based on these artefacts are the sole responsibility
of the licensee.

## 8. Change Date

The Change Date in [LICENSE.md](LICENSE.md) (2028-05-01) **does not** apply
to this document. Trained models, geocode caches and curated datasets remain
under proprietary terms after that date unless explicitly relicensed in
writing.

## 9. Contact

For commercial licensing of model weights, dataset access, or production
use rights: see the contact information published at
<https://telecomtowerpower.com.br/>.

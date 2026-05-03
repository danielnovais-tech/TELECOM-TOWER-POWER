# ROI Report - Annual Savings Model

This report gives a reproducible annual ROI model for replacing part of
legacy RF-planning workflow with TELECOM-TOWER-POWER capabilities:

- rf-signals clean-room engine integration
- QGIS -> Atoll export automation
- Sionna-assisted prediction pipeline

Use this as a finance template. Update the inputs to your real numbers.

## 1) Annual Calculation Formula

Define yearly costs:

- Legacy yearly cost:
  - `legacy_licenses`
  - `legacy_manual_hours_per_week * hourly_rate * 52`
- New yearly cost:
  - `new_platform_licenses`
  - `new_manual_hours_per_week * hourly_rate * 52`
  - `new_infra_and_training`

Then:

`annual_savings = legacy_total - new_total`

Where:

`legacy_total = legacy_licenses + legacy_manual_hours_per_week * hourly_rate * 52`

`new_total = new_platform_licenses + new_manual_hours_per_week * hourly_rate * 52 + new_infra_and_training`

## 2) Input Assumptions (Editable)

All values in BRL/year unless noted.

| Input | Conservative | Base | Aggressive |
|---|---:|---:|---:|
| Legacy licenses (`legacy_licenses`) | 48,000 | 72,000 | 96,000 |
| Legacy manual hours/week | 6 | 10 | 14 |
| New platform licenses (`new_platform_licenses`) | 12,000 | 18,000 | 24,000 |
| New manual hours/week | 3 | 3 | 2 |
| New infra + retraining (`new_infra_and_training`) | 8,000 | 12,000 | 18,000 |
| Engineering hourly rate (`hourly_rate`) | 120 | 180 | 240 |

## 3) Annual Results

Computed from the exact formula above.

| Scenario | Legacy total | New total | Annual savings |
|---|---:|---:|---:|
| Conservative | 85,440 | 38,720 | 46,720 |
| Base | 165,600 | 58,080 | 107,520 |
| Aggressive | 270,720 | 66,960 | 203,760 |

## 4) Interpretation

- Conservative case already lands in "tens of thousands" BRL/year.
- Base and aggressive cases pass 100k BRL/year.
- Main drivers are reduced seat/license dependency and reduced manual hours.

## 5) Sensitivity Check

Savings sensitivity to 1 hour/week manual reduction is:

`delta = hourly_rate * 52`

Examples:

- At BRL 120/h: +6,240 BRL/year savings per 1 h/week reduced
- At BRL 180/h: +9,360 BRL/year savings per 1 h/week reduced
- At BRL 240/h: +12,480 BRL/year savings per 1 h/week reduced

## 6) Governance Notes

- This is an internal planning model, not accounting advice.
- Keep assumptions versioned with date and owner.
- Re-baseline quarterly using real observed manual effort and infra spend.

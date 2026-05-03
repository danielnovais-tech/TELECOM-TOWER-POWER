# DF Coverage Comparison Report: itmlogic vs ITU-R P.1812

**Generated:** 2026-05-03T23:12:58.161410+00:00

## Executive Summary

- **Towers analyzed:** 10
- **Mean delta:** 3.296 dB (itmlogic - P.1812)
- **Median delta:** 4.514 dB
- **MAE delta:** 7.194 dB
- **Max abs delta:** 15.326 dB

## Interpretation

Positive delta → **itmlogic is more pessimistic** (predicts higher path loss)

Negative delta → **itmlogic is more optimistic** (predicts lower path loss)

## Performance

- **itmlogic avg runtime:** 0.472 ms
- **P.1812 avg runtime:** 1.925 ms
- **Speedup:** 4.1x

## Distance-Stratified Analysis

### 0-5 km

- **Count:** 4
- **Mean delta:** -3.302 dB
- **Median delta:** -2.105 dB
- **Stdev:** 7.716 dB
- **MAE:** 6.444 dB
- **Max abs delta:** 12.245 dB

### 5-10 km

- **Count:** 2
- **Mean delta:** 4.982 dB
- **Median delta:** 4.982 dB
- **Stdev:** 2.679 dB
- **MAE:** 4.982 dB
- **Max abs delta:** 6.876 dB

### 10+ km

- **Count:** 4
- **Mean delta:** 9.051 dB
- **Median delta:** 7.550 dB
- **Stdev:** 4.403 dB
- **MAE:** 9.051 dB
- **Max abs delta:** 15.326 dB

## Detailed Results (All Towers)

| Index | Tower ID | Distance (km) | itmlogic (dB) | P.1812 (dB) | Delta (dB) | itmlogic (ms) | P.1812 (ms) |
|-------|----------|---------------|---------------|-------------|------------|---------------|------------|
|  7 | TIM_001      |  1.340 |        93.583 |     100.830 |     -7.247 |         0.499 |      2.582 |
|  1 | CLARO_001    |  3.427 |       123.140 |     120.104 |      3.036 |         0.632 |      1.453 |
|  2 | CLARO_002    |  4.397 |       103.899 |     116.144 |    -12.245 |         0.474 |      2.018 |
| 10 | VIVO_002     |  4.545 |       123.154 |     119.907 |      3.247 |         0.393 |      1.513 |
|  9 | VIVO_001     |  5.237 |       141.118 |     138.031 |      3.087 |         0.532 |      1.995 |
|  3 | OI_001       |  7.194 |       147.125 |     140.248 |      6.876 |         0.411 |      1.607 |
|  8 | TIM_002      | 10.280 |       148.080 |     142.300 |      5.781 |         0.414 |      1.999 |
|  6 | RURAL_003    | 17.086 |       155.393 |     146.505 |      8.888 |         0.513 |      1.832 |
|  4 | RURAL_001    | 17.773 |       169.077 |     153.751 |     15.326 |         0.462 |      2.713 |
|  5 | RURAL_002    | 18.569 |       143.172 |     136.960 |      6.211 |         0.394 |      1.537 |
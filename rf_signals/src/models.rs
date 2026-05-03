// SPDX-License-Identifier: LicenseRef-TTP-Proprietary
// Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
//
// Clean-room implementation of public-domain empirical RF propagation
// models. NONE of the formulas below come from copyleft sources — every
// equation here is sourced from openly published ITU/IEEE/IEE/CCIR
// references (citations next to each model). The only purpose of this
// binary is to provide a JSON-over-stdin/stdout shim that matches the
// `rf_engines/rf_signals_engine.py` adapter wire schema.
//
// Models implemented:
//   * FSPL              — Friis free-space path loss (ITU-R P.525-4)
//   * Okumura-HATA      — Hata's empirical fit, 150–1500 MHz urban/sub/rural
//                         (M. Hata, IEEE Trans. VT-29, 1980)
//   * COST-231-HATA     — Hata extension to 1500–2000 MHz
//                         (COST 231 final report, EUR 18957, 1999)
//   * ECC-33            — Electronic Communications Committee fixed
//                         wireless model, 2–3.5 GHz (ECC Report 33, 2003)
//   * EGLI              — Egli's plane-earth empirical, 40–1000 MHz
//                         (J.J. Egli, IRE Trans. Ant. Prop., 1957)
//   * Two-ray plane-earth — direct + reflected ray (Bullington 1947)

use std::f64::consts::PI;

const C_LIGHT: f64 = 2.998e8; // m/s

/// Free-space path loss (ITU-R P.525-4, eq. 6).
///
/// Lfs = 20 log10(d) + 20 log10(f) + 20 log10(4π/c)  [d in m, f in Hz]
/// or, with d in km and f in MHz:
/// Lfs ≈ 32.44 + 20 log10(d_km) + 20 log10(f_MHz)
pub fn fspl(f_hz: f64, d_m: f64) -> f64 {
    if d_m <= 0.0 || f_hz <= 0.0 {
        return 0.0;
    }
    20.0 * (4.0 * PI * d_m * f_hz / C_LIGHT).log10()
}

/// Okumura-Hata urban macrocell, 150–1500 MHz.
///
/// Reference: M. Hata, "Empirical Formula for Propagation Loss in Land
/// Mobile Radio Services," IEEE Trans. Vehicular Technology, vol VT-29,
/// no. 3, Aug 1980, pp 317–325.
///
/// All inputs in MHz / m / km.
/// `area_kind`: 0 = large city, 1 = small/medium city,
/// 2 = suburban, 3 = open/rural.
pub fn hata(
    f_mhz: f64,
    h_b_m: f64,
    h_m_m: f64,
    d_km: f64,
    area_kind: u8,
) -> f64 {
    // Mobile antenna correction a(hm)
    let a_hm = if area_kind == 0 {
        // Large city
        if f_mhz >= 300.0 {
            3.2 * (11.75_f64 * h_m_m).log10().powi(2) - 4.97
        } else {
            8.29 * (1.54_f64 * h_m_m).log10().powi(2) - 1.1
        }
    } else {
        // Small/medium city, suburban, rural
        (1.1 * f_mhz.log10() - 0.7) * h_m_m - (1.56 * f_mhz.log10() - 0.8)
    };

    let urban = 69.55
        + 26.16 * f_mhz.log10()
        - 13.82 * h_b_m.log10()
        - a_hm
        + (44.9 - 6.55 * h_b_m.log10()) * d_km.log10();

    match area_kind {
        0 | 1 => urban,
        2 => urban - 2.0 * (f_mhz / 28.0).log10().powi(2) - 5.4, // suburban
        _ => urban - 4.78 * f_mhz.log10().powi(2) + 18.33 * f_mhz.log10() - 40.94, // open
    }
}

/// COST-231-HATA, 1500–2000 MHz extension.
///
/// Reference: COST Action 231, "Digital mobile radio towards future
/// generation systems — final report", European Commission EUR 18957,
/// 1999, ch. 4 §4.1.4.
///
/// `metro` = true for metropolitan centres (Cm = 3 dB), false for
/// medium-sized cities/suburban (Cm = 0 dB).
pub fn cost231_hata(
    f_mhz: f64,
    h_b_m: f64,
    h_m_m: f64,
    d_km: f64,
    metro: bool,
) -> f64 {
    let a_hm = (1.1 * f_mhz.log10() - 0.7) * h_m_m - (1.56 * f_mhz.log10() - 0.8);
    let cm = if metro { 3.0 } else { 0.0 };
    46.3 + 33.9 * f_mhz.log10()
        - 13.82 * h_b_m.log10()
        - a_hm
        + (44.9 - 6.55 * h_b_m.log10()) * d_km.log10()
        + cm
}

/// ECC-33 (ECC Report 33, 2003) — fixed wireless access, 2–3.5 GHz.
///
/// Reference: ECC Report 33, "The analysis of the coexistence of FWA
/// cells in the 3.4–3.8 GHz band", May 2003, §5 Annex.
///
/// L = Afs + Abm − Gb − Gr   (urban), with optional medium-city /
/// suburban / open corrections.
pub fn ecc33(f_ghz: f64, h_b_m: f64, h_m_m: f64, d_km: f64, area_kind: u8) -> f64 {
    let f_mhz = f_ghz * 1000.0;
    let a_fs = 92.4 + 20.0 * d_km.log10() + 20.0 * f_ghz.log10();
    let a_bm = 20.41 + 9.83 * d_km.log10() + 7.894 * f_mhz.log10()
        + 9.56 * f_mhz.log10().powi(2);
    let g_b = h_b_m.log10() * (13.958 + 5.8 * d_km.log10().powi(2));
    let g_r = match area_kind {
        // Medium city
        1 => (42.57 + 13.7 * f_ghz.log10()) * (h_m_m.log10() - 0.585),
        // Suburban
        2 => 0.759 * h_m_m - 1.862,
        // Large city default + open: same Gr formula as medium
        _ => (42.57 + 13.7 * f_ghz.log10()) * (h_m_m.log10() - 0.585),
    };
    a_fs + a_bm - g_b - g_r
}

/// Egli plane-earth empirical, 40–1000 MHz.
///
/// Reference: J.J. Egli, "Radio Propagation above 40 Mc over Irregular
/// Terrain," Proc. IRE, vol. 45, no. 10, Oct 1957, pp 1383–1391.
///
/// L = 40 log10(d) − 20 log10(h_b · h_m) + β(f), where the β term
/// captures the frequency dependence beyond the plane-earth core.
pub fn egli(f_mhz: f64, h_b_m: f64, h_m_m: f64, d_m: f64) -> f64 {
    let beta = if f_mhz <= 40.0 {
        76.3 - 20.0 * f_mhz.log10()
    } else {
        85.9 - 20.0 * f_mhz.log10()
    };
    let core = 40.0 * d_m.log10() - 20.0 * (h_b_m * h_m_m).log10();
    core + beta
}

/// Two-ray plane-earth (Bullington 1947).
///
/// L = 40 log10(d) − 20 log10(h_b · h_m), valid when d ≫ √(h_b · h_m)
/// and reflection coefficient Γ ≈ −1. Frequency-independent (the
/// frequency dependence is folded into the FSPL break-point distance).
pub fn two_ray(h_b_m: f64, h_m_m: f64, d_m: f64) -> f64 {
    if d_m <= 0.0 || h_b_m <= 0.0 || h_m_m <= 0.0 {
        return 0.0;
    }
    40.0 * d_m.log10() - 20.0 * (h_b_m * h_m_m).log10()
}

/// Pick a sensible default model based on frequency, mirroring the
/// Atoll / Planet defaults used in TELECOM-TOWER-POWER's other engines.
/// Returns (`model_name`, `loss_db`).
pub fn auto(
    f_hz: f64,
    h_b_m: f64,
    h_m_m: f64,
    d_km: f64,
) -> (&'static str, f64) {
    let f_mhz = f_hz / 1.0e6;
    let d_m = d_km * 1000.0;
    let loss;
    let name;
    if f_mhz < 150.0 {
        // Below Hata's lower bound — Egli is the cleanest empirical fit.
        loss = egli(f_mhz, h_b_m.max(1.0), h_m_m.max(1.0), d_m.max(1.0));
        name = "egli";
    } else if f_mhz < 1500.0 {
        loss = hata(f_mhz, h_b_m.max(30.0), h_m_m.max(1.0), d_km.max(1.0), 1);
        name = "hata";
    } else if f_mhz < 2000.0 {
        loss = cost231_hata(f_mhz, h_b_m.max(30.0), h_m_m.max(1.0), d_km.max(1.0), false);
        name = "cost231-hata";
    } else if f_mhz < 3500.0 {
        let f_ghz = f_mhz / 1000.0;
        loss = ecc33(f_ghz, h_b_m.max(30.0), h_m_m.max(1.0), d_km.max(0.1), 1);
        name = "ecc33";
    } else {
        loss = fspl(f_hz, d_m.max(1.0));
        name = "fspl";
    }
    (name, loss)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn approx(a: f64, b: f64, tol: f64) -> bool {
        (a - b).abs() < tol
    }

    #[test]
    fn fspl_900mhz_1km() {
        // Friis: 32.44 + 20log10(1) + 20log10(900) ≈ 91.53 dB
        let l = fspl(900e6, 1000.0);
        assert!(approx(l, 91.53, 0.5), "FSPL 900 MHz @ 1 km got {l}");
    }

    #[test]
    fn hata_smoke() {
        // Spot-check vs textbook (Rappaport): f=900 MHz, hb=30 m, hm=1.5 m,
        // d=1 km, urban small/medium city → ≈ 126 dB ± a couple dB.
        let l = hata(900.0, 30.0, 1.5, 1.0, 1);
        assert!(l > 120.0 && l < 132.0, "Hata smoke {l}");
    }

    #[test]
    fn cost231_smoke() {
        // f=1800 MHz, hb=30, hm=1.5, d=1 km, suburban → ~133-138 dB.
        let l = cost231_hata(1800.0, 30.0, 1.5, 1.0, false);
        assert!(l > 128.0 && l < 142.0, "COST-231 smoke {l}");
    }

    #[test]
    fn auto_picks_hata_at_900() {
        let (name, _) = auto(900e6, 30.0, 1.5, 1.0);
        assert_eq!(name, "hata");
    }

    #[test]
    fn auto_picks_fspl_at_6ghz() {
        let (name, _) = auto(6e9, 30.0, 1.5, 1.0);
        assert_eq!(name, "fspl");
    }

    #[test]
    fn fspl_monotonic_in_distance() {
        let a = fspl(900e6, 100.0);
        let b = fspl(900e6, 1000.0);
        let c = fspl(900e6, 10000.0);
        assert!(b > a && c > b);
    }

    #[test]
    fn loss_increases_with_frequency_at_fixed_distance_fspl() {
        let lo = fspl(450e6, 1000.0);
        let hi = fspl(2400e6, 1000.0);
        assert!(hi > lo);
    }
}

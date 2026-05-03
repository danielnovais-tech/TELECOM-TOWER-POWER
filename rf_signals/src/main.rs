// SPDX-License-Identifier: LicenseRef-TTP-Proprietary
// Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
//
// rfsignals-cli — JSON-over-stdin/stdout RF propagation shim.
//
// Wire schema is documented in `rf_engines/rf_signals_engine.py`. In a
// nutshell:
//
//   stdin (JSON object):
//     {
//       "command": "predict-loss",
//       "frequency_hz": 9.0e8,
//       "distances_km": [0.0, ..., d_total_km],
//       "terrain_m":    [h0,  ..., h_n],     // same length as distances
//       "tx_height_agl_m": 30.0,
//       "rx_height_agl_m": 1.5,
//       "tx_lat": ..., "tx_lon": ...,
//       "rx_lat": ..., "rx_lon": ...,
//       "polarisation": "vertical" | "horizontal",
//       "time_pct": 50.0,
//       "loc_pct":  50.0,
//       "clutter_m": [...] | null,
//       "model": "auto" | "fspl" | "hata" | "cost231-hata"
//                | "ecc33" | "egli" | "two-ray"   // optional, default "auto"
//       "area":  "large_city" | "medium_city" | "suburban" | "open"
//                                                 // optional, default medium_city
//     }
//
//   stdout (JSON object):
//     {
//       "basic_loss_db": <f64>,
//       "model": <chosen>,
//       "version": "rfsignals-cli/0.1.0",
//       "confidence": 0.7
//     }
//
// Exit code 0 on success, non-zero on parse / domain error (with a
// JSON {"error": "..."} on stdout for diagnostics).

mod models;

use std::io::Read;
use std::process::ExitCode;

use serde::{Deserialize, Serialize};

const VERSION: &str = concat!("rfsignals-cli/", env!("CARGO_PKG_VERSION"));

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
#[allow(dead_code)] // wire-schema fields (lat/lon/terrain/...) accepted for forward-compat
struct Request {
    #[serde(default = "default_command")]
    command: String,
    frequency_hz: f64,
    distances_km: Vec<f64>,
    #[serde(default)]
    terrain_m: Vec<f64>,
    tx_height_agl_m: f64,
    rx_height_agl_m: f64,
    #[serde(default)]
    tx_lat: f64,
    #[serde(default)]
    tx_lon: f64,
    #[serde(default)]
    rx_lat: f64,
    #[serde(default)]
    rx_lon: f64,
    #[serde(default = "default_polarisation")]
    polarisation: String,
    #[serde(default = "default_pct")]
    time_pct: f64,
    #[serde(default = "default_pct")]
    loc_pct: f64,
    #[serde(default)]
    clutter_m: Option<Vec<f64>>,
    #[serde(default = "default_model")]
    model: String,
    #[serde(default = "default_area")]
    area: String,
}

fn default_command() -> String { "predict-loss".into() }
fn default_polarisation() -> String { "vertical".into() }
fn default_pct() -> f64 { 50.0 }
fn default_model() -> String { "auto".into() }
fn default_area() -> String { "medium_city".into() }

#[derive(Debug, Serialize)]
struct Response {
    basic_loss_db: f64,
    model: String,
    version: &'static str,
    confidence: f64,
}

#[derive(Debug, Serialize)]
struct ErrorResponse {
    error: String,
}

fn area_kind(area: &str) -> u8 {
    match area {
        "large_city" => 0,
        "medium_city" | "small_city" => 1,
        "suburban" => 2,
        "open" | "rural" => 3,
        _ => 1,
    }
}

fn run(req: &Request) -> Result<Response, String> {
    if req.command != "predict-loss" {
        return Err(format!("unknown command: {}", req.command));
    }
    if req.distances_km.is_empty() {
        return Err("distances_km is empty".into());
    }
    let d_total_km = *req
        .distances_km
        .last()
        .ok_or_else(|| "distances_km is empty".to_string())?;
    if !(d_total_km.is_finite() && d_total_km > 0.0) {
        return Err(format!("invalid total distance: {d_total_km}"));
    }
    if !(req.frequency_hz.is_finite() && req.frequency_hz > 0.0) {
        return Err(format!("invalid frequency_hz: {}", req.frequency_hz));
    }
    let f_mhz = req.frequency_hz / 1.0e6;
    let h_b = req.tx_height_agl_m.max(0.0);
    let h_m = req.rx_height_agl_m.max(0.0);
    let d_km = d_total_km;
    let d_m = d_km * 1000.0;
    let area = area_kind(&req.area);

    let (chosen, loss) = match req.model.as_str() {
        "auto" => models::auto(req.frequency_hz, h_b, h_m, d_km),
        "fspl" => ("fspl", models::fspl(req.frequency_hz, d_m)),
        "hata" => ("hata", models::hata(f_mhz, h_b.max(30.0), h_m.max(1.0), d_km.max(1.0), area)),
        "cost231-hata" => ("cost231-hata", models::cost231_hata(f_mhz, h_b.max(30.0), h_m.max(1.0), d_km.max(1.0), area == 0)),
        "ecc33" => ("ecc33", models::ecc33(f_mhz / 1000.0, h_b.max(30.0), h_m.max(1.0), d_km.max(0.1), area)),
        "egli" => ("egli", models::egli(f_mhz, h_b.max(1.0), h_m.max(1.0), d_m.max(1.0))),
        "two-ray" => ("two-ray", models::two_ray(h_b.max(1.0), h_m.max(1.0), d_m.max(1.0))),
        other => return Err(format!("unknown model: {other}")),
    };

    if !loss.is_finite() {
        return Err(format!("model {chosen} produced non-finite loss"));
    }

    // Confidence is a coarse heuristic: empirical models within their
    // calibrated band get 0.75; FSPL/two-ray as fallbacks get 0.55.
    let confidence = match chosen {
        "fspl" | "two-ray" => 0.55,
        _ => 0.75,
    };

    Ok(Response {
        basic_loss_db: loss,
        model: chosen.into(),
        version: VERSION,
        confidence,
    })
}

fn main() -> ExitCode {
    // Accept "--json" flag for parity with the adapter call signature;
    // currently it's the only mode supported.
    let _ = std::env::args().skip(1).collect::<Vec<_>>();

    let mut buf = String::new();
    if std::io::stdin().read_to_string(&mut buf).is_err() {
        eprintln!("rfsignals-cli: failed to read stdin");
        return ExitCode::from(2);
    }

    let req: Request = match serde_json::from_str(&buf) {
        Ok(r) => r,
        Err(e) => {
            let err = ErrorResponse { error: format!("invalid request JSON: {e}") };
            println!("{}", serde_json::to_string(&err).unwrap_or_default());
            return ExitCode::from(2);
        }
    };

    match run(&req) {
        Ok(resp) => {
            println!("{}", serde_json::to_string(&resp).unwrap_or_default());
            ExitCode::SUCCESS
        }
        Err(msg) => {
            let err = ErrorResponse { error: msg };
            println!("{}", serde_json::to_string(&err).unwrap_or_default());
            ExitCode::from(1)
        }
    }
}

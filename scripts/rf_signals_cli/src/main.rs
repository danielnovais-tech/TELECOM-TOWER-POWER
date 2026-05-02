// SPDX-License-Identifier: LicenseRef-TTP-Proprietary
// Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER").
//
// Tiny CLI shim that bridges TELECOM-TOWER-POWER's Python adapter
// (rf_engines/rf_signals_engine.py) and the upstream rf-signals
// crate. The shim reads a JSON envelope from stdin, calls the
// crate's path-loss API, and writes a JSON result to stdout.
//
// The wire schema is intentionally narrow: we only expose what the
// platform's link engine needs (basic transmission loss in dB).
// Anything richer (heatmap rasters, multi-link batches) belongs in
// a separate subcommand to keep the cold-start cost low.

use anyhow::{anyhow, Result};
use clap::Parser;
use serde::{Deserialize, Serialize};
use std::io::Read;

#[derive(Parser, Debug)]
#[command(version, about = "rf-signals JSON shim for TELECOM-TOWER-POWER")]
struct Args {
    /// Read JSON request from stdin, write JSON response to stdout.
    #[arg(long, default_value_t = true)]
    json: bool,
}

#[derive(Deserialize, Debug)]
#[serde(deny_unknown_fields)]
struct Request {
    command: String,
    frequency_hz: f64,
    distances_km: Vec<f64>,
    terrain_m: Vec<f64>,
    tx_height_agl_m: f64,
    rx_height_agl_m: f64,
    tx_lat: f64,
    tx_lon: f64,
    rx_lat: f64,
    rx_lon: f64,
    polarisation: String,
    time_pct: f64,
    loc_pct: f64,
    clutter_m: Option<Vec<f64>>,
}

#[derive(Serialize, Debug)]
struct Response {
    basic_loss_db: f64,
    confidence: f64,
    model: &'static str,
    version: &'static str,
}

fn run(req: Request) -> Result<Response> {
    if req.command != "predict-loss" {
        return Err(anyhow!("unknown command: {}", req.command));
    }
    if req.distances_km.len() != req.terrain_m.len() || req.distances_km.len() < 2 {
        return Err(anyhow!("distances_km / terrain_m length mismatch"));
    }

    // The upstream crate exposes its propagation algorithms via the
    // `rf_signals::path_loss` module. We default to the ITM (Longley-Rice)
    // model which matches what cloud-rf uses. This call is intentionally
    // wrapped so a future upstream API change is isolated to this file.
    let loss_db = rf_signals::path_loss::itm::point_to_point(
        req.frequency_hz / 1e6,        // MHz
        req.tx_height_agl_m,           // m AGL
        req.rx_height_agl_m,           // m AGL
        &req.terrain_m,                // terrain profile (m AMSL)
        &req.distances_km,             // distances (km)
        if req.polarisation == "vertical" { 1 } else { 0 },
        req.time_pct / 100.0,
        req.loc_pct / 100.0,
    )
    .map_err(|e| anyhow!("rf-signals point_to_point failed: {e:?}"))?;

    Ok(Response {
        basic_loss_db: loss_db,
        confidence: 0.85,
        model: "itm",
        version: env!("CARGO_PKG_VERSION"),
    })
}

fn main() -> Result<()> {
    let _ = Args::parse();
    let mut buf = String::new();
    std::io::stdin().read_to_string(&mut buf)?;
    let req: Request = serde_json::from_str(&buf)?;
    let resp = run(req)?;
    println!("{}", serde_json::to_string(&resp)?);
    Ok(())
}

/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Band } from './Band';
/**
 * Request body for /coverage/predict.
 *
 * Provide either ``tower_id`` (existing tower) **or** the explicit
 * ``tx_lat`` / ``tx_lon`` / ``tx_height_m`` / ``band`` quartet.
 * Provide either a single receiver (``rx_lat``/``rx_lon``) **or** a
 * bounding box (``bbox``) to compute a coverage grid.
 */
export type CoveragePredictRequest = {
    tower_id?: (string | null);
    tx_lat?: (number | null);
    tx_lon?: (number | null);
    tx_height_m?: (number | null);
    tx_power_dbm?: number;
    tx_gain_dbi?: number;
    band?: (Band | null);
    rx_lat?: (number | null);
    rx_lon?: (number | null);
    rx_height_m?: number;
    rx_gain_dbi?: number;
    /**
     * [min_lat, min_lon, max_lat, max_lon] for grid mode
     */
    bbox?: (Array<number> | null);
    grid_size?: number;
    feasibility_threshold_dbm?: number;
    explain?: boolean;
};


/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type LinkAnalysisResponse = {
    feasible: boolean;
    signal_dbm: number;
    fresnel_clearance: number;
    los_ok: boolean;
    distance_km: number;
    recommendation: string;
    terrain_profile?: (Array<number> | null);
    tx_height_asl?: (number | null);
    rx_height_asl?: (number | null);
};


/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { Band } from './Band';
export type TowerInput = {
    id: string;
    lat: number;
    lon: number;
    height_m: number;
    operator: string;
    bands: Array<Band>;
    power_dbm?: number;
};


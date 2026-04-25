/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type BedrockAntennaRequest = {
    /**
     * Link analysis result
     */
    analysis: Record<string, any>;
    /**
     * Tower information
     */
    tower: Record<string, any>;
    /**
     * Target Fresnel zone clearance fraction
     */
    target_clearance?: number;
    model_id?: (string | null);
};


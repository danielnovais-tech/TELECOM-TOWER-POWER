/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type BedrockBatchAnalysisRequest = {
    /**
     * Link analysis results to analyze
     */
    batch_results: Array<Record<string, any>>;
    question?: (string | null);
    model_id?: (string | null);
    max_tokens?: (number | null);
    temperature?: (number | null);
};


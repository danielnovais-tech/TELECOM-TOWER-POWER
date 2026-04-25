/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type BedrockScenarioRequest = {
    /**
     * List of scenario dicts to compare
     */
    scenarios: Array<Record<string, any>>;
    /**
     * Optional custom question
     */
    question?: (string | null);
    /**
     * Bedrock model ID override
     */
    model_id?: (string | null);
    max_tokens?: (number | null);
    temperature?: (number | null);
};


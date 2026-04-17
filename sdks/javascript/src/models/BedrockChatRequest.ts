/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
export type BedrockChatRequest = {
    /**
     * User prompt
     */
    prompt: string;
    /**
     * Bedrock model ID override
     */
    model_id?: (string | null);
    /**
     * Max response tokens
     */
    max_tokens?: (number | null);
    /**
     * Sampling temperature
     */
    temperature?: (number | null);
    /**
     * Analysis context JSON
     */
    context?: (string | null);
};


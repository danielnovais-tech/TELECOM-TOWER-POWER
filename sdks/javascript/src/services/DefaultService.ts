/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { BedrockAntennaRequest } from '../models/BedrockAntennaRequest';
import type { BedrockBatchAnalysisRequest } from '../models/BedrockBatchAnalysisRequest';
import type { BedrockChatRequest } from '../models/BedrockChatRequest';
import type { BedrockScenarioRequest } from '../models/BedrockScenarioRequest';
import type { Body_batch_reports_batch_reports_post } from '../models/Body_batch_reports_batch_reports_post';
import type { CheckoutRequest } from '../models/CheckoutRequest';
import type { CoveragePredictRequest } from '../models/CoveragePredictRequest';
import type { KeyLookupRequest } from '../models/KeyLookupRequest';
import type { LinkAnalysisResponse } from '../models/LinkAnalysisResponse';
import type { PrefetchRequest } from '../models/PrefetchRequest';
import type { ReceiverInput } from '../models/ReceiverInput';
import type { SignupRequest } from '../models/SignupRequest';
import type { TowerInput } from '../models/TowerInput';
import type { CancelablePromise } from '../core/CancelablePromise';
import type { BaseHttpRequest } from '../core/BaseHttpRequest';
export class DefaultService {
    constructor(public readonly httpRequest: BaseHttpRequest) {}
    /**
     * Root
     * Health check and API overview.
     * @returns any Successful Response
     * @throws ApiError
     */
    public rootGet(): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/',
        });
    }
    /**
     * Health Check
     * Lightweight liveness / readiness probe for load balancers.
     * @returns any Successful Response
     * @throws ApiError
     */
    public healthCheckHealthGet(): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/health',
        });
    }
    /**
     * Add Tower
     * Add a new tower to the database.
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public addTowerTowersPost(
        requestBody: TowerInput,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/towers',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * List Towers
     * List towers with pagination. Use *offset* and *limit* to page through results.
     * @param operator
     * @param limit
     * @param offset
     * @returns any Successful Response
     * @throws ApiError
     */
    public listTowersTowersGet(
        operator?: (string | null),
        limit: number = 1000,
        offset?: number,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/towers',
            query: {
                'operator': operator,
                'limit': limit,
                'offset': offset,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Nearest Towers
     * Find nearest towers to a given location.
     * @param lat
     * @param lon
     * @param operator
     * @param limit
     * @returns any Successful Response
     * @throws ApiError
     */
    public nearestTowersTowersNearestGet(
        lat: number,
        lon: number,
        operator?: (string | null),
        limit: number = 5,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/towers/nearest',
            query: {
                'lat': lat,
                'lon': lon,
                'operator': operator,
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Tower
     * Get a single tower by ID.
     * @param towerId
     * @returns any Successful Response
     * @throws ApiError
     */
    public getTowerTowersTowerIdGet(
        towerId: string,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/towers/{tower_id}',
            path: {
                'tower_id': towerId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Update Tower
     * Update an existing tower.  The tower ID in the path must match the body.
     * @param towerId
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public updateTowerTowersTowerIdPut(
        towerId: string,
        requestBody: TowerInput,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'PUT',
            url: '/towers/{tower_id}',
            path: {
                'tower_id': towerId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Delete Tower
     * Delete a tower from the database.
     * @param towerId
     * @returns any Successful Response
     * @throws ApiError
     */
    public deleteTowerTowersTowerIdDelete(
        towerId: string,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'DELETE',
            url: '/towers/{tower_id}',
            path: {
                'tower_id': towerId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Analyze Link
     * Perform point-to-point link analysis between an existing tower and a receiver.
     * Automatically fetches real terrain elevation along the path.
     * @param towerId
     * @param requestBody
     * @returns LinkAnalysisResponse Successful Response
     * @throws ApiError
     */
    public analyzeLinkAnalyzePost(
        towerId: string,
        requestBody: ReceiverInput,
    ): CancelablePromise<LinkAnalysisResponse> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/analyze',
            query: {
                'tower_id': towerId,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Coverage Predict
     * ML-based signal coverage prediction.
     *
     * Uses a terrain-aware regression model trained on SRTM elevation
     * features. Routes to a SageMaker endpoint when
     * ``SAGEMAKER_COVERAGE_ENDPOINT`` is configured, otherwise serves the
     * locally-trained model, with a deterministic physics fallback when
     * no model artefact is available.
     *
     * Modes:
     * - **point** — provide ``rx_lat``/``rx_lon`` for a single prediction.
     * - **grid**  — provide ``bbox`` and ``grid_size`` for a coverage map.
     *
     * Restricted to Pro / Business / Enterprise tiers.
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public coveragePredictCoveragePredictPost(
        requestBody: CoveragePredictRequest,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/coverage/predict',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Plan Repeater
     * Propose an optimized repeater chain using Dijkstra path search.
     * @param towerId
     * @param requestBody
     * @param maxHops
     * @returns any Successful Response
     * @throws ApiError
     */
    public planRepeaterPlanRepeaterPost(
        towerId: string,
        requestBody: ReceiverInput,
        maxHops: number = 3,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/plan_repeater',
            query: {
                'tower_id': towerId,
                'max_hops': maxHops,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Plan Repeater Async
     * Submit a repeater-planning job. Returns a job_id; poll
     * ``GET /plan_repeater/jobs/{job_id}`` for progress and the final chain.
     *
     * Useful for large candidate sets (max_hops >= 4) where synchronous
     * completion may exceed edge/CDN HTTP timeouts.
     * @param towerId
     * @param requestBody
     * @param maxHops
     * @returns any Successful Response
     * @throws ApiError
     */
    public planRepeaterAsyncPlanRepeaterAsyncPost(
        towerId: string,
        requestBody: ReceiverInput,
        maxHops: number = 3,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/plan_repeater/async',
            query: {
                'tower_id': towerId,
                'max_hops': maxHops,
            },
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Plan Repeater Job Status
     * Return the state (queued / running / done / error) and, when ready,
     * the repeater_chain produced by ``POST /plan_repeater/async``.
     * @param jobId
     * @returns any Successful Response
     * @throws ApiError
     */
    public planRepeaterJobStatusPlanRepeaterJobsJobIdGet(
        jobId: string,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/plan_repeater/jobs/{job_id}',
            path: {
                'job_id': jobId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Export Report
     * Generate a professional PDF engineering report. Monthly quota per tier (Free: 5/mo).
     * @param towerId
     * @param lat
     * @param lon
     * @param heightM
     * @param antennaGain
     * @returns any Successful Response
     * @throws ApiError
     */
    public exportReportExportReportGet(
        towerId: string,
        lat: number,
        lon: number,
        heightM: number = 10,
        antennaGain: number = 12,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/export_report',
            query: {
                'tower_id': towerId,
                'lat': lat,
                'lon': lon,
                'height_m': heightM,
                'antenna_gain': antennaGain,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Export Report Pdf
     * Generate a professional PDF engineering report. Monthly quota per tier (Free: 5/mo).
     * @param towerId
     * @param lat
     * @param lon
     * @param heightM
     * @param antennaGain
     * @returns any Successful Response
     * @throws ApiError
     */
    public exportReportPdfExportReportPdfGet(
        towerId: string,
        lat: number,
        lon: number,
        heightM: number = 10,
        antennaGain: number = 12,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/export_report/pdf',
            query: {
                'tower_id': towerId,
                'lat': lat,
                'lon': lon,
                'height_m': heightM,
                'antenna_gain': antennaGain,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Batch Reports
     * Upload a CSV of receiver points (columns: lat,lon  and optionally
     * height, gain) and download a ZIP of PDF reports – one per receiver.
     *
     * Small batches ( <= 100 rows) are processed synchronously.
     * Larger batches are queued for the background worker and return a job_id.
     * @param towerId
     * @param formData
     * @param receiverHeightM
     * @param antennaGainDbi
     * @returns any Successful Response
     * @throws ApiError
     */
    public batchReportsBatchReportsPost(
        towerId: string,
        formData: Body_batch_reports_batch_reports_post,
        receiverHeightM: number = 10,
        antennaGainDbi: number = 12,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/batch_reports',
            query: {
                'tower_id': towerId,
                'receiver_height_m': receiverHeightM,
                'antenna_gain_dbi': antennaGainDbi,
            },
            formData: formData,
            mediaType: 'multipart/form-data',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Get Job Status
     * Poll the status of a background batch job (Pro/Enterprise only).
     * @param jobId
     * @returns any Successful Response
     * @throws ApiError
     */
    public getJobStatusJobsJobIdGet(
        jobId: string,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/jobs/{job_id}',
            path: {
                'job_id': jobId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Download Job Result
     * Download the ZIP file produced by a completed batch job (Pro/Enterprise only).
     *
     * If the result is stored in S3, returns a redirect to a presigned URL.
     * If stored locally, streams the file directly.
     * @param jobId
     * @returns any Successful Response
     * @throws ApiError
     */
    public downloadJobResultJobsJobIdDownloadGet(
        jobId: string,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/jobs/{job_id}/download',
            path: {
                'job_id': jobId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Signup Free
     * Register a free-tier account and receive an API key instantly.
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public signupFreeSignupFreePost(
        requestBody: SignupRequest,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/signup/free',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Signup Checkout
     * Create a Stripe Checkout Session for a paid plan.
     * Returns the Checkout URL the client should redirect to.
     * For enterprise plans, pass *country* to pre-download SRTM elevation tiles.
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public signupCheckoutSignupCheckoutPost(
        requestBody: CheckoutRequest,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/signup/checkout',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Stripe Webhook
     * Receive Stripe webhook events (checkout.session.completed,
     * customer.subscription.deleted, etc.).
     * @returns any Successful Response
     * @throws ApiError
     */
    public stripeWebhookStripeWebhookPost(): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/stripe_webhook',
        });
    }
    /**
     * Stripe Webhook
     * Receive Stripe webhook events (checkout.session.completed,
     * customer.subscription.deleted, etc.).
     * @returns any Successful Response
     * @throws ApiError
     */
    public stripeWebhookStripeWebhookPost1(): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/stripe/webhook',
        });
    }
    /**
     * Signup Success
     * After Stripe Checkout, the frontend redirects here with session_id.
     * Returns the provisioned API key so the user can start using the API.
     * @param sessionId
     * @returns any Successful Response
     * @throws ApiError
     */
    public signupSuccessSignupSuccessGet(
        sessionId: string,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/signup/success',
            query: {
                'session_id': sessionId,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Signup Status
     * Look up an existing API key by email address.
     * Returns a masked key, tier, and account status.
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public signupStatusSignupStatusPost(
        requestBody: KeyLookupRequest,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/signup/status',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Portal Profile
     * Return the caller's profile: masked API key, tier, limits, and account info.
     * @returns any Successful Response
     * @throws ApiError
     */
    public portalProfilePortalProfileGet(): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/portal/profile',
        });
    }
    /**
     * Portal Usage
     * Return usage statistics for the caller's API key.
     * @returns any Successful Response
     * @throws ApiError
     */
    public portalUsagePortalUsageGet(): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/portal/usage',
        });
    }
    /**
     * Portal Jobs
     * Return the caller's batch jobs (most recent first).
     * @param limit
     * @returns any Successful Response
     * @throws ApiError
     */
    public portalJobsPortalJobsGet(
        limit: number = 20,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/portal/jobs',
            query: {
                'limit': limit,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Portal Billing
     * Return billing information from Stripe for the caller.
     * @returns any Successful Response
     * @throws ApiError
     */
    public portalBillingPortalBillingGet(): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/portal/billing',
        });
    }
    /**
     * Srtm Tile Status
     * Report SRTM tile availability for a country (enterprise only).
     * @param country
     * @returns any Successful Response
     * @throws ApiError
     */
    public srtmTileStatusSrtmStatusCountryGet(
        country: string,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/srtm/status/{country}',
            path: {
                'country': country,
            },
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Srtm Prefetch
     * Start background download of SRTM tiles for a country (enterprise only).
     * Returns immediately; use GET /srtm/status/{country} to track progress.
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public srtmPrefetchSrtmPrefetchPost(
        requestBody: PrefetchRequest,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/srtm/prefetch',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Bedrock Chat
     * Send a prompt to an Amazon Bedrock base foundation model and return
     * the generated response.  Supports Titan, Claude, and Llama model families.
     * Requires PRO or ENTERPRISE tier.
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public bedrockChatBedrockChatPost(
        requestBody: BedrockChatRequest,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/bedrock/chat',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Bedrock Models
     * List available Bedrock foundation models for the AI playground.
     * @returns any Successful Response
     * @throws ApiError
     */
    public bedrockModelsBedrockModelsGet(): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'GET',
            url: '/bedrock/models',
        });
    }
    /**
     * Bedrock Compare Scenarios
     * Compare multiple RF scenarios using AI analysis.
     * Accepts 2-10 scenarios (e.g. different frequencies, antenna heights)
     * and returns an engineering comparison with recommendations.
     * Requires PRO or ENTERPRISE tier.
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public bedrockCompareScenariosBedrockComparePost(
        requestBody: BedrockScenarioRequest,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/bedrock/compare',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Bedrock Batch Analyze
     * Analyze a batch of link analysis results with AI.
     * Processes up to 500 link results and provides consolidated
     * coverage assessment, worst-link identification, and prioritized
     * remediation recommendations.
     * Requires PRO or ENTERPRISE tier.
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public bedrockBatchAnalyzeBedrockBatchAnalyzePost(
        requestBody: BedrockBatchAnalysisRequest,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/bedrock/batch-analyze',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
    /**
     * Bedrock Suggest Height
     * AI-powered antenna height recommendation based on link analysis
     * and terrain profile. Calculates the optimal height for the desired
     * Fresnel zone clearance.
     * Requires PRO or ENTERPRISE tier.
     * @param requestBody
     * @returns any Successful Response
     * @throws ApiError
     */
    public bedrockSuggestHeightBedrockSuggestHeightPost(
        requestBody: BedrockAntennaRequest,
    ): CancelablePromise<any> {
        return this.httpRequest.request({
            method: 'POST',
            url: '/bedrock/suggest-height',
            body: requestBody,
            mediaType: 'application/json',
            errors: {
                422: `Validation Error`,
            },
        });
    }
}

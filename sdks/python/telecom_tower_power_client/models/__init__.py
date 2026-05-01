# SPDX-License-Identifier: LicenseRef-TTP-Proprietary
# Copyright (c) 2026 Daniel Azevedo Novais ("TELECOM-TOWER-POWER"). All rights reserved.
"""Contains all the data models used in inputs/outputs"""

from .band import Band
from .bedrock_antenna_request import BedrockAntennaRequest
from .bedrock_antenna_request_analysis import BedrockAntennaRequestAnalysis
from .bedrock_antenna_request_tower import BedrockAntennaRequestTower
from .bedrock_batch_analysis_request import BedrockBatchAnalysisRequest
from .bedrock_batch_analysis_request_batch_results_item import BedrockBatchAnalysisRequestBatchResultsItem
from .bedrock_chat_request import BedrockChatRequest
from .bedrock_scenario_request import BedrockScenarioRequest
from .bedrock_scenario_request_scenarios_item import BedrockScenarioRequestScenariosItem
from .body_batch_reports_batch_reports_post import BodyBatchReportsBatchReportsPost
from .checkout_request import CheckoutRequest
from .coverage_predict_request import CoveragePredictRequest
from .http_validation_error import HTTPValidationError
from .key_lookup_request import KeyLookupRequest
from .link_analysis_response import LinkAnalysisResponse
from .prefetch_request import PrefetchRequest
from .receiver_input import ReceiverInput
from .signup_request import SignupRequest
from .tower_input import TowerInput
from .validation_error import ValidationError
from .validation_error_context import ValidationErrorContext

__all__ = (
    "Band",
    "BedrockAntennaRequest",
    "BedrockAntennaRequestAnalysis",
    "BedrockAntennaRequestTower",
    "BedrockBatchAnalysisRequest",
    "BedrockBatchAnalysisRequestBatchResultsItem",
    "BedrockChatRequest",
    "BedrockScenarioRequest",
    "BedrockScenarioRequestScenariosItem",
    "BodyBatchReportsBatchReportsPost",
    "CheckoutRequest",
    "CoveragePredictRequest",
    "HTTPValidationError",
    "KeyLookupRequest",
    "LinkAnalysisResponse",
    "PrefetchRequest",
    "ReceiverInput",
    "SignupRequest",
    "TowerInput",
    "ValidationError",
    "ValidationErrorContext",
)

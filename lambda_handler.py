"""
AWS Lambda handler for the Telecom Tower Power API.

Uses Mangum to adapt the FastAPI ASGI application to the
AWS Lambda + API Gateway request/response format.
"""

from mangum import Mangum
from telecom_tower_power_api import app

import os

# HttpApi stage prefix (e.g. "/prod") is included in the request path; strip it
# so FastAPI route matching works.
_STAGE = os.getenv("API_GATEWAY_BASE_PATH", "/prod")
handler = Mangum(app, lifespan="off", api_gateway_base_path=_STAGE)

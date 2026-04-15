"""
AWS Lambda handler for the Telecom Tower Power API.

Uses Mangum to adapt the FastAPI ASGI application to the
AWS Lambda + API Gateway request/response format.
"""

from mangum import Mangum
from telecom_tower_power_api import app

handler = Mangum(app, lifespan="off")

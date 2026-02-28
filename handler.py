"""
AWS Lambda handler using Mangum ASGI adapter
This wraps the FastAPI app for AWS Lambda + API Gateway and Function URL
"""
from mangum import Mangum
from main import app

# Create handlers for both API Gateway and Function URL
# lifespan="auto" ensures FastAPI lifespan events (MongoDB connection) are called
handler_api_gateway = Mangum(app, lifespan="auto", api_gateway_base_path="/prod")
handler_function_url = Mangum(app, lifespan="auto")

def handler(event, context):
    """
    Smart handler that detects request source and routes accordingly.
    - API Gateway v2 (HTTP API): has 'requestContext.stage'
    - Function URL: has 'requestContext.domainPrefix' containing 'lambda-url'
    """
    request_context = event.get("requestContext", {})
    
    # Function URL detection: domainName contains 'lambda-url'
    domain_name = request_context.get("domainName", "")
    if "lambda-url" in domain_name:
        return handler_function_url(event, context)
    
    # Default: API Gateway with stage prefix
    return handler_api_gateway(event, context)

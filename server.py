
import logging
import sys
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field, StringConstraints

# Keep all application logs on stderr, never stdout.
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
)
logger = logging.getLogger("customer-mcp")

mcp = FastMCP("customer-refund-server")

CustomerId = Annotated[
    str,
    StringConstraints(pattern=r"^CUST-[A-Z0-9]{5}$"),
]

RefundReason = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=10),
]


@mcp.tool()
def get_customer_record(customer_id: CustomerId) -> dict:
    """Retrieve a customer record by customer ID."""
    logger.info("Customer record lookup requested")

    records = {
        "CUST-A1234": {
            "customer_id": "CUST-A1234",
            "name": "Alex Morgan",
            "status": "active",
        },
        "CUST-B5678": {
            "customer_id": "CUST-B5678",
            "name": "Jordan Lee",
            "status": "active",
        },
    }

    record = records.get(customer_id)
    if record is None:
        return {
            "error": "CUSTOMER_NOT_FOUND",
            "message": "No customer record exists for this ID.",
        }

    return record


@mcp.tool()
def trigger_refund(
    customer_id: CustomerId,
    amount: Annotated[float, Field(gt=0, allow_inf_nan=False)],
    reason: RefundReason,
) -> dict:
    """Accept a mock refund request after validating its inputs."""
    logger.info("Mock refund requested")

    if customer_id not in {"CUST-A1234", "CUST-B5678"}:
        return {
            "error": "CUSTOMER_NOT_FOUND",
            "message": "Cannot refund an unknown customer.",
        }

    return {
        "status": "accepted",
        "customer_id": customer_id,
        "amount": amount,
        "reason": reason,
        "message": "Mock only; no money was transferred.",
    }


if __name__ == "__main__":
    logger.info("Starting MCP server using stdio")
    mcp.run(transport="stdio")


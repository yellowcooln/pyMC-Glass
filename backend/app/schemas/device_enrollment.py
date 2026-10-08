from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

DeviceId = Annotated[
    str,
    Field(strict=True, pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"),
]
DeviceSecret = Annotated[
    str, Field(strict=True, min_length=43, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
]


class EnrollmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)
    device_id: DeviceId
    node_name: Annotated[str, Field(strict=True, min_length=1, max_length=128)]
    pubkey: Annotated[str, Field(strict=True, min_length=1, max_length=130)]
    enrollment_token: DeviceSecret
    operational_token: DeviceSecret
    csr_pem: Annotated[str, Field(strict=True, min_length=1, max_length=14000)]


class EnrollmentTokenResponse(BaseModel):
    device_id: str
    enrollment_token: str
    expires_at: datetime


class EnrollmentResponse(BaseModel):
    device_id: str
    client_cert: str
    ca_cert: str
    cert_serial: str
    expires_at: datetime

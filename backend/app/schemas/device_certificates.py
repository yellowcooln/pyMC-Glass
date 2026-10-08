"""Public-only renewal DTOs. Node keys and CSRs are never persisted."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.device_enrollment import DeviceId


class CertificateRenewalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    device_id: DeviceId
    request_id: DeviceId
    csr_pem: Annotated[str, Field(strict=True, min_length=1, max_length=14000)]


class CertificateRenewalResponse(BaseModel):
    device_id: str
    request_id: str
    client_cert: str
    ca_cert: str
    cert_serial: str
    expires_at: datetime
    fingerprint_sha256: str
    state: Literal["issued"] = "issued"

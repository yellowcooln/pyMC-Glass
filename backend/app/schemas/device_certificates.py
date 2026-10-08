"""Public-only renewal DTOs. Node keys and CSRs are never persisted."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.device_enrollment import DeviceId


class CertificateRenewalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    device_id: DeviceId
    request_id: DeviceId
    csr_pem: Annotated[str, Field(strict=True, min_length=1, max_length=14000)]


class CertificateReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    device_id: DeviceId
    request_id: DeviceId
    cert_serial: Annotated[
        str, Field(strict=True, min_length=1, max_length=40, pattern=r"^[0-9a-f]+$")
    ]
    fingerprint_sha256: Annotated[
        str, Field(strict=True, min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    ]
    boot_id: DeviceId
    connected: Literal[True]

    @field_validator("connected", mode="before")
    @classmethod
    def connected_true(cls, value: object) -> object:
        if value is not True:
            raise ValueError("Connected must be true")
        return value

    @field_validator("cert_serial")
    @classmethod
    def positive_serial(cls, value: str) -> str:
        if int(value, 16) == 0:
            raise ValueError("Serial must be positive")
        return value


class CertificateReportResponse(BaseModel):
    device_id: str
    request_id: str
    cert_serial: str
    accepted: Literal[True] = True
    state: Literal["node_reported"] = "node_reported"


class CertificateRenewalResponse(BaseModel):
    device_id: str
    request_id: str
    client_cert: str
    ca_cert: str
    cert_serial: str
    expires_at: datetime
    fingerprint_sha256: str
    state: Literal["issued"] = "issued"

"""WebSocket protocol type definitions."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class CoreAPIPayload(BaseModel):
    """Inner CoreAPI command payload."""

    endpoint: str
    accountId: str | None = None
    subscribeElement: dict | None = None
    unsubscribeElement: dict | None = None
    getAndSubscribeElement: dict | None = None
    ping: dict | None = None
    tradeTransaction: dict | None = None
    registerClientInfo: dict | None = None
    logonWithServiceTicket: dict | None = None

    model_config = {"extra": "allow"}


class CoreAPICommand(BaseModel):
    """CoreAPI command wrapper."""

    CoreAPI: CoreAPIPayload


class WSRequest(BaseModel):
    """WebSocket request message format."""

    reqId: str
    command: list[CoreAPICommand]


class WSResponse(BaseModel):
    """WebSocket response message format."""

    reqId: str = ""
    response: list[Any] | None = None
    data: Any | None = None
    error: dict | None = None
    status: int | None = None
    events: list[dict] | None = None

    model_config = {"extra": "allow"}


class WSAuthOptions(BaseModel):
    """Authentication options for WebSocket client."""

    service_ticket: str | None = None
    tgt: str | None = None
    credentials: WSCredentials | None = None
    browser_auth: bool = False


class WSCredentials(BaseModel):
    """Email/password credentials."""

    email: str
    password: str


class WSClientConfig(BaseModel):
    """WebSocket client configuration."""

    url: str
    account_number: int
    endpoint: str = "meta1"
    ping_interval: int = 10000
    auto_reconnect: bool = True
    max_reconnect_delay: int = 30000
    app_name: str = "xStation5"
    app_version: str = "2.94.1"
    device: str = "Linux x86_64"
    auth: WSAuthOptions | None = None


class ClientInfo(BaseModel):
    """Client information for registerClientInfo command."""

    appName: str
    appVersion: str
    appBuildNumber: str = "0"
    device: str
    osVersion: str = ""
    comment: str = "Python"
    apiVersion: str = "2.73.0"
    osType: int = 0
    deviceType: int = 1


class XLoginAccountInfo(BaseModel):
    """Account info from login result."""

    accountNo: int
    currency: str
    endpointType: str


class XLoginResult(BaseModel):
    """Login response data from successful authentication."""

    accountList: list[XLoginAccountInfo] = []
    endpointList: list[str] = []
    userData: dict = {}


class WSPushEventRow(BaseModel):
    """Push event row data."""

    key: str = ""
    value: dict = {}

    model_config = {"extra": "allow"}


class WSPushEvent(BaseModel):
    """Push event in a push message."""

    eid: int
    row: WSPushEventRow

    model_config = {"extra": "allow"}


class WSPushMessage(BaseModel):
    """Push message structure for real-time data updates."""

    reqId: str = ""
    status: int = 1
    events: list[WSPushEvent] = []

    model_config = {"extra": "allow"}


class CASLoginSuccess(BaseModel):
    """Successful CAS login result."""

    type: Literal["success"] = "success"
    tgt: str
    expires_at: float


class CASLoginTwoFactorRequired(BaseModel):
    """CAS login requiring two-factor authentication."""

    type: Literal["requires_2fa"] = "requires_2fa"
    login_ticket: str
    session_id: str = ""  # backward compat (may be same as login_ticket or empty)
    two_factor_auth_type: str = "SMS"
    methods: list[str] = ["TOTP"]
    expires_at: float


CASLoginResult = CASLoginSuccess | CASLoginTwoFactorRequired


# Backward-compatible re-export — canonical location is xtb_api.exceptions
from xtb_api.exceptions import CASError as CASError  # noqa: E402, F401

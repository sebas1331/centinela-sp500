"""CAS (Central Authentication Service) client for XTB xStation5 WebSocket authentication.

Handles the complete auth flow: login → TGT → Service Ticket → WebSocket login.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import stat
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
from pydantic import BaseModel

from xtb_api.exceptions import (
    AccountBlockedError,
    CASError,
    InvalidCredentialsError,
    RateLimitedError,
    TwoFactorRequiredError,
)
from xtb_api.types.websocket import (
    CASLoginResult,
    CASLoginSuccess,
    CASLoginTwoFactorRequired,
)

if TYPE_CHECKING:
    from xtb_api.auth.browser_auth import BrowserCASAuth

logger = logging.getLogger(__name__)

_CAS_CODE_TO_CLASS: dict[str, type[CASError]] = {
    "CAS_GET_TGT_UNAUTHORIZED": InvalidCredentialsError,
    "CAS_GET_TGT_TOO_MANY_OTP_ERROR": RateLimitedError,
    "CAS_GET_TGT_OTP_LIMIT_REACHED_ERROR": RateLimitedError,
    "CAS_GET_TGT_OTP_ACCESS_BLOCKED_ERROR": AccountBlockedError,
    "CAS_2FA_MISSING_TICKET": TwoFactorRequiredError,
}


def _cas_error_for_code(code: str, message: str) -> CASError:
    """Return the most specific CAS exception subclass for a given code.

    Unknown codes fall back to plain ``CASError``.
    """
    cls = _CAS_CODE_TO_CLASS.get(code, CASError)
    return cls(code, message)


class CASServiceTicketResult(BaseModel):
    """Result from service ticket request."""

    service_ticket: str
    service: str


class CASClientConfig(BaseModel):
    """CAS client configuration."""

    base_url: str = "https://xstation.xtb.com/signon/"
    timezone_offset: str | None = None
    user_agent: str = "xStation5/2.94.1 (Linux x86_64)"
    cookies_file: Path | str | None = None
    """Path to persist HTTP cookies (CASTGC, device fingerprint, etc.) as JSON.

    When set, cookies are loaded on client creation and saved after each
    successful login or service-ticket request.  This avoids XTB "new device"
    emails on every restart.  File is written with ``chmod 0600``.
    """


class CASClient:
    """CAS authentication client for XTB xStation5.

    Flow:
    1. login(email, password) → TGT (Ticket Granting Ticket)
    2. get_service_ticket(tgt, 'xapi5') → ST (Service Ticket)
    3. Use ST with WebSocket loginWithServiceTicket

    Critical: Use service='xapi5' for WebSocket, NOT 'abigail' (that's for REST API)
    """

    _browser_auth: BrowserCASAuth | None = None

    def __init__(self, config: CASClientConfig | None = None) -> None:
        if config is not None:
            # Avoid mutating caller's config — fill in timezone if missing
            tz = config.timezone_offset if config.timezone_offset is not None else self._get_timezone_offset()
            self._config = config.model_copy(update={"timezone_offset": tz})
        else:
            self._config = CASClientConfig(timezone_offset=self._get_timezone_offset())
        self._http: httpx.AsyncClient | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._cookies_path: Path | None = (
            Path(self._config.cookies_file).expanduser() if self._config.cookies_file else None
        )

    async def _ensure_http(self) -> httpx.AsyncClient:
        """Get or create the long-lived httpx client, loading persisted cookies.

        Detects event-loop changes (e.g. after ``asyncio.run()`` in
        ``get_tgt_sync``) and replaces the stale client so we never
        reuse an ``httpx.AsyncClient`` bound to a closed loop.
        """
        current_loop = asyncio.get_running_loop()
        if self._http is None or self._http.is_closed or self._loop is not current_loop:
            if self._http and not self._http.is_closed:
                with contextlib.suppress(Exception):
                    await self._http.aclose()
            cookies = self._load_cookies()
            self._http = httpx.AsyncClient(timeout=30.0, cookies=cookies)
            self._loop = current_loop
        return self._http

    async def aclose(self) -> None:
        """Close the underlying httpx client."""
        if self._http and not self._http.is_closed:
            await self._http.aclose()
            self._http = None

    async def login(self, email: str, password: str) -> CASLoginResult:
        """Login with email/password using CAS v2 with v1 fallback.

        Tries CAS v2 first (supports 2FA), falls back to CAS v1 if v2 unavailable.

        Args:
            email: XTB account email
            password: XTB account password

        Returns:
            Either success with TGT or 2FA challenge requiring OTP code

        Raises:
            CASError: If credentials invalid, account blocked, or service unavailable
        """
        try:
            return await self._login_v2(email, password)
        except CASError as e:
            if "UNAUTHORIZED" not in e.code:
                try:
                    return await self._login_v1(email, password)
                except CASError:
                    raise e from None
            raise

    async def _login_v2(self, email: str, password: str) -> CASLoginResult:
        """Login using CAS v2 (supports 2FA)."""
        url = f"{self._config.base_url}v2/tickets"
        fingerprint = self._generate_fingerprint(self._config.user_agent)

        payload = {
            "username": email,
            "password": password,
            "fingerprint": fingerprint,
            "rememberMe": True,
        }

        headers = {
            "Content-Type": "application/json",
            "Time-Zone": self._config.timezone_offset or "+0000",
            "User-Agent": self._config.user_agent,
        }

        client = await self._ensure_http()
        resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code == 401:
            raise _cas_error_for_code("CAS_GET_TGT_UNAUTHORIZED", "Invalid credentials")

        if not resp.is_success:
            raise CASError(
                "CAS_LOGIN_FAILED",
                f"CAS v2 login failed: {resp.status_code} {resp.text}",
            )

        result = resp.json()

        # Handle success (no 2FA)
        if result.get("loginPhase") == "TGT_CREATED" and result.get("ticket"):
            self._save_cookies(client)
            return CASLoginSuccess(
                tgt=result["ticket"],
                expires_at=time.time() + 8 * 3600,  # 8 hours
            )

        # Handle 2FA required
        login_ticket = result.get("loginTicket") or result.get("sessionId") or ""
        if result.get("loginPhase") == "TWO_FACTOR_REQUIRED" and login_ticket:
            return CASLoginTwoFactorRequired(
                login_ticket=login_ticket,
                session_id=result.get("sessionId", login_ticket),
                two_factor_auth_type=result.get("twoFactorAuthType", "SMS"),
                methods=result.get("methods", ["TOTP"]),
                expires_at=time.time() + 5 * 60,  # 5 minutes
            )

        # Handle specific error codes
        code = result.get("code")
        if code:
            match code:
                case "CAS_GET_TGT_UNAUTHORIZED":
                    raise _cas_error_for_code(code, "Invalid email or password")
                case "CAS_GET_TGT_TOO_MANY_OTP_ERROR":
                    wait = result.get("data", {}).get("otpThrottleTimeRemaining", 60)
                    raise _cas_error_for_code(code, f"Too many OTP attempts. Wait {wait}s")
                case "CAS_GET_TGT_OTP_LIMIT_REACHED_ERROR":
                    raise _cas_error_for_code(code, "OTP attempt limit reached. Try again later")
                case "CAS_GET_TGT_OTP_ACCESS_BLOCKED_ERROR":
                    raise _cas_error_for_code(code, "Account temporarily blocked due to too many failed OTP attempts")
                case _:
                    raise _cas_error_for_code(code, result.get("message", "CAS login failed"))

        raise CASError("CAS_UNEXPECTED_RESPONSE", f"Unexpected login response: {result}")

    async def _login_v1(self, email: str, password: str) -> CASLoginResult:
        """Login using CAS v1 (fallback, no 2FA support)."""
        url = f"{self._config.base_url}v1/tickets"

        form_data = {"username": email, "password": password}

        headers = {
            "User-Agent": self._config.user_agent,
        }

        client = await self._ensure_http()
        resp = await client.post(url, data=form_data, headers=headers)

        if resp.status_code == 201:
            location = resp.headers.get("location", "")
            if not location:
                raise CASError(
                    "CAS_V1_NO_LOCATION",
                    "CAS v1 login succeeded but no Location header found",
                )

            match = re.search(r"/tickets/([^/]+)$", location)
            if not match:
                raise CASError(
                    "CAS_V1_INVALID_LOCATION",
                    f"CAS v1 Location header format invalid: {location}",
                )

            self._save_cookies(client)
            return CASLoginSuccess(
                tgt=match.group(1),
                expires_at=time.time() + 8 * 3600,
            )

        if resp.status_code == 401:
            raise _cas_error_for_code("CAS_GET_TGT_UNAUTHORIZED", "Invalid credentials")

        raise CASError(
            "CAS_V1_LOGIN_FAILED",
            f"CAS v1 login failed: {resp.status_code} {resp.text}",
        )

    async def login_with_two_factor(
        self,
        login_ticket: str,
        code: str,
        two_factor_auth_type: str = "SMS",
        *,
        session_id: str | None = None,
    ) -> CASLoginResult:
        """Submit two-factor authentication code to complete login.

        Uses the same ``v2/tickets`` endpoint as initial login, with a
        ``loginTicket`` + ``token`` payload — matching the real browser flow.

        Args:
            login_ticket: Login ticket from initial login (MID-xxx format).
                          For backward compat, ``session_id`` kwarg is also accepted
                          and used as login_ticket if this arg is empty.
            code: OTP code (6 digits from TOTP/SMS/EMAIL)
            two_factor_auth_type: Auth method, default ``"SMS"``
            session_id: **Deprecated** — alias for ``login_ticket``, kept for
                        backward compatibility.

        Returns:
            TGT if successful, or new 2FA challenge

        Raises:
            CASError: If code is invalid, rate limited, or account blocked
        """
        # Backward compat: accept session_id as login_ticket
        ticket = login_ticket or session_id or ""
        if not ticket:
            raise _cas_error_for_code("CAS_2FA_MISSING_TICKET", "No login ticket provided")

        url = f"{self._config.base_url}v2/tickets"

        payload = {
            "loginTicket": ticket,
            "token": code,
            "fingerprint": self._generate_fingerprint(self._config.user_agent),
            "twoFactorAuthType": two_factor_auth_type,
        }

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "Time-Zone": self._config.timezone_offset or "0",
            "User-Agent": self._config.user_agent,
        }

        client = await self._ensure_http()
        resp = await client.post(url, json=payload, headers=headers)

        if not resp.is_success:
            raise CASError(
                "CAS_2FA_REQUEST_FAILED",
                f"2FA request failed: {resp.status_code} {resp.text}",
            )

        result = resp.json()

        # Extract TGT from response body or cookies
        tgt = result.get("ticket") or result.get("tgt")
        if not tgt:
            tgt = resp.cookies.get("CASTGT") or resp.cookies.get("CASTGC")

        if result.get("loginPhase") == "TGT_CREATED" and tgt:
            self._save_cookies(client)
            return CASLoginSuccess(
                tgt=tgt,
                expires_at=time.time() + 8 * 3600,
            )

        # Some responses return TGT without explicit loginPhase
        if tgt and tgt.startswith("TGT-"):
            self._save_cookies(client)
            return CASLoginSuccess(
                tgt=tgt,
                expires_at=time.time() + 8 * 3600,
            )

        code_field = result.get("code")
        if code_field:
            raise _cas_error_for_code(code_field, result.get("message", "Two-factor authentication failed"))

        raise CASError(
            "CAS_2FA_UNEXPECTED_RESPONSE",
            f"Unexpected 2FA response: {result}",
        )

    async def get_service_ticket(self, tgt: str, service: str = "xapi5") -> CASServiceTicketResult:
        """Get Service Ticket using TGT via CAS v1 endpoint.

        Args:
            tgt: Ticket Granting Ticket from login()
            service: Service name. Use 'xapi5' for WebSocket, 'abigail' for REST API.

        Returns:
            Service ticket for the specified service
        """
        return await self._get_service_ticket_v1(tgt, service)

    async def _get_service_ticket_v1(self, tgt: str, service: str) -> CASServiceTicketResult:
        """Get Service Ticket via CAS v1 endpoint."""
        url = f"{self._config.base_url}v1/tickets/{tgt}"

        form_data = {"service": service}

        headers = {
            "User-Agent": self._config.user_agent,
            "Cookie": f"CASTGC={tgt}",
        }

        client = await self._ensure_http()
        resp = await client.post(url, data=form_data, headers=headers)

        # XTB's CAS v1 endpoint returns 404 (not 401) when the TGT is
        # missing server-side — typically after a forced logout from
        # another device or server-side rotation. Treat both as the same
        # recoverable state so AuthManager.get_service_ticket invalidates
        # the cache and performs a fresh CAS login.
        if resp.status_code in (401, 404):
            raise CASError("CAS_TGT_EXPIRED", "TGT has expired or is invalid")

        if not resp.is_success:
            raise CASError(
                "CAS_SERVICE_TICKET_FAILED",
                f"CAS v1 service ticket request failed: {resp.status_code} {resp.text}",
            )

        service_ticket = resp.text.strip()
        if not service_ticket or not service_ticket.startswith("ST-"):
            raise CASError(
                "CAS_INVALID_SERVICE_TICKET",
                f"Invalid service ticket received: {service_ticket}",
            )

        self._save_cookies(client)
        return CASServiceTicketResult(service_ticket=service_ticket, service=service)

    async def get_service_ticket_v2(self, tgt: str, service: str = "xapi5") -> CASServiceTicketResult:
        """Get Service Ticket via CAS v2 endpoint (alternative method)."""
        url = f"{self._config.base_url}v2/serviceTicket"

        payload = {"tgt": tgt, "service": service}

        headers = {
            "Content-Type": "application/json",
            "Time-Zone": self._config.timezone_offset or "+0000",
            "User-Agent": self._config.user_agent,
            "Cookie": f"CASTGC={tgt}",
        }

        client = await self._ensure_http()
        resp = await client.post(url, json=payload, headers=headers)

        if resp.status_code in (401, 404):
            raise CASError("CAS_TGT_EXPIRED", "TGT has expired or is invalid")

        if not resp.is_success:
            raise CASError(
                "CAS_SERVICE_TICKET_FAILED",
                f"CAS v2 service ticket request failed: {resp.status_code} {resp.text}",
            )

        result = resp.json()
        service_ticket = result.get("serviceTicket") or result.get("ticket")

        if not service_ticket or not service_ticket.startswith("ST-"):
            raise CASError(
                "CAS_INVALID_SERVICE_TICKET",
                f"Invalid service ticket received: {service_ticket}",
            )

        self._save_cookies(client)
        return CASServiceTicketResult(service_ticket=service_ticket, service=service)

    async def refresh_service_ticket(self, tgt: str, service: str = "xapi5") -> str:
        """Refresh service ticket using existing TGT.

        Service tickets are single-use and expire after 2-5 minutes.
        """
        try:
            result = await self.get_service_ticket(tgt, service)
            return result.service_ticket
        except CASError as e:
            if e.code == "CAS_TGT_EXPIRED":
                raise CASError("CAS_TGT_EXPIRED", "TGT has expired, please login again") from e
            raise

    def is_tgt_valid(self, login_result: CASLoginResult) -> bool:
        """Check if TGT is still valid (local expiration check only)."""
        return time.time() < login_result.expires_at

    def get_tgt_from_result(self, login_result: CASLoginResult) -> str | None:
        """Extract TGT from successful login result."""
        if isinstance(login_result, CASLoginSuccess):
            return login_result.tgt
        return None

    async def login_with_browser(self, email: str, password: str, *, headless: bool = True) -> CASLoginResult:
        """Login using browser-based authentication (Playwright).

        Bypasses Akamai WAF by using a real browser to perform the login flow.
        Falls back gracefully if Playwright is not installed.

        Args:
            email: XTB account email
            password: XTB account password
            headless: Run browser in headless mode (default True, set False for debugging)

        Returns:
            Either success with TGT or 2FA challenge requiring OTP code

        Raises:
            CASError: If login fails or Playwright not installed
        """
        try:
            from xtb_api.auth.browser_auth import BrowserCASAuth
        except ImportError as e:
            raise CASError(
                "BROWSER_AUTH_UNAVAILABLE",
                "Browser auth requires playwright. Install with: pip install playwright && playwright install chromium",
            ) from e

        self._browser_auth = BrowserCASAuth(headless=headless)
        return await self._browser_auth.login(email, password)

    async def submit_browser_otp(self, code: str) -> CASLoginResult:
        """Submit OTP code via browser for 2FA completion.

        Must be called after login_with_browser() returns CASLoginTwoFactorRequired.

        Args:
            code: 6-digit OTP code

        Returns:
            CASLoginSuccess with TGT

        Raises:
            CASError: If browser session not available or OTP fails
        """
        if not hasattr(self, "_browser_auth") or self._browser_auth is None:
            raise CASError(
                "BROWSER_AUTH_NO_SESSION",
                "No browser auth session — call login_with_browser() first",
            )
        result = await self._browser_auth.submit_otp(code)
        self._browser_auth = None
        return result

    def _load_cookies(self) -> dict[str, str]:
        """Load persisted cookies from disk, returning an empty dict on any failure."""
        if not self._cookies_path or not self._cookies_path.exists():
            return {}
        try:
            data = json.loads(self._cookies_path.read_text())
            if isinstance(data, dict):
                return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
        except (json.JSONDecodeError, OSError) as exc:
            logger.debug("Could not load cookies from %s: %s", self._cookies_path, exc)
        return {}

    def _save_cookies(self, client: httpx.AsyncClient) -> None:
        """Merge the client's cookie jar to disk as JSON with 0600 permissions."""
        if not self._cookies_path:
            return
        try:
            # Merge existing persisted cookies with current jar
            existing = self._load_cookies()
            for cookie in client.cookies.jar:
                if cookie.value is not None:
                    existing[cookie.name] = cookie.value
            if not existing:
                return

            self._cookies_path.parent.mkdir(parents=True, exist_ok=True)
            content = json.dumps(existing, indent=2)
            fd = os.open(
                str(self._cookies_path),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                stat.S_IRUSR | stat.S_IWUSR,  # 0600
            )
            try:
                os.write(fd, content.encode())
            finally:
                os.close(fd)
        except OSError as exc:
            logger.warning("Could not save cookies to %s: %s", self._cookies_path, exc)

    @staticmethod
    def _get_timezone_offset() -> str:
        """Get current timezone offset in minutes (matching browser's format).

        The XTB signon API expects the Time-Zone header as positive minutes
        east of UTC (e.g. "60" for CET/UTC+1, "120" for CEST/UTC+2).
        This matches ``new Date().getTimezoneOffset()`` negated.
        """
        now = datetime.now(UTC).astimezone()
        offset = now.utcoffset()
        offset_seconds = offset.total_seconds() if offset is not None else 0
        return str(int(offset_seconds / 60))

    @staticmethod
    def _generate_fingerprint(user_agent: str) -> str:
        """Generate SHA-256 fingerprint from user agent."""
        return hashlib.sha256(user_agent.encode()).hexdigest().upper()

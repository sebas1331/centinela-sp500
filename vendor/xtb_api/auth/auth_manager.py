"""High-level authentication manager that encapsulates the full TGT auth flow.

Handles cached sessions, REST CAS login, browser fallback, and automatic TOTP —
so consumers don't need to implement the auth chain themselves.
"""

from __future__ import annotations

import asyncio
import enum
import json
import logging
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from xtb_api.auth.cas_client import CASClient, CASClientConfig
from xtb_api.types.websocket import (
    CASError,
    CASLoginSuccess,
    CASLoginTwoFactorRequired,
)

logger = logging.getLogger(__name__)

# TGT validity: 8 hours, but refresh 5 minutes early to avoid races
TGT_LIFETIME_SECONDS = 8 * 3600
TGT_REFRESH_MARGIN_SECONDS = 5 * 60


class SessionSource(enum.StrEnum):
    """Where the current TGT came from.

    Exposed as :attr:`AuthManager.session_source` so downstream code and
    operators can confirm session reuse actually happened — XTB emails the
    account owner on every fresh CAS login, so the difference between a
    cache hit and a fresh login is user-visible.
    """

    UNCACHED = "uncached"
    MEMORY = "memory"
    SESSION_FILE = "session_file"
    CAS_LOGIN = "cas_login"
    BROWSER_LOGIN = "browser_login"


class AuthManager:
    """Manages the full XTB CAS authentication chain.

    Auth chain (tried in order):
    1. Cached TGT from session file (if configured and still valid)
    2. REST CAS login (fast, no browser)
    3. Playwright browser fallback (if WAF blocks REST)
    4. Automatic TOTP if 2FA required and totp_secret provided

    Example::

        auth = AuthManager(
            email="user@example.com",
            password="secret",
            totp_secret="BASE32SECRET",
            session_file="~/.xtb_session.json",
        )
        tgt = await auth.get_tgt()
        st = await auth.get_service_ticket()
    """

    def __init__(
        self,
        email: str,
        password: str,
        totp_secret: str = "",
        session_file: Path | str | None = None,
        cas_config: CASClientConfig | None = None,
        cookies_file: Path | str | None = None,
    ) -> None:
        """
        Args:
            email: XTB account email.
            password: XTB account password.
            totp_secret: Base32 TOTP secret for auto-2FA (optional).
                         If omitted and 2FA is required, raises CASError.
            session_file: Path to cache TGT as JSON (optional).
                          If omitted, TGT is only cached in memory.
            cas_config: Custom CAS client configuration (optional).
            cookies_file: Path to persist HTTP cookies as JSON (optional).
                          If omitted but ``session_file`` is set, defaults to
                          ``<session_file_stem>_cookies.json`` alongside it.
        """
        self._email = email
        self._password = password
        self._totp_secret = totp_secret
        self._session_file = Path(session_file).expanduser() if session_file else None

        # Derive cookies_file from session_file when not explicitly provided
        if cookies_file is None and self._session_file is not None:
            cookies_file = self._session_file.parent / f"{self._session_file.stem}_cookies.json"

        # Inject cookies_file into CAS config
        if cookies_file is not None:
            cas_config = (cas_config or CASClientConfig()).model_copy(update={"cookies_file": cookies_file})

        self._cas = CASClient(cas_config)
        self._cached_tgt: str | None = None
        self._cached_expires_at: float = 0.0
        self._session_source: SessionSource = SessionSource.UNCACHED

    @property
    def session_source(self) -> SessionSource:
        """Where the currently-held TGT came from (see :class:`SessionSource`)."""
        return self._session_source

    @property
    def session_expires_at(self) -> float | None:
        """TGT expiry as a Unix timestamp, or ``None`` if no TGT has been obtained."""
        return self._cached_expires_at or None

    async def get_tgt(self) -> str:
        """Get a valid TGT, using the full auth chain as needed.

        Chain: cached (memory/file) -> REST CAS -> browser fallback -> TOTP.

        Returns:
            Valid TGT string.

        Raises:
            CASError: If authentication fails at all stages.
        """
        # 1. Check in-memory cache
        if self._cached_tgt and self._is_tgt_fresh(self._cached_expires_at):
            self._session_source = SessionSource.MEMORY
            return self._cached_tgt

        # 2. Check session file
        if self._session_file:
            cached = self._load_session_file()
            if cached:
                tgt = str(cached["tgt"])
                self._cached_tgt = tgt
                self._cached_expires_at = float(cached["expires_at"])
                self._session_source = SessionSource.SESSION_FILE
                remaining = int(self._cached_expires_at - time.time())
                logger.info(
                    "Reusing cached TGT from %s (expires in %dh %dm — no fresh login, no email)",
                    self._session_file,
                    remaining // 3600,
                    (remaining % 3600) // 60,
                )
                return tgt

        # 3. REST CAS login (or browser fallback — _login_with_fallback sets session_source)
        logger.info("No valid cached TGT — performing fresh CAS login (XTB will email login notification)")
        result = await self._login_with_fallback()

        # 4. Handle 2FA if needed
        if isinstance(result, CASLoginTwoFactorRequired):
            result = await self._handle_two_factor(result)

        # 5. Cache and return
        self._cache_tgt(result.tgt, result.expires_at)
        return result.tgt

    def get_tgt_sync(self) -> str:
        """Synchronous wrapper for :meth:`get_tgt`.

        Uses a dedicated thread with its own event loop to avoid conflicts
        with any running loop in the caller's thread.
        """
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(lambda: asyncio.run(self.get_tgt())).result()

    async def get_service_ticket(self, service: str = "xapi5") -> str:
        """Get a service ticket, obtaining a TGT first if needed.

        Args:
            service: CAS service name. Use ``'xapi5'`` for WebSocket,
                     ``'abigail'`` for REST API.

        Returns:
            Service ticket string (``ST-...``).

        Raises:
            CASError: If TGT acquisition or service ticket request fails.
        """
        tgt = await self.get_tgt()
        try:
            st_result = await self._cas.get_service_ticket(tgt, service)
            return st_result.service_ticket
        except CASError as e:
            if e.code == "CAS_TGT_EXPIRED":
                # TGT was cached but expired server-side — clear and retry
                self._invalidate_cache()
                tgt = await self.get_tgt()
                st_result = await self._cas.get_service_ticket(tgt, service)
                return st_result.service_ticket
            raise

    def invalidate(self) -> None:
        """Clear cached TGT from memory and session file."""
        self._invalidate_cache()

    async def aclose(self) -> None:
        """Close underlying HTTP clients. Call on shutdown."""
        await self._cas.aclose()

    # -- Internal helpers --

    async def _login_with_fallback(self) -> CASLoginSuccess | CASLoginTwoFactorRequired:
        """Try REST CAS login, fall back to browser if WAF blocks."""
        try:
            result = await self._cas.login(self._email, self._password)
            self._session_source = SessionSource.CAS_LOGIN
            return result
        except CASError as e:
            # Invalid credentials — don't retry with browser
            if "UNAUTHORIZED" in e.code:
                raise
            logger.info("REST CAS login failed (%s), trying browser fallback", e.code)
            result = await self._cas.login_with_browser(self._email, self._password)
            self._session_source = SessionSource.BROWSER_LOGIN
            return result
        except Exception as e:
            # WAF often returns HTML instead of JSON → httpx decode error
            logger.info("REST CAS login failed (%s), trying browser fallback", e)
            result = await self._cas.login_with_browser(self._email, self._password)
            self._session_source = SessionSource.BROWSER_LOGIN
            return result

    async def _handle_two_factor(self, challenge: CASLoginTwoFactorRequired) -> CASLoginSuccess:
        """Handle 2FA challenge using TOTP auto-generation or browser OTP."""
        code = await self._generate_totp()

        # If browser session is active, prefer browser OTP (WAF blocks REST 2FA too)
        if hasattr(self._cas, "_browser_auth") and self._cas._browser_auth:
            logger.info("Submitting TOTP via browser OTP...")
            result = await self._cas.submit_browser_otp(code)
        else:
            # Try REST 2FA submission
            two_factor_type = "TOTP" if "TOTP" in challenge.methods else challenge.two_factor_auth_type
            result = await self._cas.login_with_two_factor(challenge.login_ticket, code, two_factor_type)

        if isinstance(result, CASLoginTwoFactorRequired):
            raise CASError(
                "AUTH_MANAGER_2FA_LOOP",
                "Server requested 2FA again after submitting code",
            )

        return result

    async def _generate_totp(self) -> str:
        """Generate a TOTP code from the stored secret.

        If fewer than 2 seconds remain in the current 30-second TOTP window,
        waits for the next window before generating the code. Without this,
        roughly 6.6% of logins fail because the code expires in transit before
        the server validates it. Waiting (rather than returning the next
        window's code) avoids relying on server-side window-drift tolerance.
        """
        if not self._totp_secret:
            raise CASError(
                "AUTH_MANAGER_2FA_NO_SECRET",
                "2FA is required but no totp_secret was provided. "
                "Pass totp_secret to AuthManager or disable 2FA on your account.",
            )
        try:
            import pyotp
        except ImportError as e:
            raise CASError(
                "AUTH_MANAGER_PYOTP_MISSING",
                "2FA requires the pyotp package. Install with: pip install 'pyotp>=2.9.0'",
            ) from e
        totp = pyotp.TOTP(self._totp_secret)
        remaining = totp.interval - (time.time() % totp.interval)
        if remaining < 2:
            # Within 2s of window boundary — wait for the next window so the
            # generated code is guaranteed valid for its full lifetime.
            await asyncio.sleep(remaining + 0.1)
        return str(totp.now())

    def _cache_tgt(self, tgt: str, expires_at: float) -> None:
        """Cache TGT in memory and optionally to session file."""
        self._cached_tgt = tgt
        self._cached_expires_at = expires_at

        if self._session_file:
            self._save_session_file(tgt, expires_at)

    def _invalidate_cache(self) -> None:
        """Clear TGT from memory and session file."""
        self._cached_tgt = None
        self._cached_expires_at = 0.0
        self._session_source = SessionSource.UNCACHED

        if self._session_file and self._session_file.exists():
            self._session_file.unlink(missing_ok=True)

    def _load_session_file(self) -> dict | None:
        """Load and validate cached TGT from session file.

        Returns:
            Dict with 'tgt' and 'expires_at' if valid, None otherwise.
        """
        if not self._session_file or not self._session_file.exists():
            return None

        try:
            # Fix permissions if file is readable by group/others (TGT is sensitive)
            file_mode = self._session_file.stat().st_mode & 0o777
            if file_mode & 0o077:
                logger.warning(
                    "Session file %s has permissive permissions (%o). Fixing to 0600.",
                    self._session_file,
                    file_mode,
                )
                self._session_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

            data = json.loads(self._session_file.read_text())
            tgt = data.get("tgt", "")
            expires_at_str = data.get("expires_at", "")

            if not tgt or not expires_at_str:
                return None

            expires_at = datetime.fromisoformat(expires_at_str).timestamp()

            if not self._is_tgt_fresh(expires_at):
                return None

            return {"tgt": tgt, "expires_at": expires_at}
        except (json.JSONDecodeError, KeyError, ValueError, OSError):
            return None

    def _save_session_file(self, tgt: str, expires_at: float) -> None:
        """Save TGT to session file as JSON with restricted permissions (0600)."""
        if not self._session_file:
            return

        extracted_at = datetime.now(UTC)
        expires_at_dt = datetime.fromtimestamp(expires_at, tz=UTC)

        data = {
            "tgt": tgt,
            "extracted_at": extracted_at.isoformat(),
            "expires_at": expires_at_dt.isoformat(),
        }

        self._session_file.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, indent=2)
        # Write with owner-only permissions to protect the TGT
        fd = os.open(
            str(self._session_file),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,  # 0600
        )
        try:
            os.write(fd, content.encode())
        finally:
            os.close(fd)

    @staticmethod
    def _is_tgt_fresh(expires_at: float) -> bool:
        """Check if TGT is still valid with a safety margin."""
        return time.time() < (expires_at - TGT_REFRESH_MARGIN_SECONDS)

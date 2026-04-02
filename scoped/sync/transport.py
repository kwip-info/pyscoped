"""HTTP transport for the management plane API.

Handles authentication, HMAC signing, and request/response
serialization. Uses ``urllib.request`` (stdlib) to avoid adding
HTTP client dependencies.

Security layers:
    - TLS (HTTPS) for encryption in transit
    - ``Authorization: Bearer <api_key>`` for identity
    - HMAC-SHA256 of batch body for integrity + authenticity
    - Signing key derived from api_key (never used raw as HMAC key)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.request
import urllib.error
from datetime import datetime
from typing import Any

from scoped.logging import get_logger
from scoped.exceptions import (
    SyncAuthenticationError,
    SyncBatchRejectedError,
    SyncTransportError,
)
from scoped.sync.models import (
    ApiError,
    SyncBatch,
    SyncBatchAck,
    SyncEntryMetadata,
    SyncVerifyRequest,
    SyncVerifyResponse,
    PingResponse,
)


_logger = get_logger("sync.transport")


class ManagementPlaneClient:
    """HTTP client for the pyscoped management plane API.

    All requests include ``Authorization: Bearer <api_key>`` and
    ``X-Pyscoped-SDK-Version`` headers. Sync batch requests also
    include an ``X-Pyscoped-Signature`` HMAC header.

    Args:
        api_key: The pyscoped API key.
        base_url: Management plane API base URL.
        timeout: Request timeout in seconds.
    """

    DEFAULT_BASE_URL = "https://api.pyscoped.dev/v1"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 30,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._signing_key = self._derive_signing_key(api_key)

    # -- Public API --------------------------------------------------------

    def ping(self) -> PingResponse:
        """Check if the management plane is reachable."""
        data = self._request("GET", "/ping")
        return PingResponse.model_validate(data)

    def push_batch(self, batch: SyncBatch) -> SyncBatchAck:
        """Push a sync batch to the management plane.

        The batch body is signed with HMAC-SHA256.
        """
        _logger.info(
            "sync.push_batch", entry_count=len(batch.entries),
        )
        data = self._request("POST", "/sync/batch", body=batch, sign=True)
        return SyncBatchAck.model_validate(data)

    def verify_sync(self, req: SyncVerifyRequest) -> SyncVerifyResponse:
        """Verify chain integrity between local and server."""
        data = self._request("POST", "/sync/verify", body=req)
        return SyncVerifyResponse.model_validate(data)

    # -- Signing -----------------------------------------------------------

    @staticmethod
    def _derive_signing_key(api_key: str) -> bytes:
        """Derive HMAC signing key from the API key.

        Uses ``SHA-256(api_key + ":pyscoped-sync-v1")``. The raw API
        key is never used directly as an HMAC key.
        """
        return hashlib.sha256(
            (api_key + ":pyscoped-sync-v1").encode()
        ).digest()

    def sign_payload(self, payload_bytes: bytes) -> str:
        """Compute HMAC-SHA256 signature of a payload.

        Args:
            payload_bytes: The raw JSON body bytes.

        Returns:
            Hex-encoded HMAC-SHA256 digest.
        """
        return hmac.new(
            self._signing_key, payload_bytes, hashlib.sha256
        ).hexdigest()

    @staticmethod
    def compute_content_hash(entries: list[SyncEntryMetadata]) -> str:
        """SHA-256 of the JSON-serialized entry list.

        Uses sorted keys and compact separators for deterministic output.
        """
        payload = json.dumps(
            [e.model_dump(mode="json") for e in entries],
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    # -- HTTP plumbing -----------------------------------------------------

    def _request(
        self,
        method: str,
        path: str,
        body: Any | None = None,
        sign: bool = False,
    ) -> dict[str, Any]:
        """Make an authenticated HTTP request to the management plane.

        Args:
            method: HTTP method (GET, POST).
            path: API path (e.g. ``"/sync/batch"``).
            body: Pydantic model to serialize as JSON body.
            sign: If True, add HMAC signature header.

        Returns:
            Parsed JSON response dict.

        Raises:
            SyncAuthenticationError: On 401/403.
            SyncBatchRejectedError: On 400.
            SyncTransportError: On other HTTP errors or network failures.
        """
        from scoped import __version__

        url = f"{self._base_url}{path}"

        body_bytes: bytes | None = None
        if body is not None:
            body_bytes = body.model_dump_json().encode("utf-8")

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "X-Pyscoped-SDK-Version": __version__,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        if sign and body_bytes is not None:
            headers["X-Pyscoped-Signature"] = self.sign_payload(body_bytes)

        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            status = exc.code
            try:
                error_body = json.loads(exc.read().decode("utf-8"))
                error = ApiError.model_validate(error_body)
                msg = error.message
            except Exception:
                msg = f"HTTP {status}"

            if status in (401, 403):
                raise SyncAuthenticationError(
                    f"Authentication failed: {msg}",
                    context={"status": status},
                ) from exc
            if status == 400:
                raise SyncBatchRejectedError(
                    f"Batch rejected: {msg}",
                    context={"status": status},
                ) from exc
            raise SyncTransportError(
                f"Management plane error: {msg}",
                context={"status": status},
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise SyncTransportError(
                f"Connection failed: {exc}",
                context={"url": url},
            ) from exc

"""Bounded stdlib HTTP client for the bigfeels memory service."""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


class BigfeelsUnavailable(RuntimeError):
    """The local memory service could not complete a request."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


_MAX_RESPONSE_BYTES = 1024 * 1024


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        del req, fp, code, msg, headers, newurl
        return None


@dataclass(frozen=True)
class AdapterConfig:
    base_url: str
    token: str
    owner_space: str
    project_spaces: tuple[str, ...] = ()
    write_space: str = ""
    budget: int = 800
    timeout: float = 0.75

    @property
    def spaces(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys((self.owner_space, *self.project_spaces)))

    def validate(self) -> "AdapterConfig":
        parsed = urllib.parse.urlsplit(self.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("BIGFEELS_MEM_URL must be an http or https URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("BIGFEELS_MEM_URL cannot include credentials, a query, or a fragment")
        if parsed.path not in {"", "/"}:
            raise ValueError("BIGFEELS_MEM_URL must not include a path")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("plain HTTP bigfeels service URLs must use a loopback host")
        if not self.token.strip():
            raise ValueError("BIGFEELS_MEM_TOKEN is required")
        if not self.owner_space.strip():
            raise ValueError("BIGFEELS_MEM_OWNER_SPACE is required")
        if not self.write_space:
            object.__setattr__(self, "write_space", self.owner_space)
        if self.write_space not in self.spaces:
            raise ValueError("BIGFEELS_MEM_WRITE_SPACE must be an allowed owner or project space")
        if not 1 <= self.budget <= 4000:
            raise ValueError("BIGFEELS_MEM_BUDGET must be between 1 and 4000")
        if not 0 < self.timeout <= 10:
            raise ValueError("BIGFEELS_MEM_TIMEOUT must be greater than 0 and at most 10 seconds")
        return self


class BigfeelsClient:
    def __init__(self, config: AdapterConfig):
        self.config = config.validate()
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _NoRedirects(),
        )

    def post(self, operation: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.config.base_url.rstrip('/')}/v1/{operation}"
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.config.timeout) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > _MAX_RESPONSE_BYTES:
                    raise BigfeelsUnavailable(
                        f"bigfeels /v1/{operation} response exceeded size limit"
                    )
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                if len(raw) > _MAX_RESPONSE_BYTES:
                    raise BigfeelsUnavailable(
                        f"bigfeels /v1/{operation} response exceeded size limit"
                    )
        except urllib.error.HTTPError as exc:
            try:
                raise BigfeelsUnavailable(
                    f"bigfeels /v1/{operation} unavailable (HTTP {exc.code})",
                    status=exc.code,
                ) from None
            finally:
                exc.close()
        except (urllib.error.URLError, TimeoutError, socket.timeout, OSError):
            raise BigfeelsUnavailable(f"bigfeels /v1/{operation} unavailable") from None
        try:
            result = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise BigfeelsUnavailable(
                f"bigfeels /v1/{operation} returned invalid JSON"
            ) from None
        if not isinstance(result, dict):
            raise BigfeelsUnavailable(
                f"bigfeels /v1/{operation} returned an invalid response"
            )
        return result

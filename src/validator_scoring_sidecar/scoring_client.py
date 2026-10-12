"""HTTP client for the public Dynamic UNL scoring service."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

import httpx

from validator_scoring_sidecar.config import SidecarConfig

# Largest response body accepted from the scoring service or an IPFS gateway.
# Measured 2026-10-12 on testnet rounds 24-26: the largest input-package or
# final-bundle file was inputs/model_request.json at 55,997 bytes (round 25),
# so 32 MiB leaves several hundred times headroom while still bounding memory.
MAX_RESPONSE_BYTES = 32 * 1024 * 1024


class ScoringClientError(RuntimeError):
    """Base error for scoring service client failures."""


class ScoringHTTPError(ScoringClientError):
    """Raised when the scoring service returns a non-success HTTP status."""

    def __init__(
        self,
        status_code: int,
        url: str,
        *,
        service_name: str = "Scoring service",
    ):
        self.status_code = status_code
        self.url = url
        super().__init__(f"{service_name} returned HTTP {status_code} for {url}")


class ScoringNetworkError(ScoringClientError):
    """Raised when the scoring service cannot be reached."""


class ScoringResponseError(ScoringClientError):
    """Raised when the scoring service returns an unexpected response body."""


class ScoringClient:
    """Client for scoring service public round metadata endpoints."""

    def __init__(
        self,
        config: SidecarConfig,
        *,
        http_client: httpx.Client | None = None,
    ):
        self._config = config
        self._http = http_client or httpx.Client(timeout=config.timeout_seconds)
        self._owns_client = http_client is None

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def round_url(self, round_id: int) -> str:
        if round_id <= 0:
            raise ValueError("round_id must be greater than zero")
        return f"{self._config.scoring_base_url}/api/scoring/rounds/{round_id}"

    def rounds_url(self, *, limit: int, offset: int = 0) -> str:
        if limit <= 0:
            raise ValueError("limit must be greater than zero")
        if offset < 0:
            raise ValueError("offset must be greater than or equal to zero")
        return (
            f"{self._config.scoring_base_url}/api/scoring/rounds"
            f"?limit={limit}&offset={offset}"
        )

    def config_url(self) -> str:
        return f"{self._config.scoring_base_url}/api/scoring/config"

    def input_package_file_url(self, round_number: int, file_path: str) -> str:
        if round_number <= 0:
            raise ValueError("round_number must be greater than zero")
        return (
            f"{self._config.scoring_base_url}/api/scoring/rounds/{round_number}"
            f"/input/{quote(file_path, safe='/')}"
        )

    def final_bundle_file_url(self, round_number: int, file_path: str) -> str:
        if round_number <= 0:
            raise ValueError("round_number must be greater than zero")
        return (
            f"{self._config.scoring_base_url}/api/scoring/rounds/{round_number}"
            f"/{quote(file_path, safe='/')}"
        )

    def ipfs_package_file_url(
        self,
        gateway_url: str,
        cid: str,
        file_path: str,
    ) -> str:
        return f"{gateway_url}/{quote(cid, safe='')}/{quote(file_path, safe='/')}"

    def fetch_round(self, round_id: int) -> dict[str, Any]:
        """Fetch one public scoring round metadata record."""

        payload = self._fetch_json(
            self.round_url(round_id),
            service_name="Scoring service",
        )
        if not isinstance(payload, dict):
            raise ScoringResponseError(
                "Scoring service returned a non-object JSON body for "
                f"{self.round_url(round_id)}"
            )
        return payload

    def fetch_rounds(self, *, limit: int, offset: int = 0) -> list[dict[str, Any]]:
        """Fetch recent public scoring round metadata records, newest first."""

        url = self.rounds_url(limit=limit, offset=offset)
        payload = self._fetch_json(url, service_name="Scoring service")
        if not isinstance(payload, dict):
            raise ScoringResponseError(
                "Scoring service returned a non-object JSON body for "
                f"{url}"
            )

        rounds = payload.get("rounds")
        if not isinstance(rounds, list):
            raise ScoringResponseError(
                f"Scoring service response for {url} is missing a rounds list"
            )
        if any(not isinstance(round_payload, dict) for round_payload in rounds):
            raise ScoringResponseError(
                f"Scoring service response for {url} contains a non-object round"
            )
        return rounds

    def fetch_config(self) -> dict[str, Any]:
        """Fetch the foundation public scoring config (chain-discovery fields)."""

        payload = self._fetch_json(self.config_url(), service_name="Scoring service")
        if not isinstance(payload, dict):
            raise ScoringResponseError(
                "Scoring service returned a non-object JSON body for "
                f"{self.config_url()}"
            )
        return payload

    def fetch_input_package_file(
        self,
        round_number: int,
        file_path: str,
    ) -> dict[str, Any] | list[Any]:
        """Fetch one frozen input package file from scoring-service HTTPS fallback."""

        return self._fetch_json(
            self.input_package_file_url(round_number, file_path),
            service_name="Scoring service",
        )

    def fetch_final_bundle_file(
        self,
        round_number: int,
        file_path: str,
    ) -> dict[str, Any] | list[Any]:
        """Fetch one final audit bundle file from scoring-service HTTPS fallback."""

        return self._fetch_json(
            self.final_bundle_file_url(round_number, file_path),
            service_name="Scoring service",
        )

    def fetch_ipfs_package_file(
        self,
        gateway_url: str,
        cid: str,
        file_path: str,
    ) -> dict[str, Any] | list[Any]:
        """Fetch one package file from an IPFS gateway path."""

        return self._fetch_json(
            self.ipfs_package_file_url(gateway_url, cid, file_path),
            service_name="IPFS gateway",
        )

    def _fetch_json(
        self,
        url: str,
        *,
        service_name: str,
    ) -> dict[str, Any] | list[Any]:
        try:
            with self._http.stream("GET", url) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    raise ScoringHTTPError(
                        response.status_code,
                        url,
                        service_name=service_name,
                    )
                body = _read_bounded_body(response, url, service_name=service_name)
        except httpx.RequestError as exc:
            raise ScoringNetworkError(
                f"Could not reach {service_name.lower()} at {url}: {exc}"
            ) from exc

        try:
            payload = json.loads(body)
        except ValueError as exc:
            raise ScoringResponseError(
                f"{service_name} returned invalid JSON for {url}"
            ) from exc

        if not isinstance(payload, (dict, list)):
            raise ScoringResponseError(
                f"{service_name} returned a non-JSON-object/array body for {url}"
            )
        return payload


def _read_bounded_body(
    response: httpx.Response,
    url: str,
    *,
    service_name: str,
) -> bytes:
    """Read a streamed body, refusing it once it exceeds MAX_RESPONSE_BYTES."""

    cap = MAX_RESPONSE_BYTES

    declared = response.headers.get("Content-Length")
    if declared is not None:
        try:
            declared_length = int(declared)
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > cap:
            raise _response_too_large(service_name, url, cap)

    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > cap:
            # Drop the partial body now: the traceback keeps this frame alive.
            chunks.clear()
            raise _response_too_large(service_name, url, cap)
        chunks.append(chunk)
    return b"".join(chunks)


def _response_too_large(service_name: str, url: str, cap: int) -> ScoringResponseError:
    return ScoringResponseError(
        f"{service_name} response for {url} exceeds {cap} bytes"
    )

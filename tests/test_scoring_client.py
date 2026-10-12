import gzip

import httpx
import pytest

from validator_scoring_sidecar import scoring_client
from validator_scoring_sidecar.config import load_config
from validator_scoring_sidecar.scoring_client import (
    ScoringClient,
    ScoringHTTPError,
    ScoringNetworkError,
    ScoringResponseError,
)


def _config():
    return load_config(base_url="https://scoring.example.org/", environ={})


def test_round_url_uses_scoring_endpoint_path():
    client = ScoringClient(_config(), http_client=httpx.Client())

    assert (
        client.round_url(123)
        == "https://scoring.example.org/api/scoring/rounds/123"
    )


def test_rounds_url_uses_scoring_endpoint_path_and_query():
    client = ScoringClient(_config(), http_client=httpx.Client())

    assert client.rounds_url(limit=20, offset=5) == (
        "https://scoring.example.org/api/scoring/rounds?limit=20&offset=5"
    )


def test_input_package_file_url_uses_input_namespace():
    client = ScoringClient(_config(), http_client=httpx.Client())

    assert client.input_package_file_url(456, "inputs/model_request.json") == (
        "https://scoring.example.org/api/scoring/rounds/456"
        "/input/inputs/model_request.json"
    )


def test_ipfs_package_file_url_uses_gateway_cid_and_file_path():
    client = ScoringClient(_config(), http_client=httpx.Client())

    assert client.ipfs_package_file_url(
        "https://ipfs.example.org/ipfs",
        "QmInput",
        "inputs/model_request.json",
    ) == "https://ipfs.example.org/ipfs/QmInput/inputs/model_request.json"


def test_fetch_round_returns_json_payload():
    def handler(request):
        assert request.url.path == "/api/scoring/rounds/123"
        return httpx.Response(200, json={"id": 123})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = ScoringClient(_config(), http_client=http_client)

    assert client.fetch_round(123) == {"id": 123}


def test_fetch_rounds_returns_round_payloads():
    def handler(request):
        assert request.url.path == "/api/scoring/rounds"
        assert request.url.params["limit"] == "20"
        assert request.url.params["offset"] == "0"
        return httpx.Response(200, json={"rounds": [{"id": 123}], "total": 1})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = ScoringClient(_config(), http_client=http_client)

    assert client.fetch_rounds(limit=20) == [{"id": 123}]


def test_fetch_rounds_rejects_malformed_payload():
    http_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"items": []})
        )
    )
    client = ScoringClient(_config(), http_client=http_client)

    with pytest.raises(ScoringResponseError, match="missing a rounds list"):
        client.fetch_rounds(limit=20)


def test_fetch_round_raises_for_http_error():
    http_client = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    )
    client = ScoringClient(_config(), http_client=http_client)

    with pytest.raises(ScoringHTTPError) as exc_info:
        client.fetch_round(123)

    assert exc_info.value.status_code == 404


def test_fetch_round_raises_for_network_error():
    def handler(request):
        raise httpx.ConnectError("connection failed", request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = ScoringClient(_config(), http_client=http_client)

    with pytest.raises(ScoringNetworkError):
        client.fetch_round(123)


def test_final_bundle_file_url_uses_round_namespace_without_input_segment():
    client = ScoringClient(_config(), http_client=httpx.Client())

    assert client.final_bundle_file_url(456, "outputs/verification_hashes.json") == (
        "https://scoring.example.org/api/scoring/rounds/456"
        "/outputs/verification_hashes.json"
    )


def test_fetch_final_bundle_file_returns_json_payload():
    def handler(request):
        assert request.url.path == (
            "/api/scoring/rounds/456/outputs/verification_hashes.json"
        )
        return httpx.Response(200, json={"model_response_hash": "abc"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = ScoringClient(_config(), http_client=http_client)

    assert client.fetch_final_bundle_file(
        456, "outputs/verification_hashes.json"
    ) == {"model_response_hash": "abc"}


TEST_CAP = 1024


@pytest.fixture
def small_cap(monkeypatch):
    monkeypatch.setattr(scoring_client, "MAX_RESPONSE_BYTES", TEST_CAP)
    return TEST_CAP


def _json_list_body(size):
    """A valid JSON array body of exactly ``size`` bytes."""

    return b'["' + b"a" * (size - 4) + b'"]'


class _CountingStream:
    """Yield ``body`` in fixed-size chunks (optionally forever) and count reads."""

    def __init__(self, body=b"", *, chunk_size=128, endless=False):
        self.body = body
        self.chunk_size = chunk_size
        self.endless = endless
        self.chunks_read = 0

    def __iter__(self):
        if self.endless:
            while True:
                self.chunks_read += 1
                yield b"a" * self.chunk_size
        for start in range(0, len(self.body), self.chunk_size):
            self.chunks_read += 1
            yield self.body[start : start + self.chunk_size]


def _streaming_client(stream, *, content_length=None):
    def handler(request):
        headers = {"Content-Type": "application/json"}
        if content_length is not None:
            headers["Content-Length"] = str(content_length)
        return httpx.Response(200, headers=headers, content=stream)

    return ScoringClient(
        _config(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_fetch_accepts_body_of_exactly_cap_with_content_length(small_cap):
    body = _json_list_body(small_cap)
    stream = _CountingStream(body)
    client = _streaming_client(stream, content_length=len(body))

    payload = client.fetch_ipfs_package_file(
        "https://gateway.example.org/ipfs", "bafycid", "inputs/a.json"
    )

    assert payload == ["a" * (small_cap - 4)]
    assert stream.chunks_read == small_cap // stream.chunk_size


def test_fetch_accepts_body_of_exactly_cap_without_content_length(small_cap):
    body = _json_list_body(small_cap)
    stream = _CountingStream(body)
    client = _streaming_client(stream)

    payload = client.fetch_input_package_file(7, "inputs/a.json")

    assert payload == ["a" * (small_cap - 4)]


def test_fetch_rejects_declared_content_length_over_cap_without_reading(small_cap):
    body = _json_list_body(small_cap + 1)
    stream = _CountingStream(body)
    client = _streaming_client(stream, content_length=len(body))

    with pytest.raises(ScoringResponseError) as exc_info:
        client.fetch_ipfs_package_file(
            "https://gateway.example.org/ipfs", "bafycid", "inputs/a.json"
        )

    assert str(exc_info.value) == (
        "IPFS gateway response for "
        "https://gateway.example.org/ipfs/bafycid/inputs/a.json "
        f"exceeds {small_cap} bytes"
    )
    assert stream.chunks_read == 0


def test_fetch_rejects_streamed_body_one_byte_over_cap(small_cap):
    body = _json_list_body(small_cap + 1)
    stream = _CountingStream(body)
    client = _streaming_client(stream)

    with pytest.raises(ScoringResponseError) as exc_info:
        client.fetch_final_bundle_file(456, "outputs/final_scores.json")

    assert str(exc_info.value) == (
        "Scoring service response for "
        "https://scoring.example.org/api/scoring/rounds/456/outputs/final_scores.json "
        f"exceeds {small_cap} bytes"
    )
    assert stream.chunks_read == small_cap // stream.chunk_size + 1


def test_fetch_stops_reading_endless_body_far_above_cap(small_cap):
    stream = _CountingStream(endless=True)
    client = _streaming_client(stream)

    with pytest.raises(ScoringResponseError, match=f"exceeds {small_cap} bytes"):
        client.fetch_round(123)

    assert stream.chunks_read == small_cap // stream.chunk_size + 1


def test_fetch_rejects_body_longer_than_its_declared_content_length(small_cap):
    stream = _CountingStream(endless=True)
    client = _streaming_client(stream, content_length=10)

    with pytest.raises(ScoringResponseError, match=f"exceeds {small_cap} bytes"):
        client.fetch_round(123)

    assert stream.chunks_read == small_cap // stream.chunk_size + 1


def test_refusal_does_not_keep_partial_body_alive(small_cap):
    stream = _CountingStream(endless=True)
    client = _streaming_client(stream)

    with pytest.raises(ScoringResponseError) as exc_info:
        client.fetch_round(123)

    traceback = exc_info.value.__traceback__
    reader_frames = []
    while traceback is not None:
        if traceback.tb_frame.f_code.co_name == "_read_bounded_body":
            reader_frames.append(traceback.tb_frame)
        traceback = traceback.tb_next
    assert len(reader_frames) == 1
    assert reader_frames[0].f_locals["chunks"] == []
    assert all(
        not isinstance(value, BaseException)
        for value in reader_frames[0].f_locals.values()
    )


def test_fetch_caps_decoded_size_of_compressed_body(small_cap):
    compressed = gzip.compress(_json_list_body(small_cap * 64))
    assert len(compressed) < small_cap

    def handler(request):
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            content=compressed,
        )

    client = ScoringClient(
        _config(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ScoringResponseError, match=f"exceeds {small_cap} bytes"):
        client.fetch_input_package_file(7, "inputs/a.json")


def test_fetch_does_not_read_body_of_http_error(small_cap):
    stream = _CountingStream(endless=True)

    def handler(request):
        return httpx.Response(503, content=stream)

    client = ScoringClient(
        _config(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ScoringHTTPError) as exc_info:
        client.fetch_round(123)

    assert str(exc_info.value) == (
        "Scoring service returned HTTP 503 for "
        "https://scoring.example.org/api/scoring/rounds/123"
    )
    assert stream.chunks_read == 0


def test_fetch_maps_network_error_while_streaming_body():
    def broken_body():
        yield b'{"id": '
        raise httpx.ReadError("connection reset")

    def handler(request):
        return httpx.Response(200, content=broken_body())

    client = ScoringClient(
        _config(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ScoringNetworkError) as exc_info:
        client.fetch_round(123)

    assert str(exc_info.value) == (
        "Could not reach scoring service at "
        "https://scoring.example.org/api/scoring/rounds/123: connection reset"
    )


def test_fetch_keeps_invalid_json_error(small_cap):
    client = _streaming_client(_CountingStream(b"{not json"))

    with pytest.raises(ScoringResponseError) as exc_info:
        client.fetch_input_package_file(7, "inputs/a.json")

    assert str(exc_info.value) == (
        "Scoring service returned invalid JSON for "
        "https://scoring.example.org/api/scoring/rounds/7/input/inputs/a.json"
    )


def test_fetch_keeps_non_object_or_array_error():
    def handler(request):
        return httpx.Response(200, json="just a string")

    client = ScoringClient(
        _config(),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ScoringResponseError) as exc_info:
        client.fetch_ipfs_package_file(
            "https://gateway.example.org/ipfs", "bafycid", "inputs/a.json"
        )

    assert str(exc_info.value) == (
        "IPFS gateway returned a non-JSON-object/array body for "
        "https://gateway.example.org/ipfs/bafycid/inputs/a.json"
    )


def test_default_max_response_bytes_is_32_mib():
    assert scoring_client.MAX_RESPONSE_BYTES == 32 * 1024 * 1024

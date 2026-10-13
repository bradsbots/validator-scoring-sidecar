import pytest

from validator_scoring_sidecar.round_metadata import (
    MissingFrozenInputMetadata,
    RoundMetadata,
    RoundMetadataError,
)


def _payload(**overrides):
    payload = {
        "id": 123,
        "round_number": 456,
        "status": "COMPLETE",
        "input_package_cid": "QmInput",
        "input_package_hash": "a" * 64,
        "input_frozen_at": "2026-05-25T00:00:00+00:00",
        "final_bundle_cid": "QmFinal",
    }
    payload.update(overrides)
    return payload


def test_round_metadata_parses_frozen_input_fields():
    metadata = RoundMetadata.from_api_payload(
        _payload(
            status=" COMPLETE ",
            input_package_cid=" QmInput ",
            final_bundle_cid=" QmFinal ",
        ),
        requested_round_id=123,
    )

    assert metadata.as_dict() == {
        "round_id": 123,
        "round_number": 456,
        "status": "COMPLETE",
        "input_package_cid": "QmInput",
        "input_package_hash": "a" * 64,
        "input_frozen_at": "2026-05-25T00:00:00+00:00",
        "final_bundle_cid": "QmFinal",
    }


@pytest.mark.parametrize(
    "field",
    ["input_package_cid", "input_package_hash", "input_frozen_at"],
)
def test_round_metadata_requires_frozen_input_fields(field):
    payload = _payload(**{field: None})

    with pytest.raises(MissingFrozenInputMetadata) as exc_info:
        RoundMetadata.from_api_payload(payload, requested_round_id=123)

    assert field in exc_info.value.missing_fields
    assert "created before frozen input metadata was introduced" in str(exc_info.value)


def test_round_metadata_reports_all_missing_frozen_input_fields():
    payload = _payload(input_package_cid=None, input_frozen_at=" ")

    with pytest.raises(MissingFrozenInputMetadata) as exc_info:
        RoundMetadata.from_api_payload(payload, requested_round_id=123)

    assert exc_info.value.missing_fields == [
        "input_package_cid",
        "input_frozen_at",
    ]


def test_round_metadata_allows_missing_final_bundle_cid():
    metadata = RoundMetadata.from_api_payload(
        _payload(final_bundle_cid=None),
        requested_round_id=123,
    )

    assert metadata.final_bundle_cid is None


def test_round_metadata_treats_blank_final_bundle_cid_as_missing():
    metadata = RoundMetadata.from_api_payload(
        _payload(final_bundle_cid=" "),
        requested_round_id=123,
    )

    assert metadata.final_bundle_cid is None


def test_round_metadata_rejects_malformed_core_fields():
    with pytest.raises(RoundMetadataError, match="round_number"):
        RoundMetadata.from_api_payload(
            _payload(round_number="456"),
            requested_round_id=123,
        )


@pytest.mark.parametrize("field", ["id", "round_number"])
def test_round_metadata_rejects_non_positive_identifiers(field):
    with pytest.raises(RoundMetadataError, match=field):
        RoundMetadata.from_api_payload(
            _payload(**{field: 0}),
            requested_round_id=123,
        )


def test_round_metadata_rejects_id_other_than_requested_round_id():
    with pytest.raises(
        RoundMetadataError,
        match="round metadata id 7 does not match requested round id 5",
    ):
        RoundMetadata.from_api_payload(_payload(id=7), requested_round_id=5)


def test_round_metadata_rejects_id_equal_to_round_number_but_not_requested():
    with pytest.raises(RoundMetadataError, match="requested round id 123"):
        RoundMetadata.from_api_payload(
            _payload(id=456, round_number=456),
            requested_round_id=123,
        )


def test_round_metadata_reports_id_mismatch_before_missing_frozen_input():
    with pytest.raises(RoundMetadataError, match="does not match") as exc_info:
        RoundMetadata.from_api_payload(
            _payload(id=7, input_package_cid=None),
            requested_round_id=5,
        )

    assert not isinstance(exc_info.value, MissingFrozenInputMetadata)


@pytest.mark.parametrize("requested_round_id", [True, 1.0, "1"])
def test_round_metadata_rejects_non_int_requested_round_id(requested_round_id):
    with pytest.raises(RoundMetadataError, match="does not match"):
        RoundMetadata.from_api_payload(
            _payload(id=1),
            requested_round_id=requested_round_id,
        )


@pytest.mark.parametrize("payload_id", [True, "123", 123.0, None])
def test_round_metadata_rejects_malformed_id_before_comparing(payload_id):
    with pytest.raises(RoundMetadataError, match="id must be an integer"):
        RoundMetadata.from_api_payload(
            _payload(id=payload_id),
            requested_round_id=123,
        )


def test_round_metadata_accepts_id_equal_to_requested_round_id():
    huge = int("9" * 400)
    metadata = RoundMetadata.from_api_payload(
        _payload(id=huge),
        requested_round_id=huge,
    )

    assert metadata.round_id == huge

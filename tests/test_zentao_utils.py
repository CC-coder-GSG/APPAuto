from app.services.zentao_utils import (
    is_placeholder_name,
    make_placeholder_name,
    normalize_version_name,
    parse_job_name_to_major_version_no,
)


def test_make_placeholder_name_strips_64bit_suffix():
    assert (
        make_placeholder_name("4.0.3.1.260513(40301047)(64-bit)")
        == "4.0.3.1.26xxxx(4030xxxx)"
    )


def test_make_placeholder_name_strips_32bit_suffix_with_space():
    assert (
        make_placeholder_name("4.0.3.18.260413_Geofennel(40318007) (32-bit)")
        == "4.0.3.18.26xxxx(4031xxxx)"
    )


def test_make_placeholder_name_strips_bd_variant_suffix():
    assert (
        make_placeholder_name("4.0.3.1.260513_BD(40301048)(64-bit)")
        == "4.0.3.1.26xxxx(4030xxxx)"
    )


def test_make_placeholder_name_strips_long_variant_suffix():
    assert (
        make_placeholder_name("4.0.3.0.260513_GALAIESSURVEY_free.95(40300129)")
        == "4.0.3.0.26xxxx(4030xxxx)"
    )


def test_make_placeholder_name_strips_alpha_series_suffix():
    assert (
        make_placeholder_name("4.0.3.20.260513_JFJ_alpha.3(40320003)")
        == "4.0.3.20.26xxxx(4032xxxx)"
    )


def test_make_placeholder_name_keeps_clean_input_canonical():
    assert (
        make_placeholder_name("4.0.3.1.260513(40301044)")
        == "4.0.3.1.26xxxx(4030xxxx)"
    )


def test_make_placeholder_name_empty_input():
    assert make_placeholder_name("") == ""
    assert make_placeholder_name(None) == ""


def test_is_placeholder_name_detects_xxxx_marker():
    assert is_placeholder_name("4.0.3.1.26xxxx(4030xxxx)") is True
    assert is_placeholder_name("4.0.3.1.260513(40301044)") is False
    assert is_placeholder_name("") is False


def test_normalize_version_name_strips_platform_suffix():
    assert (
        normalize_version_name("4.0.3.1.260413(40301023)(64-bit)")
        == "4.0.3.1.260413(40301023)"
    )


def test_parse_job_name_to_major_version_no_basic():
    assert parse_job_name_to_major_version_no("s4031") == "V4.0.3.1"
    assert parse_job_name_to_major_version_no("s40311") == "V4.0.3.11"
    assert parse_job_name_to_major_version_no("s40311-1") == "V4.0.3.11"
    assert parse_job_name_to_major_version_no("s40311_free") == "V4.0.3.11"
    assert parse_job_name_to_major_version_no("other") is None

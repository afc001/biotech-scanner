import pytest

from scanner import stats


def test_funnel_sums_across_runs_and_skips_nulls(seeded_db):
    result = stats.funnel(seeded_db)
    assert result["n_runs"] == 2
    # run2's n_fetched is NULL (migrated) -- SUM skips it rather than
    # treating it as 0, so the total equals run1's 10, not less.
    assert result["n_fetched"] == 10
    assert result["n_sic_matched"] == 10
    assert result["n_address_matched"] == 1
    assert result["n_scored"] == 3
    assert result["n_surfaced"] == 3


def test_funnel_date_range_filters_to_one_run(seeded_db):
    result = stats.funnel(seeded_db, date_from="2026-08-02", date_to="2026-08-02")
    assert result["n_runs"] == 1
    assert result["n_scored"] == 1
    assert result["n_fetched"] is None  # the only run in range never captured this


def test_precision_counts_and_ratio(seeded_db):
    result = stats.precision(seeded_db)
    assert result["relevant"] == 2
    assert result["noise"] == 1
    assert result["total_labelled"] == 3
    assert result["precision"] == pytest.approx(2 / 3)


def test_precision_with_no_labels_is_none_not_zero(db_conn):
    result = stats.precision(db_conn)
    assert result["total_labelled"] == 0
    assert result["precision"] is None


def test_component_means_grouped_by_verdict(seeded_db):
    rows = {r["analyst_verdict"]: r for r in stats.component_means(seeded_db)}

    assert rows["relevant"]["n"] == 2
    assert rows["relevant"]["mean_interest_score"] == pytest.approx((5 + 4) / 2)
    assert rows["relevant"]["mean_orcid_confirmed"] == pytest.approx(1.0)
    # 1001 has incubator_matched=1, 1003 has it NULL (migrated) -- AVG must
    # skip the NULL, not average it in as a phantom 0.
    assert rows["relevant"]["mean_incubator_matched"] == pytest.approx(1.0)

    assert rows["noise"]["n"] == 1
    assert rows["noise"]["mean_interest_score"] == pytest.approx(1.0)
    assert rows["noise"]["mean_orcid_confirmed"] == pytest.approx(0.0)
    assert rows["noise"]["mean_website_found"] == pytest.approx(1.0)

import pytest

from incremental_filter import apply_incremental_predicate


def test_adds_where_when_none_exists():
    query = "SELECT * FROM public.orders;"
    result = apply_incremental_predicate(query, "created_at", "2026-01-01", "2026-01-31")
    assert result == "SELECT * FROM public.orders WHERE created_at BETWEEN '2026-01-01' AND '2026-01-31';"


def test_adds_and_when_where_already_exists():
    query = "SELECT * FROM public.orders WHERE status = 'ACTIVE';"
    result = apply_incremental_predicate(query, "updated_at", "2026-01-01", "2026-01-31")
    assert result.endswith("AND updated_at BETWEEN '2026-01-01' AND '2026-01-31';")
    assert "WHERE status = 'ACTIVE'" in result


def test_handles_query_with_no_trailing_semicolon():
    query = "SELECT * FROM public.orders"
    result = apply_incremental_predicate(query, "created_at", "2026-01-01", "2026-01-31")
    assert result == "SELECT * FROM public.orders WHERE created_at BETWEEN '2026-01-01' AND '2026-01-31';"


def test_rejects_missing_filter_column():
    with pytest.raises(ValueError):
        apply_incremental_predicate("SELECT * FROM t;", "", "2026-01-01", "2026-01-31")


def test_rejects_missing_dates():
    with pytest.raises(ValueError):
        apply_incremental_predicate("SELECT * FROM t;", "created_at", "", "2026-01-31")


def test_rejects_from_after_to():
    with pytest.raises(ValueError):
        apply_incremental_predicate("SELECT * FROM t;", "created_at", "2026-02-01", "2026-01-01")


if __name__ == "__main__":
    test_adds_where_when_none_exists()
    test_adds_and_when_where_already_exists()
    test_handles_query_with_no_trailing_semicolon()
    print("incremental_filter self-check OK")

"""
test_recon.py
=============
Property tests and golden-fixture regression tests for the DB2
reconciliation toolkit.

Run from the repo root:
    pip install pytest pandas
    pytest scripts/db_recon/tests/ -v

No database connection required — all tests operate on CSV files or
synthetic DataFrames.
"""

import os
import sys

import pandas as pd
import pytest

# Make the db_recon scripts importable regardless of working directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from recon_fast import compare  # noqa: E402

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def load(name):
    return pd.read_csv(os.path.join(FIXTURES, name), dtype=str, keep_default_na=False)


def cmp(src, tgt, keys, **kw):
    """Thin wrapper — returns the full tuple from compare()."""
    return compare(src, tgt, keys, **kw)


def make_df(rows, cols):
    return pd.DataFrame(rows, columns=cols)


# --------------------------------------------------------------------------- #
# Golden fixture: FILL
# Expected: 1 row differing, 2 cell diffs (MIC_CODE XOFF→OFF, MARKET_ID *K02→*K01)
# --------------------------------------------------------------------------- #

class TestFillGolden:
    KEYS = ["FILL_ID", "FILL_VID"]

    def _run(self):
        return cmp(load("fill_src.csv"), load("fill_tgt.csv"), self.KEYS)

    def test_rows_differing(self):
        stats, *_ = self._run()
        assert stats["rows_with_diffs"] == 1, f"expected 1, got {stats['rows_with_diffs']}"

    def test_cell_diffs(self):
        stats, *_ = self._run()
        assert stats["cell_diffs"] == 2, f"expected 2, got {stats['cell_diffs']}"

    def test_broken_columns(self):
        _, _, broken_keys, *_ = self._run()
        assert len(broken_keys) == 1
        cols = {d["column"] for d in broken_keys[0]["diffs"] if not d.get("derived")}
        assert "MIC_CODE" in cols
        assert "MARKET_ID" in cols

    def test_mic_code_values(self):
        _, _, broken_keys, *_ = self._run()
        mic = next(d for d in broken_keys[0]["diffs"] if d["column"] == "MIC_CODE")
        assert mic["source"] == "XOFF"
        assert mic["target"] == "OFF"

    def test_market_id_values(self):
        _, _, broken_keys, *_ = self._run()
        mid = next(d for d in broken_keys[0]["diffs"] if d["column"] == "MARKET_ID")
        assert mid["source"].endswith("K02")
        assert mid["target"].endswith("K01")

    def test_no_orphans(self):
        stats, *_ = self._run()
        assert stats["only_in_source"] == 0
        assert stats["only_in_target"] == 0

    def test_empty_col_pruned(self):
        stats, _, _, compare_cols, pruned, *_ = self._run()
        assert "EMPTY_COL" in pruned

    def test_status(self):
        stats, *_ = self._run()
        assert stats["status"] == "BREAK"


# --------------------------------------------------------------------------- #
# Golden fixture: ORDER_FILL
# Expected: 0 value diffs; 1 VERSION_MISMATCH (ORDER_VID 2→1); 3 field diffs
# --------------------------------------------------------------------------- #

class TestOrderFillGolden:
    KEYS = ["ORDER_ID", "ORDER_VID", "FILL_ID", "FILL_VID"]

    def _run(self):
        return cmp(load("order_fill_src.csv"), load("order_fill_tgt.csv"), self.KEYS)

    def test_no_value_diffs(self):
        stats, _, broken_keys, *_ = self._run()
        assert stats["rows_with_diffs"] == 0
        assert len(broken_keys) == 0

    def test_version_mismatch_count(self):
        stats, *_ = self._run()
        assert stats["version_mismatched_pairs"] == 1

    def test_version_mismatch_field_diffs(self):
        _, _, _, _, _, _, paired, *_ = self._run()
        assert len(paired) == 1
        # 3 inter-version field diffs (FIELD_A, FIELD_B, FIELD_C)
        non_derived = [d for d in paired[0]["diffs"] if not d.get("derived")]
        assert len(non_derived) == 3

    def test_version_delta_column(self):
        _, _, _, _, _, _, paired, *_ = self._run()
        vdiff_cols = {d["column"] for d in paired[0]["version_diffs"]}
        assert "ORDER_VID" in vdiff_cols

    def test_version_delta_values(self):
        _, _, _, _, _, _, paired, *_ = self._run()
        order_vid_diff = next(
            d for d in paired[0]["version_diffs"] if d["column"] == "ORDER_VID"
        )
        assert order_vid_diff["source"] == "2"
        assert order_vid_diff["target"] == "1"

    def test_orphans_zero(self):
        stats, *_ = self._run()
        assert stats["only_in_source"] == 0
        assert stats["only_in_target"] == 0


# --------------------------------------------------------------------------- #
# Golden fixture: ORDER
# Expected: 4 rows differing, 12 cell diffs (SIDE 2→4, RESIDUAL_QTY 0→3, ...)
# --------------------------------------------------------------------------- #

class TestOrderGolden:
    KEYS = ["ORDER_ID", "ORDER_VID"]

    def _run(self):
        return cmp(load("order_src.csv"), load("order_tgt.csv"), self.KEYS)

    def test_rows_differing(self):
        stats, *_ = self._run()
        assert stats["rows_with_diffs"] == 4

    def test_cell_diffs(self):
        stats, *_ = self._run()
        assert stats["cell_diffs"] == 12

    def test_side_diff(self):
        _, _, broken_keys, *_ = self._run()
        all_diffs = [d for b in broken_keys for d in b["diffs"] if not d.get("derived")]
        side_diffs = [d for d in all_diffs if d["column"] == "SIDE"]
        assert len(side_diffs) == 4
        assert all(d["source"] == "2" and d["target"] == "4" for d in side_diffs)

    def test_residual_qty_diff(self):
        _, _, broken_keys, *_ = self._run()
        all_diffs = [d for b in broken_keys for d in b["diffs"] if not d.get("derived")]
        rq_diffs = [d for d in all_diffs if d["column"] == "RESIDUAL_QTY"]
        assert len(rq_diffs) == 4
        assert all(d["source"] == "0" and d["target"] == "3" for d in rq_diffs)

    def test_no_orphans(self):
        stats, *_ = self._run()
        assert stats["only_in_source"] == 0
        assert stats["only_in_target"] == 0


# --------------------------------------------------------------------------- #
# Property: bucket filter never misses a break
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("n_buckets", [1, 2, 4, 16, 64, 256, 512])
def test_bucket_filter_never_misses_break(n_buckets):
    cols = ["ID", "VID", "VALUE", "EXTRA"]
    src = make_df([[str(i), "1", f"src_{i}", "same"] for i in range(200)], cols)
    tgt_rows = [[str(i), "1", "CHANGED" if i == 99 else f"src_{i}", "same"]
                for i in range(200)]
    tgt = make_df(tgt_rows, cols)
    stats, *_ = cmp(src, tgt, ["ID", "VID"], n_buckets=n_buckets)
    assert stats["rows_with_diffs"] == 1, (
        f"bucket_count={n_buckets}: expected 1 diff, got {stats['rows_with_diffs']}"
    )
    assert stats["status"] == "BREAK"


# --------------------------------------------------------------------------- #
# Property: inject N value diffs → all found, zero false positives
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("n_diffs,n_rows", [(1, 10), (5, 100), (10, 500)])
def test_inject_diffs_all_found(n_diffs, n_rows):
    cols = ["ID", "VID", "A", "B", "C"]
    src = make_df([[str(i), "1", f"a{i}", f"b{i}", f"c{i}"] for i in range(n_rows)], cols)
    tgt_rows = [
        [str(i), "1", f"CHANGED_{i}" if i < n_diffs else f"a{i}", f"b{i}", f"c{i}"]
        for i in range(n_rows)
    ]
    tgt = make_df(tgt_rows, cols)
    stats, *_ = cmp(src, tgt, ["ID", "VID"])
    assert stats["rows_with_diffs"] == n_diffs
    assert stats["only_in_source"] == 0
    assert stats["only_in_target"] == 0


# --------------------------------------------------------------------------- #
# Property: inject M source-only and K target-only rows
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("m_src,k_tgt", [(3, 0), (0, 4), (2, 3)])
def test_inject_orphans(m_src, k_tgt):
    cols = ["ID", "VID", "VAL"]
    # 10 shared rows
    shared = [[str(i), "1", f"v{i}"] for i in range(10)]
    src_only = [[f"src_{i}", "1", "x"] for i in range(m_src)]
    tgt_only = [[f"tgt_{i}", "1", "y"] for i in range(k_tgt)]
    src = make_df(shared + src_only, cols)
    tgt = make_df(shared + tgt_only, cols)
    stats, *_ = cmp(src, tgt, ["ID", "VID"])
    assert stats["only_in_source"] == m_src
    assert stats["only_in_target"] == k_tgt
    assert stats["rows_with_diffs"] == 0


# --------------------------------------------------------------------------- #
# Property: deleted row (no version twin) must not be force-paired
# --------------------------------------------------------------------------- #

def test_deleted_row_stays_only_in_source():
    cols = ["ORDER_ID", "ORDER_VID", "FILL_ID", "FILL_VID", "VAL"]
    # Row (100, 1, 200, 1) completely absent from target — genuine delete
    src = make_df([
        ["100", "1", "200", "1", "X"],
        ["101", "1", "201", "1", "Y"],
    ], cols)
    tgt = make_df([
        ["101", "1", "201", "1", "Y"],
    ], cols)
    _, _, _, _, _, _, paired, only_src, only_tgt, _ = cmp(
        src, tgt, ["ORDER_ID", "ORDER_VID", "FILL_ID", "FILL_VID"]
    )
    assert len(paired) == 0
    assert len(only_src) == 1
    assert len(only_tgt) == 0


# --------------------------------------------------------------------------- #
# Property: version twin must be paired, not left as orphans
# --------------------------------------------------------------------------- #

def test_version_twin_is_paired():
    cols = ["ORDER_ID", "ORDER_VID", "FILL_ID", "FILL_VID", "VAL"]
    src = make_df([["100", "2", "200", "1", "X"]], cols)
    tgt = make_df([["100", "1", "200", "1", "Y"]], cols)
    stats, _, _, _, _, _, paired, only_src, only_tgt, _ = cmp(
        src, tgt, ["ORDER_ID", "ORDER_VID", "FILL_ID", "FILL_VID"]
    )
    assert len(paired) == 1
    assert stats["version_mismatched_pairs"] == 1
    assert stats["only_in_source"] == 0
    assert stats["only_in_target"] == 0


# --------------------------------------------------------------------------- #
# Property: column pruning
# --------------------------------------------------------------------------- #

def test_empty_columns_pruned():
    cols = ["ID", "VID", "REAL_COL", "EMPTY_SRC", "EMPTY_BOTH"]
    src = make_df([["1", "1", "A", "", ""], ["2", "1", "B", "", ""]], cols)
    tgt = make_df([["1", "1", "A", "X", ""], ["2", "1", "B", "Y", ""]], cols)
    stats, _, _, compare_cols, pruned, *_ = cmp(src, tgt, ["ID", "VID"])
    assert "EMPTY_BOTH" in pruned
    # EMPTY_SRC has data on target so it should NOT be pruned
    assert "EMPTY_SRC" in compare_cols


# --------------------------------------------------------------------------- #
# Property: identical tables → PASS, zero everything
# --------------------------------------------------------------------------- #

def test_identical_tables():
    cols = ["ID", "VID", "A", "B", "C"]
    rows = [[str(i), "1", f"val{i}", f"other{i}", "const"] for i in range(100)]
    df = pd.DataFrame(rows, columns=cols)
    stats, *_ = cmp(df.copy(), df.copy(), ["ID", "VID"])
    assert stats["status"] == "PASS"
    assert stats["rows_with_diffs"] == 0
    assert stats["cell_diffs"] == 0
    assert stats["only_in_source"] == 0
    assert stats["only_in_target"] == 0
    assert stats["version_mismatched_pairs"] == 0

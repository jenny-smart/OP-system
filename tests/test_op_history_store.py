import unittest
from unittest.mock import patch

import pandas as pd

import op_history_store as store


def record(record_id="row-1", source="dashboard"):
    values = [record_id, source, "2026/11", "2026/11/26 19:04:29"] + [0] * 11
    return dict(zip(store.FIELDS, values))


class HistoryStoreTest(unittest.TestCase):
    def test_append_is_idempotent_and_read_is_deduplicated(self):
        existing = [store.HEADERS]
        written = []

        def append_rows(rows):
            written.extend(rows)
            existing.extend(rows)

        frame = pd.DataFrame([record()])
        with patch.object(store, "_read_values", side_effect=lambda: existing), patch.object(
            store, "_append_values", side_effect=append_rows
        ):
            self.assertEqual(store.append({store.FILES[0]: frame})["added"], 1)
            self.assertEqual(store.append({store.FILES[0]: frame})["added"], 0)
            self.assertEqual(len(store.read(store.FILES[0])), 1)
        self.assertEqual(len(written), 1)

    def test_same_period_and_id_with_different_payload_stops_batch(self):
        first = [0, "append", *record().values()]
        existing = [store.HEADERS, first]
        conflict = pd.DataFrame([record(source="schedule")])
        with patch.object(store, "_read_values", return_value=existing), patch.object(
            store, "_append_values"
        ) as append_rows:
            with self.assertRaisesRegex(RuntimeError, "ID 衝突"):
                store.append({store.FILES[0]: conflict})
            append_rows.assert_not_called()

    def test_period_is_part_of_identity(self):
        row = record()
        existing = [
            store.HEADERS,
            [0, "append", *row.values()],
            [1, "append", *dict(row, 統計月份="2026/12").values()],
        ]
        with patch.object(store, "_read_values", return_value=existing):
            self.assertEqual(len(store.read(store.FILES[0])), 1)
            self.assertEqual(len(store.read(store.FILES[1])), 1)

    def test_malformed_header_fails_closed(self):
        with patch.object(store, "_read_values", return_value=[["wrong"]]):
            with self.assertRaisesRegex(RuntimeError, "表頭不符"):
                store.read(store.FILES[0])


if __name__ == "__main__":
    unittest.main()

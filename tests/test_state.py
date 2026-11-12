from pathlib import Path
import tempfile
import unittest

from ase.state import read_record, transaction, write_record
from ase.store import Store


class TransactionTests(unittest.TestCase):
    def test_nested_failure_rolls_back_without_discarding_outer_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "work").mkdir()
            store = Store(root / "work", root / "state")
            try:
                with transaction(store.db):
                    write_record(store.db, "test", "outer", {"kept": True}, expected_revision=0)
                    try:
                        with transaction(store.db):
                            write_record(store.db, "test", "inner", {"partial": True}, expected_revision=0)
                            raise ValueError("interrupted atomic service")
                    except ValueError:
                        pass
                self.assertEqual(read_record(store.db, "test", "outer")["payload"], {"kept": True})
                self.assertIsNone(read_record(store.db, "test", "inner")["payload"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()

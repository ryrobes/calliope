import importlib.util
from pathlib import Path
import stat

import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    'appliance_env', Path(__file__).parents[1] / 'scripts/appliance/provision-env.py')
provision = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provision)


class ProvisionTests(unittest.TestCase):
    def test_fresh_credentials_are_private_and_reprovision_preserves_them(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            provision.create_environment(path, 'test-key-$literal', 'admin@example.com', 'test-1', 'abc123')
            original = path.read_bytes()
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertIn(b"RVBBIT_CLOVER_KEY='test-key-$literal'", original)
            self.assertIn(b"WAREHOUSE_PUBLIC_URL='http://localhost:8080'", original)
            with self.assertRaises(FileExistsError):
                provision.create_environment(path, 'changed-key', 'other@example.com', 'test-2', 'def456')
            self.assertEqual(path.read_bytes(), original)

    def test_provision_refuses_symlink_and_multiline_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / 'target'
            target.write_text('existing-secret')
            link = root / '.env'
            link.symlink_to(target)
            with self.assertRaises(FileExistsError):
                provision.create_environment(link, 'test-key', 'admin@example.com', 'test-1', 'abc123')
            self.assertEqual(target.read_text(), 'existing-secret')
            with self.assertRaises(ValueError):
                provision.create_environment(root / 'new', 'key\nINJECTED=value',
                                             'admin@example.com', 'test-1', 'abc123')
            self.assertFalse((root / 'new').exists())


if __name__ == '__main__':
    unittest.main()

"""Cache entry replacement must preserve files reached by leaf aliases."""
import contextlib
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cacherecycle as ccr


OLD = b'old cached bytes'
NEW = b'updated cached bytes\x00\xff'
OUTSIDE = b'unrelated outside bytes'


class CacheWriteIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / 'source.bin'
        self.store = self.root / 'store.jsonl'
        self.cache = self.root / 'cache'
        self.target = self.cache / 'asset.bin'
        self.index = self.cache / '.cacherecycle.json'
        self.source.write_bytes(OLD)
        ccr.put(self.source, 'fixture.asset', store=self.store)
        self.sync()

    def sync(self):
        return ccr.sync(['asset.bin=fixture.asset'], store=self.store, cache=self.cache)

    def update_source(self):
        self.source.write_bytes(NEW)
        ccr.put(self.source, 'fixture.asset', store=self.store)

    def symlink(self, referent, *, link=None):
        link = self.target if link is None else link
        link.unlink()
        try:
            link.symlink_to(referent)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f'symlink creation unavailable: {error}')

    def assert_no_temporary_files(self):
        self.assertEqual({path.name for path in self.cache.iterdir()},
                         {'asset.bin', '.cacherecycle.json'})

    def test_existing_symlink_is_replaced_without_writing_outside(self):
        outside = self.root / 'outside.bin'
        outside.write_bytes(OUTSIDE)
        if hasattr(os, 'fchmod'):
            outside.chmod(0o400)
        self.symlink(outside)
        self.update_source()
        report = self.sync()
        self.assertEqual(outside.read_bytes(), OUTSIDE)
        self.assertFalse(self.target.is_symlink())
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assertEqual(report['written'], [self.target.as_posix()])
        if hasattr(os, 'fchmod'):
            self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o400)
            self.assertEqual(stat.S_IMODE(self.target.stat().st_mode),
                             stat.S_IMODE(self.source.stat().st_mode))
        self.assert_no_temporary_files()

    def test_dangling_symlink_is_replaced_without_creating_referent(self):
        outside = self.root / 'missing.bin'
        self.symlink(outside)
        self.update_source()
        self.sync()
        self.assertFalse(outside.exists())
        self.assertFalse(self.target.is_symlink())
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assert_no_temporary_files()

    def test_hardlinked_cache_update_preserves_external_inode_contents(self):
        outside = self.root / 'outside.bin'
        outside.write_bytes(OUTSIDE)
        if hasattr(os, 'fchmod'):
            outside.chmod(0o400)
        self.target.unlink()
        try:
            os.link(outside, self.target)
        except (NotImplementedError, OSError) as error:
            self.skipTest(f'hardlink creation unavailable: {error}')
        self.update_source()
        self.sync()
        self.assertEqual(outside.read_bytes(), OUTSIDE)
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assertFalse(os.path.samefile(outside, self.target))
        if hasattr(os, 'fchmod'):
            self.assertEqual(stat.S_IMODE(outside.stat().st_mode), 0o400)
            self.assertEqual(stat.S_IMODE(self.target.stat().st_mode),
                             stat.S_IMODE(self.source.stat().st_mode))
        self.assert_no_temporary_files()

    def test_manifest_symlinks_are_replaced_without_changing_referents(self):
        for existing in (False, True):
            with self.subTest(existing=existing):
                self.sync()
                outside = self.root / ('outside-' + str(existing) + '.json')
                before = self.index.read_bytes()
                if existing:
                    outside.write_bytes(before)
                self.symlink(outside, link=self.index)
                ccr.sync([], store=self.store, cache=self.cache)
                if existing:
                    self.assertEqual(outside.read_bytes(), before)
                else:
                    self.assertFalse(outside.exists())
                self.assertFalse(self.index.is_symlink())
                self.assertEqual(self.index.read_bytes(), b'{}\n')

    @unittest.skipUnless(hasattr(os, 'fchmod'), 'requires POSIX permission bits')
    def test_regular_file_update_preserves_existing_permission_bits(self):
        self.target.chmod(0o640)
        self.index.chmod(0o644)
        self.update_source()
        self.sync()
        self.assertEqual(stat.S_IMODE(self.target.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(self.index.stat().st_mode), 0o644)
        self.assertEqual(self.target.read_bytes(), NEW)

    @unittest.skipUnless(os.name == 'posix', 'requires POSIX umask behavior')
    def test_new_files_honor_umask_without_mutating_it_in_sync(self):
        # Each child owns its umask; the application must never change it.
        code = '''
from pathlib import Path
import os, stat, sys, tempfile
from unittest.mock import patch
import cacherecycle as ccr
os.umask(int(sys.argv[1]))
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    source = root / 'source.bin'
    source.write_bytes(b'payload')
    expected = stat.S_IMODE(source.stat().st_mode)
    store = root / 'store.jsonl'
    cache = root / 'cache'
    ccr.put(source, 'fixture.asset', store=store)
    with patch.object(os, 'umask', side_effect=AssertionError('sync changed umask')):
        ccr.sync(['asset.bin=fixture.asset'], store=store, cache=cache)
    for name in ('asset.bin', '.cacherecycle.json'):
        assert stat.S_IMODE((cache / name).stat().st_mode) == expected
'''
        for mask in (0o022, 0o027, 0o077):
            with self.subTest(mask=mask):
                result = subprocess.run([sys.executable, '-S', '-c', code, str(mask)],
                                        cwd=Path(ccr.__file__).parent,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_temporary_name_collision_does_not_touch_existing_entry(self):
        collision = self.cache / '.cacherecycle-collision.tmp'
        collision.write_bytes(OUTSIDE)
        self.update_source()
        with patch.object(ccr.secrets, 'token_hex',
                          side_effect=['collision', 'fresh-payload', 'fresh-index']):
            self.sync()
        self.assertEqual(collision.read_bytes(), OUTSIDE)
        self.assertEqual(self.target.read_bytes(), NEW)
        self.assertEqual({path.name for path in self.cache.iterdir()},
                         {'asset.bin', '.cacherecycle.json', collision.name})

    def test_failed_replace_preserves_previous_file_and_manifest(self):
        before = self.index.read_bytes()
        self.update_source()

        def fail_replace(temporary, target):
            self.assertEqual(Path(temporary).parent, self.cache)
            self.assertEqual(Path(temporary).read_bytes(), NEW)
            self.assertEqual(target, self.target)
            self.assertEqual(self.target.read_bytes(), OLD)
            raise OSError('injected replace failure')

        with patch.object(ccr.os, 'replace', fail_replace):
            with self.assertRaisesRegex(OSError, 'injected replace failure'):
                self.sync()
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertEqual(self.index.read_bytes(), before)
        self.assert_no_temporary_files()

    def test_partial_temporary_write_failure_preserves_previous_entry(self):
        before = self.index.read_bytes()
        self.update_source()
        real_fdopen = os.fdopen

        @contextlib.contextmanager
        def fail_write(fd, *args, **kwargs):
            with real_fdopen(fd, *args, **kwargs) as stream:
                class PartialWriter:
                    def write(self, data):
                        stream.write(data[:1])
                        raise OSError('injected write failure')
                yield PartialWriter()

        with patch.object(ccr.os, 'fdopen', fail_write):
            with self.assertRaisesRegex(OSError, 'injected write failure'):
                self.sync()
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertEqual(self.index.read_bytes(), before)
        self.assert_no_temporary_files()

    def test_cleanup_failure_keeps_original_error_and_reports_leftover(self):
        before = self.index.read_bytes()
        self.update_source()
        real_unlink = Path.unlink

        def fail_cleanup(path, *args, **kwargs):
            if path.parent == self.cache and path.name.startswith('.cacherecycle-'):
                raise PermissionError('injected cleanup failure')
            return real_unlink(path, *args, **kwargs)

        with patch.object(ccr.os, 'replace', side_effect=OSError('injected replace failure')):
            with patch.object(Path, 'unlink', fail_cleanup):
                with self.assertRaisesRegex(OSError, 'injected replace failure') as caught:
                    self.sync()
        self.assertEqual(self.target.read_bytes(), OLD)
        self.assertEqual(self.index.read_bytes(), before)
        leftovers = list(self.cache.glob('.cacherecycle-*.tmp'))
        self.assertEqual(len(leftovers), 1)
        notes = '\n'.join(caught.exception.__notes__)
        self.assertIn(str(leftovers[0]), notes)
        self.assertIn('injected cleanup failure', notes)


if __name__ == '__main__':
    unittest.main()

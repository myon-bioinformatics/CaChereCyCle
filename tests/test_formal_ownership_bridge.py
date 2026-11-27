"""Exercise the manifest deletion boundary using real temporary files."""
import itertools
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cacherecycle as ccr


PAYLOAD = bytes(range(256)) + b'\x00\r\ncache source'
UNMANAGED = b'unrelated file contents'


class FormalOwnershipBridgeTests(unittest.TestCase):
    def prepare(self, root):
        source = root / 'source.bin'
        source.write_bytes(PAYLOAD)
        store = root / 'store.jsonl'
        cache = root / 'cache'
        ccr.put(source, 'fixture.asset', store=store)
        return source, store, cache

    @staticmethod
    def items(names):
        return [name + '=fixture.asset' for name in sorted(names)]

    @staticmethod
    def names(report, kind):
        return {Path(path).name for path in report[kind]}

    def test_all_two_name_ownership_transitions_and_repeated_requests(self):
        names = ('alpha.bin', 'beta.bin')
        # Four initial states per name: absent, present but unindexed, indexed
        # and present, or indexed but externally removed. Cross every state
        # assignment with every requested subset: 4^2 * 2^2 = 64 transitions.
        for states in itertools.product(range(4), repeat=2):
            for request_bits in range(4):
                with self.subTest(states=states, request_bits=request_bits):
                    with tempfile.TemporaryDirectory() as temporary:
                        root = Path(temporary)
                        source, store, cache = self.prepare(root)
                        store_before = store.read_bytes()
                        owned = {name for name, state in zip(names, states) if state >= 2}
                        present = {name for name, state in zip(names, states) if state in (1, 2)}
                        wanted = {name for index, name in enumerate(names)
                                  if request_bits & (1 << index)}
                        ccr.sync(self.items(owned), store=store, cache=cache)
                        for name, state in zip(names, states):
                            if state == 1:
                                (cache / name).write_bytes(UNMANAGED)
                            elif state == 3:
                                (cache / name).unlink()
                        sentinel = cache / 'unlisted.bin'
                        sentinel.write_bytes(UNMANAGED)

                        report = ccr.sync(self.items(wanted), store=store, cache=cache)
                        self.assertEqual(self.names(report, 'removed'),
                                         (owned - wanted) & present)
                        self.assertEqual(self.names(report, 'written') |
                                         self.names(report, 'kept'), wanted)
                        for name, state in zip(names, states):
                            path = cache / name
                            if name in wanted:
                                self.assertEqual(path.read_bytes(), PAYLOAD)
                            elif state == 1:
                                self.assertEqual(path.read_bytes(), UNMANAGED)
                            else:
                                self.assertFalse(path.exists())
                        self.assertEqual(sentinel.read_bytes(), UNMANAGED)
                        index = json.loads((cache / '.cacherecycle.json').read_text())
                        self.assertEqual(set(index), wanted)

                        again = ccr.sync(self.items(wanted), store=store, cache=cache)
                        self.assertEqual(again['removed'], [])
                        self.assertEqual(again['written'], [])
                        self.assertEqual(self.names(again, 'kept'), wanted)
                        self.assertEqual(source.read_bytes(), PAYLOAD)
                        self.assertEqual(store.read_bytes(), store_before)

    def test_recreated_name_is_unmanaged_after_manifest_reset(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, store, cache = self.prepare(Path(temporary))
            store_before = store.read_bytes()
            ccr.sync(self.items({'alpha.bin', 'beta.bin'}), store=store, cache=cache)
            report = ccr.sync(self.items({'beta.bin'}), store=store, cache=cache)
            self.assertEqual(self.names(report, 'removed'), {'alpha.bin'})
            (cache / 'alpha.bin').write_bytes(UNMANAGED)

            report = ccr.sync([], store=store, cache=cache)
            self.assertEqual(self.names(report, 'removed'), {'beta.bin'})
            self.assertEqual((cache / 'alpha.bin').read_bytes(), UNMANAGED)
            self.assertEqual(ccr.sync([], store=store, cache=cache)['removed'], [])
            self.assertEqual(source.read_bytes(), PAYLOAD)
            self.assertEqual(store.read_bytes(), store_before)

    def test_name_and_file_guards_limit_obsolete_manifest_deletions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, store, cache = self.prepare(root)
            store_before = store.read_bytes()
            ccr.sync(self.items({'directory.bin', 'obsolete.bin'}),
                     store=store, cache=cache)
            # Ownership is attached to the indexed name, not the old digest:
            # changed contents at an obsolete managed name are still removed.
            (cache / 'obsolete.bin').write_bytes(b'changed cached contents')
            directory = cache / 'directory.bin'
            directory.unlink()
            directory.mkdir()
            (directory / 'child.bin').write_bytes(UNMANAGED)
            (cache / '.hidden').write_bytes(UNMANAGED)
            (cache / 'nested').mkdir()
            (cache / 'nested' / 'keep.bin').write_bytes(UNMANAGED)
            outside = root / 'outside.bin'
            outside.write_bytes(UNMANAGED)
            index_path = cache / '.cacherecycle.json'
            index = json.loads(index_path.read_text())
            index.update({'../outside.bin': {}, '.hidden': {}, 'nested/keep.bin': {}})
            index_path.write_text(json.dumps(index), encoding='utf-8')

            report = ccr.sync([], store=store, cache=cache)
            self.assertEqual(self.names(report, 'removed'), {'obsolete.bin'})
            self.assertEqual((directory / 'child.bin').read_bytes(), UNMANAGED)
            self.assertEqual((cache / '.hidden').read_bytes(), UNMANAGED)
            self.assertEqual((cache / 'nested' / 'keep.bin').read_bytes(), UNMANAGED)
            self.assertEqual(outside.read_bytes(), UNMANAGED)
            self.assertEqual(json.loads(index_path.read_text()), {})
            self.assertEqual(source.read_bytes(), PAYLOAD)
            self.assertEqual(store.read_bytes(), store_before)


if __name__ == '__main__':
    unittest.main()

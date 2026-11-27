# Formal verification of cache deletion ownership

`formal/Ownership.lean` models the deletion phase of `cacherecycle.sync()`:
after the requested outputs are prepared, it removes obsolete entries from
the previous cache manifest. Lean is fixed by `formal/lean-toolchain`, needs
no Mathlib, and is separate from the standard-library runtime. The execution
wrapper and named-theorem axiom audit come from the
[parent's `lean_verify.py`](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/main/lean_verify.py).

## The two guarantees

The model uses a map from names to optional contents and folds the guarded
deletion over the previous manifest's names. `owned` is that name list;
`wanted` is the new manifest's name list. `eligible(name)` represents the
plain-name check and `Path.is_file()` at the deletion phase.

| Audited theorem, in namespace `CaChereCyCle` | Guarantee | Python connection |
| --- | --- | --- |
| `prune_exact` | A name becomes absent exactly when it is in the previous manifest, absent from the new manifest, and eligible; otherwise its prior value is retained | The loop over `sorted(set(before) - set(index))`, its name/file guard, and `old.unlink()` |
| `ownership_reset` | When the previous and requested name lists are the same, the deletion pass leaves the file map unchanged | A successful call writes the new `index`; a repeated request has no obsolete names |

In `prune_exact`, "becomes absent" means the model sets that entry to `none`;
it is not a statement that an initially missing file was physically deleted.
The actual file guard decides whether an unlink is attempted. The theorem
covers arbitrary finite name lists and contents. It proves a frame condition:
unlisted names, requested names, and names failing the guard keep their value
through the deletion pass.

The model does not include output preparation or the manifest-file rewrite.
`ownership_reset` therefore does not claim that a whole `sync()` call performs
no filesystem writes or preserves modification timestamps.

## Connection to real files

`tests/test_formal_ownership_bridge.py` calls the real `put()` and `sync()` in
temporary directories. No filesystem operations or cache implementation are
mocked. It checks all 64 transitions formed by two names with four initial
states each (absent, present but unindexed, indexed and present, indexed but
missing), crossed with every requested subset. For each transition it checks
the removed-name report, resulting bytes, new manifest keys, and an unchanged
repeat request.

Additional sequences verify that a name removed from the manifest can later
hold an unrelated file without being deleted by future cleanup. They also
check directories, nested names, leading-dot names, and traversal names in an
obsolete manifest. Changed contents at an obsolete indexed name are removed:
the deletion boundary is the indexed name, not its recorded digest.

The source file and JSONL store are outside the cache in these tests. Exact
byte comparisons verify that they remain unchanged through the exercised
sync sequences. These tests supplement the existing store round-trip, basic
sync, PNG, and CLI tests in `tests/test_cacherecycle.py`.

## Assumptions and limits

The manifest is the source of name ownership. Neither the model nor `sync()`
establishes that a valid manifest entry was historically written by this
process. A forged entry for a valid filename can claim an unrelated cache
file. The invalid-name tests exercise path filtering, not authentication of
the manifest.

The model assumes a successful call and stable, distinct filesystem names:
no concurrent changes, case aliases, symlink or hardlink aliases, or overlap
between the cache outputs and the source/store paths. This deletion proof does
not establish containment for the earlier output-writing phase; alias handling
in that phase needs its own Python regressions. I/O errors, partial failure,
crash recovery, and atomic multi-file updates are outside the model. Regex and
filesystem behavior are checked by finite connection tests, not formalized in
Lean.

An explicitly requested output name may overwrite an existing unindexed file.
Preservation of unowned files applies to names that are also unrequested;
it is not a refusal to write requested outputs. The deletion theorem itself
starts after those writes.

No hash algorithm or image codec occurs in the Lean model. It makes no claim
of SHA-256 collision freedom, digest equality implying byte equality, PNG
correctness, or general Base64 correctness. Passing connection tests is
evidence about these concrete executions, not a formal verification of the
entire Python module.

## Run

The connection tests use only the Python standard library:

```sh
python -S -m unittest discover -s tests -p test_formal_ownership_bridge.py -v
```

With the parent repository checked out alongside this repository as
`myon-bioinformatics`, run the shared verifier:

```sh
python -S ../myon-bioinformatics/lean_verify.py \
  --cwd formal --required --json-output build/lean-verification.json \
  --theorem CaChereCyCle.prune_exact \
  --theorem CaChereCyCle.ownership_reset \
  Ownership.lean
```

The dedicated CI lane uses the parent workflow at a fixed commit. A missing
Lean toolchain is **unverified** and fails that required lane. Proof failure,
warnings, `sorryAx`, and unexpected axiom dependencies also fail. Normal
Python execution and tests do not require Lean.

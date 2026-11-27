import Std

/-!
The deletion phase of `cacherecycle.sync`, after requested outputs are prepared.

`owned` represents the previous manifest's names, not independently established
historical ownership. `wanted` contains the new manifest's names. `eligible`
represents the plain-name and `Path.is_file` guards in a stable filesystem.
The model addresses name-based deletion; it makes no hash or image-codec claim.
-/

namespace CaChereCyCle

variable {Name Content : Type} [DecidableEq Name]

def erase (files : Name → Option Content) (name : Name) : Name → Option Content :=
  fun query => if query = name then none else files query

/-- Fold the guarded deletion loop over the previous manifest's names. -/
def prune (wanted : List Name) (eligible : Name → Bool) :
    List Name → (Name → Option Content) → (Name → Option Content)
  | [], files => files
  | name :: rest, files =>
      if name ∉ wanted ∧ eligible name = true then
        prune wanted eligible rest (erase files name)
      else
        prune wanted eligible rest files

/-- Exactly eligible obsolete manifest names are erased; all other values stay. -/
theorem prune_exact (wanted owned : List Name) (eligible : Name → Bool)
    (files : Name → Option Content) (query : Name) :
    prune wanted eligible owned files query =
      if query ∈ owned ∧ query ∉ wanted ∧ eligible query = true then
        none
      else
        files query := by
  induction owned generalizing files with
  | nil => simp [prune]
  | cons name rest ih =>
      by_cases same : query = name
      · subst query
        by_cases remove : name ∉ wanted ∧ eligible name = true
        · simp [prune, remove, ih, erase]
        · simp [prune, remove, ih]
      · by_cases remove : name ∉ wanted ∧ eligible name = true
        · simp [prune, remove, ih, erase, same]
        · simp [prune, remove, ih, same]

/-- A successful manifest reset makes a repeated request's deletion pass inert. -/
theorem ownership_reset (wanted : List Name) (eligible : Name → Bool)
    (files : Name → Option Content) :
    prune wanted eligible wanted files = files := by
  funext query
  rw [prune_exact]
  by_cases present : query ∈ wanted <;> simp [present]

end CaChereCyCle

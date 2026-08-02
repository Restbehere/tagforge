# see-through patches

Local fixes to the vendored [see-through](https://github.com/shitagaki-lab/see-through)
pipeline that the Decompose tab drives. **A `git pull` in the see-through
checkout will overwrite these** — re-apply from here if Decompose starts
failing on images it used to handle.

Only the patch is kept here, not a copy of the upstream file.

## `head-failsafe.patch` — don't abort when a layer comes out empty

`common/utils/inference_utils.py :: apply_layerdiff` (the `v3` tag branch)
assumed every image contains a visible head. When stage 1 returned a fully
transparent `head` layer — a draped or occluded subject, a back view, a
crop above the shoulders — the failure cascaded silently:

- `cv2.findNonZero` on the empty alpha returns `None`
- `cv2.boundingRect(None)` returns `(0, 0, 0, 0)` rather than raising
- that zero-size box becomes a `0x0` crop
- `cv2.resize` finally dies with a bare `(-215:Assertion failed) !ssize.empty()`

…losing the whole run, including the body layers that had already
succeeded.

The patch adds three guards to that stage:

1. **Empty head layer** — skip the head-detail pass entirely.
2. **Degenerate crop** — a head bbox that maps outside the unpadded image
   (negative offsets produce an empty numpy view, not an error) or is
   under 8px on a side.
3. **Catch-all** — any other exception in the head stage degrades to
   "no head detail" instead of failing the job.

In every case it writes fully transparent stand-ins for the 11 head-detail
layers and returns, so the depth pass and PSD assembly still run. That is
safe because both already skip empty layers: `io_utils.load_part` ignores
anything with fewer than 5 opaque pixels, and `tag_lr_split` no-ops on
missing tags.

The skip is announced on stdout (`[see-through] no head found in this
image …`); Tag Forge's `backend/decompose.py` looks for that marker to
explain the outcome in the UI.

## Applying

The `common` package is imported from wherever the editable install
points, which may **not** be the checkout you run scripts from — check
before patching:

```bash
python -c "import common.utils.inference_utils as m; print(m.__file__)"
```

Apply to that checkout (and mirror to the other one if they differ):

```bash
cd <see-through checkout>
git apply /path/to/tagforge/see-through-patches/head-failsafe.patch
```

Verify it parses and that a normal head still crops:

```bash
python -c "import ast; ast.parse(open('common/utils/inference_utils.py', encoding='utf-8').read()); print('ok')"
```

## Reverting

```bash
cd <see-through checkout>
git apply -R /path/to/tagforge/see-through-patches/head-failsafe.patch
```

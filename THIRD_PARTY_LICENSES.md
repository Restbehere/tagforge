# Third-party licenses

Tag Forge vendors or downloads the following third-party components.

## Logomark — "forge" by Monjin Friends

The Tag Forge logomark (`frontend/src/components/TagForgeMark.tsx` and
`frontend/public/favicon.svg`) is adapted from
[**forge** by Monjin Friends](https://thenounproject.com/icon/forge-1044767/)
from [the Noun Project](https://thenounproject.com), used under the
[Creative Commons Attribution 3.0 licence](https://creativecommons.org/licenses/by/3.0/).

The original icon was redrawn as vector paths on a 24×24 grid so it can
inherit the interface's accent colour; it remains an adaptation of the
original work. Attribution is a condition of the licence — retain this
notice, and the credit shown on the app's Settings page, in any
redistribution.

## Anime2.5DRig (vendored: `frontend/public/anime25drig/`)

The rig viewer is a translated/adapted copy of
[852wa/Anime2.5DRig](https://github.com/852wa/Anime2.5DRig), MIT licensed —
see `frontend/public/anime25drig/LICENSE` (Copyright (c) 2026 hakoniwa).
The sample PSD artwork from the upstream project is **not** covered by that
license and is not distributed with this repository; supply your own
layer-separated PSDs.

## gif.js (vendored: `frontend/public/anime25drig/lib/gif.js`, `gif.worker.js`)

[jnordberg/gif.js](https://github.com/jnordberg/gif.js) 0.2.0

```
The MIT License (MIT)

Copyright (c) 2013 Johan Nordberg

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## ag-psd (vendored: `frontend/public/anime25drig/lib/ag-psd.min.js`)

[Agamnentzar/ag-psd](https://github.com/Agamnentzar/ag-psd)

```
The MIT License (MIT)

Copyright (c) 2016 Agamnentzar

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.
```

## danbooru-tag-tree (downloaded at setup, NOT redistributed)

The tag taxonomy (`backend/data/tag_tree.json`) comes from
[KohakuBlueleaf/danbooru-tag-tree](https://github.com/KohakuBlueleaf/danbooru-tag-tree),
which declares no license. It is therefore **not** shipped in this repository;
`python scripts/seed_tag_tree.py` (or `python -m backend.cli seed-tag-tree`)
downloads it to your machine at setup time.

# Anime2.5DRig

Original project: https://github.com/852wa/Anime2.5DRig (MIT, by 852wa / @8co28) — this copy is vendored into Tag Forge with the UI translated to English. Live upstream demo: https://852wa.github.io/Anime2.5DRig/

Drop a layer-separated PSD into the browser and it is auto-rigged into a moving 2.5D avatar on the spot. The setup that used to be manual work (mesh subdivision, deformation, physics tuning) is automated. No install — everything runs client-side.

## How to use

1. Drop a layer-separated PSD onto the page (or click "Load sample.psd"), or send a finished decomposition here from Tag Forge's Decompose tab.
2. The auto-rig runs and the character immediately moves with idle motion, blinking, lip-sync, and hair physics.

> Camera tracking (MediaPipe FaceMesh) and mic lip-sync only work over https or localhost (browser permission rules).

## What happens automatically

- [see-through](https://github.com/shitagaki-lab/see-through) output PSDs are accepted as-is (`mouth` → `mouth_open` auto-rename)
- **Missing closed-eye / closed-mouth art is auto-generated** (scaled and placed onto the detected anchors, tinted to match the eyelash/mouth colors; fine-tune with the dedicated sliders)
  - Custom art in `eye_close.psd` (both eyes in one file, with a gap between them) / `mouth_close.psd` at the app root takes priority; otherwise built-in generic art is used
- Low-alpha noise removal (connected-component filter)
- **Automatic left/right separation** of eyes, eyebrows, eyelashes, and closed eyes (by connected-component centroid)
- **Automatic anchor detection**: eyelid position, iris centers, mouth, neck pivot, and more
- **Automatic hair-strand detection** (peak detection on the hair-tip contour, up to 6 strands per layer)
- **Pseudo-3D head turn** via per-layer depth assignment (parallax + shear)
- Dual-spring physics per strand (**stiff at the root, fluffy at the tips**), chest bounce, breathing
- **Cross-fade** between open/closed eye and mouth states, stencil-clipped irises (constrained inside the eye whites)

## Layer naming convention

Layer names (Japanese "copy of" suffixes and full-width characters are normalized automatically):

| Layer name | Content | Required | Notes |
|---|---|---|---|
| `face` | Face base | ◎ | Anchor reference — the one truly required layer |
| `eyewhite` | Eye whites (both) | ○ | Left/right split automatically |
| `irides` | Irises (both) | ○ | Gaze movement + iris scale |
| `eyelash` | Eyelashes (open eyes) | ○ | |
| `eye_close` | Closed eyes | ○ | Cross-faded when blinking |
| `eyebrow` | Eyebrows (both) | ○ | Angle + height controls |
| `mouth_open` | Open mouth | ○ | Jaw drops with openness |
| `mouth_close` | Closed mouth | ○ | |
| `nose` | Nose | | |
| `ears` | Ears | | |
| `earwear` | Ear accessories | | |
| `neck` | Neck | | Top edge follows the head |
| `topwear` | Upper-body clothing | | Breathing + chest bounce |
| `bottomwear` | Lower-body clothing | | |
| `handwear` | Arms / hands | | Arm-height control |
| `headwear` | Hats, headbands, etc. | | |
| `front hair` | Front hair | | Strand physics + 3-block control |
| `back hair` | Back hair | | Strand physics |

- **Hair split across multiple layers**: name them `front hair_1`, `front hair_2`, `back hair_1` … — each layer becomes an independently simulated strand group (strand count is derived from layer width).
- Layers with names outside the convention still load (head/body group is inferred from position; they follow motion only).
- Layer groups (folders) are not supported — keep the structure flat.
- **About neck vs body**: with raw see-through output, resolving the neck/torso overlap can be hard and the seam may break when moving. If it misbehaves, merging the neck into the torso layer (no `neck` layer, `topwear` includes the neck) usually works better.
- A square canvas is recommended (tested 768×768 – 2048×2048).

## Features

Expression presets (smile / surprised / deadpan / winks), independent left/right eye openness, per-eye asymmetry correction with independent open/closed states (Open size/angle L/R transforms the open-eye group; Closed size/angle L/R transforms only the closed-eye art — true each state up separately, Tag Forge addition), eyebrow angle (independent + symmetric), gaze, iris scale, open/close threshold sliders for eyes and mouth, 3-block front-hair control with **front-hair-specific sway/softness**, arm height/offset, chest bounce (strength + position), body tilt, idle/random motion, random lip-sync, mic lip-sync, mouse tracking, **webcam tracking** (head XYZ, per-eye blink, mouth, gaze), background switch (transparent / green screen / dark), **clip recording** (1–10 s MP4/WebM at full resolution, or GIF at up to 640px — a transparent background records as a keyed transparent GIF; Tag Forge addition, uses bundled gif.js, MIT).

## Files

```
index.html         the app (UI + WebGL runtime)
lib/rigger.js      auto-rig builder (pure TypedArray implementation)
lib/ag-psd.min.js  PSD parser (ag-psd, MIT)
lib/genericparts.js  built-in generic closed-eye/mouth art (fallback)
eye_close.psd      closed-eye source art (optional, replaceable)
mouth_close.psd    closed-mouth source art (optional, replaceable)
sample.psd         sample model
```

Runtime is WebGL1 (mesh warp + stencil). The only external network access is the MediaPipe CDN load when camera tracking is enabled.

## Known limitations

- Flat-image decomposition is not done in this app — use Tag Forge's Decompose tab (or the [see-through demo Space](https://huggingface.co/spaces/24yearsold/see-through-demo)) to produce the layered PSD first (all post-processing here is automatic)
- Mouth opening is a simple state-swap + deform (more in-between states would smooth it)
- Depth comes from a fixed name-based table (PSD layer order is respected for draw order)

## License

MIT (bundled ag-psd is also MIT; MediaPipe is Apache-2.0, referenced from CDN).

Single-image layer decomposition is expected to come from [shitagaki-lab/see-through](https://github.com/shitagaki-lab/see-through) (Apache-2.0, SIGGRAPH 2026). This tool is an independent third-party project that post-processes and rigs that project's PSD output; see-through's code and models are not bundled.
**The artwork in the sample PSD belongs to its respective creators.** Use your own artwork when distributing models.

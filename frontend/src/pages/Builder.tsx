import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { Copy, Dice5, Film, Lock, RefreshCw, Unlock } from "lucide-react";

import { api, llmApi, type RolledScene } from "@/lib/api";
import { danbooruPostUrl, naiSpacedTags } from "@/lib/naiTags";
import { NaiSplitPanel, readNaiSplitSettings } from "@/components/NaiSplitPanel";
import { Panel } from "@/components/Panel";
import { PresetPicker } from "@/components/PresetPicker";
import { BucketBadge } from "@/components/BucketBadge";
import { ClickSpark } from "@/components/ClickSpark";
import { Field, RatingFilter, SegmentedControl } from "@/components/forms";

const BUCKETS = [
  "outfit",
  "accessory",
  "pose",
  "expression",
  "background",
  "composition",
  "extras",
];

type OriginFilter = "" | "local" | "booru";

interface SceneRef {
  id: number;
  external_id: string;
  rating: string | null;
  score: number | null;
  origin?: "local" | "booru" | "other";
  subjects?: string;
}

const LAST_LS = "tagforge-builder-last";

const SUBJECT_OPTIONS = [
  { value: "", label: "any" },
  { value: "solo", label: "1girl (solo)" },
  { value: "1girl_1boy", label: "1girl + 1boy" },
  { value: "2girls", label: "2girls only" },
  { value: "3plus_girls", label: "3+ girls" },
  { value: "multi", label: "multi-character (any)" },
] as const;

interface BuilderSnapshot {
  character: string;
  base: string;
  rating: string;
  origin: OriginFilter;
  sourceIds: number[];
  scoreMin: number | "";
  capTags: string;
  capPct: number;
  excludeTags: string;
  requireTags: string;
  subjects: string;
}

/** Last-used settings (one JSON blob), with migration from the old
 * per-field keys this page used before presets existed. */
function loadLast(): Partial<BuilderSnapshot> {
  try {
    const raw = localStorage.getItem(LAST_LS);
    if (raw) return JSON.parse(raw) as Partial<BuilderSnapshot>;
  } catch {
    /* corrupted — fall through */
  }
  const legacyPct = Number(localStorage.getItem("tagforge-builder-cap-pct"));
  return {
    capTags: localStorage.getItem("tagforge-builder-cap-tags") ?? undefined,
    capPct: Number.isFinite(legacyPct) &&
      localStorage.getItem("tagforge-builder-cap-pct") !== null
      ? legacyPct
      : undefined,
    excludeTags: localStorage.getItem("tagforge-builder-exclude") ?? undefined,
  };
}

export function Builder() {
  const sources = useQuery({ queryKey: ["sources"], queryFn: api.listSources });

  const [last] = useState<Partial<BuilderSnapshot>>(loadLast);
  const [character, setCharacter] = useState(last.character ?? "1girl, solo");
  const [base, setBase] = useState(
    last.base ?? "masterpiece, best quality, very aesthetic, absurdres",
  );
  const [rating, setRating] = useState(last.rating ?? "");
  const [origin, setOrigin] = useState<OriginFilter>(last.origin ?? "");
  const [sourceIds, setSourceIds] = useState<number[]>(
    Array.isArray(last.sourceIds) ? last.sourceIds : [],
  );
  const [scoreMin, setScoreMin] = useState<number | "">(
    typeof last.scoreMin === "number" ? last.scoreMin : "",
  );
  const [values, setValues] = useState<Record<string, string>>({});
  const [locks, setLocks] = useState<Record<string, boolean>>({});
  const [capTags, setCapTags] = useState(
    last.capTags ?? "white background, simple background",
  );
  const [capPct, setCapPct] = useState<number>(
    typeof last.capPct === "number" ? last.capPct : 25,
  );
  const [excludeTags, setExcludeTags] = useState(last.excludeTags ?? "");
  const [requireTags, setRequireTags] = useState(last.requireTags ?? "");
  const [subjects, setSubjects] = useState(last.subjects ?? "");
  // The image a coherent scene roll came from; cleared by independent rolls.
  const [sceneRef, setSceneRef] = useState<SceneRef | null>(null);

  const snapshot = (): BuilderSnapshot => ({
    character,
    base,
    rating,
    origin,
    sourceIds,
    scoreMin,
    capTags,
    capPct,
    excludeTags,
    requireTags,
    subjects,
  });

  // Auto-remember the last-used settings across sessions.
  useEffect(() => {
    try {
      localStorage.setItem(LAST_LS, JSON.stringify(snapshot()));
    } catch {
      /* quota — non-fatal */
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [character, base, rating, origin, sourceIds, scoreMin, capTags, capPct, excludeTags, requireTags, subjects]);

  const applySnapshot = (data: Record<string, unknown>) => {
    const d = data as Partial<BuilderSnapshot>;
    if (typeof d.character === "string") setCharacter(d.character);
    if (typeof d.base === "string") setBase(d.base);
    if (typeof d.rating === "string") setRating(d.rating);
    if (d.origin === "" || d.origin === "local" || d.origin === "booru")
      setOrigin(d.origin);
    if (Array.isArray(d.sourceIds))
      setSourceIds(d.sourceIds.filter((x): x is number => typeof x === "number"));
    setScoreMin(typeof d.scoreMin === "number" ? d.scoreMin : "");
    if (typeof d.capTags === "string") setCapTags(d.capTags);
    if (typeof d.capPct === "number" && Number.isFinite(d.capPct))
      setCapPct(d.capPct);
    if (typeof d.excludeTags === "string") setExcludeTags(d.excludeTags);
    if (typeof d.requireTags === "string") setRequireTags(d.requireTags);
    if (typeof d.subjects === "string") setSubjects(d.subjects);
  };

  const rollParams = () => ({
    ratings: rating ? rating.split(",") : [],
    source_ids: sourceIds,
    origin: origin || undefined,
    score_min: typeof scoreMin === "number" ? scoreMin : undefined,
    subjects,
    cap_tags: capTags.split(",").map((t) => t.trim()).filter(Boolean),
    // Backend wants an int 0-100; a cleared/odd input must not 422 the roll.
    cap_percent: Number.isFinite(capPct)
      ? Math.round(Math.max(0, Math.min(100, capPct)))
      : 100,
    exclude_tags: excludeTags.split(",").map((t) => t.trim()).filter(Boolean),
    require_tags: requireTags.split(",").map((t) => t.trim()).filter(Boolean),
  });

  const lockedValues = () => {
    const locked: Record<string, string> = {};
    for (const b of BUCKETS) {
      if (locks[b] && values[b]) locked[b] = values[b];
    }
    return locked;
  };

  // ---- prefetch pool -------------------------------------------------
  // Every roll request asks for a batch (the candidate scan dominates the
  // cost, so 10 rolls ≈ 1); the surplus is banked per settings-fingerprint
  // and served instantly on the next press. A settings/lock change alters
  // the key, which orphans stale entries — they're pruned on the next bank.
  const POOL_TARGET = 10;
  const POOL_MIN = 5;
  const poolsRef = useRef<Map<string, RolledScene[]>>(new Map());
  const refillingRef = useRef<Set<string>>(new Set());

  const poolKey = (coherent: boolean) =>
    JSON.stringify({ coherent, locked: lockedValues(), ...rollParams() });

  // Refreshed every render so async callbacks created earlier (a roll in
  // flight when settings changed) prune against CURRENT settings instead of
  // their stale closure — which would otherwise wipe the live pool.
  const keepRef = useRef<Set<string>>(new Set());
  keepRef.current = new Set([poolKey(true), poolKey(false)]);

  const bankRolls = (key: string, rolls: RolledScene[]) => {
    // Drop pools for settings that no longer exist before adding.
    const keep = keepRef.current;
    for (const k of [...poolsRef.current.keys()]) {
      if (!keep.has(k)) poolsRef.current.delete(k);
    }
    if (!keep.has(key) || !rolls.length) return;
    poolsRef.current.set(key, [
      ...(poolsRef.current.get(key) ?? []),
      ...rolls,
    ]);
  };

  const applyRoll = (r: RolledScene, coherent: boolean) => {
    // A scene roll that found no covering image returns all-empty buckets —
    // keep what the user has instead of wiping it.
    if (coherent && !r.image) {
      toast.warning(
        "No single image covers all unlocked buckets with these filters",
      );
      return;
    }
    setValues((prev) => ({ ...prev, ...r.buckets }));
    setSceneRef(coherent ? (r.image ?? null) : null);
  };

  const refillPool = async (coherent: boolean) => {
    const key = poolKey(coherent);
    const have = poolsRef.current.get(key)?.length ?? 0;
    if (have >= POOL_MIN || refillingRef.current.has(key)) return;
    refillingRef.current.add(key);
    try {
      const res = await api.rollBuilder({
        buckets: BUCKETS,
        locked: lockedValues(),
        coherent,
        count: POOL_TARGET,
        ...rollParams(),
      });
      // Coherent "not found" placeholders would replay a useless warning —
      // pool only real scenes.
      bankRolls(
        key,
        (res.rolls ?? []).filter((r) => !coherent || r.image),
      );
    } catch {
      /* background refill is best-effort; the next press rolls live */
    } finally {
      refillingRef.current.delete(key);
    }
  };

  // Warm the pool (and the DB's file cache) on page load so even the first
  // press is instant.
  useEffect(() => {
    const t = setTimeout(() => {
      void refillPool(true);
      void refillPool(false);
    }, 500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const rollMut = useMutation({
    mutationFn: (vars: { coherent: boolean; key: string }) =>
      api.rollBuilder({
        buckets: BUCKETS,
        locked: lockedValues(),
        coherent: vars.coherent,
        count: POOL_TARGET,
        ...rollParams(),
      }),
    onSuccess: (res, vars) => {
      applyRoll({ buckets: res.buckets, image: res.image }, vars.coherent);
      // Bank the rest of the batch — but only under the key the request was
      // made with, so a mid-flight settings change can't poison the pool.
      bankRolls(
        vars.key,
        (res.rolls ?? []).slice(1).filter((r) => !vars.coherent || r.image),
      );
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const doRoll = (coherent: boolean) => {
    const key = poolKey(coherent);
    const next = poolsRef.current.get(key)?.shift();
    if (next) {
      applyRoll(next, coherent);
      void refillPool(coherent);
      return;
    }
    rollMut.mutate({ coherent, key });
  };

  // ---- batch: roll -> process -> handoff, N times with an interval --------
  // Each processed result lands in /api/llm/handoff, where the NAI bridge
  // userscript picks it up and (optionally) generates. The interval is the
  // pause AFTER each handoff so NAI has time to generate before the next
  // result replaces it.
  const [batchRuns, setBatchRuns] = useState(
    () => Number(localStorage.getItem("tagforge-batch-runs")) || 10,
  );
  const [batchInterval, setBatchInterval] = useState(
    () => Number(localStorage.getItem("tagforge-batch-interval")) || 20,
  );
  const [batchNote, setBatchNote] = useState("");
  const [batchBusy, setBatchBusy] = useState(false);
  const batchStop = useRef(false);
  useEffect(() => {
    localStorage.setItem("tagforge-batch-runs", String(batchRuns));
    localStorage.setItem("tagforge-batch-interval", String(batchInterval));
  }, [batchRuns, batchInterval]);

  const batchSleep = async (ms: number) => {
    const end = Date.now() + ms;
    while (Date.now() < end) {
      if (batchStop.current) return false;
      await new Promise((r) => setTimeout(r, Math.min(400, end - Date.now())));
    }
    return true;
  };

  const runBatch = async () => {
    setBatchBusy(true);
    batchStop.current = false;
    const settings = readNaiSplitSettings();
    let failures = 0;
    let done = 0;
    try {
      for (let i = 0; i < batchRuns; i++) {
        if (batchStop.current) break;
        try {
          setBatchNote(`${i + 1}/${batchRuns} rolling…`);
          // Coherent rolls only: the splitter works from a source image's
          // full tag list, so a roll without an image is retried.
          let image: RolledScene["image"] = null;
          let buckets: Record<string, string> = {};
          for (let tries = 0; tries < 3 && !image; tries++) {
            const res = await api.rollBuilder({
              buckets: BUCKETS,
              locked: lockedValues(),
              coherent: true,
              count: 1,
              ...rollParams(),
            });
            image = res.image ?? null;
            buckets = res.buckets;
          }
          if (!image) throw new Error("no coherent scene found (3 tries)");
          applyRoll({ buckets, image }, true); // keep the visible roll in sync

          setBatchNote(`${i + 1}/${batchRuns} processing image #${image.id}…`);
          const detail = await api.getScene(image.id);
          await llmApi.naiSplit({
            tags: naiSpacedTags(detail.raw_prompt),
            ...settings,
          });
          done++;
          failures = 0;
        } catch (err) {
          failures++;
          toast.error(`batch run ${i + 1}: ${(err as Error).message}`);
          if (failures >= 3) {
            toast.error("3 consecutive failures — batch aborted");
            break;
          }
        }
        if (i < batchRuns - 1) {
          setBatchNote(
            `${i + 1}/${batchRuns} done — waiting ${batchInterval}s for NAI…`,
          );
          if (!(await batchSleep(batchInterval * 1000))) break;
        }
      }
    } finally {
      setBatchBusy(false);
      setBatchNote(
        batchStop.current
          ? `stopped after ${done}/${batchRuns}`
          : `finished ${done}/${batchRuns}`,
      );
    }
  };

  const rerollMut = useMutation({
    mutationFn: (bucket: string) => {
      const locked: Record<string, string> = {};
      for (const b of BUCKETS) {
        if (b !== bucket && values[b]) locked[b] = values[b];
      }
      return api.rollBuilder({
        buckets: [bucket],
        locked,
        ...rollParams(),
      });
    },
    onSuccess: (res, bucket) => {
      if (res.buckets[bucket] !== undefined) {
        setValues((prev) => ({ ...prev, [bucket]: res.buckets[bucket] }));
        setSceneRef(null);
      }
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const rollPending = rollMut.isPending || rerollMut.isPending;

  const excludeSet = useMemo(
    () =>
      new Set(
        excludeTags
          .split(",")
          .map((t) => t.trim().toLowerCase())
          .filter(Boolean),
      ),
    [excludeTags],
  );

  const assembled = useMemo(() => {
    const strip = (text: string) =>
      text
        .split(",")
        .map((t) => t.trim())
        .filter((t) => t && !excludeSet.has(t.toLowerCase()))
        .join(", ");
    const parts = [base, character];
    for (const b of BUCKETS) {
      if (values[b]) parts.push(strip(values[b]));
    }
    return parts.filter(Boolean).join(", ");
  }, [base, character, values, excludeSet]);

  function toggleLock(b: string) {
    setLocks((prev) => ({ ...prev, [b]: !prev[b] }));
  }

  async function copyAssembled() {
    try {
      await navigator.clipboard.writeText(assembled);
      toast.success("copied to clipboard");
    } catch {
      toast.error("Clipboard unavailable");
    }
  }

  // Full unfiltered tag list of the scene roll's source image — handy for
  // multi-character prompts where the buckets strip subject/character tags.
  async function copySceneTags() {
    if (!sceneRef) return;
    try {
      const d = await api.getScene(sceneRef.id);
      await navigator.clipboard.writeText(naiSpacedTags(d.raw_prompt));
      toast.success("Source image's full tag list copied (NAI format)");
    } catch (err) {
      toast.error((err as Error).message);
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Builder</h1>
        <p className="text-sm text-text-muted">
          Roll coherent per-bucket combos from the corpus and assemble a prompt
          around your character.
        </p>
      </div>

      <Panel
        title="Inputs"
        actions={
          <PresetPicker
            kind="builder"
            getSnapshot={() => snapshot() as unknown as Record<string, unknown>}
            applySnapshot={applySnapshot}
          />
        }
      >
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <Field label="Character / subject">
            <textarea
              className="pf-input min-h-[60px] py-2 font-mono"
              value={character}
              onChange={(e) => setCharacter(e.target.value)}
            />
          </Field>
          <Field label="Base / quality prompt">
            <textarea
              className="pf-input min-h-[60px] py-2 font-mono"
              value={base}
              onChange={(e) => setBase(e.target.value)}
            />
          </Field>
          <Field label="Ratings">
            <RatingFilter value={rating} onChange={setRating} />
          </Field>
          <Field label="Score min">
            <input
              type="number"
              className="pf-input"
              value={scoreMin}
              onChange={(e) =>
                setScoreMin(e.target.value === "" ? "" : Number(e.target.value))
              }
            />
          </Field>
          <Field
            label={
              <>
                Background limiter{" "}
                <span className="font-normal normal-case text-text-subtle">
                  — rolls containing these tags are allowed only N% of the time
                </span>
              </>
            }
          >
            <div className="flex items-center gap-2">
              <input
                className="pf-input flex-1 font-mono"
                value={capTags}
                placeholder="white background, simple background"
                onChange={(e) => setCapTags(e.target.value)}
              />
              <input
                type="number"
                className="pf-input w-20"
                min={0}
                max={100}
                value={capPct}
                title="Chance (%) that a capped background is still accepted"
                onChange={(e) => setCapPct(Number(e.target.value))}
              />
              <span className="text-xs text-text-subtle">%</span>
            </div>
          </Field>
          <Field
            label={
              <>
                Exclude from prompt{" "}
                <span className="font-normal normal-case text-text-subtle">
                  — tags stripped from rolls and the assembled prompt
                </span>
              </>
            }
          >
            <input
              className="pf-input w-full font-mono"
              value={excludeTags}
              placeholder="e.g. blurry, watermark, twintails"
              onChange={(e) => setExcludeTags(e.target.value)}
            />
          </Field>
          <Field
            label={
              <>
                Roll for tags{" "}
                <span className="font-normal normal-case text-text-subtle">
                  — only roll from images containing ALL these tags
                </span>
              </>
            }
          >
            <input
              className="pf-input w-full font-mono"
              value={requireTags}
              placeholder="e.g. beach — whole-tag match, space or underscore form"
              onChange={(e) => setRequireTags(e.target.value)}
            />
          </Field>
          <Field
            label={
              <>
                Subjects{" "}
                <span className="font-normal normal-case text-text-subtle">
                  — exclusive character-count filter
                </span>
              </>
            }
          >
            <select
              className="pf-input"
              value={subjects}
              onChange={(e) => setSubjects(e.target.value)}
            >
              {SUBJECT_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Origin">
            <SegmentedControl<OriginFilter>
              options={[
                { value: "", label: "all" },
                { value: "local", label: "local" },
                { value: "booru", label: "booru" },
              ]}
              value={origin}
              onChange={(o) => {
                setOrigin(o);
                // Drop selections the new origin hides — otherwise the roll
                // sends contradictory source_ids + origin, matches nothing,
                // and silently wipes every unlocked bucket to "".
                if (o) {
                  setSourceIds((prev) =>
                    prev.filter((id) =>
                      (sources.data ?? []).some(
                        (s) => s.id === id && s.origin === o,
                      ),
                    ),
                  );
                }
              }}
            />
          </Field>
          <Field label="Sources" className="md:col-span-2">
            <div className="flex flex-wrap gap-2">
              {(sources.data ?? [])
                .filter((s) => !origin || s.origin === origin)
                .map((s) => (
                  <label
                    key={s.id}
                    className={`pf-pill cursor-pointer ${
                      sourceIds.includes(s.id)
                        ? "border-brand bg-brand/15 text-brand"
                        : ""
                    }`}
                  >
                    <input
                      type="checkbox"
                      className="hidden"
                      checked={sourceIds.includes(s.id)}
                      onChange={(e) =>
                        setSourceIds((prev) =>
                          e.target.checked
                            ? [...prev, s.id]
                            : prev.filter((x) => x !== s.id),
                        )
                      }
                    />
                    {s.label}
                  </label>
                ))}
              {!sources.data?.length && (
                <span className="text-xs text-text-muted">
                  no sources yet — ingest first
                </span>
              )}
            </div>
          </Field>
        </div>
      </Panel>

      <Panel
        title="Buckets"
        description={
          sceneRef ? (
            <span className="inline-flex flex-wrap items-center gap-x-2">
              <span>
                coherent scene from image #{sceneRef.id} ({sceneRef.external_id})
                {sceneRef.rating ? ` · rating ${sceneRef.rating}` : ""}
                {sceneRef.score != null ? ` · score ${sceneRef.score}` : ""} — all
                buckets appeared together in this image
              </span>
              {sceneRef.subjects ? (
                <span
                  className="pf-pill border-brand/40 font-mono text-brand"
                  title="Character count in the source image (the buckets strip these tags)"
                >
                  {sceneRef.subjects}
                </span>
              ) : null}
              <button
                type="button"
                className="underline underline-offset-2 hover:text-text"
                title="Copy the source image's entire unfiltered tag list (NAI spacing) — includes character/subject tags the buckets drop"
                onClick={() => void copySceneTags()}
              >
                copy full tags
              </button>
              {danbooruPostUrl(sceneRef.external_id, sceneRef.origin) && (
                <a
                  href={danbooruPostUrl(sceneRef.external_id, sceneRef.origin)!}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline underline-offset-2 hover:text-text"
                  title="Open the source post on Danbooru"
                >
                  danbooru
                </a>
              )}
            </span>
          ) : undefined
        }
        actions={
          <div className="flex items-center gap-2">
            <button
              type="button"
              className="pf-btn-ghost"
              title="Copy the assembled prompt (same as the panel below)"
              onClick={copyAssembled}
            >
              <Copy size={13} /> Copy
            </button>
            <button
              type="button"
              className="pf-btn-ghost"
              onClick={() => {
                setValues({});
                setLocks({});
                setSceneRef(null);
              }}
            >
              Clear all
            </button>
            <button
              type="button"
              className="pf-btn"
              disabled={rollPending}
              title="Fill all unlocked buckets from a single image — details are guaranteed to work together"
              onClick={() => doRoll(true)}
            >
              <Film
                size={14}
                className={
                  rollMut.isPending && rollMut.variables?.coherent === true
                    ? "animate-spin"
                    : undefined
                }
              />{" "}
              Roll scene
            </button>
            <ClickSpark>
              <button
                type="button"
                className="pf-btn-primary"
                disabled={rollPending}
                onClick={() => doRoll(false)}
              >
                <Dice5
                  size={14}
                  className={
                    rollMut.isPending && rollMut.variables?.coherent === false
                      ? "animate-spin"
                      : undefined
                  }
                />{" "}
                Roll
              </button>
            </ClickSpark>
          </div>
        }
      >
        <ul className="space-y-2">
          {BUCKETS.map((b) => (
            <li
              key={b}
              className="rounded border border-line bg-bg-subtle/60 p-2"
            >
              <div className="mb-1 flex items-center justify-between gap-2">
                <BucketBadge bucket={b} />
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    className="pf-btn-ghost h-7 px-2"
                    title={`Reroll ${b} only (others kept)`}
                    disabled={rollPending}
                    onClick={() => rerollMut.mutate(b)}
                  >
                    <RefreshCw
                      size={12}
                      className={
                        rerollMut.isPending && rerollMut.variables === b
                          ? "animate-spin"
                          : undefined
                      }
                    />
                  </button>
                  <button
                    type="button"
                    className="pf-btn-ghost h-7 px-2"
                    onClick={() => toggleLock(b)}
                  >
                    {locks[b] ? (
                      <Lock size={12} className="text-accent-amber" />
                    ) : (
                      <Unlock size={12} />
                    )}
                    {locks[b] ? "locked" : "unlocked"}
                  </button>
                </div>
              </div>
              <textarea
                className="pf-input min-h-[40px] py-2 font-mono"
                value={values[b] ?? ""}
                onChange={(e) => {
                  setValues((p) => ({ ...p, [b]: e.target.value }));
                  setSceneRef(null);
                }}
                placeholder="(empty — roll to populate)"
              />
            </li>
          ))}
        </ul>
      </Panel>

      <Panel
        title="Assembled prompt"
        actions={
          <button
            type="button"
            className="pf-btn-primary"
            onClick={copyAssembled}
          >
            <Copy size={14} /> Copy
          </button>
        }
      >
        <textarea
          readOnly
          className="pf-input min-h-[140px] py-2 font-mono"
          value={assembled}
        />
      </Panel>

      <Panel
        title="NAI prompt splitter"
        description={
          sceneRef
            ? `will process the FULL tag list of source image #${sceneRef.id} (includes character/count tags the buckets strip)`
            : "will process the assembled prompt above — roll a scene first to work from a source image's full tags"
        }
      >
        <div className="mb-3 flex flex-wrap items-center gap-2 rounded border border-line bg-bg-subtle/30 p-2">
          <span className="text-xs font-semibold">Batch</span>
          <input
            type="number"
            min={1}
            max={500}
            className="pf-input h-8 w-16"
            value={batchRuns}
            onChange={(e) => setBatchRuns(Number(e.target.value) || 1)}
            disabled={batchBusy}
            title="How many scenes to roll and process"
          />
          <span className="text-xs text-text-muted">runs ×</span>
          <input
            type="number"
            min={0}
            max={600}
            className="pf-input h-8 w-16"
            value={batchInterval}
            onChange={(e) => setBatchInterval(Number(e.target.value) || 0)}
            disabled={batchBusy}
            title="Seconds to wait after each handoff so NAI can generate before the next one lands"
          />
          <span className="text-xs text-text-muted">s interval</span>
          {batchBusy ? (
            <button
              type="button"
              className="pf-btn h-8 px-3 text-xs"
              onClick={() => {
                batchStop.current = true;
              }}
            >
              Stop
            </button>
          ) : (
            <button
              type="button"
              className="pf-btn-primary h-8 px-3 text-xs"
              onClick={() => void runBatch()}
              title="Roll a coherent scene, process its full tags, hand off to the NAI bridge — repeat"
            >
              Start batch
            </button>
          )}
          {batchNote && (
            <span className="text-xs text-text-muted">{batchNote}</span>
          )}
        </div>
        <NaiSplitPanel
          allowCompose
          resolveInput={async () => {
            if (sceneRef) {
              const d = await api.getScene(sceneRef.id);
              return {
                tags: naiSpacedTags(d.raw_prompt),
                label: `image #${sceneRef.id} full tags`,
              };
            }
            return assembled
              ? { tags: assembled, label: "assembled prompt" }
              : null;
          }}
        />
      </Panel>
    </div>
  );
}

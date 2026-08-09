/* NAI prompt splitter: sends a flat tag list to the local LLM (llama-swap)
 * and renders NovelAI V4.5 base + per-character prompts with copy buttons.
 * Embeddable block (no Panel wrapper) so it fits Builder and the Scenes
 * detail column alike. */

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Play, Power, Wand2 } from "lucide-react";
import { toast } from "sonner";

import {
  llmApi,
  type BubbleMode,
  type NaiSplitResult,
  type TextPosition,
} from "@/lib/api";
import { cn } from "@/lib/cn";
import { Checkbox, CopyButton, SegmentedControl } from "@/components/forms";

type Mode = "split" | "natural";

const MODE_LS = "tagforge-nai-mode";
const MODEL_LS = "tagforge-nai-model";
const SPEECH_LS = "tagforge-nai-speech";
const STRIP_LS = "tagforge-nai-strip-identity";
const INVENT_BG_LS = "tagforge-nai-invent-bg";
const ENRICH_BG_LS = "tagforge-nai-enrich-bg";
const BUBBLE_LS = "tagforge-nai-bubble";
const TEXTPOS_LS = "tagforge-nai-text-pos";

/** The panel's persisted settings, for callers that process without the
 *  panel UI (Builder's batch runner). Same keys, same defaults. */
export function readNaiSplitSettings() {
  return {
    mode: (localStorage.getItem(MODE_LS) as Mode) || "natural",
    model: localStorage.getItem(MODEL_LS) || undefined,
    include_speech: localStorage.getItem(SPEECH_LS) === "1",
    strip_identity: localStorage.getItem(STRIP_LS) === "1",
    invent_background: localStorage.getItem(INVENT_BG_LS) === "1",
    enrich_background: localStorage.getItem(ENRICH_BG_LS) === "1",
    bubble: (localStorage.getItem(BUBBLE_LS) as BubbleMode) || "auto",
    text_position:
      (localStorage.getItem(TEXTPOS_LS) as TextPosition) || "attributed",
  };
}

const BUBBLE_OPTS: { value: BubbleMode; label: string }[] = [
  { value: "auto", label: "auto" },
  { value: "on", label: "bubble" },
  { value: "off", label: "no bubble" },
];

const TEXTPOS_OPTS: { value: TextPosition; label: string }[] = [
  { value: "attributed", label: "by speaker" },
  { value: "placed", label: "placed" },
  { value: "free", label: "free" },
];

export function NaiSplitPanel({
  resolveInput,
  allowCompose = false,
  className,
}: {
  /** Provides the tag list to process (called on click, may fetch). */
  resolveInput: () =>
    | Promise<{ tags: string; label: string } | null>
    | { tags: string; label: string }
    | null;
  /** Show the compose-from-idea input (author a prompt from scratch). */
  allowCompose?: boolean;
  className?: string;
}) {
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["llm", "status"],
    queryFn: llmApi.status,
    refetchInterval: 15_000,
  });

  const [mode, setMode] = useState<Mode>(
    () => (localStorage.getItem(MODE_LS) as Mode) || "natural",
  );
  const [model, setModel] = useState<string>(
    () => localStorage.getItem(MODEL_LS) ?? "",
  );
  const [speech, setSpeech] = useState(
    () => localStorage.getItem(SPEECH_LS) === "1",
  );
  const [stripIdentity, setStripIdentity] = useState(
    () => localStorage.getItem(STRIP_LS) === "1",
  );
  const [inventBg, setInventBg] = useState(
    () => localStorage.getItem(INVENT_BG_LS) === "1",
  );
  const [enrichBg, setEnrichBg] = useState(
    () => localStorage.getItem(ENRICH_BG_LS) === "1",
  );
  const [bubble, setBubble] = useState<BubbleMode>(
    () => (localStorage.getItem(BUBBLE_LS) as BubbleMode) || "auto",
  );
  const [textPos, setTextPos] = useState<TextPosition>(
    () => (localStorage.getItem(TEXTPOS_LS) as TextPosition) || "attributed",
  );
  useEffect(() => {
    localStorage.setItem(MODE_LS, mode);
    localStorage.setItem(SPEECH_LS, speech ? "1" : "0");
    localStorage.setItem(STRIP_LS, stripIdentity ? "1" : "0");
    localStorage.setItem(INVENT_BG_LS, inventBg ? "1" : "0");
    localStorage.setItem(ENRICH_BG_LS, enrichBg ? "1" : "0");
    localStorage.setItem(BUBBLE_LS, bubble);
    localStorage.setItem(TEXTPOS_LS, textPos);
    if (model) localStorage.setItem(MODEL_LS, model);
  }, [mode, model, speech, stripIdentity, inventBg, enrichBg, bubble, textPos]);

  const models = status.data?.models ?? [];
  const remote = status.data?.remote === true;
  // A remembered local model must not override a remote endpoint's
  // configured model — the backend treats an explicit model as the winner.
  const effectiveModel = remote
    ? (status.data?.default_model ?? "")
    : model && models.includes(model)
      ? model
      : (status.data?.default_model ?? models[0] ?? "");

  const [busySecs, setBusySecs] = useState<number | null>(null);
  const busyTimer = useRef<number | undefined>(undefined);
  const [sourceLabel, setSourceLabel] = useState("");
  const [result, setResult] = useState<NaiSplitResult | null>(null);
  const [idea, setIdea] = useState("");

  useEffect(() => () => window.clearInterval(busyTimer.current), []);

  const startMut = useMutation({
    mutationFn: llmApi.start,
    onSuccess: (res) => {
      if (res.up) toast.success("LLM server is up");
      else toast.error(res.error ?? "server did not start");
      void qc.invalidateQueries({ queryKey: ["llm", "status"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });
  const unloadMut = useMutation({
    mutationFn: llmApi.unload,
    onSuccess: () => {
      toast.success("Model unloaded — VRAM freed");
      void qc.invalidateQueries({ queryKey: ["llm", "status"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const splitMut = useMutation({
    mutationFn: async () => {
      const input = await resolveInput();
      if (!input || !input.tags.trim()) throw new Error("nothing to process");
      const res = await llmApi.naiSplit({
        tags: input.tags,
        mode,
        model: effectiveModel,
        include_speech: speech,
        strip_identity: stripIdentity,
        invent_background: inventBg,
        enrich_background: enrichBg,
        bubble,
        text_position: textPos,
      });
      return { res, label: input.label };
    },
    onMutate: () => {
      // Clear the previous run so a stale result is never shown (or
      // relabeled) while the new one is generating.
      setResult(null);
      setSourceLabel("");
      setBusySecs(0);
      window.clearInterval(busyTimer.current);
      busyTimer.current = window.setInterval(
        () => setBusySecs((s) => (s === null ? null : s + 1)),
        1000,
      );
    },
    onSettled: () => {
      window.clearInterval(busyTimer.current);
      setBusySecs(null);
      void qc.invalidateQueries({ queryKey: ["llm", "status"] });
    },
    onSuccess: ({ res, label }) => {
      setResult(res);
      setSourceLabel(label);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const composeMut = useMutation({
    mutationFn: async () => {
      if (!idea.trim()) throw new Error("describe the idea first");
      return llmApi.naiCompose({ idea: idea.trim(), model: effectiveModel });
    },
    onMutate: () => {
      setResult(null);
      setSourceLabel("");
      setBusySecs(0);
      window.clearInterval(busyTimer.current);
      busyTimer.current = window.setInterval(
        () => setBusySecs((s) => (s === null ? null : s + 1)),
        1000,
      );
    },
    onSettled: () => {
      window.clearInterval(busyTimer.current);
      setBusySecs(null);
      void qc.invalidateQueries({ queryKey: ["llm", "status"] });
    },
    onSuccess: (res) => {
      setResult(res);
      setSourceLabel("your idea");
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const anyBusy = splitMut.isPending || composeMut.isPending;

  const up = status.data?.up ?? false;
  const running = status.data?.running ?? [];
  const loaded = running.filter((r) => r.model);

  // Idle-unload ttl (minutes, 0 = keep loaded). Local draft while typing;
  // applied on blur/Enter so half-typed numbers never hit the config.
  const serverTtl = status.data?.ttl_minutes ?? null;
  const [ttlDraft, setTtlDraft] = useState<string>("");
  const ttlShown = ttlDraft !== "" ? ttlDraft : serverTtl !== null ? String(serverTtl) : "";
  const ttlMut = useMutation({
    mutationFn: (minutes: number) => llmApi.setTtl(minutes),
    onSuccess: (res) => {
      toast.success(
        res.ttl_minutes === 0
          ? "Models will stay loaded until unloaded manually"
          : `Models now unload after ${res.ttl_minutes} min idle`,
      );
      setTtlDraft("");
      void qc.invalidateQueries({ queryKey: ["llm", "status"] });
    },
    onError: (err: Error) => {
      setTtlDraft("");
      toast.error(err.message);
    },
  });
  const applyTtl = () => {
    if (ttlDraft === "") return;
    const n = Math.round(Number(ttlDraft));
    if (!Number.isFinite(n) || n < 0) {
      setTtlDraft("");
      return;
    }
    if (n !== serverTtl) ttlMut.mutate(n);
    else setTtlDraft("");
  };

  return (
    <div className={cn("space-y-3", className)}>
      {/* server bar */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
        <span className="flex items-center gap-1.5 text-text-subtle">
          <span
            className={cn(
              "inline-block h-1.5 w-1.5 rounded-full",
              status.isLoading
                ? "bg-text-subtle/40"
                : up
                  ? "bg-accent-green"
                  : "bg-accent-amber",
            )}
          />
          {remote
            ? `remote endpoint — ${status.data?.target ?? "configured in Settings"}`
            : up
              ? loaded.length
                ? `loaded: ${loaded.map((r) => `${r.model} (${r.state})`).join(", ")}`
                : "LLM server up — model loads on first use"
              : "LLM server offline"}
        </span>
        {!up && !remote && (
          <button
            type="button"
            className="pf-btn h-7 px-2 text-xs"
            disabled={startMut.isPending}
            onClick={() => startMut.mutate()}
          >
            {startMut.isPending ? (
              <Loader2 size={12} className="mr-1 inline animate-spin" />
            ) : (
              <Play size={12} className="mr-1 inline" />
            )}
            Start server
          </button>
        )}
        {serverTtl !== null && !remote && (
          <span
            className="flex items-center gap-1 text-text-subtle"
            title="Idle minutes before the model is unloaded from VRAM. 0 = keep it loaded indefinitely. Applies live via llama-swap's config watch."
          >
            unload after
            <input
              type="number"
              min={0}
              max={1440}
              className="pf-input h-7 w-16 px-1 text-center text-xs"
              value={ttlShown}
              disabled={ttlMut.isPending}
              onChange={(e) => setTtlDraft(e.target.value)}
              onBlur={applyTtl}
              onKeyDown={(e) => {
                if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              }}
            />
            {serverTtl === 0 && ttlDraft === "" ? (
              <span className="text-accent-amber">min — stays loaded</span>
            ) : (
              <span>min idle</span>
            )}
          </span>
        )}
        {up && !remote && loaded.length > 0 && (
          <button
            type="button"
            className="pf-btn-ghost h-7 px-2 text-xs"
            title="Unload the model from VRAM now (it reloads on the next request)"
            disabled={unloadMut.isPending}
            onClick={() => unloadMut.mutate()}
          >
            <Power size={12} className="mr-1 inline" />
            Unload VRAM
          </button>
        )}
      </div>

      {/* controls */}
      <div className="flex flex-wrap items-center gap-2">
        <SegmentedControl<Mode>
          options={[
            { value: "split", label: "Split characters" },
            { value: "natural", label: "Split + natural language" },
          ]}
          value={mode}
          onChange={setMode}
        />
        <span
          title="Author in-image text, art-directed: size, colour, treatment and placement in plain English, plus bubble control (a speech bubble, or -1::speech bubble:: to suppress one for bare impact lettering). Styling is matched to the scene. Off = all text tags stripped."
        >
          <Checkbox checked={speech} onChange={setSpeech} label="dialogue" />
        </span>
        <span
          title="Remove character names, series tags and innate looks (hair/eye/skin colors, body traits) so you can drop your own characters into the scene. Outfits, expressions and poses are kept."
        >
          <Checkbox
            checked={stripIdentity}
            onChange={setStripIdentity}
            label="strip identity"
          />
        </span>
        <span
          title="If the background is white/simple/plain, invent a fitting setting from the image's other tags (outfit, mood, props) — a few concrete tags + one setting sentence, seawall-register, not a detail dump."
        >
          <Checkbox
            checked={inventBg}
            onChange={setInventBg}
            label="invent background"
          />
        </span>
        <span
          title="Keep the existing background and add a few complementary details (objects, lighting, time of day, depth cues)."
        >
          <Checkbox
            checked={enrichBg}
            onChange={setEnrichBg}
            label="enrich background"
          />
        </span>
        {remote ? (
          <span
            className="pf-pill h-9 font-mono text-xs text-text-muted"
            title="Set under Settings → LLM providers. Picking a local model here would override it."
          >
            {effectiveModel || "no model set"}
          </span>
        ) : (
          <select
            className="pf-input h-9 w-auto font-mono text-xs"
            value={effectiveModel}
            onChange={(e) => setModel(e.target.value)}
            disabled={!models.length}
            title="Local model (llama-swap swaps on demand)"
          >
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
            {!models.length && <option value="">no models</option>}
          </select>
        )}
        <button
          type="button"
          className="pf-btn-primary h-9 px-3"
          disabled={!up || anyBusy || !effectiveModel}
          title={
            up
              ? "Send the tags to the local LLM"
              : "Start the LLM server first"
          }
          onClick={() => splitMut.mutate()}
        >
          {splitMut.isPending ? (
            <Loader2 size={14} className="mr-1.5 inline animate-spin" />
          ) : (
            <Wand2 size={14} className="mr-1.5 inline" />
          )}
          Process
        </button>
        {busySecs !== null && (
          <span className="text-xs text-text-subtle">
            {busySecs}s{" "}
            {busySecs > 8 && loaded.length === 0
              ? "— loading the model into VRAM, first call can take a minute"
              : ""}
          </span>
        )}
      </div>

      {/* Text-rendering options — only meaningful while dialogue is on. */}
      {speech && (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded border border-line bg-bg-subtle/40 px-2.5 py-2">
          <span className="text-xs font-medium text-text-muted">Bubble</span>
          <span title="auto = the model decides per scene (conversation gets a bubble, impact lettering does not). bubble = always draw one. no bubble = always suppress it with -1::speech bubble:: so the words sit as bare lettering.">
            <SegmentedControl<BubbleMode>
              options={BUBBLE_OPTS}
              value={bubble}
              onChange={setBubble}
            />
          </span>
          <span className="ml-1 text-xs font-medium text-text-muted">Text</span>
          <span title={'by speaker = written as she says "…" so the image model places the text beside whoever makes it (also works for sounds: a soft "mmm" around her body). placed = rough position on the frame instead (top left, next to her face). free = no position at all, the model chooses.'}>
            <SegmentedControl<TextPosition>
              options={TEXTPOS_OPTS}
              value={textPos}
              onChange={setTextPos}
            />
          </span>
        </div>
      )}

      {allowCompose && (
        <div className="flex items-start gap-2">
          <textarea
            className="pf-input min-h-[52px] flex-1 py-2 text-xs"
            placeholder='Or compose a prompt from an idea — e.g. "2koma meme, 2 characters: me spending all day on a style / the lead dev changing the checkpoint so it stops working"'
            value={idea}
            onChange={(e) => setIdea(e.target.value)}
          />
          <button
            type="button"
            className="pf-btn h-9 shrink-0 px-3"
            disabled={!up || anyBusy || !effectiveModel || !idea.trim()}
            title="Author a full NAI prompt (base + characters + dialogue) from your idea"
            onClick={() => composeMut.mutate()}
          >
            {composeMut.isPending ? (
              <Loader2 size={14} className="mr-1.5 inline animate-spin" />
            ) : (
              <Wand2 size={14} className="mr-1.5 inline" />
            )}
            Compose
          </button>
        </div>
      )}

      {/* result */}
      {result && (
        <div className="space-y-2">
          <div className="text-[11px] text-text-subtle">
            from {sourceLabel} · {result.model} · {result.mode} · {result.secs}s
          </div>
          <div className="rounded border border-line bg-bg-subtle/60 p-2">
            <div className="mb-1 flex items-center justify-between gap-2">
              <span className="pf-label">Base prompt</span>
              <CopyButton text={result.base_prompt} title="Copy base prompt" />
            </div>
            <textarea
              readOnly
              className="pf-input min-h-[64px] py-2 font-mono text-xs"
              value={result.base_prompt}
            />
          </div>
          {result.dialogue ? (
            <div className="rounded border border-line bg-bg-subtle/60 p-2">
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="pf-label">
                  Dialogue{" "}
                  <span className="font-normal normal-case text-text-subtle">
                    — already included in the base prompt
                  </span>
                </span>
                <CopyButton text={result.dialogue} title="Copy dialogue only" />
              </div>
              <textarea
                readOnly
                className="pf-input min-h-[48px] py-2 font-mono text-xs"
                value={result.dialogue}
              />
            </div>
          ) : null}
          {result.characters.map((ch, i) => (
            <div
              key={i}
              className="rounded border border-line bg-bg-subtle/60 p-2"
            >
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="pf-label">
                  Character {i + 1}
                  {ch.name ? (
                    <span className="ml-1 font-normal normal-case text-text-subtle">
                      — {ch.name}
                    </span>
                  ) : null}
                </span>
                <CopyButton
                  text={ch.prompt}
                  title={`Copy character ${i + 1} prompt`}
                />
              </div>
              <textarea
                readOnly
                className="pf-input min-h-[48px] py-2 font-mono text-xs"
                value={ch.prompt}
              />
            </div>
          ))}
          {!result.characters.length && (
            <div className="text-xs text-text-muted">
              No characters detected — everything landed in the base prompt.
            </div>
          )}
        </div>
      )}
    </div>
  );
}

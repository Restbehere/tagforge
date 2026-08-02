/* Settings → LLM providers.
 *
 * Stage 3 (tag classification) and the NAI splitter are configured
 * independently on purpose: this corpus is explicit, and hosted models may
 * refuse or quietly soften it, so users need to send classification to an
 * open-weights endpoint without giving up a local splitter (or the reverse).
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Loader2, PlugZap } from "lucide-react";

import {
  llmApi,
  type LlmFeatureConfig,
  type LlmKind,
  type LlmConfigResponse,
} from "@/lib/api";
import { Panel } from "@/components/Panel";
import { Checkbox, Field } from "@/components/forms";

type Feature = "stage3" | "splitter";

const KIND_LABEL: Record<LlmKind, string> = {
  openai: "OpenAI",
  openai_compatible: "OpenAI-compatible (OpenRouter, Groq, …)",
  anthropic: "Anthropic",
  local: "Local (llama-swap)",
  echo: "Echo (dry run)",
};

const BLURB: Record<Feature, string> = {
  stage3:
    "Buckets leftover tags during classification. Sends batches of ~50 tag names — no images.",
  splitter:
    "Turns a tag list into a NovelAI prompt. Sends the full prompt text, so this is the one most likely to be refused by a hosted model.",
};

export function LlmProviderPanel() {
  const qc = useQueryClient();
  const cfg = useQuery({ queryKey: ["llm", "config"], queryFn: llmApi.getConfig });

  return (
    <Panel
      title="LLM providers"
      description="Point tag classification and the prompt splitter at whichever endpoint you like — OpenAI, any OpenAI-compatible gateway, or a local server. Keys are stored locally and never returned by the API."
    >
      {cfg.isPending && (
        <p className="text-sm text-text-muted">Loading…</p>
      )}
      {cfg.data && (
        <div className="space-y-5">
          {(["stage3", "splitter"] as Feature[]).map((f) => (
            <FeatureForm
              key={f}
              feature={f}
              data={cfg.data}
              // Prefix key refreshes ["llm","config"] AND ["llm","status"],
              // so the splitter panel reflects a provider change straight
              // away instead of on its next 15s poll. refetchType "all"
              // also refreshes the splitter's query while it is unmounted
              // (we are on Settings), so navigating to Builder shows the new
              // endpoint immediately rather than flashing the old one.
              onSaved={() =>
                qc.invalidateQueries({ queryKey: ["llm"], refetchType: "all" })
              }
            />
          ))}
        </div>
      )}
    </Panel>
  );
}

function FeatureForm({
  feature,
  data,
  onSaved,
}: {
  feature: Feature;
  data: LlmConfigResponse;
  onSaved: () => void;
}) {
  const initial = data.config[feature];
  const [form, setForm] = useState<LlmFeatureConfig>(initial);
  const [apiKey, setApiKey] = useState("");
  // Whether the model was typed rather than prefilled. A prefilled name
  // belongs to the provider that supplied it, so it should be replaced on a
  // provider switch; a typed one must never be overwritten.
  const [modelTouched, setModelTouched] = useState(false);

  const saved = JSON.stringify(data.config[feature]);
  const dirty = JSON.stringify(form) !== saved || !!apiKey;
  // Re-sync when the server copy changes (e.g. after the other panel saves),
  // but never over the top of edits in progress — both panels re-render on
  // any save, and this used to discard whatever the other one held.
  useEffect(() => {
    if (!dirty) {
      setForm(data.config[feature]);
      // The typed-vs-prefilled distinction only spans the current unsaved
      // edit. Leaving it set meant a saved model kept surviving later
      // provider switches, so the old provider's model stayed in the box.
      setModelTouched(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [saved, feature]);

  const set = <K extends keyof LlmFeatureConfig>(k: K, v: LlmFeatureConfig[K]) =>
    setForm((p) => ({ ...p, [k]: v }));

  const saveMut = useMutation({
    mutationFn: () =>
      llmApi.putConfig({
        [feature]: { ...form, ...(apiKey ? { api_key: apiKey } : {}) },
      }),
    onSuccess: () => {
      setApiKey("");
      setModelTouched(false);
      toast.success(`${feature === "stage3" ? "Stage 3" : "Splitter"} settings saved`);
      onSaved();
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const testMut = useMutation({
    mutationFn: () => llmApi.testConfig(feature),
    onSuccess: (r) =>
      r.ok ? toast.success(r.detail) : toast.error(r.detail),
    onError: (err: Error) => toast.error(err.message),
  });

  const listId = `models-${feature}`;
  const isLocal = form.kind === "local";
  const isEcho = form.kind === "echo";
  const needsUrl = form.kind === "openai_compatible";
  const hint = data.key_hints[feature];
  // Only llama-swap can pick a model for itself; everywhere else a blank
  // one has nothing to fall back on.
  const needsModel = !isLocal && !isEcho;
  const missingModel = needsModel && !form.model.trim();
  // Same story for the endpoint: only 'local' and 'openai' have one of
  // their own. A blank gateway URL used to fall through to whatever the
  // caller's default was — the local server, or api.openai.com.
  const missingUrl = needsUrl && !form.base_url.trim();
  const incomplete = missingModel || missingUrl;
  const kinds = data.supported_kinds[feature] ?? data.kinds;
  // The stored key belongs to the provider still saved, so the hint does not
  // describe a provider the user has only just selected.
  const kindChanged = form.kind !== data.config[feature].kind;
  const effectiveHint = kindChanged ? "" : hint;
  // Concurrency only bites on the Stage 3 fan-out — the splitter issues one
  // request at a time. Temperature matters to both features (reasoning
  // models reject it), except Anthropic, which pins its own.
  const showConcurrency = feature === "stage3";
  const showTemperature = form.kind !== "anthropic";

  return (
    <div className="rounded border border-line bg-bg-subtle/30 p-3">
      <div className="mb-3">
        <h3 className="text-sm font-semibold">
          {feature === "stage3" ? "Tag classification (Stage 3)" : "NAI prompt splitter"}
        </h3>
        <p className="text-xs text-text-muted">{BLURB[feature]}</p>
      </div>

      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Field label="Provider">
          <select
            className="pf-input"
            value={form.kind}
            onChange={(e) => {
              const kind = e.target.value as LlmKind;
              setApiKey("");
              setForm((p) => ({
                ...p,
                kind,
                // Prefill a model rather than leave a remote provider
                // model-less, and drop one that was itself a prefill — it
                // named the previous provider's model. A typed name stays.
                model: modelTouched
                  ? p.model
                  : data.default_models[kind] || "",
                // Only a gateway has its own endpoint. Carrying one across a
                // switch left an invisible URL steering the request: the
                // field is hidden for every other kind, so an OpenAI-labelled
                // target went on talking to the old gateway — with the
                // OpenAI key attached.
                base_url: kind === "openai_compatible" ? p.base_url : "",
              }));
            }}
          >
            {kinds.map((k) => (
              <option key={k} value={k}>
                {KIND_LABEL[k]}
              </option>
            ))}
          </select>
        </Field>

        <Field
          label={
            <>
              Model{" "}
              <span className="font-normal normal-case text-text-subtle">
                — type any name the endpoint accepts
              </span>
            </>
          }
        >
          <input
            className={`pf-input font-mono text-xs${
              missingModel ? " border-accent-rose/50" : ""
            }`}
            list={listId}
            value={form.model}
            onChange={(e) => {
              setModelTouched(true);
              set("model", e.target.value);
            }}
            // A greyed-out "e.g. gpt-4.1-mini" reads as a filled field at a
            // glance, which is how an empty model got saved and then sent
            // the local model's name to OpenAI. Say it is required instead.
            placeholder={isLocal ? "(server default)" : "required"}
          />
          <datalist id={listId}>
            {(data.suggested_models[feature] ?? []).map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
          {missingModel && (
            <p className="mt-1 text-[11px] text-accent-rose">
              {form.kind === "openai_compatible"
                ? "Required — use the model slug your gateway publishes."
                : "Required — this provider has no default to fall back on."}
            </p>
          )}
        </Field>

        {needsUrl && (
          <Field
            label={
              <>
                Base URL{" "}
                <span className="font-normal normal-case text-text-subtle">
                  — as the provider publishes it
                </span>
              </>
            }
            className="md:col-span-2"
          >
            <input
              className={`pf-input font-mono text-xs${
                missingUrl ? " border-accent-rose/50" : ""
              }`}
              value={form.base_url}
              onChange={(e) => set("base_url", e.target.value)}
              placeholder="https://openrouter.ai/api/v1"
            />
            {missingUrl && (
              <p className="mt-1 text-[11px] text-accent-rose">
                Required — without it, requests fall through to{" "}
                {feature === "stage3" ? "OpenAI" : "the local server"} instead.
              </p>
            )}
          </Field>
        )}

        {!isLocal && !isEcho && (
          <Field
            label={
              <>
                API key{" "}
                <span className="font-normal normal-case text-text-subtle">
                  {effectiveHint ? `— currently ${effectiveHint}` : "— none stored"}
                </span>
              </>
            }
            className="md:col-span-2"
          >
            <input
              type="password"
              className="pf-input font-mono text-xs"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={
                effectiveHint ? "leave blank to keep the stored key" : "sk-…"
              }
              autoComplete="off"
            />
            {kindChanged ? (
              // Keys are stored per provider, so the one on file belongs to
              // the provider still saved — not the one now selected.
              <p className="mt-1 text-[11px] text-text-muted">
                Keys are kept per provider. {KIND_LABEL[form.kind]} has none
                stored yet — enter one before saving.
              </p>
            ) : (
              !effectiveHint &&
              !apiKey && (
                <p className="mt-1 text-[11px] text-text-muted">
                  No key stored — requests go out unauthenticated, which most
                  hosted endpoints reject.
                </p>
              )
            )}
          </Field>
        )}
      </div>

      {(showConcurrency || showTemperature) && (
        <details className="mt-3">
          <summary className="cursor-pointer text-xs text-text-muted hover:text-text">
            Advanced
          </summary>
          <div className="mt-2 flex flex-wrap items-center gap-4">
            {showConcurrency && (
              <label className="flex items-center gap-2 text-xs">
                <span className="text-text-muted">Parallel requests</span>
                <input
                  type="number"
                  min={1}
                  max={12}
                  className="pf-input h-8 w-16"
                  value={form.max_concurrency}
                  // Clamped here, not just by the min/max attributes — those
                  // do not stop typing, and clearing the box yields NaN,
                  // which the API rejects with a raw 422.
                  onChange={(e) => {
                    const n = Number(e.target.value);
                    set(
                      "max_concurrency",
                      Number.isFinite(n) && n >= 1 ? Math.min(12, Math.floor(n)) : 1,
                    );
                  }}
                  title="Free tiers on shared gateways will rate-limit a wide fan-out. 3 is a safe start."
                />
              </label>
            )}
            {showTemperature && (
              <span title="Reasoning models (o-series, gpt-5) reject an explicit temperature and will error on every request. Untick for those.">
                <Checkbox
                  checked={form.send_temperature}
                  onChange={(v) => set("send_temperature", v)}
                  label="send temperature"
                />
              </span>
            )}
          </div>
        </details>
      )}

      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          className="pf-btn-primary h-8 px-3 text-xs"
          disabled={saveMut.isPending || incomplete}
          title={incomplete ? "Fill the required fields first" : undefined}
          onClick={() => saveMut.mutate()}
        >
          {saveMut.isPending ? (
            <Loader2 size={12} className="mr-1 inline animate-spin" />
          ) : null}
          Save
        </button>
        <button
          type="button"
          className="pf-btn h-8 px-3 text-xs"
          disabled={testMut.isPending || incomplete}
          onClick={() => testMut.mutate()}
          title={
            incomplete
              ? "Fill the required fields first"
              : "Send one tiny request so a bad URL, key or model name surfaces now rather than mid-run"
          }
        >
          {testMut.isPending ? (
            <Loader2 size={12} className="mr-1 inline animate-spin" />
          ) : (
            <PlugZap size={12} className="mr-1 inline" />
          )}
          Test
        </button>
      </div>
    </div>
  );
}

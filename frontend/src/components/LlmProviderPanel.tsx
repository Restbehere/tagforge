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
              onSaved={() => qc.invalidateQueries({ queryKey: ["llm", "config"] })}
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

  // Re-sync when the server copy changes (e.g. after another save).
  useEffect(() => setForm(data.config[feature]), [data, feature]);

  const set = <K extends keyof LlmFeatureConfig>(k: K, v: LlmFeatureConfig[K]) =>
    setForm((p) => ({ ...p, [k]: v }));

  const saveMut = useMutation({
    mutationFn: () =>
      llmApi.putConfig({
        [feature]: { ...form, ...(apiKey ? { api_key: apiKey } : {}) },
      }),
    onSuccess: () => {
      setApiKey("");
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
            onChange={(e) => set("kind", e.target.value as LlmKind)}
          >
            {data.kinds.map((k) => (
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
            className="pf-input font-mono text-xs"
            list={listId}
            value={form.model}
            onChange={(e) => set("model", e.target.value)}
            placeholder={isLocal ? "(server default)" : "e.g. gpt-4.1-mini"}
          />
          <datalist id={listId}>
            {(data.suggested_models[feature] ?? []).map((m) => (
              <option key={m} value={m} />
            ))}
          </datalist>
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
              className="pf-input font-mono text-xs"
              value={form.base_url}
              onChange={(e) => set("base_url", e.target.value)}
              placeholder="https://openrouter.ai/api/v1"
            />
          </Field>
        )}

        {!isLocal && !isEcho && (
          <Field
            label={
              <>
                API key{" "}
                <span className="font-normal normal-case text-text-subtle">
                  {hint ? `— currently ${hint}` : "— none stored"}
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
              placeholder={hint ? "leave blank to keep the stored key" : "sk-…"}
              autoComplete="off"
            />
          </Field>
        )}
      </div>

      <details className="mt-3">
        <summary className="cursor-pointer text-xs text-text-muted hover:text-text">
          Advanced
        </summary>
        <div className="mt-2 flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-2 text-xs">
            <span className="text-text-muted">Parallel requests</span>
            <input
              type="number"
              min={1}
              max={12}
              className="pf-input h-8 w-16"
              value={form.max_concurrency}
              onChange={(e) => set("max_concurrency", Number(e.target.value))}
              title="Free tiers on shared gateways will rate-limit a wide fan-out. 3 is a safe start."
            />
          </label>
          <span
            title="Reasoning models (o-series, gpt-5) reject an explicit temperature and will error on every request. Untick for those."
          >
            <Checkbox
              checked={form.send_temperature}
              onChange={(v) => set("send_temperature", v)}
              label="send temperature"
            />
          </span>
        </div>
      </details>

      <div className="mt-3 flex items-center gap-2">
        <button
          type="button"
          className="pf-btn-primary h-8 px-3 text-xs"
          disabled={saveMut.isPending}
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
          disabled={testMut.isPending}
          onClick={() => testMut.mutate()}
          title="Send one tiny request so a bad URL, key or model name surfaces now rather than mid-run"
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

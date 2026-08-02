/* Rig — turn a decomposed PSD into a live 2.5D avatar.
 *
 * Embeds a vendored, English-translated copy of Anime2.5DRig (MIT, by
 * 852wa — github.com/852wa/Anime2.5DRig), which auto-rigs see-through
 * style layered PSDs entirely client-side: blinking, lip-sync, hair
 * physics, webcam tracking. Tag Forge feeds it PSDs from the Decompose
 * tab via postMessage; dropping any .psd straight onto the viewer works
 * too (that part is handled by the embedded app itself).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Loader2, PersonStanding } from "lucide-react";
import { toast } from "sonner";

import {
  api,
  decomposeInputUrl,
  decomposePsdUrl,
  type DecompItemRow,
} from "@/lib/api";
import { cn } from "@/lib/cn";

const RIG_URL = "/anime25drig/index.html";

export function Rig() {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [ready, setReady] = useState(false);
  const [loadingId, setLoadingId] = useState<number | null>(null);
  const [loadedId, setLoadedId] = useState<number | null>(null);

  const items = useQuery({
    queryKey: ["decompose", "items"],
    queryFn: () => api.decomposeItems(200),
  });
  const done = (items.data?.items ?? []).filter(
    (r) => r.status === "done" && r.has_psd,
  );

  // The item we sent and are waiting on; resolved by the iframe's
  // rig-load-done ack (or the safety timeout).
  const pendingRef = useRef<{ id: number; timer: number } | null>(null);

  const settlePending = useCallback((error: string | null) => {
    const pending = pendingRef.current;
    if (!pending) return;
    window.clearTimeout(pending.timer);
    pendingRef.current = null;
    setLoadingId(null);
    if (error) toast.error(error);
    else setLoadedId(pending.id);
  }, []);

  // The embedded app announces itself once its scripts are up, and acks
  // every injected PSD with rig-load-done {error}.
  useEffect(() => {
    function onMsg(e: MessageEvent) {
      if (e.origin !== window.location.origin) return;
      const data = e.data as { type?: string; error?: string | null } | null;
      if (data?.type === "rig-ready") setReady(true);
      if (data?.type === "rig-load-done") settlePending(data.error ?? null);
    }
    window.addEventListener("message", onMsg);
    return () => window.removeEventListener("message", onMsg);
  }, [settlePending]);

  useEffect(
    () => () => {
      if (pendingRef.current) window.clearTimeout(pendingRef.current.timer);
    },
    [],
  );

  const sendPsd = useCallback(
    async (item: Pick<DecompItemRow, "id">) => {
      const win = iframeRef.current?.contentWindow;
      if (!win) {
        toast.error("The rig viewer has not loaded yet");
        return;
      }
      setLoadingId(item.id);
      try {
        const res = await fetch(decomposePsdUrl(item.id));
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const buf = await res.arrayBuffer();
        // Loading stays on until the viewer acks (parse can block for a
        // few seconds); the timeout is a backstop against a dead viewer.
        pendingRef.current = {
          id: item.id,
          timer: window.setTimeout(
            () =>
              settlePending(
                "The rig viewer did not respond — try reloading the page",
              ),
            60_000,
          ),
        };
        win.postMessage(
          { type: "loadPsd", buffer: buf },
          window.location.origin,
          [buf],
        );
      } catch (err) {
        setLoadingId(null);
        toast.error(`Could not load the PSD: ${(err as Error).message}`);
      }
    },
    [settlePending],
  );

  // Deep link: /rig?item=<id> auto-loads that decomposition once ready.
  // Items older than the picker's 200-row window are fetched individually.
  const autoLoaded = useRef(false);
  useEffect(() => {
    if (!ready || autoLoaded.current || items.data === undefined) return;
    const wanted = new URLSearchParams(window.location.search).get("item");
    autoLoaded.current = true;
    if (!wanted) return;
    const item = done.find((r) => r.id === Number(wanted));
    if (item) {
      void sendPsd(item);
      return;
    }
    void api
      .decomposeItem(Number(wanted))
      .then((d) => {
        if (d.status === "done" && d.has_psd) void sendPsd({ id: d.id });
        else toast.error(`Decomposition #${wanted} has no PSD to rig`);
      })
      .catch(() => toast.error(`Decomposition #${wanted} was not found`));
  }, [ready, items.data, done, sendPsd]);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <h1 className="pf-section-title flex items-center gap-2">
          <PersonStanding size={16} className="text-text-muted" />
          Rig — live 2.5D avatar
        </h1>
        <span
          className="flex items-center gap-1.5 text-[11px] text-text-subtle"
          title={
            ready
              ? "viewer loaded"
              : "waiting for the embedded viewer to load…"
          }
        >
          <span
            className={cn(
              "inline-block h-1.5 w-1.5 rounded-full",
              ready ? "bg-accent-green" : "bg-accent-amber",
            )}
          />
          {ready ? "viewer ready" : "loading viewer…"}
        </span>
        <div className="ml-auto flex items-center gap-3">
          <span className="text-[11px] text-text-subtle">
            powered by{" "}
            <a
              href="https://github.com/852wa/Anime2.5DRig"
              target="_blank"
              rel="noopener noreferrer"
              className="underline hover:text-text"
            >
              Anime2.5DRig
            </a>{" "}
            (MIT)
          </span>
          <a
            href={RIG_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="pf-btn-ghost flex h-7 items-center gap-1 px-2 text-xs"
            title="Open the viewer alone in a new tab"
          >
            <ExternalLink size={12} />
            standalone
          </a>
        </div>
      </div>

      {done.length > 0 ? (
        <div className="flex items-center gap-2 overflow-x-auto pb-1">
          <span className="shrink-0 text-[11px] uppercase tracking-wide text-text-subtle">
            Load a decomposition:
          </span>
          {done.map((r) => (
            <button
              key={r.id}
              type="button"
              disabled={!ready || loadingId !== null}
              className={cn(
                "relative shrink-0 overflow-hidden rounded-md border transition disabled:opacity-50",
                loadedId === r.id
                  ? "border-brand ring-1 ring-brand"
                  : "border-line hover:border-brand",
              )}
              title={`${r.original_name} — rig this character`}
              onClick={() => void sendPsd(r)}
            >
              <img
                src={decomposeInputUrl(r.id, r.created_at)}
                alt={r.original_name}
                loading="lazy"
                className="h-14 w-14 object-cover"
              />
              {loadingId === r.id && (
                <span className="absolute inset-0 grid place-items-center bg-bg/60">
                  <Loader2 size={16} className="animate-spin text-text" />
                </span>
              )}
            </button>
          ))}
          <span className="shrink-0 text-[11px] text-text-subtle">
            …or drop any .psd straight onto the viewer
          </span>
        </div>
      ) : items.isError ? (
        <div className="flex items-center gap-2 text-xs text-accent-rose">
          Couldn't reach the backend to list decompositions.
          <button
            type="button"
            className="pf-btn-ghost h-6 px-2 text-xs"
            onClick={() => void items.refetch()}
          >
            retry
          </button>
        </div>
      ) : (
        <div className="text-xs text-text-muted">
          No finished decompositions yet — run one on the Decompose tab, or
          drop any layered .psd straight onto the viewer below.
        </div>
      )}

      <iframe
        ref={iframeRef}
        src={RIG_URL}
        title="Anime2.5DRig viewer"
        allow="camera; microphone"
        // onLoad backstop: by the load event the embedded script has run,
        // so the picker can't get stuck if the rig-ready message is missed.
        onLoad={() => setReady(true)}
        className="w-full rounded-md border border-line bg-black/20"
        style={{ height: "calc(100vh - 230px)", minHeight: 480 }}
      />
      <p className="text-[11px] text-text-subtle">
        Everything runs locally in your browser. Camera tracking downloads
        MediaPipe from a CDN when first enabled (needs internet + camera
        permission). Use the viewer&apos;s README button for the full layer
        naming guide.
      </p>
    </div>
  );
}

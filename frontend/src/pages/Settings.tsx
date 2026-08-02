import { useEffect, useState } from "react";
import { Check, HardDrive, Monitor, Moon, Plus, Sun, Trash2, X } from "lucide-react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { Panel } from "@/components/Panel";
import { LlmProviderPanel } from "@/components/LlmProviderPanel";
import { ConfirmButton } from "@/components/forms";
import { api } from "@/lib/api";
import { ACCENTS, useTheme, type ThemeMode } from "@/lib/theme";
import { cn } from "@/lib/cn";
import pkg from "../../package.json";

function mb(sizeBytes: number): string {
  return `${(sizeBytes / 1048576).toFixed(1)} MB`;
}

const MODES: { id: ThemeMode; label: string; icon: typeof Sun; hint: string }[] = [
  { id: "light", label: "Light", icon: Sun, hint: "Always light" },
  { id: "dark", label: "Dark", icon: Moon, hint: "Always dark" },
  { id: "system", label: "System", icon: Monitor, hint: "Follow Windows setting" },
];

export function Settings() {
  const { mode, setMode, accent, setAccent } = useTheme();

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="text-sm text-text-muted">
          Appearance and app information.
        </p>
      </div>

      <Panel title="Appearance">
        <div className="space-y-6">
          <div>
            <div className="pf-label mb-2">Theme</div>
            <div className="inline-flex rounded-lg border border-line bg-bg-subtle p-0.5 text-xs">
              {MODES.map(({ id, label, icon: Icon, hint }) => (
                <button
                  key={id}
                  onClick={() => setMode(id)}
                  title={hint}
                  className={cn(
                    "flex items-center gap-1.5 rounded-md px-3 py-1.5 transition-colors",
                    mode === id
                      ? "bg-brand text-brand-fg"
                      : "text-text-muted hover:text-text",
                  )}
                >
                  <Icon size={12} /> {label}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="pf-label mb-2">Accent color</div>
            <div className="flex gap-3">
              {ACCENTS.map((a) => (
                <button
                  key={a.id}
                  title={a.label}
                  onClick={() => setAccent(a.id)}
                  style={{ background: a.swatch }}
                  className={cn(
                    "grid h-8 w-8 place-items-center rounded-full transition",
                    accent === a.id
                      ? "ring-2 ring-brand ring-offset-2 ring-offset-bg"
                      : "hover:scale-110",
                  )}
                >
                  {accent === a.id && (
                    <Check size={14} className="text-white" />
                  )}
                </button>
              ))}
            </div>
            <p className="mt-2 text-[11px] text-text-subtle">
              Used for buttons, highlights, focus rings, and the trends chart.
            </p>
          </div>
        </div>
      </Panel>

      <MaintenancePanel />

      <LlmProviderPanel />

      <Panel title="About">
        <dl className="space-y-2 text-sm">
          <InfoRow label="App" value={`Tag Forge v${pkg.version}`} />
          <InfoRow label="Backend" value="127.0.0.1:9301" mono />
          <InfoRow label="Frontend dev server" value=":9300" mono />
          <InfoRow label="Data" value="Local SQLite — nothing leaves this machine" />
        </dl>
        {/* CC BY 3.0 requires the credit to travel with the work. */}
        <p className="mt-4 border-t border-line pt-3 text-xs text-text-muted">
          Logomark adapted from{" "}
          <a
            href="https://thenounproject.com/icon/forge-1044767/"
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2 hover:text-text"
          >
            “forge” by Monjin Friends
          </a>{" "}
          (Noun Project), used under{" "}
          <a
            href="https://creativecommons.org/licenses/by/3.0/"
            target="_blank"
            rel="noopener noreferrer"
            className="underline underline-offset-2 hover:text-text"
          >
            CC BY 3.0
          </a>
          .
        </p>
      </Panel>
    </div>
  );
}

function MaintenancePanel() {
  const qc = useQueryClient();

  const backupsQuery = useQuery({
    queryKey: ["backups"],
    queryFn: api.listBackups,
  });

  const backupMut = useMutation({
    mutationFn: api.runBackup,
    onSuccess: (row) => {
      toast.success(`Backup written — ${mb(row.size_bytes)}`);
      const failed = (row.mirrors ?? []).filter((m) => !m.ok);
      if (failed.length > 0) {
        toast.warning(
          `Mirror copy failed: ${failed.map((m) => m.dir).join(", ")}`,
        );
      }
      qc.invalidateQueries({ queryKey: ["backups"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const deleteMut = useMutation({
    mutationFn: (name: string) => api.deleteBackup(name),
    onSuccess: (_res, name) => {
      toast.success(`deleted ${name}`);
      qc.invalidateQueries({ queryKey: ["backups"] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const configQuery = useQuery({
    queryKey: ["backup-config"],
    queryFn: api.getBackupConfig,
  });

  const [dirs, setDirs] = useState<string[]>([]);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (configQuery.data && !dirty) {
      setDirs(configQuery.data.mirror_dirs);
    }
  }, [configQuery.data, dirty]);

  const saveConfigMut = useMutation({
    mutationFn: (mirror_dirs: string[]) => api.putBackupConfig(mirror_dirs),
    onSuccess: (cfg) => {
      toast.success("Mirror locations saved");
      // Seed the cache from the PUT response synchronously — invalidating
      // instead would let the sync effect re-run against the STALE cached
      // config and flash the editor back to pre-save values.
      qc.setQueryData(["backup-config"], cfg);
      setDirty(false);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const backups = backupsQuery.data ?? [];

  return (
    <Panel title="Maintenance" description="Snapshot the SQLite database.">
      <div className="space-y-4">
        <button
          type="button"
          className="pf-btn-primary"
          disabled={backupMut.isPending}
          onClick={() => backupMut.mutate()}
        >
          <HardDrive size={14} />
          {backupMut.isPending ? "Backing up…" : "Back up database"}
        </button>

        {backups.length === 0 ? (
          <p className="text-sm text-text-muted">No backups yet.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {backups.map((b) => (
              <li
                key={b.name}
                className="flex items-center justify-between gap-3 border-b border-line/60 pb-2 last:border-0 last:pb-0"
              >
                <span className="min-w-0 truncate font-mono text-xs">
                  {b.name}
                </span>
                <span className="flex shrink-0 items-center gap-3">
                  <span className="text-xs text-text-muted">
                    {mb(b.size_bytes)}
                  </span>
                  <span className="text-xs text-text-subtle">
                    {new Date(b.created_at).toLocaleString()}
                  </span>
                  <ConfirmButton
                    className="pf-btn-ghost h-6 px-2 text-[11px]"
                    confirmLabel="delete?"
                    title={`Delete ${b.name}`}
                    disabled={deleteMut.isPending}
                    onConfirm={() => deleteMut.mutate(b.name)}
                  >
                    <Trash2 size={12} />
                  </ConfirmButton>
                </span>
              </li>
            ))}
          </ul>
        )}

        <p className="text-[11px] text-text-subtle">
          Backups are written to backend/data/backups/ on this machine.
        </p>

        <div className="space-y-2 border-t border-line/60 pt-4">
          <div className="pf-label">Mirror locations</div>

          {dirs.map((dir, i) => (
            <div key={i} className="flex items-center gap-2">
              <input
                className="pf-input flex-1 font-mono text-xs"
                value={dir}
                placeholder="D:\backups\tagforge"
                onChange={(e) => {
                  setDirs(dirs.map((d, j) => (j === i ? e.target.value : d)));
                  setDirty(true);
                }}
              />
              <button
                type="button"
                className="pf-btn-ghost h-7 px-2"
                title="Remove location"
                onClick={() => {
                  setDirs(dirs.filter((_, j) => j !== i));
                  setDirty(true);
                }}
              >
                <X size={12} />
              </button>
            </div>
          ))}

          <div className="flex items-center gap-2">
            <button
              type="button"
              className="pf-btn"
              onClick={() => {
                setDirs([...dirs, ""]);
                setDirty(true);
              }}
            >
              <Plus size={12} />
              add location
            </button>
            <button
              type="button"
              className="pf-btn-primary"
              // isError guard: with the config unloaded the editor shows
              // zero rows — saving from that state would wipe the mirrors.
              disabled={
                saveConfigMut.isPending ||
                configQuery.isLoading ||
                configQuery.isError
              }
              onClick={() =>
                saveConfigMut.mutate(
                  dirs.map((d) => d.trim()).filter(Boolean),
                )
              }
            >
              {saveConfigMut.isPending ? "Saving…" : "Save locations"}
            </button>
          </div>

          <p className="text-[11px] text-text-subtle">
            Every backup is also copied to these folders (e.g. a second drive);
            failures there never block the primary backup.
          </p>
          {configQuery.data && (
            <p className="text-[11px] text-text-muted">
              Auto-backup: runs when you open the app if the last backup is
              older than {configQuery.data.auto_days} days.
            </p>
          )}
        </div>
      </div>
    </Panel>
  );
}

function InfoRow({
  label,
  value,
  mono,
}: {
  label: string;
  value: string;
  mono?: boolean;
}) {
  return (
    <div className="flex items-center justify-between border-b border-line/60 pb-2 last:border-0 last:pb-0">
      <dt className="text-text-muted">{label}</dt>
      <dd className={mono ? "font-mono text-xs" : ""}>{value}</dd>
    </div>
  );
}

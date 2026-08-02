/* Compact saved-preset control: pick to apply, save/overwrite/delete.
 * Backed by the generic /api/presets store; used by the Ingest booru card
 * and the Export filters. */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Save, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { api, type PresetRow } from "@/lib/api";
import { ConfirmButton } from "@/components/forms";
import { cn } from "@/lib/cn";

export function PresetPicker({
  kind,
  getSnapshot,
  applySnapshot,
  className,
}: {
  kind: string;
  getSnapshot: () => Record<string, unknown>;
  applySnapshot: (data: Record<string, unknown>) => void;
  className?: string;
}) {
  const qc = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | "new" | "">("");
  const [saveAsName, setSaveAsName] = useState("");

  const presets = useQuery({
    queryKey: ["presets", kind],
    queryFn: () => api.listPresets(kind),
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["presets", kind] });

  const createMut = useMutation({
    mutationFn: ({ name }: { name: string }) =>
      api.createPreset(kind, name, getSnapshot()),
    onSuccess: (row) => {
      invalidate();
      setSelectedId(row.id);
      setSaveAsName("");
      toast.success(`saved preset "${row.name}"`);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const updateMut = useMutation({
    mutationFn: ({ id }: { id: number; name: string }) =>
      api.updatePreset(id, getSnapshot()),
    onSuccess: (_row, vars) => {
      invalidate();
      toast.success(`updated preset "${vars.name}"`);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const deleteMut = useMutation({
    mutationFn: ({ id }: { id: number; name: string }) => api.deletePreset(id),
    onSuccess: (_res, vars) => {
      invalidate();
      setSelectedId("");
      toast.success(`deleted preset "${vars.name}"`);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const pending =
    createMut.isPending || updateMut.isPending || deleteMut.isPending;
  const selected =
    typeof selectedId === "number"
      ? (presets.data ?? []).find((p) => p.id === selectedId)
      : undefined;

  function onSelect(value: string) {
    if (value === "") {
      setSelectedId("");
      return;
    }
    if (value === "new") {
      setSelectedId("new");
      return;
    }
    const preset = (presets.data ?? []).find((p) => p.id === Number(value));
    if (preset) {
      setSelectedId(preset.id);
      applySnapshot(preset.data);
      toast.success(`applied preset "${preset.name}"`);
    }
  }

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      <select
        className="pf-input h-8 w-44 text-xs"
        value={String(selectedId)}
        onChange={(e) => onSelect(e.target.value)}
        disabled={pending}
      >
        <option value="">— presets —</option>
        <option value="new">+ save current as…</option>
        {(presets.data ?? []).map((p: PresetRow) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>

      {selectedId === "new" && (
        <>
          <input
            className="pf-input h-8 w-36 text-xs"
            placeholder="preset name…"
            value={saveAsName}
            onChange={(e) => setSaveAsName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                if (saveAsName.trim())
                  createMut.mutate({ name: saveAsName.trim() });
              }
            }}
          />
          <button
            type="button"
            className="pf-btn h-8 gap-1 text-[11px]"
            disabled={pending || !saveAsName.trim()}
            onClick={() => createMut.mutate({ name: saveAsName.trim() })}
          >
            <Save size={11} /> save
          </button>
        </>
      )}

      {selected && (
        <>
          <ConfirmButton
            className="pf-btn h-8 gap-1 text-[11px]"
            confirmLabel="overwrite?"
            disabled={pending}
            title={`Overwrite "${selected.name}" with the current settings`}
            onConfirm={() =>
              updateMut.mutate({ id: selected.id, name: selected.name })
            }
          >
            <Save size={11} /> update
          </ConfirmButton>
          <ConfirmButton
            className="pf-btn h-8 gap-1 text-[11px]"
            confirmLabel="delete?"
            disabled={pending}
            title={`Delete preset "${selected.name}"`}
            onConfirm={() =>
              deleteMut.mutate({ id: selected.id, name: selected.name })
            }
          >
            <Trash2 size={11} />
          </ConfirmButton>
        </>
      )}
    </div>
  );
}

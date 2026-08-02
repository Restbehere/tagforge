import { useEffect } from "react";
import { Link, useLocation } from "wouter";
import {
  LayoutDashboard,
  Download,
  Database,
  Tag,
  Wand2,
  Layers,
  LineChart,
  PersonStanding,
  Telescope,
  Settings as SettingsIcon,
  Sun,
  Moon,
} from "lucide-react";
import { toast } from "sonner";

import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useTheme } from "@/lib/theme";
import { GlobalJobTray } from "@/components/GlobalJobTray";
import { TagForgeMark } from "@/components/TagForgeMark";
import pkg from "../../package.json";

/* Module-level guard so the auto-backup fires once per app load, even under
 * StrictMode's double-mount in dev. */
let autoBackupTriggered = false;

interface NavItem {
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
}

const NAV: NavItem[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/ingest", label: "Ingest", icon: Telescope },
  { to: "/scenes", label: "Scenes", icon: Database },
  { to: "/tags", label: "Tags", icon: Tag },
  { to: "/trends", label: "Trends", icon: LineChart },
  { to: "/builder", label: "Builder", icon: Wand2 },
  { to: "/decompose", label: "Decompose", icon: Layers },
  { to: "/rig", label: "Rig", icon: PersonStanding },
  { to: "/export", label: "Export", icon: Download },
];

export function Layout({ children }: { children: React.ReactNode }) {
  const [location] = useLocation();
  const { resolvedTheme, setMode } = useTheme();

  useEffect(() => {
    if (autoBackupTriggered) return;
    autoBackupTriggered = true;
    const attempt = (retriesLeft: number) => {
      api
        .autoBackup()
        .then((r) => {
          if (r.backed_up) {
            toast.success(
              "weekly auto-backup created" +
                (r.size_bytes
                  ? ` (${(r.size_bytes / 1048576).toFixed(1)} MB)`
                  : ""),
            );
          }
        })
        .catch(() => {
          // Backend may still be cold-starting (dev.bat launches both
          // servers together) — retry a few times before giving up.
          if (retriesLeft > 0) {
            window.setTimeout(() => attempt(retriesLeft - 1), 10_000);
          }
        });
    };
    attempt(3);
  }, []);

  return (
    <div className="flex h-screen overflow-hidden">
      <div className="pf-dots" aria-hidden />
      <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-bg-panel/50">
        <div className="flex items-center gap-2.5 px-4 py-4">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-brand text-brand-fg shadow-md">
            <TagForgeMark size={26} />
          </div>
          <div className="font-display text-[22px] font-bold leading-none tracking-tight">
            Tag Forge
          </div>
        </div>

        <nav className="flex-1 space-y-0.5 px-2 py-2">
          {NAV.map((item) => {
            const Icon = item.icon;
            const active =
              location === item.to ||
              (item.to !== "/" && location.startsWith(item.to));
            return (
              <Link
                key={item.to}
                href={item.to}
                className={cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition",
                  active
                    ? "bg-brand/15 text-text"
                    : "text-text-muted hover:bg-bg-subtle hover:text-text",
                )}
              >
                <Icon size={16} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="space-y-0.5 px-2 pb-2">
          <Link
            href="/settings"
            className={cn(
              "flex items-center gap-3 rounded-md px-3 py-2 text-sm transition",
              location.startsWith("/settings")
                ? "bg-brand/15 text-text"
                : "text-text-muted hover:bg-bg-subtle hover:text-text",
            )}
          >
            <SettingsIcon size={16} />
            <span className="flex-1">Settings</span>
            <button
              className="rounded p-1 text-text-subtle transition hover:bg-bg-hover hover:text-text"
              title={`Switch to ${resolvedTheme === "dark" ? "light" : "dark"} mode`}
              onClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setMode(resolvedTheme === "dark" ? "light" : "dark");
              }}
            >
              {resolvedTheme === "dark" ? <Sun size={13} /> : <Moon size={13} />}
            </button>
          </Link>
        </div>

        <div className="border-t border-line p-3 text-[11px] leading-snug text-text-subtle">
          Backend at <span className="font-mono">127.0.0.1:9301</span>
          <br />
          Frontend dev <span className="font-mono">:9300</span>
          <br />
          Version <span className="font-mono">v{pkg.version}</span>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1400px] px-6 py-6">{children}</div>
      </main>

      <GlobalJobTray />
    </div>
  );
}

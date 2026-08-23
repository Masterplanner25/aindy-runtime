import { useState, type ComponentType, type SVGProps } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { useAuth } from "@aindy/ui-kit";
import { useSystem } from "@aindy/ui-kit";
import { FEATURE_FLAGS } from "../../api/_routes.js";

// ── Icons (inline, no dependency) ──────────────────────────────────────────────
type IconProps = SVGProps<SVGSVGElement>;
const Icon =
  (path: string): ComponentType<IconProps> =>
  (props: IconProps) =>
    (
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
        width={18}
        height={18}
        {...props}
      >
        <path d={path} />
      </svg>
    );

const IconAgent = Icon("M12 2a5 5 0 0 1 5 5v1a5 5 0 0 1-10 0V7a5 5 0 0 1 5-5ZM5 21a7 7 0 0 1 14 0");
const IconFlows = Icon("M4 6h6M14 6h6M7 6v12M17 6v6M4 18h6M14 12h6");
const IconObs = Icon("M3 12h4l3 8 4-16 3 8h4");
const IconHealth = Icon("M3 12h3l2-5 4 10 2-5h7");
const IconExec = Icon("M8 5l8 7-8 7M4 5v14");
const IconApprovals = Icon("M9 12l2 2 4-4M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z");
const IconRegistry = Icon("M4 4h16v6H4zM4 14h16v6H4z");
const IconUsers = Icon("M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM23 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75");
const IconWebhooks = Icon("M12 3a4 4 0 0 1 3.5 5.9l2.6 4.5M18 21a4 4 0 0 1-3.5-6H9.3M6 9a4 4 0 1 0 4 6.5l2.5-4.4");
const IconDlq = Icon("M4 6h16M6 6v12a2 2 0 0 0 2 2h8a2 2 0 0 0 2-2V6M10 11v5M14 11v5M9 6V4h6v2");
const IconTrace = Icon("M5 12a7 7 0 0 1 14 0M9 12a3 3 0 0 1 6 0M12 12v.01");
const IconLogout = Icon("M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9");
const IconChevron = Icon("M15 18l-6-6 6-6");

// ── Nav model ───────────────────────────────────────────────────────────────
interface NavItem {
  to: string;
  label: string;
  icon: ComponentType<IconProps>;
  /** false ⇒ this screen is not part of runtime-only surface (marked in sidebar). */
  runtime: boolean;
  /** If false, the NavLink is hidden entirely (route stays mounted). Gate on FEATURE_FLAGS key. */
  featureFlag?: boolean;
}

const NAV_ITEMS: NavItem[] = [
  { to: "/agent",        label: "Agent Console",  icon: IconAgent,     runtime: false },
  { to: "/flows",        label: "Flow Engine",    icon: IconFlows,     runtime: true  },
  { to: "/observability",label: "Observability",  icon: IconObs,       runtime: true  },
  { to: "/health",       label: "Health",         icon: IconHealth,    runtime: true  },
  { to: "/executions",   label: "Executions",     icon: IconExec,      runtime: true  },
  { to: "/approvals",    label: "Approvals",      icon: IconApprovals, runtime: false },
  { to: "/registry",     label: "Agent Registry", icon: IconRegistry,  runtime: false },
  { to: "/users",        label: "Users",          icon: IconUsers,     runtime: true  },
  { to: "/webhooks",     label: "Webhooks",       icon: IconWebhooks,  runtime: true  },
  { to: "/dead-letters", label: "Dead Letters",   icon: IconDlq,       runtime: true  },
  { to: "/trace",        label: "RippleTrace",    icon: IconTrace,     runtime: false, featureFlag: FEATURE_FLAGS.RIPPLETRACE_VIEWER },
];

export default function PlatformShell() {
  const { user, logout } = useAuth();
  const { system } = useSystem();
  const runtimeOnly = system?.runtime?.boot_mode === "runtime-only";

  // Collapse persists across reloads in the real SPA; falls back gracefully.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem("aindy.nav.collapsed") === "1";
    } catch {
      return false;
    }
  });

  const toggleCollapsed = () => {
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem("aindy.nav.collapsed", next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  const handleSignOut = () => {
    // logout() clears the stored token and nulls auth state; PlatformGuard then
    // redirects to /login on the next render. No manual navigate needed.
    logout();
  };

  const width = collapsed ? "w-16" : "w-60";

  return (
    <div className="flex h-full min-h-0 bg-zinc-950 text-zinc-100">
      {/* ── Sidebar ─────────────────────────────────────────────── */}
      <aside
        className={`${width} flex-shrink-0 flex flex-col border-r border-zinc-800/60 bg-zinc-900/40 transition-[width] duration-200 ease-out`}
      >
        {/* Brand + collapse toggle */}
        <div className="flex items-center gap-2 h-14 px-3 border-b border-zinc-800/60">
          {!collapsed && (
            <span className="flex-1 truncate font-black tracking-tight text-sm">
              <span className="text-[#00ffaa]">A.I.N.D.Y.</span>
              <span className="text-zinc-500 font-medium"> Platform</span>
            </span>
          )}
          <button
            onClick={toggleCollapsed}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            className="ml-auto p-1.5 rounded-md text-zinc-500 hover:text-zinc-200 hover:bg-zinc-800/60 transition-colors"
          >
            <IconChevron
              className={`transition-transform duration-200 ${collapsed ? "rotate-180" : ""}`}
            />
          </button>
        </div>

        {/* Nav links */}
        <nav className="flex-1 overflow-y-auto custom-scrollbar py-3 px-2 space-y-1">
          {NAV_ITEMS.filter(({ featureFlag }) => featureFlag !== false).map(({ to, label, icon: ItemIcon, runtime }) => {
            const dimmed = runtimeOnly && !runtime;
            return (
              <NavLink
                key={to}
                to={to}
                title={collapsed ? label : undefined}
                className={({ isActive }) =>
                  [
                    "group relative flex items-center gap-3 rounded-lg px-3 py-2 text-xs font-medium transition-colors",
                    isActive
                      ? "bg-[#00ffaa]/10 text-[#00ffaa] border border-[#00ffaa]/20"
                      : "text-zinc-400 hover:text-zinc-100 hover:bg-zinc-800/40 border border-transparent",
                    dimmed ? "opacity-60" : "",
                  ].join(" ")
                }
              >
                <ItemIcon className="flex-shrink-0" />
                {!collapsed && (
                  <span className="flex-1 truncate flex items-center gap-2">
                    {label}
                    {dimmed && (
                      <span
                        title="Available in full deployment (requires aindy-apps-monolith)"
                        className="text-[8px] uppercase tracking-wider text-zinc-600 border border-zinc-700/60 rounded px-1 py-0.5 cursor-help">
                        app
                      </span>
                    )}
                  </span>
                )}
              </NavLink>
            );
          })}
        </nav>

        {/* Footer: user + sign-out */}
        <div className="border-t border-zinc-800/60 p-2">
          {!collapsed && user?.email && (
            <p className="px-2 pb-2 text-[10px] text-zinc-500 truncate" title={user.email}>
              {user.email}
            </p>
          )}
          <button
            onClick={handleSignOut}
            title={collapsed ? "Sign out" : undefined}
            className="w-full flex items-center gap-3 rounded-lg px-3 py-2 text-xs font-medium text-zinc-400 hover:text-red-300 hover:bg-red-500/10 transition-colors"
          >
            <IconLogout className="flex-shrink-0" />
            {!collapsed && <span>Sign out</span>}
          </button>
        </div>
      </aside>

      {/* ── Main content ─────────────────────────────────────────── */}
      <main className="flex-1 min-w-0 overflow-y-auto custom-scrollbar p-6">
        <Outlet />
      </main>
    </div>
  );
}

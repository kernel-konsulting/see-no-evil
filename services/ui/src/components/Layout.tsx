import { NavLink, Outlet } from "react-router-dom";
import {
  LayoutDashboard,
  Monitor,
  Shield,
  ScrollText,
  ShieldAlert,
  ShieldCheck,
  Settings as SettingsIcon,
  Users as UsersIcon,
  LogOut,
} from "lucide-react";
import { useAuth } from "@/lib/use-auth";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

const NAV_ITEMS: ReadonlyArray<{
  to: string;
  label: string;
  icon: typeof LayoutDashboard;
  adminOnly?: boolean;
}> = [
  { to: "/dashboard", label: "Dashboard", icon: LayoutDashboard },
  { to: "/devices", label: "Devices", icon: Monitor, adminOnly: true },
  { to: "/profiles", label: "Profiles", icon: Shield, adminOnly: true },
  {
    to: "/quarantine",
    label: "Quarantine",
    icon: ShieldAlert,
    adminOnly: true,
  },
  { to: "/audit", label: "Audit Log", icon: ScrollText },
  { to: "/settings", label: "Settings", icon: SettingsIcon, adminOnly: true },
  { to: "/users", label: "Users", icon: UsersIcon, adminOnly: true },
  { to: "/setup", label: "Device setup", icon: ShieldCheck, adminOnly: true },
];

export default function Layout() {
  const { logout, role, me, isAdmin } = useAuth();
  const items = NAV_ITEMS.filter((item) => !item.adminOnly || isAdmin);

  return (
    <div className="flex min-h-screen">
      {/* Sidebar */}
      <aside className="w-56 flex-shrink-0 border-r bg-muted/40 flex flex-col">
        <div className="px-6 py-5 border-b">
          <span className="font-semibold text-lg tracking-tight">
            see-no-evil
          </span>
          {me && (
            <div className="mt-1 text-xs text-muted-foreground truncate">
              {me.email}{" "}
              <span className="ml-1 inline-block px-1.5 py-0.5 rounded bg-muted text-[10px] uppercase tracking-wide">
                {role ?? "user"}
              </span>
            </div>
          )}
        </div>
        <nav className="flex-1 py-4 px-3 space-y-1">
          {items.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary text-primary-foreground"
                    : "text-muted-foreground hover:bg-accent hover:text-accent-foreground",
                )
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>
        <div className="p-3 border-t">
          <Button
            variant="ghost"
            className="w-full justify-start gap-3 text-muted-foreground"
            onClick={logout}
          >
            <LogOut className="h-4 w-4" />
            Sign out
          </Button>
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto p-8">
        <Outlet />
      </main>
    </div>
  );
}

import { useQuery } from "@tanstack/react-query";
import {
  getHealth,
  getDashboardStats,
  listAudit,
  type WindowStats,
} from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Monitor,
  ShieldCheck,
  AlertTriangle,
  Activity,
  ShieldAlert,
} from "lucide-react";

export default function Dashboard() {
  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
  });
  const { data: stats } = useQuery({
    queryKey: ["dashboard", "stats"],
    queryFn: getDashboardStats,
    refetchInterval: 30_000,
  });
  const { data: audit } = useQuery({
    queryKey: ["audit", "recent"],
    queryFn: () => listAudit(10),
  });

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold">Dashboard</h1>

      {/* Status banner */}
      {health && (
        <div
          className={`flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium ${
            health.status === "ok"
              ? "bg-green-50 text-green-700"
              : "bg-yellow-50 text-yellow-700"
          }`}
        >
          {health.status === "ok" ? (
            <ShieldCheck className="h-4 w-4" />
          ) : (
            <AlertTriangle className="h-4 w-4" />
          )}
          System {health.status === "ok" ? "healthy" : "ready"}
        </div>
      )}

      {/* Top-level totals */}
      <div className="grid gap-4 sm:grid-cols-2">
        <StatCard
          icon={<Monitor className="h-5 w-5 text-muted-foreground" />}
          title="Devices"
          value={stats?.devices ?? "—"}
        />
        <StatCard
          icon={<ShieldAlert className="h-5 w-5 text-amber-600" />}
          title="Pending in quarantine"
          value={stats?.quarantine_pending ?? "—"}
        />
      </div>

      {/* Per-window decision breakdown */}
      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Decisions by window
        </h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-5">
          {(stats?.windows ?? []).map((w) => (
            <WindowCard key={w.label} window={w} />
          ))}
        </div>
      </div>

      {/* Recent audit */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Activity className="h-4 w-4" />
            Recent activity
          </CardTitle>
        </CardHeader>
        <CardContent>
          {!audit?.length ? (
            <p className="text-sm text-muted-foreground">No recent activity.</p>
          ) : (
            <ul className="space-y-2">
              {audit.map((entry) => (
                <li key={entry.id} className="flex items-center gap-3 text-sm">
                  <span
                    className={`w-14 rounded-full px-2 py-0.5 text-center text-xs font-medium ${
                      entry.decision === "allow"
                        ? "bg-green-100 text-green-700"
                        : entry.decision === "block"
                          ? "bg-red-100 text-red-700"
                          : "bg-yellow-100 text-yellow-700"
                    }`}
                  >
                    {entry.decision}
                  </span>
                  <span className="flex-1 truncate font-mono text-xs text-muted-foreground">
                    {entry.url}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {new Date(entry.ts).toLocaleTimeString()}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function WindowCard({ window: w }: { window: WindowStats }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-medium">{w.label}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1">
        <Row label="Allowed" value={w.allowed} tone="text-green-700" />
        <Row label="Blocked" value={w.blocked} tone="text-red-700" />
        <Row
          label="Pending"
          value={w.quarantined_pending}
          tone="text-amber-700"
        />
      </CardContent>
    </Card>
  );
}

function Row({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone: string;
}) {
  return (
    <div className="flex items-baseline justify-between">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span className={`text-xl font-semibold tabular-nums ${tone}`}>
        {value.toLocaleString()}
      </span>
    </div>
  );
}

function StatCard({
  icon,
  title,
  value,
}: {
  icon: React.ReactNode;
  title: string;
  value: number | string;
}) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        {icon}
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
      </CardContent>
    </Card>
  );
}

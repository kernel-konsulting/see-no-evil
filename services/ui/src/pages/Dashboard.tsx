import { useQuery } from "@tanstack/react-query";
import { getHealth, listDevices, listAudit } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Monitor, ShieldCheck, AlertTriangle, Activity } from "lucide-react";

export default function Dashboard() {
  const { data: health } = useQuery({ queryKey: ["health"], queryFn: getHealth });
  const { data: devices } = useQuery({ queryKey: ["devices"], queryFn: listDevices });
  const { data: audit } = useQuery({
    queryKey: ["audit", "recent"],
    queryFn: () => listAudit(10),
  });

  const blocked = audit?.filter((a) => a.decision === "block").length ?? 0;
  const allowed = audit?.filter((a) => a.decision === "allow").length ?? 0;

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

      {/* Stat cards */}
      <div className="grid gap-4 sm:grid-cols-3">
        <StatCard
          icon={<Monitor className="h-5 w-5 text-muted-foreground" />}
          title="Devices"
          value={devices?.length ?? "—"}
        />
        <StatCard
          icon={<ShieldCheck className="h-5 w-5 text-green-600" />}
          title="Allowed (last 10)"
          value={allowed}
        />
        <StatCard
          icon={<AlertTriangle className="h-5 w-5 text-red-600" />}
          title="Blocked (last 10)"
          value={blocked}
        />
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

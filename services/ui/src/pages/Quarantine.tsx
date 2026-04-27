import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listQuarantine,
  allowQuarantine,
  denyQuarantine,
  deleteQuarantine,
  type QuarantineItem,
} from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import { Check, X, Trash2, ShieldAlert } from "lucide-react";
import { toast } from "@/hooks/use-toast";

type StatusFilter = "pending" | "allowed" | "denied" | "all";

const STATUS_TABS: { value: StatusFilter; label: string }[] = [
  { value: "pending", label: "Pending" },
  { value: "allowed", label: "Allowed" },
  { value: "denied", label: "Denied" },
  { value: "all", label: "All" },
];

export default function Quarantine() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<StatusFilter>("pending");

  const { data: items = [], isLoading } = useQuery({
    queryKey: ["quarantine", filter],
    queryFn: () => listQuarantine(filter),
  });

  const allow = useMutation({
    mutationFn: allowQuarantine,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["quarantine"] });
      toast({ title: "Allowed" });
    },
    onError: () => toast({ title: "Allow failed", variant: "destructive" }),
  });

  const deny = useMutation({
    mutationFn: denyQuarantine,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["quarantine"] });
      toast({ title: "Denied" });
    },
    onError: () => toast({ title: "Deny failed", variant: "destructive" }),
  });

  const del = useMutation({
    mutationFn: deleteQuarantine,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["quarantine"] });
      toast({ title: "Deleted" });
    },
    onError: () => toast({ title: "Delete failed", variant: "destructive" }),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <ShieldAlert className="h-6 w-6" />
          Quarantine
        </h1>
        <div className="flex gap-1">
          {STATUS_TABS.map((tab) => (
            <Button
              key={tab.value}
              variant={filter === tab.value ? "default" : "outline"}
              size="sm"
              onClick={() => setFilter(tab.value)}
            >
              {tab.label}
            </Button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : items.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          No {filter === "all" ? "" : filter} items.
        </p>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {items.map((item) => (
            <QuarantineCard
              key={item.id}
              item={item}
              onAllow={() => allow.mutate(item.id)}
              onDeny={() => deny.mutate(item.id)}
              onDelete={() => {
                if (confirm("Delete this quarantine entry?")) del.mutate(item.id);
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function QuarantineCard({
  item,
  onAllow,
  onDeny,
  onDelete,
}: {
  item: QuarantineItem;
  onAllow: () => void;
  onDeny: () => void;
  onDelete: () => void;
}) {
  const pending = item.status === "pending";
  const topScore = topClassifierScore(item.classifier_scores);

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm break-all">{shortenUrl(item.url)}</CardTitle>
        <CardDescription className="text-xs">
          {new Date(item.ts).toLocaleString()}
          {item.content_type ? ` · ${item.content_type}` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {item.thumbnail_b64 ? (
          <img
            src={`data:image/png;base64,${item.thumbnail_b64}`}
            alt="blurred preview"
            className="w-full h-32 object-cover rounded border filter blur-sm"
          />
        ) : (
          <div className="w-full h-16 rounded bg-muted flex items-center justify-center text-xs text-muted-foreground">
            no preview
          </div>
        )}
        <div className="text-xs space-y-1">
          <div>
            <span className="text-muted-foreground">Reason:</span>{" "}
            <span className="font-mono">{item.reason}</span>
          </div>
          {topScore && (
            <div>
              <span className="text-muted-foreground">Top score:</span>{" "}
              <span className="font-mono">
                {topScore.label} = {topScore.value.toFixed(2)}
              </span>
            </div>
          )}
          <div>
            <span className="text-muted-foreground">Status:</span>{" "}
            <StatusBadge status={item.status} />
          </div>
        </div>
        <div className="flex gap-2 pt-1">
          {pending ? (
            <>
              <Button size="sm" onClick={onAllow} className="flex-1">
                <Check className="h-4 w-4 mr-1" /> Allow
              </Button>
              <Button size="sm" variant="destructive" onClick={onDeny} className="flex-1">
                <X className="h-4 w-4 mr-1" /> Deny
              </Button>
            </>
          ) : (
            <Button
              size="sm"
              variant="outline"
              onClick={onDelete}
              className="flex-1"
              aria-label="Delete"
            >
              <Trash2 className="h-4 w-4 mr-1" /> Delete
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function StatusBadge({ status }: { status: QuarantineItem["status"] }) {
  const cls =
    status === "pending"
      ? "bg-yellow-100 text-yellow-900"
      : status === "allowed"
        ? "bg-green-100 text-green-900"
        : "bg-red-100 text-red-900";
  return (
    <span className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${cls}`}>
      {status}
    </span>
  );
}

function shortenUrl(u: string, n = 80): string {
  return u.length <= n ? u : u.slice(0, n) + "…";
}

function topClassifierScore(
  scores: Record<string, number>,
): { label: string; value: number } | null {
  const entries = Object.entries(scores);
  if (entries.length === 0) return null;
  entries.sort((a, b) => b[1] - a[1]);
  return { label: entries[0][0], value: entries[0][1] };
}

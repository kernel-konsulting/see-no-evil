import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listQuarantine,
  allowQuarantine,
  denyQuarantine,
  allowAllQuarantine,
  denyAllQuarantine,
  deleteQuarantine,
  flagQuarantine,
  type QuarantineItem,
} from "@/lib/api";
import { useAuth } from "@/lib/use-auth";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  CardDescription,
} from "@/components/ui/card";
import {
  Check,
  CheckCheck,
  X,
  Ban,
  Trash2,
  ShieldAlert,
  Flag,
  FileText,
  Link as LinkIcon,
  Video,
  HelpCircle,
} from "lucide-react";
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
  const { isAdmin } = useAuth();
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

  const flag = useMutation({
    mutationFn: ({ id, note }: { id: number; note: string }) =>
      flagQuarantine(id, note),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["quarantine"] });
      toast({
        title: "Reported",
        description: "An admin will review this.",
      });
    },
    onError: () => toast({ title: "Report failed", variant: "destructive" }),
  });

  const allowAll = useMutation({
    mutationFn: allowAllQuarantine,
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["quarantine"] });
      toast({
        title: `Allowed ${result.updated} pending item${result.updated === 1 ? "" : "s"}`,
      });
    },
    onError: () => toast({ title: "Allow all failed", variant: "destructive" }),
  });

  const denyAll = useMutation({
    mutationFn: denyAllQuarantine,
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["quarantine"] });
      toast({
        title: `Denied ${result.updated} pending item${result.updated === 1 ? "" : "s"}`,
      });
    },
    onError: () => toast({ title: "Deny all failed", variant: "destructive" }),
  });

  const pendingCount = items.filter((item) => item.status === "pending").length;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold flex items-center gap-2">
          <ShieldAlert className="h-6 w-6" />
          Quarantine
        </h1>
        <div className="flex flex-wrap justify-end gap-2">
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
          {filter === "pending" && pendingCount > 0 && (
            <div className="flex gap-1">
              <Button
                size="sm"
                variant="outline"
                disabled={allowAll.isPending || denyAll.isPending}
                onClick={() => {
                  if (confirm("Allow all pending quarantine items?"))
                    allowAll.mutate();
                }}
              >
                <CheckCheck className="h-4 w-4 mr-1" /> Allow all
              </Button>
              <Button
                size="sm"
                variant="destructive"
                disabled={allowAll.isPending || denyAll.isPending}
                onClick={() => {
                  if (confirm("Deny all pending quarantine items?"))
                    denyAll.mutate();
                }}
              >
                <Ban className="h-4 w-4 mr-1" /> Deny all
              </Button>
            </div>
          )}
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
              isAdmin={isAdmin}
              onAllow={() => allow.mutate(item.id)}
              onDeny={() => deny.mutate(item.id)}
              onDelete={() => {
                if (confirm("Delete this quarantine entry?"))
                  del.mutate(item.id);
              }}
              onFlag={(note) => flag.mutate({ id: item.id, note })}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function QuarantineCard({
  item,
  isAdmin,
  onAllow,
  onDeny,
  onDelete,
  onFlag,
}: {
  item: QuarantineItem;
  isAdmin: boolean;
  onAllow: () => void;
  onDeny: () => void;
  onDelete: () => void;
  onFlag: (note: string) => void;
}) {
  const pending = item.status === "pending";
  const topScore = topClassifierScore(item.classifier_scores);
  const [flagOpen, setFlagOpen] = useState(false);
  const [flagNote, setFlagNote] = useState("");
  const [expanded, setExpanded] = useState(false);

  return (
    <Card
      className={expanded ? "md:col-span-2 lg:col-span-3" : ""}
      onClick={() => setExpanded((value) => !value)}
    >
      <CardHeader className="pb-3">
        <CardTitle className="text-sm break-all whitespace-normal leading-5">
          {item.url}
        </CardTitle>
        <CardDescription className="text-xs">
          {new Date(item.ts).toLocaleString()}
          {item.content_type ? ` · ${item.content_type}` : ""}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        <PreviewBlock item={item} expanded={expanded} />
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
          {item.flag_note && (
            <div className="rounded border border-amber-300 bg-amber-50 p-2 text-amber-900">
              <div className="flex items-center gap-1 font-medium">
                <Flag className="h-3 w-3" /> Flagged as false positive
              </div>
              <div>
                by {item.flagged_by ?? "unknown"}{" "}
                {item.flagged_at
                  ? `· ${new Date(item.flagged_at).toLocaleString()}`
                  : ""}
              </div>
              <div className="mt-1 italic">“{item.flag_note}”</div>
            </div>
          )}
        </div>
        <div
          className="flex flex-wrap gap-2 pt-1"
          onClick={(e) => e.stopPropagation()}
        >
          {isAdmin && pending && (
            <>
              <Button size="sm" onClick={onAllow} className="flex-1">
                <Check className="h-4 w-4 mr-1" /> Allow
              </Button>
              <Button
                size="sm"
                variant="destructive"
                onClick={onDeny}
                className="flex-1"
              >
                <X className="h-4 w-4 mr-1" /> Deny
              </Button>
            </>
          )}
          {isAdmin && !pending && (
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
          {!item.flag_note && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setFlagOpen((v) => !v)}
              className={isAdmin && pending ? "" : "flex-1"}
            >
              <Flag className="h-4 w-4 mr-1" />
              {flagOpen ? "Cancel" : "Report false positive"}
            </Button>
          )}
        </div>
        {flagOpen && (
          <div
            className="space-y-2 border-t pt-2"
            onClick={(e) => e.stopPropagation()}
          >
            <Textarea
              placeholder="Why is this a false positive? (optional)"
              value={flagNote}
              onChange={(e) => setFlagNote(e.target.value)}
              maxLength={500}
              rows={3}
            />
            <div className="flex justify-end gap-2">
              <Button
                size="sm"
                onClick={() => {
                  onFlag(flagNote);
                  setFlagOpen(false);
                  setFlagNote("");
                }}
              >
                Submit report
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function PreviewBlock({
  item,
  expanded,
}: {
  item: QuarantineItem;
  expanded: boolean;
}) {
  if (item.thumbnail_b64) {
    return (
      <img
        src={`data:image/jpeg;base64,${item.thumbnail_b64}`}
        alt="quarantined preview"
        className={`w-full rounded border ${
          expanded
            ? "max-h-[70vh] object-contain bg-muted"
            : "h-32 object-cover filter blur-sm"
        }`}
      />
    );
  }
  const ct = (item.content_type ?? "").toLowerCase();
  let icon = <HelpCircle className="h-8 w-8" />;
  let label = "No preview available";
  if (ct.startsWith("text/") || ct.includes("json") || ct.includes("xml")) {
    icon = <FileText className="h-8 w-8" />;
    label = "Text content blocked";
  } else if (ct.startsWith("video/")) {
    icon = <Video className="h-8 w-8" />;
    label = "Video content blocked";
  } else if (
    item.reason.startsWith("deny_url") ||
    item.reason.startsWith("deny_domain")
  ) {
    icon = <LinkIcon className="h-8 w-8" />;
    label = "URL/domain blocked";
  }
  return (
    <div className="flex w-full h-32 flex-col items-center justify-center gap-2 rounded border bg-muted text-muted-foreground">
      {icon}
      <div className="text-xs">{label}</div>
    </div>
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
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-medium ${cls}`}
    >
      {status}
    </span>
  );
}

function topClassifierScore(
  scores: Record<string, number>,
): { label: string; value: number } | null {
  const entries = Object.entries(scores);
  if (entries.length === 0) return null;
  entries.sort((a, b) => b[1] - a[1]);
  return { label: entries[0][0], value: entries[0][1] };
}

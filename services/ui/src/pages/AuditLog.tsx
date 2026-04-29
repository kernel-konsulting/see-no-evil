import { useEffect, useRef, useState, Fragment } from "react";
import {
  useInfiniteQuery,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { clearAudit, listAuditPage } from "@/lib/api";
import type { AuditEntry } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ChevronDown, ChevronUp, ImageOff, Trash2 } from "lucide-react";

const PAGE_SIZE = 100;

function formatScore(value: unknown): string {
  if (typeof value === "number") return value.toFixed(3);
  return String(value);
}

function ScoresTable({ scores }: { scores: Record<string, unknown> }) {
  const entries = Object.entries(scores ?? {});
  if (entries.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">
        No classifier scores recorded for this request.
      </p>
    );
  }
  return (
    <div className="grid gap-1 text-xs font-mono">
      {entries.map(([k, v]) => {
        const num = typeof v === "number" ? v : NaN;
        const pct = Number.isFinite(num) ? Math.min(100, num * 100) : 0;
        const high = Number.isFinite(num) && num >= 0.5;
        return (
          <div
            key={k}
            className="grid grid-cols-[14rem_1fr_4rem] items-center gap-2"
          >
            <span className="truncate">{k}</span>
            <div className="h-2 w-full bg-muted rounded">
              <div
                className={`h-2 rounded ${high ? "bg-red-500" : "bg-blue-500"}`}
                style={{ width: `${pct}%` }}
              />
            </div>
            <span
              className={`text-right ${high ? "text-red-600 font-semibold" : ""}`}
            >
              {formatScore(v)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function Row({ entry }: { entry: AuditEntry }) {
  const [open, setOpen] = useState(false);
  const [urlOpen, setUrlOpen] = useState(false);
  const reason =
    entry.reason?.trim() ||
    (entry.decision === "block" ? "unspecified_block" : "—");
  const hasDetail =
    !!entry.content_type ||
    !!entry.thumbnail_b64 ||
    Object.keys(entry.classifier_scores ?? {}).length > 0;
  return (
    <Fragment>
      <TableRow
        className={hasDetail ? "cursor-pointer hover:bg-muted/40" : undefined}
        onClick={() => hasDetail && setOpen((v) => !v)}
      >
        <TableCell className="w-6 align-top">
          {hasDetail ? (
            open ? (
              <ChevronUp className="h-3.5 w-3.5 text-muted-foreground" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5 text-muted-foreground" />
            )
          ) : null}
        </TableCell>
        <TableCell className="text-xs text-muted-foreground whitespace-nowrap align-top">
          {new Date(entry.ts).toLocaleString()}
        </TableCell>
        <TableCell className="align-top">
          <span
            className={`rounded-full px-2 py-0.5 text-xs font-medium ${
              entry.decision === "allow"
                ? "bg-green-100 text-green-700"
                : entry.decision === "block"
                  ? "bg-red-100 text-red-700"
                  : "bg-yellow-100 text-yellow-700"
            }`}
          >
            {entry.decision}
          </span>
        </TableCell>
        <TableCell className="text-xs text-muted-foreground whitespace-nowrap align-top">
          {entry.content_type?.split(";")[0] ?? "—"}
        </TableCell>
        <TableCell className="align-top w-16">
          {entry.thumbnail_b64 ? (
            <img
              src={`data:image/jpeg;base64,${entry.thumbnail_b64}`}
              alt="analysed"
              className="h-10 w-10 rounded object-cover border"
            />
          ) : (
            <div
              className="flex h-10 w-10 items-center justify-center rounded border bg-muted text-muted-foreground"
              title="No thumbnail available"
            >
              <ImageOff className="h-4 w-4" />
            </div>
          )}
        </TableCell>
        <TableCell className="max-w-xl align-top">
          <button
            type="button"
            className={`block w-full text-left font-mono text-xs leading-5 text-foreground break-all whitespace-normal hover:underline ${
              urlOpen ? "" : "max-h-10 overflow-hidden"
            }`}
            aria-expanded={urlOpen}
            title={entry.url}
            onClick={(event) => {
              event.stopPropagation();
              setUrlOpen((value) => !value);
            }}
          >
            {entry.url}
          </button>
        </TableCell>
        <TableCell className="text-xs text-muted-foreground align-top">
          {reason}
        </TableCell>
      </TableRow>
      {open && hasDetail && (
        <TableRow className="bg-muted/20">
          <TableCell />
          <TableCell colSpan={6} className="space-y-2 py-3">
            {entry.thumbnail_b64 && (
              <div>
                <div className="text-xs font-medium mb-1">
                  Image analysed by classifier
                </div>
                <img
                  src={`data:image/jpeg;base64,${entry.thumbnail_b64}`}
                  alt="analysed"
                  className="max-h-48 rounded border"
                />
              </div>
            )}
            {entry.content_type && (
              <div className="text-xs">
                <span className="font-medium">Content-Type:</span>{" "}
                <span className="font-mono">{entry.content_type}</span>
              </div>
            )}
            <div className="text-xs font-medium">Classifier scores</div>
            <ScoresTable scores={entry.classifier_scores ?? {}} />
          </TableCell>
        </TableRow>
      )}
    </Fragment>
  );
}

export default function AuditLog() {
  const queryClient = useQueryClient();
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const {
    data,
    isLoading,
    isFetching,
    hasNextPage,
    fetchNextPage,
    isFetchingNextPage,
    refetch,
  } = useInfiniteQuery({
    queryKey: ["audit", "infinite"],
    queryFn: ({ pageParam }) =>
      listAuditPage({ limit: PAGE_SIZE, beforeId: pageParam ?? null }),
    initialPageParam: null as number | null,
    getNextPageParam: (lastPage) => {
      if (!lastPage || lastPage.length < PAGE_SIZE) return undefined;
      return lastPage[lastPage.length - 1].id;
    },
    refetchInterval: 10_000,
    refetchOnWindowFocus: false,
  });
  const clearMutation = useMutation({
    mutationFn: clearAudit,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["audit"] }),
  });

  const entries: AuditEntry[] = data?.pages.flat() ?? [];

  // Auto-fetch the next page when the sentinel scrolls into view.
  useEffect(() => {
    const node = sentinelRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (records) => {
        if (records[0]?.isIntersecting && hasNextPage && !isFetchingNextPage) {
          void fetchNextPage();
        }
      },
      { rootMargin: "200px 0px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [hasNextPage, isFetchingNextPage, fetchNextPage]);

  const handleClear = () => {
    if (!entries.length) return;
    if (!window.confirm("Clear all audit log entries?")) return;
    clearMutation.mutate();
  };

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Audit Log</h1>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>
              {entries.length.toLocaleString()} entries loaded
              {isFetching && !isFetchingNextPage && (
                <span className="ml-2 text-sm font-normal text-muted-foreground">
                  Refreshing…
                </span>
              )}
            </span>
            <div className="flex items-center gap-2 text-sm font-normal">
              <Button
                variant="outline"
                size="sm"
                onClick={() => refetch()}
                disabled={isLoading}
              >
                Refresh
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={handleClear}
                disabled={
                  !entries.length || isLoading || clearMutation.isPending
                }
              >
                <Trash2 className="h-4 w-4" />
                Clear logs
              </Button>
            </div>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <p className="p-6 text-sm text-muted-foreground">Loading…</p>
          ) : !entries.length ? (
            <div className="p-6 space-y-2 text-sm">
              <p className="text-muted-foreground">No audit entries yet.</p>
              <p className="text-xs text-muted-foreground">
                Every HTTPS request that flows through the proxy is recorded
                here. If this list stays empty while clients are browsing,
                either (a) the see-no-evil CA is not <em>trusted</em> on the
                client (so the browser is bypassing the proxy), or (b) the
                client isn’t configured to use the proxy. See the{" "}
                <a className="underline" href="/setup">
                  Setup
                </a>{" "}
                page.
              </p>
            </div>
          ) : (
            <>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-6" />
                    <TableHead>Time</TableHead>
                    <TableHead>Decision</TableHead>
                    <TableHead>Content-Type</TableHead>
                    <TableHead className="w-16">Preview</TableHead>
                    <TableHead>URL</TableHead>
                    <TableHead>Reason</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {entries.map((entry) => (
                    <Row key={entry.id} entry={entry} />
                  ))}
                </TableBody>
              </Table>
              <div
                ref={sentinelRef}
                className="flex items-center justify-center p-4 text-xs text-muted-foreground"
              >
                {isFetchingNextPage
                  ? "Loading more…"
                  : hasNextPage
                    ? "Scroll to load more"
                    : "End of log"}
                {hasNextPage && !isFetchingNextPage && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="ml-3"
                    onClick={() => fetchNextPage()}
                  >
                    Load more
                  </Button>
                )}
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

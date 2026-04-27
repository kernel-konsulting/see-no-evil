import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { listAudit } from "@/lib/api";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ChevronLeft, ChevronRight } from "lucide-react";

const PAGE_SIZE = 50;

export default function AuditLog() {
  const [limit, setLimit] = useState(PAGE_SIZE);
  const { data, isLoading, isFetching } = useQuery({
    queryKey: ["audit", limit],
    queryFn: () => listAudit(limit),
    placeholderData: (prev) => prev,
  });

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Audit Log</h1>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center justify-between">
            <span>
              {data ? `${data.length.toLocaleString()} entries` : "—"}
              {isFetching && <span className="ml-2 text-sm font-normal text-muted-foreground">Loading…</span>}
            </span>
            <div className="flex items-center gap-2 text-sm font-normal">
              <Button
                variant="outline"
                size="icon"
                onClick={() => setLimit((value) => Math.max(10, value - 10))}
                disabled={limit <= 10 || isLoading}
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <span>showing {limit}</span>
              <Button
                variant="outline"
                size="icon"
                onClick={() => setLimit((value) => Math.min(200, value + 10))}
                disabled={limit >= 200 || isLoading}
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          </CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          {isLoading ? (
            <p className="p-6 text-sm text-muted-foreground">Loading…</p>
          ) : !data?.length ? (
            <p className="p-6 text-sm text-muted-foreground">No audit entries yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Decision</TableHead>
                  <TableHead>URL</TableHead>
                  <TableHead>Reason</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.map((entry) => (
                  <TableRow key={entry.id}>
                    <TableCell className="text-xs text-muted-foreground whitespace-nowrap">
                      {new Date(entry.ts).toLocaleString()}
                    </TableCell>
                    <TableCell>
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
                    <TableCell className="max-w-xs truncate font-mono text-xs">
                      {entry.url}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {entry.reason ?? "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

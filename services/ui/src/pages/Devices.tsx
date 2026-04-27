import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  listDevices,
  listProfiles,
  updateDevice,
  deleteDevice,
  createDevice,
} from "@/lib/api";
import type { Device } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Pencil, Trash2, Plus } from "lucide-react";
import { toast } from "@/hooks/use-toast";

export default function Devices() {
  const qc = useQueryClient();
  const { data: devices = [], isLoading } = useQuery({
    queryKey: ["devices"],
    queryFn: listDevices,
  });
  const { data: profiles = [] } = useQuery({
    queryKey: ["profiles"],
    queryFn: listProfiles,
  });

  const [editing, setEditing] = useState<Device | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState({
    mac: "",
    name: "",
    profile_id: "",
    bypass_proxy: false,
  });

  const updateMut = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<Device> }) =>
      updateDevice(id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["devices"] });
      setEditing(null);
      toast({ title: "Device updated" });
    },
    onError: () => toast({ title: "Update failed", variant: "destructive" }),
  });

  const deleteMut = useMutation({
    mutationFn: deleteDevice,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["devices"] });
      toast({ title: "Device removed" });
    },
    onError: () => toast({ title: "Delete failed", variant: "destructive" }),
  });

  const createMut = useMutation({
    mutationFn: createDevice,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["devices"] });
      setAdding(false);
      setForm({ mac: "", name: "", profile_id: "", bypass_proxy: false });
      toast({ title: "Device added" });
    },
    onError: () => toast({ title: "Create failed", variant: "destructive" }),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Devices</h1>
        <Button size="sm" onClick={() => setAdding(true)}>
          <Plus className="h-4 w-4 mr-1" />
          Add device
        </Button>
      </div>

      {/* Add form */}
      {adding && (
        <Card>
          <CardHeader>
            <CardTitle>Add device</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1">
              <Label>MAC address</Label>
              <Input
                placeholder="aa:bb:cc:dd:ee:ff"
                value={form.mac}
                onChange={(e) =>
                  setForm((f) => ({ ...f, mac: e.target.value }))
                }
              />
            </div>
            <div className="space-y-1">
              <Label>Name</Label>
              <Input
                placeholder="Kids iPad"
                value={form.name}
                onChange={(e) =>
                  setForm((f) => ({ ...f, name: e.target.value }))
                }
              />
            </div>
            <div className="space-y-1">
              <Label>Profile</Label>
              <select
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={form.profile_id}
                onChange={(e) =>
                  setForm((f) => ({ ...f, profile_id: e.target.value }))
                }
              >
                <option value="">Select profile</option>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <label className="flex items-center gap-2 text-sm sm:col-span-3">
              <input
                type="checkbox"
                checked={form.bypass_proxy}
                onChange={(e) =>
                  setForm((f) => ({ ...f, bypass_proxy: e.target.checked }))
                }
              />
              Bypass MITM proxy for this device
            </label>
            <div className="flex gap-2 sm:col-span-3">
              <Button
                onClick={() =>
                  createMut.mutate({
                    mac: form.mac,
                    name: form.name || null,
                    profile_id: Number(form.profile_id),
                    bypass_proxy: form.bypass_proxy,
                  })
                }
                disabled={!form.mac || !form.profile_id || createMut.isPending}
              >
                Save
              </Button>
              <Button variant="outline" onClick={() => setAdding(false)}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Edit inline */}
      {editing && (
        <Card>
          <CardHeader>
            <CardTitle>Edit — {editing.mac}</CardTitle>
          </CardHeader>
          <CardContent className="grid gap-4 sm:grid-cols-3">
            <div className="space-y-1">
              <Label>Name</Label>
              <Input
                value={editing.name}
                onChange={(e) =>
                  setEditing((d) => d && { ...d, name: e.target.value })
                }
              />
            </div>
            <div className="space-y-1">
              <Label>Profile</Label>
              <select
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                value={editing.profile_id ?? ""}
                onChange={(e) =>
                  setEditing(
                    (d) => d && { ...d, profile_id: e.target.value || null },
                  )
                }
              >
                <option value="">Select profile</option>
                {profiles.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <label className="flex items-center gap-2 text-sm sm:col-span-3">
              <input
                type="checkbox"
                checked={editing.bypass_proxy}
                onChange={(e) =>
                  setEditing(
                    (d) => d && { ...d, bypass_proxy: e.target.checked },
                  )
                }
              />
              Bypass MITM proxy for this device
            </label>
            <div className="flex gap-2 sm:col-span-3">
              <Button
                onClick={() =>
                  updateMut.mutate({
                    id: editing.id,
                    payload: {
                      name: editing.name,
                      profile_id: editing.profile_id,
                      bypass_proxy: editing.bypass_proxy,
                    },
                  })
                }
                disabled={updateMut.isPending}
              >
                Save
              </Button>
              <Button variant="outline" onClick={() => setEditing(null)}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent className="p-0">
          {isLoading ? (
            <p className="p-6 text-sm text-muted-foreground">Loading…</p>
          ) : devices.length === 0 ? (
            <p className="p-6 text-sm text-muted-foreground">
              No devices registered yet.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>MAC</TableHead>
                  <TableHead>IP</TableHead>
                  <TableHead>Vendor</TableHead>
                  <TableHead>Last seen</TableHead>
                  <TableHead>Profile</TableHead>
                  <TableHead className="w-20" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {devices.map((d) => {
                  const isNew =
                    d.created_at &&
                    Date.now() - new Date(d.created_at).getTime() <
                      7 * 24 * 60 * 60 * 1000;
                  return (
                    <TableRow key={d.id}>
                      <TableCell className="font-medium">
                        {d.name}
                        {isNew && (
                          <span className="ml-2 inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium uppercase tracking-wide text-primary">
                            New
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {d.mac}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {d.ip ?? "—"}
                      </TableCell>
                      <TableCell className="text-xs">
                        {d.vendor ?? "—"}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {d.last_seen_at
                          ? new Date(d.last_seen_at).toLocaleString()
                          : "—"}
                      </TableCell>
                      <TableCell>
                        {profiles.find((p) => p.id === d.profile_id)?.name ??
                          "—"}
                      </TableCell>
                      <TableCell>
                        <div className="flex gap-1">
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => setEditing(d)}
                            aria-label="Edit"
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => {
                              if (confirm(`Remove device "${d.name}"?`)) {
                                deleteMut.mutate(d.id);
                              }
                            }}
                            aria-label="Delete"
                          >
                            <Trash2 className="h-3.5 w-3.5 text-destructive" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

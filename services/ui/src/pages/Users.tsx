import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listUsers, createUser, deleteUser, updateUser } from "@/lib/api";
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
import { Loader2, Plus, Trash2, KeyRound } from "lucide-react";
import { toast } from "@/hooks/use-toast";

export default function Users() {
  const qc = useQueryClient();
  const { data: users = [], isLoading } = useQuery({
    queryKey: ["users"],
    queryFn: listUsers,
  });

  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState<{
    email: string;
    password: string;
    role: "admin" | "viewer";
  }>({ email: "", password: "", role: "admin" });
  const [pwUserId, setPwUserId] = useState<number | null>(null);
  const [pwValue, setPwValue] = useState("");

  const createMut = useMutation({
    mutationFn: createUser,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      setAdding(false);
      setForm({ email: "", password: "", role: "admin" });
      toast({ title: "User created" });
    },
    onError: () =>
      toast({ title: "Could not create user", variant: "destructive" }),
  });

  const deleteMut = useMutation({
    mutationFn: deleteUser,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      toast({ title: "User deleted" });
    },
    onError: (err: unknown) => {
      const msg =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Delete failed";
      toast({ title: msg, variant: "destructive" });
    },
  });

  const pwMut = useMutation({
    mutationFn: ({ id, password }: { id: number; password: string }) =>
      updateUser(id, { password }),
    onSuccess: () => {
      setPwUserId(null);
      setPwValue("");
      toast({ title: "Password updated" });
    },
    onError: () =>
      toast({ title: "Password change failed", variant: "destructive" }),
  });

  const roleMut = useMutation({
    mutationFn: ({ id, role }: { id: number; role: string }) =>
      updateUser(id, { role }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      toast({ title: "Role updated" });
    },
    onError: () =>
      toast({ title: "Role change failed", variant: "destructive" }),
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading users…
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-4xl">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">User accounts</h1>
        <Button onClick={() => setAdding((v) => !v)}>
          <Plus className="h-4 w-4 mr-2" />
          {adding ? "Cancel" : "Add user"}
        </Button>
      </div>

      {adding && (
        <Card>
          <CardHeader>
            <CardTitle>New user</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div>
              <Label>Email</Label>
              <Input
                value={form.email}
                onChange={(e) => setForm({ ...form, email: e.target.value })}
                placeholder="user@example.com"
              />
            </div>
            <div>
              <Label>Password</Label>
              <Input
                type="password"
                value={form.password}
                onChange={(e) => setForm({ ...form, password: e.target.value })}
              />
            </div>
            <div>
              <Label>Role</Label>
              <select
                value={form.role}
                onChange={(e) =>
                  setForm({
                    ...form,
                    role: e.target.value as "admin" | "viewer",
                  })
                }
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
              >
                <option value="admin">admin — full control</option>
                <option value="viewer">
                  viewer — read audit/quarantine, can flag false positives
                </option>
              </select>
            </div>
            <Button
              onClick={() => createMut.mutate(form)}
              disabled={
                createMut.isPending || !form.email || form.password.length < 8
              }
            >
              {createMut.isPending && (
                <Loader2 className="h-4 w-4 animate-spin mr-2" />
              )}
              Create
            </Button>
            {form.password.length > 0 && form.password.length < 8 && (
              <p className="text-xs text-destructive">
                Password must be at least 8 characters.
              </p>
            )}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardContent className="p-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Email</TableHead>
                <TableHead>Role</TableHead>
                <TableHead>Created</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {users.map((u) => (
                <TableRow key={u.id}>
                  <TableCell className="font-mono text-sm">{u.email}</TableCell>
                  <TableCell>
                    <select
                      value={u.role}
                      onChange={(e) =>
                        roleMut.mutate({ id: u.id, role: e.target.value })
                      }
                      className="h-8 rounded border border-input bg-background px-2 text-sm"
                      disabled={roleMut.isPending}
                    >
                      <option value="admin">admin</option>
                      <option value="viewer">viewer</option>
                    </select>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {new Date(u.created_at).toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right space-x-2">
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setPwUserId(u.id);
                        setPwValue("");
                      }}
                    >
                      <KeyRound className="h-3.5 w-3.5 mr-1" />
                      Password
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      onClick={() => {
                        if (
                          confirm(
                            `Delete user ${u.email}? This cannot be undone.`,
                          )
                        ) {
                          deleteMut.mutate(u.id);
                        }
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </TableCell>
                </TableRow>
              ))}
              {users.length === 0 && (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="text-center text-muted-foreground py-8"
                  >
                    No users yet.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {pwUserId !== null && (
        <Card>
          <CardHeader>
            <CardTitle>Change password</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <Input
              type="password"
              value={pwValue}
              onChange={(e) => setPwValue(e.target.value)}
              placeholder="New password (min 8 chars)"
            />
            <div className="flex gap-2">
              <Button
                onClick={() =>
                  pwMut.mutate({ id: pwUserId, password: pwValue })
                }
                disabled={pwMut.isPending || pwValue.length < 8}
              >
                {pwMut.isPending && (
                  <Loader2 className="h-4 w-4 animate-spin mr-2" />
                )}
                Update
              </Button>
              <Button variant="outline" onClick={() => setPwUserId(null)}>
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

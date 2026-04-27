import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { listProfiles, createProfile, updateProfile, deleteProfile } from "@/lib/api";
import type { Profile } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Pencil, Trash2, Plus, ShieldCheck, ShieldOff } from "lucide-react";
import { toast } from "@/hooks/use-toast";

const IMAGE_LABELS = ["porn", "hentai", "sexy"] as const;

type ProfileForm = {
  name: string;
  description: string;
  quota_minutes_per_day: number;
  notify_on_block: boolean;
  image_thresholds: Record<string, number>;
  allow_domains: string[];
  deny_domains: string[];
  deny_url_keywords: string[];
  allow_youtube_channels: string[];
  deny_youtube_channels: string[];
};

const defaultForm: ProfileForm = {
  name: "",
  description: "",
  quota_minutes_per_day: 0,
  notify_on_block: false,
  image_thresholds: { porn: 0.6, hentai: 0.6, sexy: 0.9 },
  allow_domains: [],
  deny_domains: [],
  deny_url_keywords: [],
  allow_youtube_channels: [],
  deny_youtube_channels: [],
};

function profileToForm(p: Profile): ProfileForm {
  return {
    name: p.name,
    description: p.description,
    quota_minutes_per_day: p.quota_minutes_per_day,
    notify_on_block: p.notify_on_block,
    image_thresholds: { ...p.image_thresholds },
    allow_domains: [...(p.allow_domains ?? [])],
    deny_domains: [...(p.deny_domains ?? [])],
    deny_url_keywords: [...(p.deny_url_keywords ?? [])],
    allow_youtube_channels: [...(p.allow_youtube_channels ?? [])],
    deny_youtube_channels: [...(p.deny_youtube_channels ?? [])],
  };
}

function formToPayload(f: ProfileForm): Partial<Profile> & { name: string } {
  return {
    name: f.name,
    description: f.description,
    quota_minutes_per_day: f.quota_minutes_per_day,
    notify_on_block: f.notify_on_block,
    image_thresholds: f.image_thresholds,
    allow_domains: f.allow_domains,
    deny_domains: f.deny_domains,
    deny_url_keywords: f.deny_url_keywords,
    allow_youtube_channels: f.allow_youtube_channels,
    deny_youtube_channels: f.deny_youtube_channels,
  };
}

export default function Profiles() {
  const qc = useQueryClient();
  const { data: profiles = [], isLoading } = useQuery({
    queryKey: ["profiles"],
    queryFn: listProfiles,
  });

  const [editingId, setEditingId] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState<ProfileForm>(defaultForm);

  const createMut = useMutation({
    mutationFn: createProfile,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profiles"] });
      setAdding(false);
      setForm(defaultForm);
      toast({ title: "Profile created" });
    },
    onError: () => toast({ title: "Create failed", variant: "destructive" }),
  });

  const updateMut = useMutation({
    mutationFn: ({ id, payload }: { id: number; payload: Partial<Profile> }) =>
      updateProfile(id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profiles"] });
      setEditingId(null);
      toast({ title: "Profile updated" });
    },
    onError: () => toast({ title: "Update failed", variant: "destructive" }),
  });

  const deleteMut = useMutation({
    mutationFn: deleteProfile,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profiles"] });
      toast({ title: "Profile deleted" });
    },
    onError: () => toast({ title: "Delete failed", variant: "destructive" }),
  });

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Profiles</h1>
        <Button
          size="sm"
          onClick={() => {
            setAdding(true);
            setEditingId(null);
            setForm(defaultForm);
          }}
        >
          <Plus className="h-4 w-4 mr-1" />
          New profile
        </Button>
      </div>

      {adding && (
        <ProfileFormCard
          title="New profile"
          values={form}
          onChange={setForm}
          onSave={() => createMut.mutate(formToPayload(form))}
          onCancel={() => setAdding(false)}
          saving={createMut.isPending}
        />
      )}

      {isLoading ? (
        <p className="text-sm text-muted-foreground">Loading…</p>
      ) : profiles.length === 0 ? (
        <p className="text-sm text-muted-foreground">No profiles yet. Create one above.</p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {profiles.map((p) =>
            editingId === p.id ? (
              <ProfileFormCard
                key={p.id}
                title={`Edit — ${p.name}`}
                values={form}
                onChange={setForm}
                onSave={() => updateMut.mutate({ id: p.id, payload: formToPayload(form) })}
                onCancel={() => setEditingId(null)}
                saving={updateMut.isPending}
              />
            ) : (
              <Card key={p.id}>
                <CardHeader className="flex flex-row items-start justify-between space-y-0">
                  <div>
                    <CardTitle className="text-base">{p.name}</CardTitle>
                    <CardDescription className="text-xs mt-1">
                      Created {new Date(p.created_at).toLocaleDateString()}
                    </CardDescription>
                  </div>
                  <div className="flex gap-1">
                    <Button
                      variant="ghost"
                      size="icon"
                      onClick={() => {
                        setEditingId(p.id);
                        setAdding(false);
                        setForm(profileToForm(p));
                      }}
                      aria-label="Edit"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon"
                      aria-label="Delete"
                      onClick={() => {
                        if (confirm(`Delete profile "${p.name}"?`)) deleteMut.mutate(p.id);
                      }}
                    >
                      <Trash2 className="h-3.5 w-3.5 text-destructive" />
                    </Button>
                  </div>
                </CardHeader>
                <CardContent className="space-y-2 text-sm">
                  <p className="text-muted-foreground">{p.description || "No description"}</p>
                  <Flag
                    on={p.quota_minutes_per_day > 0}
                    label={`Quota: ${p.quota_minutes_per_day} min/day`}
                  />
                  <Flag on={p.notify_on_block} label="Notify on block" />
                  <ThresholdSummary thresholds={p.image_thresholds} />
                  <div className="text-xs text-muted-foreground">
                    {p.deny_domains.length} deny · {p.allow_domains.length} allow ·{" "}
                    {p.deny_url_keywords.length} keyword
                  </div>
                </CardContent>
              </Card>
            ),
          )}
        </div>
      )}
    </div>
  );
}

function ProfileFormCard({
  title,
  values,
  onChange,
  onSave,
  onCancel,
  saving,
}: {
  title: string;
  values: ProfileForm;
  onChange: (f: ProfileForm) => void;
  onSave: () => void;
  onCancel: () => void;
  saving: boolean;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-1">
          <Label>Name</Label>
          <Input
            value={values.name}
            onChange={(e) => onChange({ ...values, name: e.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label>Description</Label>
          <Input
            value={values.description}
            onChange={(e) => onChange({ ...values, description: e.target.value })}
          />
        </div>
        <div className="space-y-1">
          <Label>Daily quota (minutes, 0 = unlimited)</Label>
          <Input
            type="number"
            min={0}
            value={values.quota_minutes_per_day}
            onChange={(e) =>
              onChange({ ...values, quota_minutes_per_day: Number(e.target.value || 0) })
            }
          />
        </div>
        <label className="flex items-center gap-2 text-sm cursor-pointer">
          <input
            type="checkbox"
            className="h-4 w-4"
            checked={values.notify_on_block}
            onChange={(e) => onChange({ ...values, notify_on_block: e.target.checked })}
          />
          Notify on block
        </label>

        <div className="space-y-2 pt-2 border-t">
          <Label className="text-base">Image classifier strictness</Label>
          <p className="text-xs text-muted-foreground">
            Lower = stricter (blocks at lower confidence). 1.0 effectively disables the class.
          </p>
          {IMAGE_LABELS.map((label) => (
            <ThresholdSlider
              key={label}
              label={label}
              value={values.image_thresholds[label] ?? 1.0}
              onChange={(v) =>
                onChange({
                  ...values,
                  image_thresholds: { ...values.image_thresholds, [label]: v },
                })
              }
            />
          ))}
        </div>

        <div className="space-y-2 pt-2 border-t">
          <Label className="text-base">Domain & keyword rules</Label>
          <ListEditor
            label="Deny domains"
            values={values.deny_domains}
            placeholder="e.g. tiktok.com or *.example.net"
            onChange={(v) => onChange({ ...values, deny_domains: v })}
          />
          <ListEditor
            label="Allow domains (when set, only these pass)"
            values={values.allow_domains}
            placeholder="e.g. khanacademy.org"
            onChange={(v) => onChange({ ...values, allow_domains: v })}
          />
          <ListEditor
            label="Deny URL keywords"
            values={values.deny_url_keywords}
            placeholder="e.g. /adult/"
            onChange={(v) => onChange({ ...values, deny_url_keywords: v })}
          />
        </div>

        <div className="space-y-2 pt-2 border-t">
          <Label className="text-base">YouTube channels</Label>
          <ListEditor
            label="Deny channels"
            values={values.deny_youtube_channels}
            placeholder="@handle or UCxxxxxxxx"
            onChange={(v) => onChange({ ...values, deny_youtube_channels: v })}
          />
          <ListEditor
            label="Allow channels (when set, only these pass)"
            values={values.allow_youtube_channels}
            placeholder="@handle or UCxxxxxxxx"
            onChange={(v) => onChange({ ...values, allow_youtube_channels: v })}
          />
        </div>

        <div className="flex gap-2 pt-2">
          <Button onClick={onSave} disabled={!values.name || saving}>
            Save
          </Button>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

function ThresholdSlider({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-sm">
        <span className="capitalize">{label}</span>
        <span className="font-mono text-xs">{value.toFixed(2)}</span>
      </div>
      <input
        type="range"
        min={0}
        max={1}
        step={0.05}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full"
        aria-label={`${label} threshold`}
      />
    </div>
  );
}

function ListEditor({
  label,
  values,
  placeholder,
  onChange,
}: {
  label: string;
  values: string[];
  placeholder?: string;
  onChange: (v: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const add = () => {
    const v = draft.trim();
    if (v && !values.includes(v)) onChange([...values, v]);
    setDraft("");
  };
  return (
    <div className="space-y-1">
      <Label className="text-xs text-muted-foreground">{label}</Label>
      <div className="flex gap-1">
        <Input
          value={draft}
          placeholder={placeholder}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
        />
        <Button type="button" size="sm" variant="outline" onClick={add}>
          Add
        </Button>
      </div>
      {values.length > 0 && (
        <div className="flex flex-wrap gap-1 pt-1">
          {values.map((v) => (
            <span
              key={v}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-muted text-xs font-mono"
            >
              {v}
              <button
                type="button"
                aria-label={`Remove ${v}`}
                className="text-muted-foreground hover:text-destructive"
                onClick={() => onChange(values.filter((x) => x !== v))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

function ThresholdSummary({ thresholds }: { thresholds: Record<string, number> }) {
  const entries = Object.entries(thresholds);
  if (entries.length === 0) {
    return <div className="text-xs text-muted-foreground">No thresholds set</div>;
  }
  return (
    <div className="text-xs text-muted-foreground">
      Thresholds:{" "}
      {entries.map(([k, v], i) => (
        <span key={k} className="font-mono">
          {i > 0 ? ", " : ""}
          {k}={v.toFixed(2)}
        </span>
      ))}
    </div>
  );
}

function Flag({ on, label }: { on: boolean; label: string }) {
  return (
    <div className={`flex items-center gap-1.5 ${on ? "text-green-700" : "text-muted-foreground"}`}>
      {on ? <ShieldCheck className="h-3.5 w-3.5" /> : <ShieldOff className="h-3.5 w-3.5" />}
      {label}
    </div>
  );
}

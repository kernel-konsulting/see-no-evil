import { useEffect, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { getSettings, updateSettings } from "@/lib/api";
import type { RuntimeSettings } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Loader2, Save } from "lucide-react";
import { toast } from "@/hooks/use-toast";

function linesToList(s: string): string[] {
  return s
    .split(/[\n,]/)
    .map((x) => x.trim())
    .filter((x) => x.length > 0);
}

function listToLines(xs: string[] | undefined): string {
  return (xs ?? []).join("\n");
}

export default function Settings() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["settings"],
    queryFn: getSettings,
  });

  const [draft, setDraft] = useState<RuntimeSettings | null>(null);
  const [allowText, setAllowText] = useState("");
  const [denyText, setDenyText] = useState("");
  const [keywordText, setKeywordText] = useState("");

  useEffect(() => {
    if (data && !draft) {
      setDraft(data);
      setAllowText(listToLines(data.lists.global_allow_domains));
      setDenyText(listToLines(data.lists.global_deny_domains));
      setKeywordText(listToLines(data.lists.global_deny_keywords));
    }
  }, [data, draft]);

  const saveMut = useMutation({
    mutationFn: updateSettings,
    onSuccess: (s) => {
      qc.setQueryData(["settings"], s);
      setDraft(s);
      toast({ title: "Settings saved" });
    },
    onError: () => toast({ title: "Save failed", variant: "destructive" }),
  });

  if (isLoading || !draft) {
    return (
      <div className="flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading settings…
      </div>
    );
  }

  function patchInspect(key: keyof RuntimeSettings["inspect"], value: boolean) {
    setDraft((d) =>
      d ? { ...d, inspect: { ...d.inspect, [key]: value } } : d,
    );
  }

  function patchTextThreshold(value: number) {
    setDraft((d) =>
      d ? { ...d, text: { ...d.text, nsfw_threshold: value } } : d,
    );
  }

  function patchImageThreshold(
    key: keyof RuntimeSettings["image"],
    value: number,
  ) {
    setDraft((d) => (d ? { ...d, image: { ...d.image, [key]: value } } : d));
  }

  function patchNotif<K extends keyof RuntimeSettings["notifications"]>(
    key: K,
    value: RuntimeSettings["notifications"][K],
  ) {
    setDraft((d) =>
      d ? { ...d, notifications: { ...d.notifications, [key]: value } } : d,
    );
  }

  function handleSave() {
    if (!draft) return;
    const payload: RuntimeSettings = {
      ...draft,
      lists: {
        global_allow_domains: linesToList(allowText),
        enforce_global_allowlist: draft.lists.enforce_global_allowlist,
        global_deny_domains: linesToList(denyText),
        global_deny_keywords: linesToList(keywordText),
      },
    };
    saveMut.mutate(payload);
  }

  return (
    <div className="space-y-6 max-w-3xl">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Settings</h1>
        <div className="flex items-center gap-3">
          <ModeToggle />
          <Button onClick={handleSave} disabled={saveMut.isPending}>
            {saveMut.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
            ) : (
              <Save className="h-4 w-4 mr-2" />
            )}
            Save changes
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Inspection</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {(
            [
              ["image", "Inspect images (NSFW classifier)"],
              ["video", "Inspect video frames"],
              ["text", "Inspect text content"],
              ["domain", "Apply DNS/domain rules"],
              ["url", "Apply URL keyword rules"],
            ] as const
          ).map(([key, label]) => (
            <label key={key} className="flex items-center gap-3 text-sm">
              <input
                type="checkbox"
                className="h-4 w-4"
                checked={draft.inspect[key]}
                onChange={(e) => patchInspect(key, e.target.checked)}
              />
              {label}
            </label>
          ))}
          <p className="text-xs text-muted-foreground pt-2">
            Disabling a category causes the proxy to skip that classifier
            entirely. Settings take effect within ~30 seconds.
          </p>
          <div className="space-y-2 border-t pt-3">
            <div className="flex items-center justify-between gap-3">
              <Label>Text content strictness</Label>
              <span className="font-mono text-xs text-muted-foreground">
                {draft.text.nsfw_threshold.toFixed(2)}
              </span>
            </div>
            <input
              type="range"
              min={0.1}
              max={1}
              step={0.05}
              value={draft.text.nsfw_threshold}
              onChange={(e) => patchTextThreshold(Number(e.target.value))}
              className="w-full"
            />
            <p className="text-xs text-muted-foreground">
              Blocks text when the NSFW score is at or above this value. Move
              left to block more borderline text; move right to block only
              higher-confidence matches.
            </p>
          </div>
          <div className="space-y-2 border-t pt-3">
            <Label>Image strictness</Label>
            <p className="text-xs text-muted-foreground">
              Lower values block more borderline images (lingerie, swimwear,
              suggestive ads). The classifier always sees the full-resolution
              image — these sliders just decide when to act on its scores.
              Profile-level thresholds, when set, override these values.
            </p>
            {(
              [
                ["sexy_threshold", "Suggestive / lingerie"],
                ["porn_threshold", "Explicit"],
                ["hentai_threshold", "Hentai / cartoon explicit"],
              ] as const
            ).map(([key, label]) => (
              <div key={key} className="space-y-1">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-sm">{label}</span>
                  <span className="font-mono text-xs text-muted-foreground">
                    {draft.image[key].toFixed(2)}
                  </span>
                </div>
                <input
                  type="range"
                  min={0.1}
                  max={1}
                  step={0.05}
                  value={draft.image[key]}
                  onChange={(e) =>
                    patchImageThreshold(key, Number(e.target.value))
                  }
                  className="w-full"
                />
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Global allow / deny lists</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label>Always allow (domains, one per line)</Label>
            <textarea
              className="w-full mt-1 h-24 rounded-md border bg-background px-3 py-2 text-sm font-mono"
              value={allowText}
              onChange={(e) => setAllowText(e.target.value)}
              placeholder="example.com&#10;*.school.edu"
            />
            <p className="text-xs text-muted-foreground mt-1">
              These entries override profile/domain blocks. They do not block
              other domains unless the advanced allowlist switch below is on.
            </p>
          </div>
          <label className="flex items-center gap-2 text-sm rounded-md border border-dashed p-3 advanced-only">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={draft.lists.enforce_global_allowlist}
              onChange={(e) =>
                setDraft((d) =>
                  d
                    ? {
                        ...d,
                        lists: {
                          ...d.lists,
                          enforce_global_allowlist: e.target.checked,
                        },
                      }
                    : d,
                )
              }
            />
            Advanced: only allow domains listed above; block every other domain.
          </label>
          <div>
            <Label>Always block (domains, one per line)</Label>
            <textarea
              className="w-full mt-1 h-24 rounded-md border bg-background px-3 py-2 text-sm font-mono"
              value={denyText}
              onChange={(e) => setDenyText(e.target.value)}
              placeholder="ads.example.net&#10;*.tracker.io"
            />
          </div>
          <div>
            <Label>Block URLs containing keywords</Label>
            <textarea
              className="w-full mt-1 h-24 rounded-md border bg-background px-3 py-2 text-sm font-mono"
              value={keywordText}
              onChange={(e) => setKeywordText(e.target.value)}
              placeholder="onlyfans&#10;casino"
            />
          </div>
        </CardContent>
      </Card>

      <Card className="advanced-only">
        <CardHeader>
          <CardTitle>Notifications</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <label className="flex items-center gap-3 text-sm">
            <input
              type="checkbox"
              className="h-4 w-4"
              checked={draft.notifications.enabled}
              onChange={(e) => patchNotif("enabled", e.target.checked)}
            />
            Enable notifications
          </label>
          <div>
            <Label>ntfy URL (e.g. https://ntfy.sh/yourtopic)</Label>
            <Input
              value={draft.notifications.ntfy_url}
              onChange={(e) => patchNotif("ntfy_url", e.target.value)}
              placeholder="https://ntfy.sh/seenoevil-alerts"
            />
          </div>
          <div>
            <Label>Webhook URL</Label>
            <Input
              value={draft.notifications.webhook_url}
              onChange={(e) => patchNotif("webhook_url", e.target.value)}
              placeholder="https://example.com/hook"
            />
          </div>
          <div>
            <Label>Webhook bearer token (optional)</Label>
            <Input
              type="password"
              value={draft.notifications.webhook_token}
              onChange={(e) => patchNotif("webhook_token", e.target.value)}
            />
          </div>
          <div className="grid grid-cols-3 gap-2 pt-1">
            {(
              [
                ["on_block", "On block"],
                ["on_quarantine", "On quarantine"],
                ["on_panic", "On panic"],
              ] as const
            ).map(([k, l]) => (
              <label key={k} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  className="h-4 w-4"
                  checked={draft.notifications[k]}
                  onChange={(e) => patchNotif(k, e.target.checked)}
                />
                {l}
              </label>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function ModeToggle() {
  const [mode, setMode] = useState<"basic" | "advanced">(() => {
    if (typeof window === "undefined") return "basic";
    return (
      (localStorage.getItem("seenoevil:settings-mode") as
        | "basic"
        | "advanced"
        | null) ?? "basic"
    );
  });
  useEffect(() => {
    localStorage.setItem("seenoevil:settings-mode", mode);
    document.body.dataset.settingsMode = mode;
    return () => {
      delete document.body.dataset.settingsMode;
    };
  }, [mode]);
  return (
    <div className="inline-flex rounded-md border bg-background">
      {(["basic", "advanced"] as const).map((m) => (
        <button
          key={m}
          type="button"
          onClick={() => setMode(m)}
          className={`px-3 py-1.5 text-sm capitalize ${
            mode === m
              ? "bg-primary text-primary-foreground"
              : "text-muted-foreground hover:bg-muted"
          }`}
        >
          {m}
        </button>
      ))}
    </div>
  );
}

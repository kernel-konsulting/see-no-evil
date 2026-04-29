import { useQuery } from "@tanstack/react-query";
import { Download, ShieldCheck, Globe, AlertTriangle } from "lucide-react";
import { CA_DOWNLOAD_URL, getCaInfo, type CaInstallSteps } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";

const PLATFORMS: { key: keyof CaInstallSteps; label: string }[] = [
  { key: "macos", label: "macOS" },
  { key: "ios", label: "iOS / iPadOS" },
  { key: "android", label: "Android" },
  { key: "windows", label: "Windows" },
  { key: "linux", label: "Linux" },
];

export default function Setup() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["ca-info"],
    queryFn: getCaInfo,
  });

  return (
    <div className="space-y-6 max-w-4xl">
      <div>
        <h1 className="text-2xl font-bold">Device setup</h1>
        <p className="text-muted-foreground mt-1">
          Install the see-no-evil root certificate on each device so the proxy
          can inspect HTTPS traffic, then point the device at the proxy.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-5 w-5" /> Root certificate
          </CardTitle>
          <CardDescription>
            Public certificate only — the matching private key never leaves the
            proxy container.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {isLoading && (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
          {isError && (
            <div className="flex items-start gap-2 rounded-md bg-yellow-50 px-3 py-2 text-sm text-yellow-800">
              <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0" />
              <span>
                Could not reach the certificate endpoint:{" "}
                {(error as Error)?.message}
              </span>
            </div>
          )}
          {data && !data.present && (
            <div className="flex items-start gap-2 rounded-md bg-yellow-50 px-3 py-2 text-sm text-yellow-800">
              <AlertTriangle className="h-4 w-4 mt-0.5 flex-shrink-0" />
              <span>
                Certificate not yet generated at <code>{data.path}</code>. The
                proxy creates it on first start; check that the proxy container
                is running.
              </span>
            </div>
          )}
          <div>
            <Button asChild disabled={!data?.present}>
              <a href={CA_DOWNLOAD_URL} download="seenoevil-ca.crt">
                <Download className="h-4 w-4 mr-2" />
                Download seenoevil-ca.crt
              </a>
            </Button>
            {data?.present && (
              <span className="ml-3 text-xs text-muted-foreground">
                {data.size_bytes} bytes · {data.path}
              </span>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Install instructions</CardTitle>
          <CardDescription>Choose your device platform.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-6">
          {PLATFORMS.map(({ key, label }) => {
            const steps = data?.install?.[key] ?? [];
            return (
              <div key={key}>
                <h3 className="font-semibold mb-2">{label}</h3>
                {steps.length === 0 ? (
                  <p className="text-sm text-muted-foreground">—</p>
                ) : (
                  <ol className="list-decimal list-inside space-y-1 text-sm">
                    {steps.map((s, i) => (
                      <li key={i}>{s}</li>
                    ))}
                  </ol>
                )}
              </div>
            );
          })}
        </CardContent>
      </Card>

      <Card className="border-yellow-300 bg-yellow-50/40">
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-yellow-700" />
            Still seeing “untrusted” warnings?
          </CardTitle>
          <CardDescription>
            On macOS, adding the certificate to the System keychain is{" "}
            <strong>not enough</strong> — you must also explicitly mark it as{" "}
            <em>Always Trust</em>. This is the most common cause of every site
            showing a certificate warning even after “installing” the cert.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <div>
            <h4 className="font-semibold mb-1">macOS — set trust override</h4>
            <ol className="list-decimal list-inside space-y-1">
              <li>
                Open <strong>Keychain Access</strong> → <strong>System</strong>{" "}
                keychain.
              </li>
              <li>
                Find <code>see-no-evil MITM CA</code> (search top-right).
              </li>
              <li>
                Double-click it → expand <strong>Trust</strong>.
              </li>
              <li>
                Set <strong>“When using this certificate”</strong> →{" "}
                <strong>Always Trust</strong>.
              </li>
              <li>Close the window — you’ll be prompted for your password.</li>
              <li>Quit and re-open the browser.</li>
            </ol>
            <p className="mt-2 text-xs text-muted-foreground">
              Verify with{" "}
              <code className="font-mono">
                security dump-trust-settings -d | grep -A1 see-no-evil
              </code>
              . If it prints nothing, trust is not yet set.
            </p>
          </div>
          <div>
            <h4 className="font-semibold mb-1">
              No block decisions in the audit log?
            </h4>
            <p>
              When the CA isn’t trusted, browsers fall back to passing HTTPS
              traffic <em>through</em> the proxy as an opaque tunnel — so
              classifiers never see the content and no decisions are recorded.
              Fixing the trust step above usually makes the audit log start
              filling up immediately.
            </p>
          </div>
        </CardContent>
      </Card>

      {data?.proxy_setup && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Globe className="h-5 w-5" /> Point the device at the proxy
            </CardTitle>
            <CardDescription>{data.proxy_setup.summary}</CardDescription>
          </CardHeader>
          <CardContent>
            <dl className="space-y-2 text-sm">
              <div className="grid grid-cols-[7rem_1fr] gap-2">
                <dt className="font-medium">macOS</dt>
                <dd className="text-muted-foreground">
                  {data.proxy_setup.macos}
                </dd>
              </div>
              <div className="grid grid-cols-[7rem_1fr] gap-2">
                <dt className="font-medium">iOS</dt>
                <dd className="text-muted-foreground">
                  {data.proxy_setup.ios}
                </dd>
              </div>
              <div className="grid grid-cols-[7rem_1fr] gap-2">
                <dt className="font-medium">Android</dt>
                <dd className="text-muted-foreground">
                  {data.proxy_setup.android}
                </dd>
              </div>
              <div className="grid grid-cols-[7rem_1fr] gap-2">
                <dt className="font-medium">Windows</dt>
                <dd className="text-muted-foreground">
                  {data.proxy_setup.windows}
                </dd>
              </div>
            </dl>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

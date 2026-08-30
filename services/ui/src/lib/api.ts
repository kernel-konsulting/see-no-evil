import axios from "axios";
import { clearStoredAuth } from "./storage";

// ---------------------------------------------------------------------------
// Types — mirror FastAPI schemas.py
// ---------------------------------------------------------------------------

export interface TokenResponse {
  email: string;
}

export interface Device {
  id: number;
  mac: string;
  name: string | null;
  profile_id: number;
  bypass_proxy: boolean;
  ip: string | null;
  vendor: string | null;
  last_seen_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface Profile {
  id: number;
  name: string;
  description: string;
  image_thresholds: Record<string, number>;
  schedule: Record<string, string>;
  quota_minutes_per_day: number;
  allow_domains: string[];
  enforce_allowlist: boolean;
  deny_domains: string[];
  deny_url_keywords: string[];
  allow_youtube_channels: string[];
  deny_youtube_channels: string[];
  notify_on_block: boolean;
  created_at: string;
  updated_at: string;
}

export interface AuditEntry {
  id: number;
  ts: string;
  device_id: number | null;
  profile_id: number | null;
  url: string;
  content_type: string | null;
  decision: "allow" | "block" | "warn";
  reason: string;
  classifier_scores: Record<string, unknown>;
  thumbnail_b64: string | null;
  signature_valid: boolean | null;
}

export interface HealthResponse {
  status: "ok" | "ready";
}

// ---------------------------------------------------------------------------
// Axios instance
// ---------------------------------------------------------------------------

export const http = axios.create({
  baseURL: "/v1",
  headers: { "Content-Type": "application/json" },
  withCredentials: true,
});

// On 401 redirect to /login.
http.interceptors.response.use(
  (res) => res,
  (err) => {
    if (err.response?.status === 401) {
      clearStoredAuth();
      window.location.href = "/login";
    }
    return Promise.reject(err);
  },
);

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export async function login(
  username: string,
  password: string,
): Promise<TokenResponse> {
  const { data } = await http.post<TokenResponse>("/auth/login", {
    email: username,
    password,
  });
  return data;
}

export interface MeResponse {
  email: string;
  role: "admin" | "viewer" | string;
}

export async function getMe(): Promise<MeResponse> {
  const { data } = await http.get<MeResponse>("/auth/me");
  return data;
}

export interface OidcInfo {
  enabled: boolean;
  label: string;
}

export async function getOidcInfo(): Promise<OidcInfo> {
  const { data } = await http.get<OidcInfo>("/auth/oidc/info");
  return data;
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

export interface WindowStats {
  label: string;
  seconds: number | null;
  allowed: number;
  blocked: number;
  quarantined_pending: number;
}

export interface DashboardStats {
  devices: number;
  quarantine_pending: number;
  windows: WindowStats[];
}

export async function getDashboardStats(): Promise<DashboardStats> {
  const { data } = await http.get<DashboardStats>("/dashboard/stats");
  return data;
}

// ---------------------------------------------------------------------------
// Devices
// ---------------------------------------------------------------------------

export async function listDevices(): Promise<Device[]> {
  const { data } = await http.get<Device[]>("/devices");
  return data;
}

export async function createDevice(
  payload: Pick<Device, "mac" | "name" | "profile_id" | "bypass_proxy">,
): Promise<Device> {
  const { data } = await http.post<Device>("/devices", payload);
  return data;
}

export async function updateDevice(
  id: number,
  payload: Partial<Pick<Device, "name" | "profile_id" | "bypass_proxy">>,
): Promise<Device> {
  const { data } = await http.patch<Device>(`/devices/${id}`, payload);
  return data;
}

export async function deleteDevice(id: number): Promise<void> {
  await http.delete(`/devices/${id}`);
}

// ---------------------------------------------------------------------------
// Profiles
// ---------------------------------------------------------------------------

export async function listProfiles(): Promise<Profile[]> {
  const { data } = await http.get<Profile[]>("/profiles");
  return data;
}

export async function createProfile(
  payload: Pick<Profile, "name"> &
    Partial<Omit<Profile, "id" | "created_at" | "updated_at">>,
): Promise<Profile> {
  const { data } = await http.post<Profile>("/profiles", payload);
  return data;
}

export async function updateProfile(
  id: number,
  payload: Partial<Omit<Profile, "id" | "created_at" | "updated_at">>,
): Promise<Profile> {
  const { data } = await http.patch<Profile>(`/profiles/${id}`, payload);
  return data;
}

export async function deleteProfile(id: number): Promise<void> {
  await http.delete(`/profiles/${id}`);
}

// ---------------------------------------------------------------------------
// Audit log
// ---------------------------------------------------------------------------

export async function listAudit(limit = 100): Promise<AuditEntry[]> {
  const { data } = await http.get<AuditEntry[]>("/audit", {
    params: { limit },
  });
  return data;
}

export interface AuditPageOptions {
  limit?: number;
  beforeId?: number | null;
}

export async function listAuditPage(
  opts: AuditPageOptions = {},
): Promise<AuditEntry[]> {
  const params: Record<string, number> = { limit: opts.limit ?? 100 };
  if (opts.beforeId != null) params.before_id = opts.beforeId;
  const { data } = await http.get<AuditEntry[]>("/audit", { params });
  return data;
}

export async function clearAudit(): Promise<void> {
  await http.delete("/audit");
}

// ---------------------------------------------------------------------------
// Scanner
// ---------------------------------------------------------------------------

export interface ScanResult {
  ok: boolean;
  cidr?: string;
  devices_found?: number;
  devices_created?: number;
  duration_seconds?: number;
  note?: string;
  error?: string;
}

export async function scanNetwork(): Promise<ScanResult> {
  const { data } = await http.post<ScanResult>("/scanner/scan", null, {
    timeout: 180_000,
  });
  return data;
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await http.get<HealthResponse>("/healthz", {
    baseURL: "",
    withCredentials: false,
  });
  return data;
}

// ---------------------------------------------------------------------------
// MITM root CA
// ---------------------------------------------------------------------------

export interface CaInstallSteps {
  macos: string[];
  ios: string[];
  android: string[];
  windows: string[];
  linux: string[];
}

export interface CaProxySetup {
  summary: string;
  macos: string;
  ios: string;
  android: string;
  windows: string;
}

export interface CaInfo {
  present: boolean;
  path: string;
  size_bytes: number;
  download_url: string;
  install: CaInstallSteps;
  proxy_setup: CaProxySetup;
}

export async function getCaInfo(): Promise<CaInfo> {
  const { data } = await http.get<CaInfo>("/v1/ca/info", {
    baseURL: "",
    withCredentials: false,
  });
  return data;
}

export const CA_DOWNLOAD_URL = "/v1/ca/cert";

// ---------------------------------------------------------------------------
// Quarantine
// ---------------------------------------------------------------------------

export interface QuarantineItem {
  id: number;
  ts: string;
  device_id: number | null;
  profile_id: number | null;
  url: string;
  content_type: string | null;
  reason: string;
  classifier_scores: Record<string, number>;
  thumbnail_b64: string | null;
  status: "pending" | "allowed" | "denied";
  resolved_at: string | null;
  resolved_by: string | null;
  flag_note: string | null;
  flagged_by: string | null;
  flagged_at: string | null;
}

export async function listQuarantine(
  status: "pending" | "allowed" | "denied" | "all" = "pending",
  limit = 100,
): Promise<QuarantineItem[]> {
  const { data } = await http.get<QuarantineItem[]>("/quarantine", {
    params: { status, limit },
  });
  return data;
}

export async function allowQuarantine(id: number): Promise<QuarantineItem> {
  const { data } = await http.post<QuarantineItem>(`/quarantine/${id}/allow`);
  return data;
}

export async function denyQuarantine(id: number): Promise<QuarantineItem> {
  const { data } = await http.post<QuarantineItem>(`/quarantine/${id}/deny`);
  return data;
}

export interface BulkQuarantineResult {
  updated: number;
}

export async function allowAllQuarantine(): Promise<BulkQuarantineResult> {
  const { data } = await http.post<BulkQuarantineResult>(
    "/quarantine/bulk-allow",
    {},
  );
  return data;
}

export async function denyAllQuarantine(): Promise<BulkQuarantineResult> {
  const { data } = await http.post<BulkQuarantineResult>(
    "/quarantine/bulk-deny",
    {},
  );
  return data;
}

export async function flagQuarantine(
  id: number,
  note: string,
): Promise<QuarantineItem> {
  const { data } = await http.post<QuarantineItem>(`/quarantine/${id}/flag`, {
    note,
  });
  return data;
}

export async function deleteQuarantine(id: number): Promise<void> {
  await http.delete(`/quarantine/${id}`);
}

// ---------------------------------------------------------------------------
// Settings (runtime)
// ---------------------------------------------------------------------------

export interface RuntimeSettings {
  inspect: {
    image: boolean;
    video: boolean;
    text: boolean;
    domain: boolean;
    url: boolean;
  };
  lists: {
    global_allow_domains: string[];
    enforce_global_allowlist: boolean;
    global_deny_domains: string[];
    global_deny_keywords: string[];
  };
  text: {
    nsfw_threshold: number;
  };
  image: {
    sexy_threshold: number;
    porn_threshold: number;
    hentai_threshold: number;
  };
  notifications: {
    enabled: boolean;
    ntfy_url: string;
    webhook_url: string;
    webhook_token: string;
    on_block: boolean;
    on_quarantine: boolean;
    on_panic: boolean;
  };
}

export async function getSettings(): Promise<RuntimeSettings> {
  const { data } = await http.get<RuntimeSettings>("/settings");
  return data;
}

export async function updateSettings(
  patch: Partial<RuntimeSettings>,
): Promise<RuntimeSettings> {
  const { data } = await http.put<RuntimeSettings>("/settings", patch);
  return data;
}

// ---------------------------------------------------------------------------
// Users
// ---------------------------------------------------------------------------

export interface AdminUser {
  id: number;
  email: string;
  role: string;
  disabled: boolean;
  created_at: string;
  updated_at: string;
}

export async function listUsers(): Promise<AdminUser[]> {
  const { data } = await http.get<AdminUser[]>("/users");
  return data;
}

export async function createUser(payload: {
  email: string;
  password: string;
  role?: string;
}): Promise<AdminUser> {
  const { data } = await http.post<AdminUser>("/users", payload);
  return data;
}

export async function updateUser(
  id: number,
  payload: { password?: string; role?: string; disabled?: boolean },
): Promise<AdminUser> {
  const { data } = await http.patch<AdminUser>(`/users/${id}`, payload);
  return data;
}

export async function deleteUser(id: number): Promise<void> {
  await http.delete(`/users/${id}`);
}

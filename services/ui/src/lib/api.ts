import axios from "axios";

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
      localStorage.removeItem("sne_authenticated");
      window.location.href = "/login";
    }
    return Promise.reject(err);
  },
);

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export async function login(username: string, password: string): Promise<TokenResponse> {
  const { data } = await http.post<TokenResponse>("/auth/login", {
    email: username,
    password,
  });
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
  payload: Pick<Profile, "name"> & Partial<Omit<Profile, "id" | "created_at" | "updated_at">>,
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

export async function listAudit(limit = 50): Promise<AuditEntry[]> {
  const { data } = await http.get<AuditEntry[]>("/audit", {
    params: { limit },
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

export async function deleteQuarantine(id: number): Promise<void> {
  await http.delete(`/quarantine/${id}`);
}

import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { AuthProvider } from "@/lib/auth";
import { useAuth } from "@/lib/use-auth";
import Layout from "@/components/Layout";
import Login from "@/pages/Login";
import Dashboard from "@/pages/Dashboard";
import Devices from "@/pages/Devices";
import Profiles from "@/pages/Profiles";
import Quarantine from "@/pages/Quarantine";
import AuditLog from "@/pages/AuditLog";
import Setup from "@/pages/Setup";
import SettingsPage from "@/pages/Settings";
import Users from "@/pages/Users";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
    },
  },
});

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const { authenticated } = useAuth();
  return authenticated ? <>{children}</> : <Navigate to="/login" replace />;
}

function AdminRoute({ children }: { children: React.ReactNode }) {
  const { authenticated, role, me } = useAuth();
  if (!authenticated) return <Navigate to="/login" replace />;
  // Wait for role to load before deciding (avoids a redirect flash).
  if (me === null) return null;
  if (role !== "admin") return <Navigate to="/dashboard" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route
              path="/"
              element={
                <PrivateRoute>
                  <Layout />
                </PrivateRoute>
              }
            >
              <Route index element={<Navigate to="/dashboard" replace />} />
              <Route path="dashboard" element={<Dashboard />} />
              <Route
                path="quarantine"
                element={
                  <AdminRoute>
                    <Quarantine />
                  </AdminRoute>
                }
              />
              <Route path="audit" element={<AuditLog />} />
              <Route
                path="devices"
                element={
                  <AdminRoute>
                    <Devices />
                  </AdminRoute>
                }
              />
              <Route
                path="profiles"
                element={
                  <AdminRoute>
                    <Profiles />
                  </AdminRoute>
                }
              />
              <Route
                path="settings"
                element={
                  <AdminRoute>
                    <SettingsPage />
                  </AdminRoute>
                }
              />
              <Route
                path="users"
                element={
                  <AdminRoute>
                    <Users />
                  </AdminRoute>
                }
              />
              <Route
                path="setup"
                element={
                  <AdminRoute>
                    <Setup />
                  </AdminRoute>
                }
              />
            </Route>
          </Routes>
          <Toaster />
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
}

import { useEffect, useState, type ReactNode } from "react";
import { getMe, login as apiLogin, type MeResponse } from "./api";
import { AuthContext } from "./auth-context";
import { clearStoredAuth, getStoredAuth, setStoredAuth } from "./storage";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState<boolean>(getStoredAuth);
  const [me, setMe] = useState<MeResponse | null>(null);

  // Whenever we believe we're authenticated, load /v1/auth/me to learn the
  // user's role. We avoid blocking initial render and gracefully degrade if
  // the call fails (the axios 401 interceptor handles redirect-to-login).
  useEffect(() => {
    let cancelled = false;
    if (!authenticated) {
      setMe(null);
      return;
    }
    getMe()
      .then((m) => {
        if (!cancelled) setMe(m);
      })
      .catch(() => {
        if (!cancelled) setMe(null);
      });
    return () => {
      cancelled = true;
    };
  }, [authenticated]);

  async function login(username: string, password: string) {
    await apiLogin(username, password);
    setStoredAuth();
    setAuthenticated(true);
    try {
      setMe(await getMe());
    } catch {
      // ignore; effect above will retry on next render
    }
  }

  function logout() {
    clearStoredAuth();
    setAuthenticated(false);
    setMe(null);
  }

  const value = {
    authenticated,
    me,
    role: me?.role ?? null,
    isAdmin: me?.role === "admin",
    login,
    logout,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

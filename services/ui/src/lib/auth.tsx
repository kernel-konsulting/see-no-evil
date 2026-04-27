import { useState, type ReactNode } from "react";
import { login as apiLogin } from "./api";
import { AuthContext } from "./auth-context";
import { clearStoredAuth, getStoredAuth, setStoredAuth } from "./storage";

export function AuthProvider({ children }: { children: ReactNode }) {
  const [authenticated, setAuthenticated] = useState<boolean>(getStoredAuth);

  async function login(username: string, password: string) {
    await apiLogin(username, password);
    setStoredAuth();
    setAuthenticated(true);
  }

  function logout() {
    clearStoredAuth();
    setAuthenticated(false);
  }

  const value = { authenticated, login, logout };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

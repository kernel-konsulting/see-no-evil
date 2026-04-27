import { createContext, useContext, useState } from "react";
import { login as apiLogin } from "./api";

interface AuthContextValue {
  authenticated: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [authenticated, setAuthenticated] = useState<boolean>(
    () => localStorage.getItem("sne_authenticated") === "true",
  );

  async function login(username: string, password: string) {
    await apiLogin(username, password);
    localStorage.setItem("sne_authenticated", "true");
    setAuthenticated(true);
  }

  function logout() {
    localStorage.removeItem("sne_authenticated");
    setAuthenticated(false);
  }

  const value = { authenticated, login, logout };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}

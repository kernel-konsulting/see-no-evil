import { createContext } from "react";
import type { MeResponse } from "./api";

export interface AuthContextValue {
  authenticated: boolean;
  me: MeResponse | null;
  role: string | null;
  isAdmin: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

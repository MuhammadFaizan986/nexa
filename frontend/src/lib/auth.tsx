"use client";

/**
 * Who is signed in, for the whole app.
 *
 * On first load we ask the API `/me` with whatever token is stored. If that
 * fails, the user is sent to the login page. Everything else in the UI can then
 * assume `user` and `tenant` exist.
 */

import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { request, tokens } from "@/lib/api";
import type { Tenant, User } from "@/lib/types";

type AuthState = {
  user: User | null;
  tenant: Tenant | null;
  loading: boolean;
  isAdmin: boolean;
  login: (tenantSlug: string, email: string, password: string) => Promise<void>;
  register: (organisation: string, email: string, password: string) => Promise<void>;
  logout: () => void;
  reload: () => Promise<void>;
};

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [loading, setLoading] = useState(true);
  const router = useRouter();

  const reload = useCallback(async () => {
    if (!tokens.access()) {
      setUser(null);
      setTenant(null);
      setLoading(false);
      return;
    }
    try {
      const me = await request<{ user: User; tenant: Tenant }>("/me");
      setUser(me.user);
      setTenant(me.tenant);
    } catch {
      tokens.clear();
      setUser(null);
      setTenant(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const login = async (tenantSlug: string, email: string, password: string) => {
    const data = await request<{ access_token: string; refresh_token: string }>("/auth/login", {
      method: "POST",
      body: { tenant_slug: tenantSlug, email, password },
    });
    tokens.save(data.access_token, data.refresh_token);
    await reload();
    router.push("/chat");
  };

  const register = async (organisation: string, email: string, password: string) => {
    const data = await request<{
      tokens: { access_token: string; refresh_token: string };
    }>("/auth/register", {
      method: "POST",
      body: { tenant_name: organisation, email, password },
    });
    tokens.save(data.tokens.access_token, data.tokens.refresh_token);
    await reload();
    router.push("/documents");
  };

  const logout = () => {
    tokens.clear();
    setUser(null);
    setTenant(null);
    router.push("/login");
  };

  const isAdmin = user?.role === "owner" || user?.role === "admin";

  return (
    <AuthContext.Provider
      value={{ user, tenant, loading, isAdmin, login, register, logout, reload }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}

// Auth store — small Zustand slice with user + tokens.

"use client";

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { api, setTokens } from "./api";
import type { TokenPair, User } from "./types";

interface AuthState {
  user: User | null;
  loading: boolean;
  setUser: (u: User | null) => void;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, full_name?: string) => Promise<void>;
  logout: () => Promise<void>;
  fetchMe: () => Promise<void>;
}

export const useAuth = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      loading: false,

      setUser: (u) => set({ user: u }),

      async login(email, password) {
        set({ loading: true });
        try {
          const { data } = await api.post<TokenPair>("/auth/login", { email, password });
          setTokens(data);
          const me = await api.get<User>("/auth/me");
          set({ user: me.data });
        } finally {
          set({ loading: false });
        }
      },

      async register(email, password, full_name) {
        await api.post<User>("/auth/register", { email, password, full_name });
        await useAuth.getState().login(email, password);
      },

      async logout() {
        try {
          const refresh = localStorage.getItem("cc.refresh");
          if (refresh) await api.post("/auth/logout", { refresh_token: refresh });
        } catch {
          // ignore — token may already be invalid
        }
        setTokens(null);
        set({ user: null });
      },

      async fetchMe() {
        try {
          const me = await api.get<User>("/auth/me");
          set({ user: me.data });
        } catch {
          setTokens(null);
          set({ user: null });
        }
      },
    }),
    { name: "cc-auth", storage: createJSONStorage(() => localStorage) }
  )
);

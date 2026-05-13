// Axios client with automatic access-token refresh on 401.
// Tokens live in memory for the SPA tab plus localStorage as a fallback so a
// hard reload keeps you signed in.

import axios, { type AxiosInstance, type InternalAxiosRequestConfig } from "axios";
import type { TokenPair } from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const ACCESS_KEY = "cc.access";
const REFRESH_KEY = "cc.refresh";

let accessToken: string | null = null;
let refreshToken: string | null = null;
let refreshPromise: Promise<string> | null = null;

if (typeof window !== "undefined") {
  accessToken = localStorage.getItem(ACCESS_KEY);
  refreshToken = localStorage.getItem(REFRESH_KEY);
}

export function setTokens(pair: TokenPair | null) {
  accessToken = pair?.access_token ?? null;
  refreshToken = pair?.refresh_token ?? null;
  if (typeof window !== "undefined") {
    if (pair) {
      localStorage.setItem(ACCESS_KEY, pair.access_token);
      localStorage.setItem(REFRESH_KEY, pair.refresh_token);
    } else {
      localStorage.removeItem(ACCESS_KEY);
      localStorage.removeItem(REFRESH_KEY);
    }
  }
}

export function getAccessToken(): string | null {
  return accessToken;
}

export function getRefreshToken(): string | null {
  return refreshToken;
}

export const api: AxiosInstance = axios.create({
  baseURL: `${API_URL}/api/v1`,
  withCredentials: false,
});

api.interceptors.request.use((config: InternalAxiosRequestConfig) => {
  if (accessToken) {
    config.headers.Authorization = `Bearer ${accessToken}`;
  }
  return config;
});

async function attemptRefresh(): Promise<string> {
  if (refreshPromise) return refreshPromise;
  if (!refreshToken) throw new Error("no refresh token");
  refreshPromise = axios
    .post<TokenPair>(`${API_URL}/api/v1/auth/refresh`, { refresh_token: refreshToken })
    .then((res) => {
      setTokens(res.data);
      return res.data.access_token;
    })
    .finally(() => {
      refreshPromise = null;
    });
  return refreshPromise;
}

api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const original = error.config;
    if (
      error.response?.status === 401 &&
      !original._retry &&
      refreshToken &&
      original.url !== "/auth/refresh"
    ) {
      original._retry = true;
      try {
        const newAccess = await attemptRefresh();
        original.headers.Authorization = `Bearer ${newAccess}`;
        return api.request(original);
      } catch {
        setTokens(null);
        if (typeof window !== "undefined") {
          window.location.href = "/login";
        }
      }
    }
    return Promise.reject(error);
  }
);

export function buildWsUrl(path: string): string {
  const base = (process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000").replace(/\/$/, "");
  return `${base}${path.startsWith("/") ? path : `/${path}`}`;
}

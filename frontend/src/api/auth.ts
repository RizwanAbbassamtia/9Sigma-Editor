/**
 * Sign-in calls (`/api/auth`): who is signed in, sign in, sign out, change the password.
 *
 * A 401 anywhere else means the session ended; `api/client.ts` reports it to the
 * `onUnauthorized` listeners and the app shell sends the person to the sign-in page.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import type { AuthStatus, ChangePasswordBody, LoginBody, PublicUser } from "../types/auth";
import { api, isUnauthorized, onUnauthorized } from "./client";

export const authKeys = {
  all: ["auth"] as const,
  me: ["auth", "me"] as const,
  status: ["auth", "status"] as const,
};

export function getAuthStatus(): Promise<AuthStatus> {
  return api.get<AuthStatus>("/api/auth/status");
}

/** The signed-in person, or null (a 401 is an answer here, not a problem). */
export async function getMe(): Promise<PublicUser | null> {
  try {
    return await api.get<PublicUser>("/api/auth/me", { reportUnauthorized: false });
  } catch (error) {
    if (isUnauthorized(error)) return null;
    throw error;
  }
}

export function login(body: LoginBody): Promise<PublicUser> {
  return api.post<PublicUser>("/api/auth/login", body, { reportUnauthorized: false });
}

export function logout(): Promise<void> {
  return api.post<void>("/api/auth/logout", {});
}

export function changePassword(body: ChangePasswordBody): Promise<PublicUser> {
  return api.post<PublicUser>("/api/auth/change-password", body);
}

// ---- hooks ------------------------------------------------------------------------------------

/** The signed-in person: `data` is a user, `null` when nobody is signed in. */
export function useMe() {
  return useQuery({ queryKey: authKeys.me, queryFn: getMe, staleTime: 60_000, retry: 1 });
}

export function useAuthStatus() {
  return useQuery({ queryKey: authKeys.status, queryFn: getAuthStatus, staleTime: 60_000, retry: 1 });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: login,
    onSuccess: (user) => {
      queryClient.setQueryData(authKeys.me, user);
      void queryClient.invalidateQueries({ queryKey: authKeys.status });
    },
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: logout,
    onSettled: () => {
      queryClient.setQueryData(authKeys.me, null);
      queryClient.removeQueries({ predicate: (query) => query.queryKey[0] !== "auth" });
      void queryClient.invalidateQueries({ queryKey: authKeys.status });
    },
  });
}

export function useChangePassword() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: changePassword,
    onSuccess: (user) => queryClient.setQueryData(authKeys.me, user),
  });
}

/**
 * True once any call answered 401 while this component was mounted: the session ended, so the
 * shell shows the sign-in page with a "signed out" notice.
 */
export function useSessionEnded(): boolean {
  const queryClient = useQueryClient();
  const [ended, setEnded] = useState(false);
  useEffect(
    () =>
      onUnauthorized(() => {
        queryClient.setQueryData(authKeys.me, null);
        setEnded(true);
      }),
    [queryClient],
  );
  return ended;
}

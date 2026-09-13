import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "@/api/client";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (failureCount, error) => {
        if (error instanceof ApiError && error.status === 401) return false;
        return failureCount < 1;
      },
      staleTime: 5_000,
      // Refetch on focus so a user returning to a stale tab (dead SSE,
      // laptop wake) sees current state without a manual reload.
      refetchOnWindowFocus: true,
    },
    mutations: {
      retry: false,
    },
  },
});

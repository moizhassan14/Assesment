// Same-origin access works with the Angular development proxy and production nginx.
export const environment = {
  apiBaseUrl: "/api",
  pollIntervalMs: 2500,
} as const;

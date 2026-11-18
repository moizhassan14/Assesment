import { Routes } from "@angular/router";
export const routes: Routes = [
  {
    path: "",
    pathMatch: "full",
    loadComponent: () => import("./features/overview").then((m) => m.Overview),
  },
  {
    path: "brands/new",
    loadComponent: () =>
      import("./features/brand-form").then((m) => m.BrandForm),
  },
  {
    path: "brands/:brandId/edit",
    loadComponent: () =>
      import("./features/brand-form").then((m) => m.BrandForm),
  },
  {
    path: "brands/:brandId",
    loadComponent: () =>
      import("./features/brand-detail").then((m) => m.BrandDetail),
    children: [
      {
        path: "",
        pathMatch: "full",
        loadComponent: () =>
          import("./features/brand-summary").then((m) => m.BrandSummary),
      },
      {
        path: "history",
        loadComponent: () =>
          import("./features/history").then((m) => m.History),
      },
    ],
  },
  {
    path: "runs/:runId",
    loadComponent: () => import("./features/run-shell").then((m) => m.RunShell),
    children: [
      { path: "", pathMatch: "full", redirectTo: "progress" },
      {
        path: "progress",
        loadComponent: () =>
          import("./features/progress").then((m) => m.RunProgress),
      },
      {
        path: "overview",
        loadComponent: () =>
          import("./features/report").then((m) => m.ReportOverview),
      },
      {
        path: "opportunities",
        loadComponent: () =>
          import("./features/opportunities").then((m) => m.Opportunities),
      },
      {
        path: "recommendations",
        loadComponent: () =>
          import("./features/recommendations").then((m) => m.Recommendations),
      },
    ],
  },
  {
    path: "**",
    loadComponent: () => import("./features/not-found").then((m) => m.NotFound),
  },
];

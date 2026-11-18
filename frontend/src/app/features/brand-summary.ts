import { Component, inject } from "@angular/core";
import { DatePipe } from "@angular/common";
import { RouterLink } from "@angular/router";
import { BrandStore } from "./brand-store";
import { isActive } from "../core/models";
import { Icon, Metric, MetricPipe, Status } from "../shared/ui";
import { StartAnalysis } from "../shared/start-analysis";
@Component({
  imports: [
    DatePipe,
    RouterLink,
    Icon,
    Metric,
    MetricPipe,
    Status,
    StartAnalysis,
  ],
  templateUrl: "./brand-summary.html",
})
export class BrandSummary {
  readonly store = inject(BrandStore);
  readonly active = isActive;
  activeRunId() {
    const run = this.store.brand()?.latest_run;
    return run && isActive(run.status) ? run.id : null;
  }
  hasEvidence() {
    return this.store
      .runs()
      .some((run) => (run.headline?.queries_measured ?? 0) > 0);
  }
}

import { Component, computed, inject } from "@angular/core";
import { DatePipe } from "@angular/common";
import { RouterLink } from "@angular/router";
import { RunStore } from "./run-store";
import {
  Icon,
  Metric,
  MetricPipe,
  State,
  LabelPipe,
  Status,
} from "../shared/ui";
import { CoverageNotice } from "../shared/coverage-notice";
import { Observation } from "../core/models";
@Component({
  imports: [
    DatePipe,
    RouterLink,
    Icon,
    Metric,
    MetricPipe,
    State,
    LabelPipe,
    Status,
    CoverageNotice,
  ],
  templateUrl: "./report.html",
})
export class ReportOverview {
  readonly store = inject(RunStore);
  readonly partialCount = computed(
    () =>
      this.store.report()?.opportunities.filter((o) => o.is_partial).length ??
      0,
  );
  readonly maxMentions = computed(() =>
    Math.max(
      0,
      ...(this.store
        .report()
        ?.visibility?.share_of_voice.map((v) => v.ai_mentions) ?? []),
    ),
  );
  readonly aiHasEvidence = computed(() => {
    const v = this.store.report()?.visibility;
    return !!v && (v.chatgpt.measured > 0 || v.google_ai_overview.present > 0);
  });
  readonly selectedCount = computed(
    () =>
      this.store.report()?.queries.filter((q) => q.selected_for_measurement)
        .length ?? 0,
  );
  readonly freshest = computed(
    () =>
      this.store
        .report()
        ?.observations.filter(
          (o) => o.status !== "failed" && o.status !== "skipped",
        )
        .map((o) => o.fetched_at)
        .sort()
        .at(-1) ?? null,
  );
  priorityCount(priority: "high" | "medium" | "low") {
    return (
      this.store
        .report()
        ?.recommendations.filter((r) => r.priority === priority).length ?? 0
    );
  }
  presence(observation: Observation) {
    if (observation.status === "failed") return "Unknown · measurement failed";
    if (observation.status === "skipped") return "Not measured";
    if (observation.status === "not_present") return "No AI Overview shown";
    if (observation.surface === "google_organic")
      return observation.brand.rank !== null
        ? `Rank #${observation.brand.rank}`
        : "Not found within fetched depth";
    return observation.brand.cited
      ? "Cited"
      : observation.brand.mentioned
        ? "Mentioned, not cited"
        : "Not mentioned or cited";
  }
}

import { Component, computed, inject, signal } from "@angular/core";
import { DatePipe, DecimalPipe } from "@angular/common";
import { RouterLink } from "@angular/router";
import { RunStore } from "./run-store";
import { Icon, State, Status, MetricPipe, LabelPipe } from "../shared/ui";
import { CoverageNotice } from "../shared/coverage-notice";
import { Opportunity, ScoreComponent } from "../core/models";
@Component({
  imports: [
    DatePipe,
    DecimalPipe,
    RouterLink,
    Icon,
    State,
    Status,
    MetricPipe,
    LabelPipe,
    CoverageNotice,
  ],
  templateUrl: "./opportunities.html",
})
export class Opportunities {
  readonly store = inject(RunStore);
  readonly search = signal("");
  readonly confidence = signal("all");
  readonly coverage = signal("all");
  readonly sort = signal("score");
  readonly filtered = computed(() =>
    [...(this.store.report()?.opportunities ?? [])]
      .filter(
        (o) =>
          o.query.toLowerCase().includes(this.search().toLowerCase()) &&
          (this.confidence() === "all" || o.confidence === this.confidence()) &&
          (this.coverage() === "all" ||
            o.is_partial === (this.coverage() === "partial")),
      )
      .sort((a, b) =>
        this.sort() === "query"
          ? a.query.localeCompare(b.query)
          : this.sort() === "volume"
            ? (b.metrics?.search_volume ?? -1) -
              (a.metrics?.search_volume ?? -1)
            : (b.score ?? -1) - (a.score ?? -1),
      ),
  );
  inputValue(event: Event) {
    return (event.target as HTMLInputElement).value;
  }
  components(
    opportunity: Opportunity,
  ): { key: string; value: ScoreComponent }[] {
    return Object.entries(opportunity.components).map(([key, value]) => ({
      key,
      value,
    }));
  }
  observations(id: string) {
    const queryId = this.store
      .report()
      ?.opportunities.find((o) => o.id === id)?.query_id;
    return (
      this.store.report()?.observations.filter((o) => o.query_id === queryId) ??
      []
    );
  }
}

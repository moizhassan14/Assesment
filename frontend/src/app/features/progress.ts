import { Component, inject } from "@angular/core";
import { DatePipe, DecimalPipe } from "@angular/common";
import { RouterLink } from "@angular/router";
import { RunStore } from "./run-store";
import { Icon, Status, MetricPipe } from "../shared/ui";
import { CoverageNotice, issueDescription } from "../shared/coverage-notice";
import { isActive } from "../core/models";
@Component({
  imports: [
    DatePipe,
    DecimalPipe,
    RouterLink,
    Icon,
    Status,
    MetricPipe,
    CoverageNotice,
  ],
  templateUrl: "./progress.html",
})
export class RunProgress {
  readonly store = inject(RunStore);
  readonly active = isActive;
  readonly describe = issueDescription;
}

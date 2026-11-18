import {
  Component,
  computed,
  input,
  output,
  Pipe,
  PipeTransform,
  signal,
  Injectable,
} from "@angular/core";
import { DecimalPipe, PercentPipe } from "@angular/common";
import { errorBody, friendlyError } from "../core/errors";

const paths: Record<string, string> = {
  signal: "M4 18V12M9 18V8M14 18V4M19 18V10",
  grid: "M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z",
  plus: "M12 5v14M5 12h14",
  arrow: "M5 12h14M13 6l6 6-6 6",
  back: "M19 12H5M11 6l-6 6 6 6",
  search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0",
  globe:
    "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18",
  spark: "m12 3 2.7 6.3L21 12l-6.3 2.7L12 21l-2.7-6.3L3 12l6.3-2.7L12 3",
  clock: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 7v5l3 2",
  chart: "M4 3v17h17M8 14l4-4 4 2 5-7",
  check: "m5 12 4 4L19 6",
  alert: "m12 3 10 18H2L12 3M12 9v4M12 16v1",
  refresh: "M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 14 6M18 18a8 8 0 0 1-14-6",
  copy: "M8 8h12v12H8zM16 8V4H4v12h4",
  download: "M12 3v12M7 10l5 5 5-5M4 16v5h16v-5",
  chevron: "m9 5 7 7-7 7",
  close: "M6 6l12 12M6 18 18 6",
  users:
    "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2M13 7a4 4 0 1 1-8 0 4 4 0 0 1 8 0M17 4a4 4 0 0 1 0 8M18 15a4 4 0 0 1 4 4v2",
  file: "M14 2H5v20h14V7l-5-5M14 2v6h5M8 12h8M8 16h6",
  menu: "M4 6h16M4 12h16M4 18h16",
  info: "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 11v6M12 7v1",
  target:
    "M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M17 12a5 5 0 1 1-10 0 5 5 0 0 1 10 0M12 11v2",
  settings: "M4 7h16M4 17h16M8 4v6M16 14v6",
};
@Component({
  selector: "app-icon",
  template:
    '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path [attr.d]="path()"/></svg>',
  styles:
    ":host{display:inline-flex;width:1.2em;height:1.2em;flex-shrink:0;vertical-align:middle}svg{width:100%;height:100%}",
})
export class Icon {
  readonly name = input("signal");
  readonly path = computed(() => paths[this.name()] ?? paths["info"]);
}
const labels: Record<string, string> = {
  partial_success: "Partial results",
  success: "Complete",
  failed: "Failed",
  running: "Running",
  queued: "Queued",
  pending: "Pending",
  degraded: "Incomplete",
  skipped: "Skipped",
  discover: "Discovery",
  remeasure: "Re-check",
  chatgpt: "ChatGPT",
  google_organic: "Google organic",
  google_ai_overview: "AI Overview",
  not_comparable: "Not comparable",
  rule_based_fallback: "Rule-based fallback",
  llm: "AI generated",
  insufficient: "Insufficient evidence",
  not_present: "Not present",
};
@Pipe({ name: "label" })
export class LabelPipe implements PipeTransform {
  transform(value: string | null | undefined): string {
    if (!value) return "Unavailable";
    return (
      labels[value] ??
      value.replaceAll("_", " ").replace(/^./, (c) => c.toUpperCase())
    );
  }
}
@Pipe({ name: "metric" })
export class MetricPipe implements PipeTransform {
  transform(
    value: number | null | undefined,
    type: "number" | "percent" | "score" | "delta" = "number",
  ): string {
    if (value === null || value === undefined || !Number.isFinite(value))
      return "—";
    if (type === "percent")
      return new PercentPipe("en-US").transform(value, "1.0-0") ?? "—";
    if (type === "delta")
      return `${value > 0 ? "+" : ""}${new DecimalPipe("en-US").transform(value * 100, "1.0-1")} pp`;
    return (
      new DecimalPipe("en-US").transform(
        value,
        type === "score" ? "1.0-1" : "1.0-0",
      ) ?? "—"
    );
  }
}
@Component({
  selector: "app-status",
  imports: [LabelPipe],
  template:
    '<span class="status" [attr.data-status]="status()"><span class="status-dot"></span>{{status() | label}}</span>',
})
export class Status {
  readonly status = input<string>("pending");
}
@Component({
  selector: "app-state",
  imports: [Icon],
  template: `
    @if (loading()) {
      <div class="loading-state" role="status" aria-live="polite">
        <div class="skeleton skeleton-title"></div>
        <div class="skeleton skeleton-line"></div>
        <div class="skeleton-grid">
          <div class="skeleton"></div>
          <div class="skeleton"></div>
          <div class="skeleton"></div>
        </div>
        <span class="sr-only">Loading {{ title() }}</span>
      </div>
    } @else {
      <div
        class="empty-state"
        [class.error-state]="!!error()"
        [attr.role]="error() ? 'alert' : null"
      >
        <div class="empty-icon">
          <app-icon [name]="error() ? 'alert' : icon()" />
        </div>
        <h2>{{ error() ? "Let’s get you reconnected" : title() }}</h2>
        <p>{{ error() ? message() : description() }}</p>
        @if (reference()) {
          <code class="support-reference">Reference: {{ reference() }}</code>
        }
        @if (error()) {
          <button class="button secondary" (click)="retry.emit()">
            <app-icon name="refresh" /> Try again
          </button>
        }
        <ng-content />
      </div>
    }
  `,
  styles: ":host{display:block}",
})
export class State {
  readonly loading = input(false);
  readonly title = input("Nothing here yet");
  readonly description = input("");
  readonly icon = input("spark");
  readonly error = input<unknown>(null);
  readonly retry = output();
  readonly message = computed(() => friendlyError(this.error()));
  readonly reference = computed(() => errorBody(this.error())?.request_id);
}
@Component({
  selector: "app-metric",
  imports: [Icon],
  template:
    '<article class="metric-card"><div class="metric-top"><span>{{label()}}</span><app-icon [name]="icon()"/></div><strong>{{value()}}</strong><p>{{hint()}}</p><ng-content/></article>',
})
export class Metric {
  readonly label = input("");
  readonly value = input("—");
  readonly hint = input("");
  readonly icon = input("chart");
}
@Injectable({ providedIn: "root" })
export class Notices {
  readonly message = signal("");
  private timeout?: ReturnType<typeof setTimeout>;
  show(message: string) {
    clearTimeout(this.timeout);
    this.message.set(message);
    this.timeout = setTimeout(() => this.message.set(""), 6000);
  }
}

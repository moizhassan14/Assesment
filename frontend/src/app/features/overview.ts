import { Component, DestroyRef, inject, signal, computed } from "@angular/core";
import { DatePipe } from "@angular/common";
import { RouterLink } from "@angular/router";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import {
  catchError,
  debounceTime,
  distinctUntilChanged,
  forkJoin,
  map,
  of,
  startWith,
  Subject,
  switchMap,
  tap,
} from "rxjs";
import { Api } from "../core/api";
import { Brand, Comparison, Page, Report, isActive } from "../core/models";
import { Icon, State, Status, Metric, MetricPipe } from "../shared/ui";
interface BrandRow {
  brand: Brand;
  report: Report | null;
  comparison: Comparison | null;
}
@Component({
  imports: [RouterLink, DatePipe, Icon, State, Status, Metric, MetricPipe],
  templateUrl: "./overview.html",
})
export class Overview {
  private readonly api = inject(Api);
  private readonly destroyRef = inject(DestroyRef);
  private readonly requests = new Subject<{ q: string; offset: number }>();
  private readonly searches = new Subject<string>();
  readonly rows = signal<BrandRow[]>([]);
  readonly total = signal(0);
  readonly offset = signal(0);
  readonly query = signal("");
  readonly loading = signal(true);
  readonly error = signal<unknown>(null);
  readonly analyzed = computed(
    () => this.rows().filter((r) => r.brand.latest_run?.headline).length,
  );
  readonly queryCount = computed(() =>
    this.analyzed()
      ? this.rows().reduce(
          (sum, r) =>
            sum + (r.brand.latest_run?.headline?.queries_measured ?? 0),
          0,
        )
      : null,
  );
  readonly reportsMissing = computed(() =>
    this.rows().some((r) => r.brand.latest_run?.headline && !r.report),
  );
  readonly opportunityCount = computed(() =>
    this.rows().some((r) => r.report)
      ? this.rows().reduce(
          (sum, r) => sum + (r.report?.opportunities.length ?? 0),
          0,
        )
      : null,
  );
  constructor() {
    this.searches
      .pipe(
        debounceTime(300),
        distinctUntilChanged(),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((q) => {
        this.query.set(q);
        this.offset.set(0);
        this.refresh();
      });
    this.requests
      .pipe(
        startWith({ q: "", offset: 0 }),
        tap(() => {
          this.loading.set(true);
          this.error.set(null);
        }),
        switchMap((params) =>
          this.api.brands(params.q, params.offset).pipe(
            switchMap((page) => this.enrich(page)),
            catchError((error) => {
              this.error.set(error);
              return of(null);
            }),
          ),
        ),
        takeUntilDestroyed(this.destroyRef),
      )
      .subscribe((result) => {
        if (result) {
          this.rows.set(result.rows);
          this.total.set(result.total);
        }
        this.loading.set(false);
      });
  }
  private enrich(page: Page<Brand>) {
    if (!page.data.length)
      return of({ rows: [] as BrandRow[], total: page.meta.total });
    return forkJoin(
      page.data.map((brand) =>
        forkJoin({
          report:
            brand.latest_run && !isActive(brand.latest_run.status)
              ? this.api
                  .report(brand.latest_run.id)
                  .pipe(catchError(() => of(null)))
              : of(null),
          comparison:
            brand.stats.run_count >= 2 && brand.latest_run?.headline
              ? this.api.compare(brand.id).pipe(catchError(() => of(null)))
              : of(null),
        }).pipe(map((extra) => ({ brand, ...extra }))),
      ),
    ).pipe(map((rows) => ({ rows, total: page.meta.total })));
  }
  search(event: Event) {
    this.searches.next((event.target as HTMLInputElement).value.trim());
  }
  refresh() {
    this.requests.next({ q: this.query(), offset: this.offset() });
  }
  page(direction: number) {
    this.offset.update((value) => Math.max(0, value + direction * 12));
    this.refresh();
  }
}

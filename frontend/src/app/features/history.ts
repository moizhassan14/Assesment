import { Component, computed, DestroyRef, inject, signal } from "@angular/core";
import { DatePipe } from "@angular/common";
import { RouterLink } from "@angular/router";
import { FormControl, FormGroup, ReactiveFormsModule } from "@angular/forms";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { catchError, of, Subject, switchMap, tap } from "rxjs";
import { BrandStore } from "./brand-store";
import { Api } from "../core/api";
import { Comparison, RunSummary, isActive } from "../core/models";
import { errorBody } from "../core/errors";
import { Icon, State, Status, MetricPipe, LabelPipe } from "../shared/ui";
@Component({
  imports: [
    DatePipe,
    RouterLink,
    ReactiveFormsModule,
    Icon,
    State,
    Status,
    MetricPipe,
    LabelPipe,
  ],
  templateUrl: "./history.html",
})
export class History {
  private readonly store = inject(BrandStore);
  private readonly api = inject(Api);
  private readonly destroyRef = inject(DestroyRef);
  readonly brandId = this.store.brand()!.id;
  private readonly pages = new Subject<number>();
  private readonly comparisons = new Subject<{
    base?: string;
    target?: string;
  }>();
  readonly runs = signal<RunSummary[]>([]);
  readonly total = signal(0);
  readonly offset = signal(0);
  readonly loading = signal(true);
  readonly error = signal<unknown>(null);
  readonly comparison = signal<Comparison | null>(null);
  readonly compareLoading = signal(true);
  readonly compareError = signal<unknown>(null);
  readonly selectionError = signal("");
  readonly active = isActive;
  readonly completed = computed(() =>
    this.runs().filter(
      (r) => r.status === "success" || r.status === "partial_success",
    ),
  );
  readonly form = new FormGroup({
    base: new FormControl("", { nonNullable: true }),
    target: new FormControl("", { nonNullable: true }),
  });
  constructor() {
    this.pages
      .pipe(
        tap(() => {
          this.loading.set(true);
          this.error.set(null);
        }),
        switchMap((offset) =>
          this.api.runs(this.brandId, offset).pipe(
            catchError((error) => {
              this.error.set(error);
              return of(null);
            }),
          ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((page) => {
        if (page) {
          this.runs.set(page.data);
          this.total.set(page.meta.total);
          const complete = this.completed();
          if (complete.length >= 2)
            this.form.setValue({
              base: complete[1].id,
              target: complete[0].id,
            });
        }
        this.loading.set(false);
      });
    this.comparisons
      .pipe(
        tap(() => {
          this.compareLoading.set(true);
          this.compareError.set(null);
          this.comparison.set(null);
        }),
        switchMap((ids) =>
          this.api.compare(this.brandId, ids.base, ids.target).pipe(
            catchError((error) => {
              if (errorBody(error)?.code !== "not_comparable")
                this.compareError.set(error);
              return of(null);
            }),
          ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((comparison) => {
        this.comparison.set(comparison);
        this.compareLoading.set(false);
      });
    this.loadPage();
    this.loadComparison();
  }
  loadPage() {
    this.pages.next(this.offset());
  }
  page(direction: number) {
    this.offset.update((n) => Math.max(0, n + direction * 20));
    this.loadPage();
  }
  loadComparison() {
    this.comparisons.next({});
  }
  compareSelected() {
    this.selectionError.set("");
    const ids = this.form.getRawValue();
    const base = this.runs().find((r) => r.id === ids.base);
    const target = this.runs().find((r) => r.id === ids.target);
    if (!base || !target || base.id === target.id) return;
    if (base.created_at > target.created_at) {
      this.selectionError.set(
        "Choose an earlier baseline and a later target analysis.",
      );
      return;
    }
    this.comparisons.next({ base: ids.base, target: ids.target });
  }
}

import { inject, Injectable, signal } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { catchError, of, Subject, switchMap, tap } from "rxjs";
import { Api } from "../core/api";
import { Report, Run, isActive } from "../core/models";
@Injectable()
export class RunStore {
  private readonly api = inject(Api);
  private readonly requests = new Subject<string>();
  private readonly reports = new Subject<string | null>();
  private id = "";
  private lastReportAt = 0;
  readonly run = signal<Run | null>(null);
  readonly report = signal<Report | null>(null);
  readonly loading = signal(true);
  readonly error = signal<unknown>(null);
  readonly reportLoading = signal(true);
  readonly reportError = signal<unknown>(null);
  constructor() {
    this.reports
      .pipe(
        tap(() => {
          this.reportLoading.set(true);
          this.reportError.set(null);
        }),
        switchMap((id) =>
          id
            ? this.api.report(id).pipe(
                catchError((error) => {
                  this.reportError.set(error);
                  return of(null);
                }),
              )
            : of(null),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((report) => {
        if (report) this.report.set(report);
        this.reportLoading.set(false);
      });
    this.requests
      .pipe(
        tap(() => {
          this.loading.set(true);
          this.error.set(null);
          this.run.set(null);
          this.report.set(null);
          this.reportLoading.set(true);
          this.lastReportAt = 0;
        }),
        switchMap((id) =>
          this.api.pollRun(id).pipe(
            catchError((error) => {
              this.error.set(error);
              return of(null);
            }),
          ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((run) => {
        if (run) {
          this.run.set(run);
          if (
            !isActive(run.status) ||
            Date.now() - this.lastReportAt >= 10000
          ) {
            this.lastReportAt = Date.now();
            this.reports.next(run.id);
          }
        }
        this.loading.set(false);
      });
  }
  load(id: string) {
    this.id = id;
    this.reports.next(null);
    this.requests.next(id);
  }
  refresh() {
    this.load(this.id);
  }
  refreshReport() {
    this.reports.next(this.id);
  }
}

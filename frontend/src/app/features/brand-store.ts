import { DestroyRef, inject, Injectable, signal } from "@angular/core";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { catchError, forkJoin, of, Subject, switchMap, tap } from "rxjs";
import { Api } from "../core/api";
import { Brand, RunSummary } from "../core/models";
@Injectable()
export class BrandStore {
  private readonly api = inject(Api);
  private readonly requests = new Subject<string>();
  private id = "";
  readonly brand = signal<Brand | null>(null);
  readonly runs = signal<RunSummary[]>([]);
  readonly runsTotal = signal(0);
  readonly runsError = signal(false);
  readonly loading = signal(true);
  readonly error = signal<unknown>(null);
  constructor() {
    this.requests
      .pipe(
        tap(() => {
          this.loading.set(true);
          this.error.set(null);
          this.runsError.set(false);
          this.brand.set(null);
        }),
        switchMap((id) =>
          forkJoin({
            brand: this.api.brand(id),
            runs: this.api.runs(id).pipe(
              catchError(() => {
                this.runsError.set(true);
                return of(null);
              }),
            ),
          }).pipe(
            catchError((error) => {
              this.error.set(error);
              return of(null);
            }),
          ),
        ),
        takeUntilDestroyed(),
      )
      .subscribe((result) => {
        if (result) {
          this.brand.set(result.brand);
          this.runs.set(result.runs?.data ?? []);
          this.runsTotal.set(result.runs?.meta.total ?? 0);
        }
        this.loading.set(false);
      });
  }
  load(id: string) {
    this.id = id;
    this.requests.next(id);
  }
  refresh() {
    this.load(this.id);
  }
}

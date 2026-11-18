import { inject, Injectable } from "@angular/core";
import {
  HttpClient,
  HttpErrorResponse,
  HttpHeaders,
} from "@angular/common/http";
import {
  defer,
  expand,
  map,
  EMPTY,
  timer,
  switchMap,
  retry,
  throwError,
  shareReplay,
} from "rxjs";
import { environment } from "../../environments/environment";
import {
  Brand,
  BrandInput,
  Comparison,
  Envelope,
  Health,
  Page,
  PublicConfig,
  Report,
  Run,
  RunAccepted,
  RunInput,
  RunSummary,
  isActive,
} from "./models";

@Injectable({ providedIn: "root" })
export class Api {
  private readonly http = inject(HttpClient);
  readonly base = environment.apiBaseUrl;
  private readonly config$ = defer(() =>
    this.get<PublicConfig>("/config"),
  ).pipe(shareReplay({ bufferSize: 1, refCount: false }));
  private get<T>(path: string) {
    return this.http
      .get<Envelope<T>>(`${this.base}${path}`)
      .pipe(map((response) => response.data));
  }
  config() {
    return this.config$;
  }
  health() {
    return this.get<Health>("/health");
  }
  brands(q = "", offset = 0) {
    return this.http.get<Page<Brand>>(`${this.base}/brands`, {
      params: { q, offset, limit: 12 },
    });
  }
  brand(id: string) {
    return this.get<Brand>(`/brands/${encodeURIComponent(id)}`);
  }
  createBrand(body: BrandInput) {
    return this.http
      .post<Envelope<Brand>>(`${this.base}/brands`, body)
      .pipe(map((response) => response.data));
  }
  updateBrand(
    id: string,
    body: Pick<
      BrandInput,
      "name" | "description" | "seed_keywords" | "competitors"
    >,
  ) {
    return this.http
      .patch<Envelope<Brand>>(
        `${this.base}/brands/${encodeURIComponent(id)}`,
        body,
      )
      .pipe(map((response) => response.data));
  }
  startRun(brandId: string, body: RunInput, key: string) {
    return this.http
      .post<Envelope<RunAccepted>>(
        `${this.base}/brands/${encodeURIComponent(brandId)}/runs`,
        body,
        {
          headers: new HttpHeaders({ "Idempotency-Key": key }),
          observe: "response",
        },
      )
      .pipe(
        map((response) => {
          if (response.status !== 202 || !response.body?.data.run_id)
            throw new Error("The analysis was not accepted.");
          return response.body.data;
        }),
      );
  }
  runs(brandId: string, offset = 0) {
    return this.http.get<Page<RunSummary>>(
      `${this.base}/brands/${encodeURIComponent(brandId)}/runs`,
      { params: { offset, limit: 20 } },
    );
  }
  run(id: string) {
    return this.get<Run>(`/runs/${encodeURIComponent(id)}`);
  }
  report(id: string) {
    return this.get<Report>(`/runs/${encodeURIComponent(id)}/report`);
  }
  compare(id: string, base?: string, target?: string) {
    return this.http
      .get<Envelope<Comparison>>(
        `${this.base}/brands/${encodeURIComponent(id)}/compare`,
        {
          params:
            base && target ? { base_run_id: base, target_run_id: target } : {},
        },
      )
      .pipe(map((response) => response.data));
  }
  exportUrl(id: string) {
    return `${this.base}/runs/${encodeURIComponent(id)}/report?format=csv`;
  }
  pollRun(id: string, interval: number = environment.pollIntervalMs) {
    const request = () =>
      this.run(id).pipe(
        retry({
          count: 2,
          delay: (error: unknown, attempt) =>
            error instanceof HttpErrorResponse &&
            (error.status === 0 || error.status === 429 || error.status >= 500)
              ? timer(Math.min(1000 * 2 ** (attempt - 1), 8000))
              : throwError(() => error),
        }),
      );
    // Start the next request after the prior response. Slow calls are never cancelled by a timer.
    return defer(request).pipe(
      expand((run) =>
        isActive(run.status) ? timer(interval).pipe(switchMap(request)) : EMPTY,
      ),
    );
  }
}

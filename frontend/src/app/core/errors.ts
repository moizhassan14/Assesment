import { ErrorHandler, inject, Injectable, signal } from "@angular/core";
import {
  HttpBackend,
  HttpClient,
  HttpErrorResponse,
  HttpInterceptorFn,
} from "@angular/common/http";
import { catchError, throwError } from "rxjs";
import { ApiErrorBody } from "./models";
import { environment } from "../../environments/environment";

export function errorBody(error: unknown): ApiErrorBody["error"] | undefined {
  if (!(error instanceof HttpErrorResponse)) return undefined;
  const body: unknown = error.error;
  if (!body || typeof body !== "object" || !("error" in body)) return undefined;
  const detail: unknown = body.error;
  return detail &&
    typeof detail === "object" &&
    "code" in detail &&
    typeof detail.code === "string"
    ? (detail as ApiErrorBody["error"])
    : undefined;
}
export function friendlyError(error: unknown): string {
  if (!(error instanceof HttpErrorResponse))
    return "Something interrupted this request. Please try again.";
  const messages: Record<string, string> = {
    duplicate_brand:
      "This domain is already tracked. Open the existing brand or use another domain.",
    run_in_progress: "An analysis is already running for this brand.",
    nothing_to_remeasure:
      "Complete an analysis with measured queries before re-checking.",
    not_comparable:
      "Two completed analyses with comparable evidence are needed.",
    validation_error: "Review the highlighted fields and try again.",
    too_many_runs: "The analysis queue is full. Wait a moment and try again.",
  };
  const code = errorBody(error)?.code;
  if (code && messages[code]) return messages[code];
  if (error.status === 0)
    return "We could not reach the server. Check your connection and retry.";
  if (error.status === 404)
    return "This record could not be found. It may have been removed or the link is incorrect.";
  if (error.status === 503)
    return "The service is temporarily unavailable. Please retry shortly.";
  return "The request could not be completed. Please retry. If it continues, share the reference below with support.";
}
@Injectable({ providedIn: "root" })
export class Diagnostics {
  private readonly http = new HttpClient(inject(HttpBackend));
  readonly runtimeError = signal(false);
  private lastSent = 0;
  report(kind: "http" | "runtime", requestId: string | null, status?: number) {
    if (Date.now() - this.lastSent < 3000) return;
    this.lastSent = Date.now();
    const path = location.pathname;
    this.http
      .post(`${environment.apiBaseUrl}/client-errors`, {
        message:
          kind === "http"
            ? `API request failed (HTTP ${status ?? "unknown"}).`
            : "Unexpected browser runtime failure.",
        kind,
        url: path,
        request_id: requestId,
        run_id: path.match(/\/runs\/([a-f0-9-]{36})/i)?.[1] ?? null,
        occurred_at: new Date().toISOString(),
      })
      .subscribe({
        error: () => {
          /* Diagnostics must never break the product or recurse. */
        },
      });
  }
}
export const apiInterceptor: HttpInterceptorFn = (request, next) => {
  if (!request.url.startsWith(environment.apiBaseUrl + "/"))
    return next(request);
  const diagnostics = inject(Diagnostics);
  const requestId = crypto.randomUUID();
  return next(
    request.clone({ setHeaders: { "X-Request-ID": requestId } }),
  ).pipe(
    catchError((error: unknown) => {
      if (
        error instanceof HttpErrorResponse &&
        (error.status === 0 || error.status >= 500)
      )
        diagnostics.report(
          "http",
          errorBody(error)?.request_id ??
            error.headers.get("X-Request-ID") ??
            requestId,
          error.status,
        );
      return throwError(() => error);
    }),
  );
};
@Injectable()
export class BrowserErrorHandler implements ErrorHandler {
  private readonly diagnostics = inject(Diagnostics);
  handleError(error: unknown): void {
    console.error("Unexpected application error", error);
    this.diagnostics.runtimeError.set(true);
    this.diagnostics.report("runtime", null);
  }
}

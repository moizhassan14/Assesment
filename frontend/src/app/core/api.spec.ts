import { TestBed } from "@angular/core/testing";
import { provideHttpClient } from "@angular/common/http";
import {
  HttpTestingController,
  provideHttpClientTesting,
} from "@angular/common/http/testing";
import { firstValueFrom } from "rxjs";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Api } from "./api";
import { Run, RunStatus } from "./models";

// Only the status matters to polling; HTTP fixtures are restricted to automated tests.
const runResponse = (status: RunStatus) => ({ data: { id: "run-1", status } });
describe("API contracts and long-running polling", () => {
  let api: Api;
  let http: HttpTestingController;
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideHttpClient(), provideHttpClientTesting()],
    });
    api = TestBed.inject(Api);
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => {
    http.verify();
    vi.useRealTimers();
  });
  it("requires HTTP 202 and preserves the idempotency key", async () => {
    const accepted = firstValueFrom(
      api.startRun(
        "brand-1",
        { mode: "discover", force_refresh: false },
        "stable-key",
      ),
    );
    const request = http.expectOne("/api/brands/brand-1/runs");
    expect(request.request.method).toBe("POST");
    expect(request.request.headers.get("Idempotency-Key")).toBe("stable-key");
    expect(request.request.body).toEqual({
      mode: "discover",
      force_refresh: false,
    });
    request.flush(
      { data: { run_id: "run-1", status: "queued" } },
      { status: 202, statusText: "Accepted" },
    );
    expect((await accepted).run_id).toBe("run-1");
  });
  it("rejects a response that did not accept a run", async () => {
    const result = firstValueFrom(
      api.startRun(
        "b",
        { mode: "discover", force_refresh: false },
        "stable-key",
      ),
    );
    http
      .expectOne("/api/brands/b/runs")
      .flush({ data: { run_id: "r" } }, { status: 200, statusText: "OK" });
    await expect(result).rejects.toThrow("not accepted");
  });
  it("shares public configuration without repeated calls", () => {
    const values: unknown[] = [];
    api.config().subscribe((v) => values.push(v));
    api.config().subscribe((v) => values.push(v));
    http.expectOne("/api/config").flush({ data: { data_mode: "live" } });
    expect(values).toHaveLength(2);
    api.config().subscribe();
    http.expectNone("/api/config");
  });
  it("polls at the configured interval and stops after partial success", async () => {
    vi.useFakeTimers();
    const statuses: RunStatus[] = [];
    let complete = false;
    api.pollRun("run-1", 2500).subscribe({
      next: (run) => statuses.push(run.status),
      complete: () => (complete = true),
    });
    http.expectOne("/api/runs/run-1").flush(runResponse("running"));
    await vi.advanceTimersByTimeAsync(2499);
    http.expectNone("/api/runs/run-1");
    await vi.advanceTimersByTimeAsync(1);
    http.expectOne("/api/runs/run-1").flush(runResponse("partial_success"));
    await vi.advanceTimersByTimeAsync(10000);
    http.expectNone("/api/runs/run-1");
    expect(statuses).toEqual(["running", "partial_success"]);
    expect(complete).toBe(true);
  });
  it.each<RunStatus>(["success", "failed"])(
    "stops on terminal state %s",
    async (status) => {
      vi.useFakeTimers();
      let done = false;
      api.pollRun("r", 100).subscribe({ complete: () => (done = true) });
      http.expectOne("/api/runs/r").flush(runResponse(status));
      await vi.advanceTimersByTimeAsync(1000);
      http.expectNone("/api/runs/r");
      expect(done).toBe(true);
    },
  );
  it("does not cancel a slow status request when the polling interval elapses", async () => {
    vi.useFakeTimers();
    const subscription = api.pollRun("r", 100).subscribe();
    const request = http.expectOne("/api/runs/r");
    await vi.advanceTimersByTimeAsync(3000);
    expect(request.cancelled).toBe(false);
    http.expectNone("/api/runs/r");
    request.flush(runResponse("success"));
    subscription.unsubscribe();
  });
  it("cleans up polling when the consumer is destroyed", async () => {
    vi.useFakeTimers();
    const subscription = api.pollRun("r", 100).subscribe();
    http.expectOne("/api/runs/r").flush(runResponse("running"));
    subscription.unsubscribe();
    await vi.advanceTimersByTimeAsync(1000);
    http.expectNone("/api/runs/r");
  });
  it("retries a transient provider-side status error and recovers", async () => {
    vi.useFakeTimers();
    const values: Run[] = [];
    api.pollRun("r", 100).subscribe((v) => values.push(v));
    http
      .expectOne("/api/runs/r")
      .flush({}, { status: 503, statusText: "Unavailable" });
    await vi.advanceTimersByTimeAsync(1000);
    http.expectOne("/api/runs/r").flush(runResponse("success"));
    expect(values[0].status).toBe("success");
  });
  it("does not retry a nonexistent run", async () => {
    vi.useFakeTimers();
    let status: number | undefined;
    api
      .pollRun("missing", 100)
      .subscribe({ error: (error) => (status = error.status) });
    http
      .expectOne("/api/runs/missing")
      .flush({}, { status: 404, statusText: "Not Found" });
    await vi.advanceTimersByTimeAsync(10000);
    http.expectNone("/api/runs/missing");
    expect(status).toBe(404);
  });
  it("surfaces an exhausted network failure for explicit recovery", async () => {
    vi.useFakeTimers();
    let failed = false;
    api.pollRun("r", 100).subscribe({ error: () => (failed = true) });
    http.expectOne("/api/runs/r").error(new ProgressEvent("network"));
    await vi.advanceTimersByTimeAsync(1000);
    http.expectOne("/api/runs/r").error(new ProgressEvent("network"));
    await vi.advanceTimersByTimeAsync(2000);
    http.expectOne("/api/runs/r").error(new ProgressEvent("network"));
    expect(failed).toBe(true);
    await vi.advanceTimersByTimeAsync(10000);
    http.expectNone("/api/runs/r");
  });
});

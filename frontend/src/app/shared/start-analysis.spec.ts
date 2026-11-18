import { TestBed } from "@angular/core/testing";
import { provideHttpClient } from "@angular/common/http";
import {
  HttpTestingController,
  provideHttpClientTesting,
} from "@angular/common/http/testing";
import { provideRouter, Router } from "@angular/router";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { StartAnalysis } from "./start-analysis";

describe("Analysis submission protects paid work", () => {
  let http: HttpTestingController;
  let component: StartAnalysis;
  let navigate: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    TestBed.configureTestingModule({
      imports: [StartAnalysis],
      providers: [
        provideHttpClient(),
        provideHttpClientTesting(),
        provideRouter([]),
      ],
    });
    const fixture = TestBed.createComponent(StartAnalysis);
    fixture.componentRef.setInput("brandId", "brand-1");
    component = fixture.componentInstance;
    http = TestBed.inject(HttpTestingController);
    navigate = vi
      .spyOn(TestBed.inject(Router), "navigate")
      .mockResolvedValue(true);
  });
  afterEach(() => {
    http.verify();
  });
  it("opens the accepted run and blocks repeated clicks during submission", () => {
    component.start();
    component.start();
    const request = http.expectOne("/api/brands/brand-1/runs");
    request.flush(
      { data: { run_id: "accepted-run" } },
      { status: 202, statusText: "Accepted" },
    );
    expect(navigate).toHaveBeenCalledWith([
      "/runs",
      "accepted-run",
      "progress",
    ]);
  });
  it("reuses an idempotency key after an ambiguous network failure", () => {
    component.start();
    const first = http.expectOne("/api/brands/brand-1/runs");
    const key = first.request.headers.get("Idempotency-Key");
    first.error(new ProgressEvent("network"));
    expect(component.submitting()).toBe(false);
    component.start();
    const second = http.expectOne("/api/brands/brand-1/runs");
    expect(second.request.headers.get("Idempotency-Key")).toBe(key);
    second.flush(
      { data: { run_id: "same-run" } },
      { status: 202, statusText: "Accepted" },
    );
  });
  it("uses a new key for a changed request after a failed attempt", () => {
    component.start();
    const first = http.expectOne("/api/brands/brand-1/runs");
    const key = first.request.headers.get("Idempotency-Key");
    first.error(new ProgressEvent("network"));
    component.form.controls.force.setValue(true);
    component.start();
    const second = http.expectOne("/api/brands/brand-1/runs");
    expect(second.request.headers.get("Idempotency-Key")).not.toBe(key);
    expect(second.request.body.force_refresh).toBe(true);
    second.flush(
      { data: { run_id: "new-run" } },
      { status: 202, statusText: "Accepted" },
    );
  });
  it("follows the backend’s duplicate-run reference without resubmitting", () => {
    component.start();
    http.expectOne("/api/brands/brand-1/runs").flush(
      {
        error: {
          code: "run_in_progress",
          message: "Already running",
          details: [],
          request_id: "ref",
          meta: { existing_run_id: "existing-run" },
        },
      },
      { status: 409, statusText: "Conflict" },
    );
    expect(navigate).toHaveBeenCalledWith([
      "/runs",
      "existing-run",
      "progress",
    ]);
    http.expectNone("/api/brands/brand-1/runs");
  });
  it("sends remeasure without a new question", () => {
    component.form.setValue({
      mode: "remeasure",
      question: "An old optional question",
      force: false,
    });
    component.start();
    const request = http.expectOne("/api/brands/brand-1/runs");
    expect(request.request.body).toEqual({
      mode: "remeasure",
      force_refresh: false,
    });
    request.flush(
      { data: { run_id: "recheck" } },
      { status: 202, statusText: "Accepted" },
    );
  });
});

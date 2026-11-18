import { HttpErrorResponse } from "@angular/common/http";
import { describe, expect, it } from "vitest";
import { errorBody, friendlyError } from "./errors";
describe("Safe failure presentation", () => {
  it("never exposes a raw server traceback", () => {
    const error = new HttpErrorResponse({
      status: 500,
      error: {
        error: {
          code: "internal_error",
          message: "Traceback: private server values",
          request_id: "support-123",
          details: [],
        },
      },
    });
    expect(friendlyError(error)).not.toContain("Traceback");
    expect(errorBody(error)?.request_id).toBe("support-123");
  });
  it("distinguishes unavailable resources from network failure", () => {
    expect(friendlyError(new HttpErrorResponse({ status: 404 }))).toContain(
      "not be found",
    );
    expect(friendlyError(new HttpErrorResponse({ status: 0 }))).toContain(
      "connection",
    );
  });
});

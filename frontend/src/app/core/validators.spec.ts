import { FormControl } from "@angular/forms";
import { describe, expect, it } from "vitest";
import {
  domainValidator,
  normalizeDomain,
  optionalQuestionValidator,
  seedKeywordsValidator,
  trimmedRequired,
} from "./validators";
import { MetricPipe } from "../shared/ui";
describe("Honest metrics and contract validation", () => {
  it("distinguishes unknown measurements from measured zero", () => {
    const pipe = new MetricPipe();
    expect(pipe.transform(null, "percent")).toBe("—");
    expect(pipe.transform(undefined)).toBe("—");
    expect(pipe.transform(0, "percent")).toBe("0%");
    expect(pipe.transform(0, "score")).toBe("0");
    expect(pipe.transform(0.5, "percent")).toBe("50%");
  });
  it("labels a rate delta in percentage points", () => {
    const pipe = new MetricPipe();
    expect(pipe.transform(0.15, "delta")).toBe("+15 pp");
    expect(pipe.transform(-0.15, "delta")).toBe("-15 pp");
    expect(pipe.transform(null, "delta")).toBe("—");
  });
  it("normalizes accepted URLs and rejects malformed domains", () => {
    expect(normalizeDomain(" https://www.Example.com/path?q=1 ")).toBe(
      "example.com",
    );
    expect(
      domainValidator(new FormControl("https://www.example.com/path")),
    ).toBeNull();
    for (const value of [
      "localhost",
      "bad domain.com",
      "-bad.com",
      "javascript:example.com",
      "example..com",
    ])
      expect(domainValidator(new FormControl(value))).toEqual({ domain: true });
  });
  it("rejects whitespace-only names", () => {
    expect(trimmedRequired(new FormControl("  "))).toEqual({ required: true });
  });
  it("enforces unique keyword count, length, and word limits", () => {
    const validate = seedKeywordsValidator(2);
    expect(
      validate(new FormControl("marketing software, analytics")),
    ).toBeNull();
    for (const value of [
      "",
      "a",
      "one,two,three",
      "Software,software",
      "one two three four five six seven eight nine ten eleven",
    ])
      expect(validate(new FormControl(value))).not.toBeNull();
  });
  it("validates optional research questions without requiring them", () => {
    const validate = optionalQuestionValidator(400);
    expect(validate(new FormControl(""))).toBeNull();
    expect(validate(new FormControl("  "))).toBeNull();
    expect(validate(new FormControl("short"))).not.toBeNull();
    expect(validate(new FormControl("How visible is our brand?"))).toBeNull();
  });
});

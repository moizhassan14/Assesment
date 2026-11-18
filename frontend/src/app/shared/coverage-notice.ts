import { Component, computed, input } from "@angular/core";
import { Report, RunIssue } from "../core/models";
import { Icon, LabelPipe } from "./ui";
const issueLabels: Record<string, string> = {
  provider_auth_failed: "Provider credentials could not be verified.",
  provider_insufficient_funds:
    "The provider account needs funds before this measurement can run.",
  provider_rate_limited: "The provider rate limit prevented some measurements.",
  provider_timeout: "A provider took too long to respond.",
  provider_network_error: "A provider could not be reached.",
  provider_server_error: "A provider was temporarily unavailable.",
  provider_invalid_request:
    "A provider could not accept a measurement request.",
  provider_unexpected_response:
    "A provider returned a response that could not be verified.",
  provider_no_results: "The provider returned no usable results.",
  budget_exceeded: "A configured research budget was reached.",
  circuit_open: "Calls to an unavailable dependency were paused.",
  llm_unavailable:
    "AI synthesis was unavailable; a fallback may have been used.",
  llm_rate_limited:
    "AI synthesis reached its rate limit. Available measurements are preserved.",
  llm_auth_failed:
    "AI synthesis provider access could not be verified. Available measurements are preserved.",
  llm_invalid_request:
    "AI synthesis could not accept the request; a fallback may have been used.",
  llm_invalid_output: "AI output failed validation and was not used.",
  llm_tool_call_rejected: "An invalid tool call was safely rejected.",
  no_evidence: "No usable visibility evidence was obtained.",
  worker_crashed: "The analysis worker stopped before finishing.",
  interrupted_by_restart: "A server restart interrupted this analysis.",
  internal_error: "This stage could not complete.",
};
export function issueDescription(issue: RunIssue): string {
  return (
    issueLabels[issue.code] ??
    "This stage could not complete. Other available evidence is preserved."
  );
}
@Component({
  selector: "app-coverage-notice",
  imports: [Icon, LabelPipe],
  templateUrl: "./coverage-notice.html",
})
export class CoverageNotice {
  readonly report = input<Report | null>(null);
  readonly failed = computed(() => [
    ...new Set(
      this.report()
        ?.observations.filter((o) => o.status === "failed")
        .map((o) => o.surface) ?? [],
    ),
  ]);
  readonly describe = issueDescription;
}

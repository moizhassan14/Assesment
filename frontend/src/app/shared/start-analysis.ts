import { Component, DestroyRef, inject, input, signal } from "@angular/core";
import { Router, RouterLink } from "@angular/router";
import { FormControl, FormGroup, ReactiveFormsModule } from "@angular/forms";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { Api } from "../core/api";
import { RunInput, RunMode } from "../core/models";
import { errorBody, friendlyError } from "../core/errors";
import { optionalQuestionValidator } from "../core/validators";
import { Icon } from "./ui";
@Component({
  selector: "app-start-analysis",
  imports: [ReactiveFormsModule, RouterLink, Icon],
  templateUrl: "./start-analysis.html",
})
export class StartAnalysis {
  readonly brandId = input.required<string>();
  readonly canRemeasure = input(false);
  readonly activeRunId = input<string | null>(null);
  private readonly api = inject(Api);
  private readonly router = inject(Router);
  private readonly destroyRef = inject(DestroyRef);
  readonly form = new FormGroup({
    mode: new FormControl<RunMode>("discover", { nonNullable: true }),
    question: new FormControl(String(history.state?.question ?? ""), {
      nonNullable: true,
      validators: [optionalQuestionValidator(400)],
    }),
    force: new FormControl(false, { nonNullable: true }),
  });
  readonly submitting = signal(false);
  readonly error = signal<unknown>(null);
  readonly message = signal("");
  readonly reference = signal("");
  private key = "";
  private fingerprint = "";
  start() {
    if (this.submitting()) return;
    this.form.markAllAsTouched();
    if (
      this.form.controls.mode.value === "discover" &&
      this.form.controls.question.invalid
    )
      return;
    const value = this.form.getRawValue();
    const body: RunInput = { mode: value.mode, force_refresh: value.force };
    if (value.mode === "discover" && value.question.trim())
      body.question = value.question.trim();
    const fingerprint = JSON.stringify({ brandId: this.brandId(), body });
    if (fingerprint !== this.fingerprint || !this.key) {
      this.key = crypto.randomUUID();
      this.fingerprint = fingerprint;
    }
    this.submitting.set(true);
    this.error.set(null);
    this.api
      .startRun(this.brandId(), body, this.key)
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: (accepted) => {
          this.key = "";
          void this.router.navigate(["/runs", accepted.run_id, "progress"]);
        },
        error: (error) => {
          this.submitting.set(false);
          const detail = errorBody(error);
          const existing = detail?.meta?.["existing_run_id"];
          if (
            detail?.code === "run_in_progress" &&
            typeof existing === "string"
          ) {
            void this.router.navigate(["/runs", existing, "progress"]);
            return;
          }
          this.error.set(error);
          this.message.set(friendlyError(error));
          this.reference.set(detail?.request_id ?? "");
        },
      });
  }
}

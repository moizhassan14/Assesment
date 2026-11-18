import { Component, DestroyRef, inject, signal } from "@angular/core";
import { ActivatedRoute, Router, RouterLink } from "@angular/router";
import {
  FormArray,
  FormControl,
  FormGroup,
  ReactiveFormsModule,
  Validators,
} from "@angular/forms";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { forkJoin, of, switchMap } from "rxjs";
import { Api } from "../core/api";
import { Brand, BrandInput, PublicConfig } from "../core/models";
import { errorBody } from "../core/errors";
import {
  domainValidator,
  normalizeDomain,
  optionalQuestionValidator,
  seedKeywordsValidator,
  trimmedRequired,
} from "../core/validators";
import { Icon, Notices, State } from "../shared/ui";
type CompetitorForm = FormGroup<{
  name: FormControl<string>;
  domain: FormControl<string>;
}>;
@Component({
  imports: [ReactiveFormsModule, RouterLink, Icon, State],
  templateUrl: "./brand-form.html",
})
export class BrandForm {
  private readonly api = inject(Api);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly notices = inject(Notices);
  private readonly destroyRef = inject(DestroyRef);
  readonly brandId = this.route.snapshot.paramMap.get("brandId");
  readonly editing = signal(!!this.brandId);
  readonly config = signal<PublicConfig | null>(null);
  readonly loading = signal(true);
  readonly loadError = signal<unknown>(null);
  readonly submitError = signal<unknown>(null);
  readonly submitting = signal(false);
  readonly competitorError = signal("");
  readonly existingBrand = signal<string | null>(null);
  readonly form = new FormGroup({
    name: new FormControl("", {
      nonNullable: true,
      validators: [trimmedRequired, Validators.maxLength(80)],
    }),
    domain: new FormControl("", {
      nonNullable: true,
      validators: [Validators.required, domainValidator],
    }),
    description: new FormControl("", {
      nonNullable: true,
      validators: [Validators.maxLength(500)],
    }),
    market: new FormControl("0", {
      nonNullable: true,
      validators: [Validators.required],
    }),
    keywords: new FormControl("", {
      nonNullable: true,
      validators: [seedKeywordsValidator(5)],
    }),
    question: new FormControl("", {
      nonNullable: true,
      validators: [optionalQuestionValidator(400)],
    }),
    competitors: new FormArray<CompetitorForm>([]),
  });
  constructor() {
    this.load();
  }
  load() {
    this.loading.set(true);
    this.loadError.set(null);
    forkJoin({
      config: this.api.config(),
      brand: this.brandId ? this.api.brand(this.brandId) : of(null),
    })
      .pipe(takeUntilDestroyed(this.destroyRef))
      .subscribe({
        next: ({ config, brand }) => {
          this.config.set(config);
          this.form.controls.keywords.setValidators(
            seedKeywordsValidator(config.limits.max_seed_keywords),
          );
          this.form.controls.question.setValidators(
            optionalQuestionValidator(config.limits.question_max_length),
          );
          if (brand) this.populate(brand, config);
          this.loading.set(false);
        },
        error: (error) => {
          this.loadError.set(error);
          this.loading.set(false);
        },
      });
  }
  private populate(brand: Brand, config: PublicConfig) {
    const index = config.markets.findIndex(
      (m) =>
        m.location_code === brand.market.location_code &&
        m.language_code === brand.market.language_code,
    );
    this.form.patchValue({
      name: brand.name,
      domain: brand.domain,
      description: brand.description ?? "",
      keywords: brand.seed_keywords.join(", "),
      market: String(index),
    });
    this.form.controls.market.disable();
    this.form.controls.competitors.clear();
    brand.competitors.forEach((c) => this.addCompetitor(c.name, c.domain));
  }
  addCompetitor(name = "", domain = "") {
    if (
      this.form.controls.competitors.length >=
      (this.config()?.limits.max_competitors ?? 5)
    )
      return;
    this.form.controls.competitors.push(
      new FormGroup({
        name: new FormControl(name, {
          nonNullable: true,
          validators: [trimmedRequired, Validators.maxLength(80)],
        }),
        domain: new FormControl(domain, {
          nonNullable: true,
          validators: [Validators.required, domainValidator],
        }),
      }),
    );
  }
  removeCompetitor(index: number) {
    this.form.controls.competitors.removeAt(index);
    this.competitorError.set("");
  }
  invalid(name: keyof typeof this.form.controls) {
    const control = this.form.controls[name];
    return control.touched && control.invalid;
  }
  feedback(name: keyof typeof this.form.controls) {
    const control = this.form.controls[name];
    return control.touched ? this.controlFeedback(control.errors) : "";
  }
  competitorFeedback(index: number, field: "name" | "domain") {
    const control = this.form.controls.competitors.at(index).controls[field];
    return control.touched ? this.controlFeedback(control.errors) : "";
  }
  private controlFeedback(errors: Record<string, unknown> | null): string {
    if (!errors) return "";
    if (errors["server"])
      return "Review this value; the server could not accept it.";
    if (errors["required"]) return "This field is required.";
    if (errors["domain"])
      return "Enter a valid website or domain, such as example.com.";
    if (errors["maxlength"]) return "This value is too long.";
    return String(
      errors["keywords"] ?? errors["question"] ?? "Check this value.",
    );
  }
  submit() {
    if (this.submitting()) return;
    this.form.markAllAsTouched();
    this.competitorError.set("");
    if (this.form.invalid || !this.config()) {
      this.focusInvalid();
      return;
    }
    const value = this.form.getRawValue();
    const domains = value.competitors.map((c) => normalizeDomain(c.domain));
    if (
      new Set(domains).size !== domains.length ||
      domains.includes(normalizeDomain(value.domain))
    ) {
      this.competitorError.set(
        "Use unique competitor domains that differ from your brand.",
      );
      return;
    }
    const market = this.config()!.markets[Number(value.market)];
    if (!market) {
      this.form.controls.market.setErrors({ server: true });
      return;
    }
    const body: BrandInput = {
      name: value.name.trim(),
      domain: normalizeDomain(value.domain),
      description: value.description.trim() || null,
      market: {
        location_code: market.location_code,
        language_code: market.language_code,
      },
      seed_keywords: value.keywords
        .split(/[,\n]/)
        .map((v) => v.trim())
        .filter(Boolean),
      competitors: value.competitors.map((c) => ({
        name: c.name.trim(),
        domain: normalizeDomain(c.domain),
      })),
    };
    this.submitting.set(true);
    this.submitError.set(null);
    this.existingBrand.set(null);
    const request = this.brandId
      ? this.api.updateBrand(this.brandId, {
          name: body.name,
          description: body.description,
          seed_keywords: body.seed_keywords,
          competitors: body.competitors,
        })
      : this.api.createBrand(body);
    request.pipe(takeUntilDestroyed(this.destroyRef)).subscribe({
      next: (brand) => {
        this.notices.show(
          this.editing()
            ? "Brand profile updated."
            : "Your brand is ready. Start an analysis to find your first opportunities.",
        );
        void this.router.navigate(["/brands", brand.id], {
          state: { question: value.question.trim() },
        });
      },
      error: (error) => {
        this.submitting.set(false);
        this.submitError.set(error);
        const detail = errorBody(error);
        const id = detail?.meta?.["existing_id"];
        this.existingBrand.set(typeof id === "string" ? id : null);
        for (const item of detail?.details ?? []) {
          const path = (item.field ?? "")
            .replace(/\[(\d+)\]/g, ".$1")
            .replace(/^seed_keywords(?:\.\d+)?$/, "keywords");
          const control = this.form.get(path);
          if (control) {
            control.setErrors({ server: true });
            control.markAsTouched();
          }
        }
        this.focusInvalid();
      },
    });
  }
  private focusInvalid() {
    setTimeout(() =>
      document
        .querySelector<HTMLElement>(
          "input.ng-invalid,textarea.ng-invalid,select.ng-invalid",
        )
        ?.focus(),
    );
  }
}

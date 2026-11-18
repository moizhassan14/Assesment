import { AbstractControl, ValidationErrors, ValidatorFn } from "@angular/forms";
export function normalizeDomain(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .replace(/^www\./, "")
    .split(/[/?#]/)[0]
    .replace(/\.$/, "");
}
export const domainValidator: ValidatorFn = (
  control: AbstractControl,
): ValidationErrors | null => {
  const domain = normalizeDomain(String(control.value ?? ""));
  return domain.length <= 253 &&
    /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/.test(domain)
    ? null
    : { domain: true };
};
export const trimmedRequired: ValidatorFn = (control) =>
  typeof control.value === "string" && control.value.trim()
    ? null
    : { required: true };
export const seedKeywordsValidator =
  (max: number): ValidatorFn =>
  (control) => {
    const values = String(control.value ?? "")
      .split(/[,\n]/)
      .map((value) => value.trim())
      .filter(Boolean);
    if (!values.length || values.length > max)
      return { keywords: `Enter between 1 and ${max} seed keywords.` };
    if (
      values.some(
        (value) =>
          value.length < 2 ||
          value.length > 80 ||
          value.split(/\s+/).length > 10,
      )
    )
      return {
        keywords:
          "Each keyword needs 2–80 characters and no more than 10 words.",
      };
    return new Set(values.map((value) => value.toLowerCase())).size ===
      values.length
      ? null
      : { keywords: "Remove duplicate keywords." };
  };
export const optionalQuestionValidator =
  (max: number): ValidatorFn =>
  (control) => {
    const value = String(control.value ?? "").trim();
    return value && (value.length < 10 || value.length > max)
      ? { question: `Use 10–${max} characters, or leave this empty.` }
      : null;
  };

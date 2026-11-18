import { Component, inject } from "@angular/core";
import {
  ActivatedRoute,
  RouterLink,
  RouterLinkActive,
  RouterOutlet,
} from "@angular/router";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { BrandStore } from "./brand-store";
import { Icon, State, Status } from "../shared/ui";
@Component({
  providers: [BrandStore],
  imports: [RouterLink, RouterLinkActive, RouterOutlet, Icon, State, Status],
  templateUrl: "./brand-detail.html",
})
export class BrandDetail {
  readonly store = inject(BrandStore);
  constructor() {
    inject(ActivatedRoute)
      .paramMap.pipe(takeUntilDestroyed())
      .subscribe((params) => this.store.load(params.get("brandId")!));
  }
}

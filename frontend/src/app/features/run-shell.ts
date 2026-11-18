import { Component, inject } from "@angular/core";
import {
  ActivatedRoute,
  RouterLink,
  RouterLinkActive,
  RouterOutlet,
} from "@angular/router";
import { DatePipe } from "@angular/common";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { Api } from "../core/api";
import { RunStore } from "./run-store";
import { Icon, State, Status } from "../shared/ui";
@Component({
  providers: [RunStore],
  imports: [
    DatePipe,
    RouterLink,
    RouterLinkActive,
    RouterOutlet,
    Icon,
    State,
    Status,
  ],
  templateUrl: "./run-shell.html",
})
export class RunShell {
  readonly store = inject(RunStore);
  readonly api = inject(Api);
  constructor() {
    inject(ActivatedRoute)
      .paramMap.pipe(takeUntilDestroyed())
      .subscribe((params) => this.store.load(params.get("runId")!));
  }
}

import { Component, HostListener, inject, signal } from "@angular/core";
import {
  Router,
  RouterLink,
  RouterLinkActive,
  RouterOutlet,
  NavigationEnd,
} from "@angular/router";
import { takeUntilDestroyed } from "@angular/core/rxjs-interop";
import { filter } from "rxjs";
import { Diagnostics } from "./core/errors";
import { Icon, Notices } from "./shared/ui";
@Component({
  selector: "app-root",
  imports: [RouterOutlet, RouterLink, RouterLinkActive, Icon],
  templateUrl: "./app.html",
})
export class App {
  readonly menuOpen = signal(false);
  readonly notices = inject(Notices);
  readonly diagnostics = inject(Diagnostics);
  constructor() {
    inject(Router)
      .events.pipe(
        filter((event) => event instanceof NavigationEnd),
        takeUntilDestroyed(),
      )
      .subscribe(() => this.menuOpen.set(false));
  }
  @HostListener("document:keydown.escape") closeMenu() {
    if (this.menuOpen()) {
      this.menuOpen.set(false);
      document.querySelector<HTMLElement>(".mobile-menu")?.focus();
    }
  }
  @HostListener("document:keydown", ["$event"]) trapMenuFocus(
    event: KeyboardEvent,
  ) {
    if (!this.menuOpen() || event.key !== "Tab") return;
    const links = Array.from(
      document.querySelectorAll<HTMLElement>(".sidebar a,.nav-scrim"),
    );
    const first = links[0];
    const last = links.at(-1);
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (
      !event.shiftKey &&
      (document.activeElement === last ||
        document.activeElement === document.querySelector(".mobile-menu"))
    ) {
      event.preventDefault();
      first?.focus();
    }
  }
  reload() {
    location.reload();
  }
}

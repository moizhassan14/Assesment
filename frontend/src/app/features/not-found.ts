import { Component } from "@angular/core";
import { RouterLink } from "@angular/router";
import { State } from "../shared/ui";
@Component({
  imports: [State, RouterLink],
  template:
    '<app-state title="This page is off the map" description="The link may be incorrect. Head back to your workspace to find your brands." icon="search"><a class="button primary" routerLink="/">Back to overview</a></app-state>',
})
export class NotFound {}

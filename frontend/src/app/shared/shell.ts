import { Component, computed, inject } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { Auth } from '../core/auth';
import { Role } from '../core/models';

const NAV: Record<Role, { path: string; label: string }[]> = {
  rider: [
    { path: '/rider', label: 'Book a ride' },
    { path: '/rider/history', label: 'My rides' },
    { path: '/rider/verification', label: 'Verification' },
    { path: '/rider/settings', label: 'Accessibility' },
  ],
  driver: [
    { path: '/driver', label: 'Drive' },
    { path: '/driver/history', label: 'Ride log' },
  ],
  college_admin: [
    { path: '/admin', label: 'Overview' },
    { path: '/admin/verifications', label: 'Verifications' },
    { path: '/admin/rides', label: 'Live rides' },
    { path: '/admin/fleet', label: 'Fleet' },
    { path: '/admin/roster', label: 'Roster' },
    { path: '/admin/anomalies', label: 'Anomalies' },
  ],
  super_admin: [{ path: '/admin', label: 'Overview' }],
};

@Component({
  selector: 'app-shell',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    <a class="skip-link" href="#main">Skip to content</a>
    <header class="topbar">
      <div class="container bar">
        <a class="brand" routerLink="/" aria-label="RideAssist home">
          <span class="logo" aria-hidden="true">♿</span>
          <span>RideAssist</span>
        </a>
        @if (auth.me(); as me) {
          <span class="college muted small">{{ me.college_name ?? 'Platform' }}</span>
          <div class="user">
            <span class="small"
              ><strong>{{ me.full_name }}</strong>
              <span class="muted"> · {{ roleLabel() }}</span></span
            >
            <button class="btn btn-outline" (click)="auth.logout()">Log out</button>
          </div>
        }
      </div>
      <nav class="container nav" aria-label="Main">
        @for (link of links(); track link.path) {
          <a
            [routerLink]="link.path"
            routerLinkActive="active"
            [routerLinkActiveOptions]="{ exact: true }"
            ariaCurrentWhenActive="page"
            >{{ link.label }}</a
          >
        }
      </nav>
    </header>
    <main id="main" class="container" tabindex="-1">
      <router-outlet />
    </main>
  `,
  styles: `
    .topbar {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      position: sticky;
      top: 0;
      z-index: 1000;
    }
    .bar {
      display: flex;
      align-items: center;
      gap: 1rem;
      flex-wrap: wrap;
      padding-bottom: 0.25rem;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 0.5rem;
      font-weight: 800;
      font-size: 1.15rem;
      color: var(--text);
      text-decoration: none;
    }
    .logo {
      display: grid;
      place-items: center;
      width: 34px;
      height: 34px;
      border-radius: 10px;
      background: var(--primary);
      color: var(--on-primary);
    }
    .user {
      margin-left: auto;
      display: flex;
      align-items: center;
      gap: 0.75rem;
    }
    .nav {
      display: flex;
      gap: 0.25rem;
      overflow-x: auto;
      padding-top: 0;
      padding-bottom: 0;
      a {
        padding: 0.7rem 0.9rem;
        color: var(--text-muted);
        text-decoration: none;
        font-weight: 600;
        border-bottom: 3px solid transparent;
        white-space: nowrap;
      }
      a.active {
        color: var(--primary);
        border-bottom-color: var(--primary);
      }
    }
    main {
      padding-top: 1.25rem;
      padding-bottom: 4rem;
      outline: none;
    }
  `,
})
export class Shell {
  protected auth = inject(Auth);
  protected links = computed(() => NAV[this.auth.role() ?? 'rider']);
  protected roleLabel = computed(
    () =>
      ({ rider: 'Student', driver: 'Driver', college_admin: 'Disability cell', super_admin: 'Platform admin' })[
        this.auth.role() ?? 'rider'
      ],
  );
}

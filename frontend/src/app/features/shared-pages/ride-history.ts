import { Component, OnInit, computed, inject, signal } from '@angular/core';

import { Api, errorMessage } from '../../core/api';
import { Auth } from '../../core/auth';
import { RIDE_STATUS, dateTime, distance } from '../../core/format';
import { Ride } from '../../core/models';

/** Ride history for riders ("My rides") and drivers ("Ride log"): the API filters by role. */
@Component({
  selector: 'app-ride-history',
  template: `
    <div class="spread">
      <h1>{{ isDriver() ? 'Ride log' : 'My rides' }}</h1>
      @if (isDriver() && rides().length) {
        <p class="muted">{{ completed() }} completed · {{ km() }} driven</p>
      }
    </div>
    @if (error()) {
      <div class="alert alert-danger" role="alert">{{ error() }}</div>
    }
    @if (loading()) {
      <p class="muted" aria-live="polite">Loading…</p>
    } @else if (!rides().length) {
      <div class="card empty">No rides yet.</div>
    } @else {
      <ul class="list">
        @for (r of rides(); track r.id) {
          <li class="card">
            <div class="spread">
              <strong>{{ when(r.requested_at) }}</strong>
              <span [class]="'badge ' + status(r).tone">{{ status(r).label }}</span>
            </div>
            <dl class="small">
              <dt>From</dt><dd>{{ r.pickup.address || 'Pinned location' }}</dd>
              <dt>To</dt><dd>{{ r.dropoff.address || 'Pinned location' }}</dd>
              @if (isDriver()) { <dt>Rider</dt><dd>{{ r.rider_name }}</dd> }
              @else if (r.driver) { <dt>Driver</dt><dd>{{ r.driver.full_name }} · {{ r.driver.vehicle?.plate_no }}</dd> }
              @if (r.distance_m) { <dt>Distance</dt><dd>{{ dist(r.distance_m) }}</dd> }
              @if (r.cancel_reason) { <dt>Note</dt><dd>{{ r.cancel_reason }}</dd> }
            </dl>
          </li>
        }
      </ul>
    }
  `,
  styles: `
    .list { list-style: none; padding: 0; margin: 1rem 0 0; display: grid; gap: 0.75rem; }
    dl { display: grid; grid-template-columns: auto 1fr; gap: 0.2rem 0.75rem; margin: 0.6rem 0 0; }
    dt { color: var(--text-muted); } dd { margin: 0; }
  `,
})
export class RideHistory implements OnInit {
  private api = inject(Api);
  private auth = inject(Auth);
  protected isDriver = computed(() => this.auth.role() === 'driver');
  protected rides = signal<Ride[]>([]);
  protected loading = signal(true);
  protected error = signal<string | null>(null);
  protected completed = computed(() => this.rides().filter((r) => r.status === 'completed').length);
  protected km = computed(() => distance(this.rides().reduce((s, r) => s + (r.distance_m ?? 0), 0)));

  async ngOnInit(): Promise<void> {
    try {
      this.rides.set(await this.api.myRides(50));
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.loading.set(false);
    }
  }

  protected when = dateTime;
  protected dist = distance;
  protected status = (r: Ride) => RIDE_STATUS[r.status];
}

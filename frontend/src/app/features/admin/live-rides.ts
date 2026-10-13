import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';

import { Api, errorMessage } from '../../core/api';
import { Auth } from '../../core/auth';
import { RIDE_STATUS, dateTime, duration } from '../../core/format';
import { Ride } from '../../core/models';
import { MapMarker, MapView } from '../../shared/map-view';

@Component({
  selector: 'app-admin-live-rides',
  imports: [MapView],
  template: `
    <div class="spread">
      <h1>Live rides</h1>
      <span class="small muted">{{ rides().length }} active · refreshes every 5 s</span>
    </div>
    @if (error()) {
      <div class="alert alert-danger" role="alert">{{ error() }}</div>
    }
    <div class="layout">
      <app-map [center]="campus()" [circle]="campus()" [markers]="markers()" height="min(65vh, 600px)" label="Active rides on campus" />
      <section>
        @if (!rides().length) {
          <div class="card empty">No active rides right now.</div>
        }
        <ul class="list">
          @for (r of rides(); track r.id) {
            <li class="card">
              <div class="spread">
                <strong>{{ r.rider_name }}</strong>
                <span [class]="'badge ' + status(r).tone">{{ status(r).label }}</span>
              </div>
              <div class="small muted">
                Requested {{ when(r.requested_at) }} · waiting {{ waited(r) }}
                @if (r.needs_wheelchair) { · ♿ }
              </div>
              <div class="small">
                {{ r.pickup.address || 'Pinned' }} → {{ r.dropoff.address || 'Pinned' }}
              </div>
              @if (r.driver) {
                <div class="small">Driver: {{ r.driver.full_name }} ({{ r.driver.vehicle?.plate_no }})</div>
              }
            </li>
          }
        </ul>
      </section>
    </div>
  `,
  styles: `
    .layout { display: grid; grid-template-columns: minmax(0, 1fr) 380px; gap: 1rem; align-items: start; }
    @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
    .list { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.6rem; }
  `,
})
export class AdminLiveRides implements OnInit, OnDestroy {
  private api = inject(Api);
  private auth = inject(Auth);
  protected rides = signal<Ride[]>([]);
  protected error = signal<string | null>(null);
  protected campus = computed(() => this.auth.me()?.campus ?? null);
  private timer?: ReturnType<typeof setInterval>;

  protected markers = computed<MapMarker[]>(() =>
    this.rides().flatMap((r) => {
      const out: MapMarker[] = [{ id: `p-${r.id}`, kind: 'pickup', label: `Pickup: ${r.rider_name}`, ...r.pickup }];
      if (r.driver?.position) out.push({ id: `d-${r.id}`, kind: 'driver', label: `Driver ${r.driver.full_name}`, ...r.driver.position });
      return out;
    }),
  );

  async ngOnInit(): Promise<void> {
    await this.load();
    this.timer = setInterval(() => this.load(), 5000);
  }

  private async load(): Promise<void> {
    try {
      this.rides.set(await this.api.adminRides(true));
      this.error.set(null);
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }

  protected status = (r: Ride) => RIDE_STATUS[r.status];
  protected when = dateTime;
  protected waited = (r: Ride) => duration((Date.now() - new Date(r.requested_at).getTime()) / 1000);

  ngOnDestroy(): void {
    clearInterval(this.timer);
  }
}

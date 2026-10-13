import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';

import { Api, errorMessage } from '../../core/api';
import { Auth } from '../../core/auth';
import { RIDE_STATUS, VEHICLE_KIND, distance, duration } from '../../core/format';
import { LatLng, NearbyVehicle, Place, Ride } from '../../core/models';
import { Toasts } from '../../core/toast';
import { RideSocket, Tracking } from '../../core/tracking';
import { MapMarker, MapView } from '../../shared/map-view';

const ACTIVE = new Set(['requested', 'accepted', 'ongoing']);
const STEPS = [
  { key: 'requested', label: 'Finding a driver' },
  { key: 'accepted', label: 'Driver on the way' },
  { key: 'ongoing', label: 'On board' },
  { key: 'completed', label: 'Arrived' },
];

@Component({
  selector: 'app-rider-home',
  imports: [FormsModule, RouterLink, MapView],
  template: `
    <p class="sr-only" aria-live="assertive">{{ announcement() }}</p>

    @if (!canBook()) {
      <div class="alert alert-warning stack" role="status">
        <strong>You can't book rides yet.</strong>
        <span>Your PwD verification isn't complete. <a routerLink="/rider/verification">See what's needed</a>.</span>
      </div>
    }

    <div class="layout">
      <!-- ============ left: controls ============ -->
      <section class="panel stack" aria-labelledby="panel-title">
        @switch (mode()) {
          @case ('booking') {
            <div class="card stack">
              <h1 id="panel-title">Where to?</h1>
              @if (lastExpired()) {
                <div class="alert alert-danger" role="alert">
                  No shuttle was free in time. Your locations are kept, so try again.
                </div>
              }
              <fieldset class="pick">
                <legend class="sr-only">Choose which point the map click sets</legend>
                <button type="button" class="pick-btn" [class.on]="picking() === 'pickup'" [attr.aria-pressed]="picking() === 'pickup'" (click)="picking.set('pickup')">
                  <span class="dot pickup" aria-hidden="true">P</span>
                  <span><strong>Pickup</strong><br /><span class="small muted">{{ pickup() ? coord(pickup()!) : 'Tap the map or use GPS' }}</span></span>
                </button>
                <button type="button" class="pick-btn" [class.on]="picking() === 'drop'" [attr.aria-pressed]="picking() === 'drop'" (click)="picking.set('drop')">
                  <span class="dot drop" aria-hidden="true">D</span>
                  <span><strong>Drop</strong><br /><span class="small muted">{{ drop() ? coord(drop()!) : 'Tap the map' }}</span></span>
                </button>
              </fieldset>
              <button class="btn btn-outline" type="button" (click)="useMyLocation()">📍 Use my current location as pickup</button>

              <div class="field">
                <label for="pickup-addr">Pickup landmark</label>
                <input id="pickup-addr" [(ngModel)]="pickupAddress" placeholder="e.g. Hostel BH-3 main gate" maxlength="300" />
              </div>
              <div class="field">
                <label for="drop-addr">Drop landmark</label>
                <input id="drop-addr" [(ngModel)]="dropAddress" placeholder="e.g. Library entrance" maxlength="300" />
              </div>
              <label class="check"><input type="checkbox" [(ngModel)]="needsWheelchair" /> I need a wheelchair-accessible vehicle</label>
              <div class="field">
                <label for="notes">Note for the driver</label>
                <textarea id="notes" [(ngModel)]="notes" maxlength="300" placeholder="e.g. Ramp needed, please come to the side gate"></textarea>
              </div>
              <p class="small muted" aria-live="polite">{{ nearby().length }} free shuttle{{ nearby().length === 1 ? '' : 's' }} nearby</p>
              <button class="btn btn-primary btn-lg" type="button" (click)="book()" [disabled]="!canBook() || !pickup() || !drop() || busy()">
                {{ busy() ? 'Requesting…' : 'Request ride' }}
              </button>
            </div>
          }

          @case ('tracking') {
            <div class="card stack">
              <div class="spread">
                <h1 id="panel-title">{{ statusLabel() }}</h1>
                <span class="badge" [class]="'badge ' + statusTone()">{{ ride()!.status }}</span>
              </div>
              <ol class="steps" aria-label="Ride progress">
                @for (s of steps; track s.key; let i = $index) {
                  <li [class.done]="i < stepIndex()" [class.current]="i === stepIndex()" [attr.aria-current]="i === stepIndex() ? 'step' : null">
                    {{ s.label }}
                  </li>
                }
              </ol>

              @if (ride()!.status === 'requested') {
                <p class="muted">Offering your ride to the nearest available driver. Each driver has 15 seconds to accept.</p>
              }

              @if (ride()!.driver; as d) {
                <div class="driver">
                  <div class="avatar" aria-hidden="true">{{ d.full_name[0] }}</div>
                  <div>
                    <strong>{{ d.full_name }}</strong>
                    <div class="small muted">{{ d.vehicle?.plate_no }} · {{ d.vehicle ? kind(d.vehicle.kind) : '' }}</div>
                  </div>
                  @if (d.phone) {
                    <a class="btn btn-outline call" [href]="'tel:' + d.phone" [attr.aria-label]="'Call ' + d.full_name">📞 Call</a>
                  }
                </div>
                @if (ride()!.route; as r) {
                  <p class="eta"><strong>{{ eta(r.eta_seconds) }}</strong>
                    <span class="muted"> · {{ dist(r.distance_m) }} {{ ride()!.status === 'accepted' ? 'to your pickup' : 'to destination' }}</span></p>
                }
              }

              <dl class="trip small">
                <dt>Pickup</dt><dd>{{ ride()!.pickup.address || coord(ride()!.pickup) }}</dd>
                <dt>Drop</dt><dd>{{ ride()!.dropoff.address || coord(ride()!.dropoff) }}</dd>
                @if (ride()!.needs_wheelchair) { <dt>Vehicle</dt><dd>♿ Wheelchair accessible</dd> }
              </dl>

              <div class="row">
                @if (ride()!.status !== 'ongoing') {
                  <button class="btn btn-outline" type="button" (click)="cancel()">Cancel ride</button>
                }
                <button class="btn btn-sos btn-lg grow" type="button" (click)="sos()">SOS: alert campus security</button>
              </div>
            </div>
          }

          @case ('rate') {
            <div class="card stack">
              <h1 id="panel-title">You've arrived 🎉</h1>
              <p class="muted">Trip distance {{ dist(ride()!.distance_m) }} with {{ ride()!.driver?.full_name }}.</p>
              <fieldset class="stars">
                <legend><strong>How was your ride?</strong></legend>
                @for (n of [1, 2, 3, 4, 5]; track n) {
                  <label [class.on]="n <= rating()">
                    <input type="radio" name="rating" class="sr-only" [value]="n" (change)="rating.set(n)" />
                    <span aria-hidden="true">★</span><span class="sr-only">{{ n }} star{{ n > 1 ? 's' : '' }}</span>
                  </label>
                }
              </fieldset>
              <div class="field">
                <label for="comment">Comment (optional)</label>
                <textarea id="comment" [(ngModel)]="comment" maxlength="500"></textarea>
              </div>
              <div class="row">
                <button class="btn btn-primary" type="button" (click)="submitRating()" [disabled]="!rating()">Submit rating</button>
                <button class="btn btn-ghost" type="button" (click)="finish()">Skip</button>
              </div>
            </div>
          }
        }
      </section>

      <!-- ============ right: map ============ -->
      <section class="map-col" [class.picking]="mode() === 'booking'" aria-label="Map">
        <app-map
          [center]="campusCenter()"
          [circle]="campus()"
          [markers]="markers()"
          [route]="ride()?.route?.polyline ?? null"
          [autoFit]="mode() !== 'booking'"
          height="min(70vh, 640px)"
          label="Campus map. Click to set the selected pickup or drop point."
          (mapClick)="onMapClick($event)"
        />
        @if (mode() === 'booking') {
          <p class="small muted">Click the map to place your <strong>{{ picking() }}</strong> point. Both points must be on campus.</p>
        }
      </section>
    </div>
  `,
  styles: `
    .layout { display: grid; grid-template-columns: minmax(300px, 420px) minmax(0, 1fr); gap: 1.25rem; align-items: start; margin-top: 1rem; }
    @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } .map-col { order: -1; } }
    .pick { border: 0; padding: 0; margin: 0; display: grid; gap: 0.5rem; }
    .pick-btn { display: flex; gap: 0.75rem; align-items: center; text-align: left; padding: 0.7rem; border-radius: var(--radius-sm);
      border: 2px solid var(--border); background: var(--surface); color: var(--text); font: inherit; cursor: pointer; min-height: 56px; }
    .pick-btn.on { border-color: var(--primary); background: var(--primary-soft); }
    .dot { display: grid; place-items: center; width: 30px; height: 30px; border-radius: 50%; color: #fff; font-weight: 800; flex: none; }
    .dot.pickup { background: #0f7b3e; } .dot.drop { background: #b42318; }
    .steps { list-style: none; padding: 0; margin: 0; display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.35rem; font-size: 0.78rem; }
    .steps li { padding-top: 0.5rem; border-top: 4px solid var(--border); color: var(--text-muted); }
    .steps li.done { border-color: var(--success); color: var(--text); }
    .steps li.current { border-color: var(--primary); color: var(--text); font-weight: 700; }
    .driver { display: flex; align-items: center; gap: 0.75rem; padding: 0.75rem; background: var(--surface-2); border-radius: var(--radius-sm); }
    .avatar { width: 44px; height: 44px; border-radius: 50%; display: grid; place-items: center; background: var(--primary); color: var(--on-primary); font-weight: 800; font-size: 1.2rem; }
    .call { margin-left: auto; }
    .eta { font-size: 1.15rem; margin: 0; }
    .trip { display: grid; grid-template-columns: auto 1fr; gap: 0.25rem 0.75rem; margin: 0; }
    .trip dt { color: var(--text-muted); } .trip dd { margin: 0; }
    .grow { flex: 1; }
    .stars { border: 0; padding: 0; display: flex; gap: 0.25rem; align-items: center; flex-wrap: wrap; }
    .stars legend { margin-bottom: 0.5rem; width: 100%; }
    .stars label { font-size: 2.2rem; color: var(--border); cursor: pointer; line-height: 1; padding: 0.1rem; }
    .stars label.on { color: #f5a524; }
    .stars label:focus-within { outline: 3px solid var(--focus); border-radius: 6px; }
  `,
})
export class RiderHome implements OnInit, OnDestroy {
  private api = inject(Api);
  private auth = inject(Auth);
  private tracking = inject(Tracking);
  private toasts = inject(Toasts);

  protected readonly steps = STEPS;
  protected ride = signal<Ride | null>(null);
  protected driverPos = signal<LatLng | null>(null);
  protected nearby = signal<NearbyVehicle[]>([]);
  protected pickup = signal<LatLng | null>(null);
  protected drop = signal<LatLng | null>(null);
  protected picking = signal<'pickup' | 'drop'>('pickup');
  protected busy = signal(false);
  protected announcement = signal('');
  protected lastExpired = signal(false);
  protected rating = signal(0);
  protected comment = '';
  protected pickupAddress = '';
  protected dropAddress = '';
  protected notes = this.auth.me()?.student?.pickup_notes ?? '';
  protected needsWheelchair = this.auth.me()?.student?.needs_wheelchair ?? false;

  private socket?: RideSocket;
  private sub?: Subscription;
  private pollTimer?: ReturnType<typeof setInterval>;

  protected campus = computed(() => this.auth.me()?.campus ?? null);
  protected campusCenter = computed(() => this.campus() ?? { lat: 25.4304, lng: 81.7713 });
  protected canBook = computed(() => {
    const s = this.auth.me()?.student;
    return s?.verification_status === 'verified' && (!s.verification_expires_at || new Date(s.verification_expires_at) > new Date());
  });
  protected mode = computed<'booking' | 'tracking' | 'rate'>(() => {
    const r = this.ride();
    if (!r) return 'booking';
    if (ACTIVE.has(r.status)) return 'tracking';
    return r.status === 'completed' ? 'rate' : 'booking';
  });
  protected stepIndex = computed(() => STEPS.findIndex((s) => s.key === this.ride()?.status));
  protected statusLabel = computed(() => (this.ride() ? RIDE_STATUS[this.ride()!.status].label : ''));
  protected statusTone = computed(() => (this.ride() ? RIDE_STATUS[this.ride()!.status].tone : ''));

  protected markers = computed<MapMarker[]>(() => {
    const out: MapMarker[] = [];
    const r = this.ride();
    if (r && this.mode() === 'tracking') {
      out.push({ id: 'pickup', kind: 'pickup', label: 'Pickup', ...r.pickup });
      out.push({ id: 'drop', kind: 'drop', label: 'Drop', ...r.dropoff });
      const d = this.driverPos() ?? r.driver?.position;
      if (d) out.push({ id: 'driver', kind: 'driver', label: 'Your driver', ...d });
      return out;
    }
    if (this.pickup()) out.push({ id: 'pickup', kind: 'pickup', label: 'Pickup', ...this.pickup()! });
    if (this.drop()) out.push({ id: 'drop', kind: 'drop', label: 'Drop', ...this.drop()! });
    this.nearby().forEach((v, i) =>
      out.push({ id: `v${i}`, kind: 'vehicle', label: `Free shuttle, ${distance(v.distance_m)} away`, lat: v.lat, lng: v.lng }),
    );
    return out;
  });

  async ngOnInit(): Promise<void> {
    try {
      const active = await this.api.activeRide();
      if (active) this.attach(active);
    } catch {
      /* shown as empty booking state */
    }
    this.pollTimer = setInterval(() => this.tick(), 5000);
    this.tick();
  }

  private async tick(): Promise<void> {
    const r = this.ride();
    try {
      if (r && ACTIVE.has(r.status)) {
        this.update(await this.api.ride(r.id)); // refreshes ETA/route; also a fallback if the socket drops
      } else if (this.canBook()) {
        this.nearby.set(await this.api.nearby(this.pickup() ?? this.campusCenter()));
      }
    } catch {
      /* transient; next tick retries */
    }
  }

  private attach(ride: Ride): void {
    this.ride.set(ride);
    this.driverPos.set(ride.driver?.position ?? null);
    this.socket?.close();
    this.sub?.unsubscribe();
    this.socket = this.tracking.connect(ride.id);
    this.sub = this.socket.events.subscribe(async (e) => {
      if (e.type === 'location' && e.from === 'driver') this.driverPos.set({ lat: e.lat, lng: e.lng });
      if (e.type === 'snapshot' && e.driver) this.driverPos.set(e.driver);
      if (e.type === 'status') this.update(await this.api.ride(ride.id));
    });
  }

  private update(next: Ride): void {
    const prev = this.ride();
    this.ride.set(next);
    if (prev?.status === next.status) return;
    this.announcement.set(RIDE_STATUS[next.status].label);
    if (next.status === 'accepted') this.toasts.success(`${next.driver?.full_name} is on the way`);
    if (next.status === 'expired') {
      this.lastExpired.set(true);
      this.ride.set(null);
    }
    if (next.status === 'cancelled') this.ride.set(null);
  }

  protected onMapClick(at: LatLng): void {
    if (this.mode() !== 'booking') return;
    if (this.picking() === 'pickup') {
      this.pickup.set(at);
      if (!this.drop()) this.picking.set('drop');
      this.announcement.set('Pickup set');
    } else {
      this.drop.set(at);
      this.announcement.set('Drop set');
    }
  }

  protected useMyLocation(): void {
    if (!navigator.geolocation) {
      this.toasts.error('Location is not available in this browser');
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (p) => {
        this.pickup.set({ lat: p.coords.latitude, lng: p.coords.longitude });
        this.picking.set('drop');
        this.toasts.show('Pickup set to your current location');
      },
      () => this.toasts.error('Could not get your location. Tap the map instead.'),
      { enableHighAccuracy: true, timeout: 10000 },
    );
  }

  protected async book(): Promise<void> {
    this.busy.set(true);
    this.lastExpired.set(false);
    try {
      const place = (p: LatLng, address: string): Place => ({ ...p, address: address.trim() || null });
      const ride = await this.api.requestRide({
        pickup: place(this.pickup()!, this.pickupAddress),
        dropoff: place(this.drop()!, this.dropAddress),
        needs_wheelchair: this.needsWheelchair,
        notes: this.notes.trim() || null,
      });
      this.attach(ride);
      this.announcement.set('Ride requested. Finding a driver.');
    } catch (e) {
      this.toasts.error(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  protected async cancel(): Promise<void> {
    if (!confirm('Cancel this ride?')) return;
    try {
      await this.api.cancelRide(this.ride()!.id);
      this.socket?.close();
      this.ride.set(null);
      this.toasts.show('Ride cancelled');
    } catch (e) {
      this.toasts.error(errorMessage(e));
    }
  }

  protected async sos(): Promise<void> {
    if (!confirm('Send an emergency alert with your live location to campus security?')) return;
    try {
      const res = await this.api.sos(this.ride()!.id);
      this.toasts.success(res.message);
    } catch (e) {
      this.toasts.error(errorMessage(e));
    }
  }

  protected async submitRating(): Promise<void> {
    try {
      await this.api.rate(this.ride()!.id, this.rating(), this.comment);
      this.toasts.success('Thanks for the feedback!');
    } catch (e) {
      this.toasts.error(errorMessage(e));
    }
    this.finish();
  }

  protected finish(): void {
    this.socket?.close();
    this.ride.set(null);
    this.rating.set(0);
    this.comment = '';
    this.pickup.set(null);
    this.drop.set(null);
    this.picking.set('pickup');
  }

  protected coord(p: LatLng): string {
    return `${p.lat.toFixed(5)}, ${p.lng.toFixed(5)}`;
  }
  protected eta = duration;
  protected dist = distance;
  protected kind = (k: keyof typeof VEHICLE_KIND) => VEHICLE_KIND[k];

  ngOnDestroy(): void {
    clearInterval(this.pollTimer);
    this.sub?.unsubscribe();
    this.socket?.close();
  }
}

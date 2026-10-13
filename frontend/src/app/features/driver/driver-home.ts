import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Subscription } from 'rxjs';

import { Api, errorMessage } from '../../core/api';
import { Auth } from '../../core/auth';
import { RIDE_STATUS, VEHICLE_KIND, distance, duration, stepToward } from '../../core/format';
import { DriverStatus, LatLng, Offer, Ride } from '../../core/models';
import { Toasts } from '../../core/toast';
import { RideSocket, Tracking } from '../../core/tracking';
import { MapMarker, MapView } from '../../shared/map-view';

const TICK_MS = 2000;
const SIM_SPEED_MPS = 9; // ~32 km/h campus shuttle
const HEARTBEAT_EVERY_TICKS = 5; // idle drivers ping every 10 s (server drops them after 60 s)

@Component({
  selector: 'app-driver-home',
  imports: [FormsModule, MapView],
  template: `
    <p class="sr-only" aria-live="assertive">{{ announcement() }}</p>
    <div class="layout">
      <section class="stack">
        <!-- availability -->
        <div class="card stack">
          <div class="spread">
            <h1>{{ status()?.is_online ? 'You are online' : 'You are offline' }}</h1>
            <span class="badge" [class.badge-success]="status()?.is_online">{{ status()?.is_online ? 'Online' : 'Offline' }}</span>
          </div>
          @if (status()?.vehicle; as v) {
            <p class="muted small">Vehicle {{ v.plate_no }} · {{ kind(v.kind) }} · seats {{ v.capacity }}</p>
          } @else {
            <div class="alert alert-warning small">No vehicle assigned. Ask your college admin to assign one in Fleet.</div>
          }
          <fieldset class="source">
            <legend class="small"><strong>Location source</strong></legend>
            <label class="check"><input type="radio" name="src" value="sim" [(ngModel)]="source" (change)="applySource()" /> Simulated (demo: click the map to place yourself)</label>
            <label class="check"><input type="radio" name="src" value="gps" [(ngModel)]="source" (change)="applySource()" /> Device GPS</label>
          </fieldset>
          @if (!ride()) {
            <button class="btn btn-lg" [class.btn-primary]="!status()?.is_online" [class.btn-outline]="status()?.is_online"
                    type="button" (click)="toggleOnline()" [disabled]="busy() || !status()?.vehicle">
              {{ status()?.is_online ? 'Go offline' : 'Go online' }}
            </button>
          }
        </div>

        <!-- incoming offer -->
        @if (offer(); as o) {
          <div class="card stack offer" role="alertdialog" aria-labelledby="offer-title" aria-describedby="offer-desc">
            <div class="spread">
              <h2 id="offer-title">New ride request</h2>
              <span class="countdown" [attr.aria-label]="secondsLeft() + ' seconds left to accept'">{{ secondsLeft() }}s</span>
            </div>
            <div id="offer-desc" class="stack">
              <p><strong>{{ o.rider_name }}</strong> · pickup {{ dist(o.distance_to_pickup_m) }} away</p>
              @if (o.needs_wheelchair) { <span class="badge badge-warning">♿ Wheelchair user</span> }
              @if (o.rider_notes) {
                <div class="alert alert-warning"><strong>Accessibility note:</strong> {{ o.rider_notes }}</div>
              }
              <dl class="trip small">
                <dt>Pickup</dt><dd>{{ o.pickup.address || 'Pinned location' }}</dd>
                <dt>Drop</dt><dd>{{ o.dropoff.address || 'Pinned location' }}</dd>
              </dl>
            </div>
            <div class="row">
              <button class="btn btn-primary btn-lg grow" type="button" (click)="accept()" [disabled]="busy()">Accept</button>
              <button class="btn btn-outline btn-lg" type="button" (click)="decline()" [disabled]="busy()">Decline</button>
            </div>
          </div>
        } @else if (status()?.is_online && !ride()) {
          <div class="card empty" aria-live="polite">Waiting for ride requests…</div>
        }

        <!-- active ride -->
        @if (ride(); as r) {
          <div class="card stack">
            <div class="spread">
              <h2>{{ r.status === 'accepted' ? 'Head to pickup' : r.status === 'ongoing' ? 'Drive to drop-off' : statusLabel() }}</h2>
              <span [class]="'badge ' + statusTone()">{{ statusLabel() }}</span>
            </div>
            <p><strong>{{ r.rider_name }}</strong>
              @if (r.needs_wheelchair) { <span class="badge badge-warning">♿ Wheelchair</span> }</p>
            @if (r.rider_notes) {
              <div class="alert alert-warning"><strong>Before you arrive:</strong> {{ r.rider_notes }}</div>
            }
            @if (r.route; as route) {
              <p class="eta"><strong>{{ eta(route.eta_seconds) }}</strong> <span class="muted">· {{ dist(route.distance_m) }}</span></p>
            }
            <dl class="trip small">
              <dt>Pickup</dt><dd>{{ r.pickup.address || 'Pinned location' }}</dd>
              <dt>Drop</dt><dd>{{ r.dropoff.address || 'Pinned location' }}</dd>
            </dl>
            @if (source === 'sim' && (r.status === 'accepted' || r.status === 'ongoing')) {
              <label class="check"><input type="checkbox" [(ngModel)]="simDrive" /> Simulate driving toward the {{ r.status === 'accepted' ? 'pickup' : 'drop-off' }}</label>
            }
            <div class="row">
              @if (r.status === 'accepted') {
                <button class="btn btn-primary btn-lg grow" type="button" (click)="act('start')" [disabled]="busy()">Rider on board: start</button>
                <button class="btn btn-outline" type="button" (click)="handBack()" [disabled]="busy()">Hand back</button>
              }
              @if (r.status === 'ongoing') {
                <button class="btn btn-primary btn-lg grow" type="button" (click)="act('complete')" [disabled]="busy()">Complete ride</button>
              }
              @if (r.status === 'completed') {
                <p class="grow">Ride complete · {{ dist(r.distance_m) }}. Rate {{ r.rider_name }}?</p>
                @for (n of [1, 2, 3, 4, 5]; track n) {
                  <button class="btn btn-outline" type="button" (click)="rateRider(n)" [attr.aria-label]="n + ' stars'">{{ n }}★</button>
                }
                <button class="btn btn-ghost" type="button" (click)="ride.set(null)">Skip</button>
              }
              @if (r.status === 'accepted' || r.status === 'ongoing') {
                <button class="btn btn-sos" type="button" (click)="sos()">SOS</button>
              }
            </div>
          </div>
        }
      </section>

      <section aria-label="Map">
        <app-map [center]="position()" [circle]="campus()" [markers]="markers()" [route]="ride()?.route?.polyline ?? null"
                 height="min(72vh, 660px)" label="Driver map" [autoFit]="!!ride() || !!offer()" (mapClick)="placeMe($event)" />
        <p class="small muted">Your position: {{ position().lat.toFixed(5) }}, {{ position().lng.toFixed(5) }}
          @if (source === 'sim' && !ride()) { · click the map to move }</p>
      </section>
    </div>
  `,
  styles: `
    .layout { display: grid; grid-template-columns: minmax(300px, 420px) minmax(0, 1fr); gap: 1.25rem; align-items: start; }
    @media (max-width: 900px) { .layout { grid-template-columns: 1fr; } }
    .source { border: 0; padding: 0; margin: 0; }
    .offer { border: 2px solid var(--primary); }
    .countdown { font-size: 1.6rem; font-weight: 800; color: var(--primary); font-variant-numeric: tabular-nums; }
    .trip { display: grid; grid-template-columns: auto 1fr; gap: 0.25rem 0.75rem; margin: 0; }
    .trip dt { color: var(--text-muted); } .trip dd { margin: 0; }
    .eta { font-size: 1.15rem; margin: 0; }
    .grow { flex: 1; }
  `,
})
export class DriverHome implements OnInit, OnDestroy {
  private api = inject(Api);
  private auth = inject(Auth);
  private tracking = inject(Tracking);
  private toasts = inject(Toasts);

  protected status = signal<DriverStatus | null>(null);
  protected offer = signal<Offer | null>(null);
  protected ride = signal<Ride | null>(null);
  protected campus = computed(() => this.auth.me()?.campus ?? null);
  protected position = signal<LatLng>(this.auth.me()?.campus ?? { lat: 25.4304, lng: 81.7713 });
  protected busy = signal(false);
  protected announcement = signal('');
  protected source: 'sim' | 'gps' = 'sim';
  protected simDrive = true;
  private now = signal(Date.now());
  private ticks = 0;
  private announcedOffer: string | null = null;
  private timer?: ReturnType<typeof setInterval>;
  private clock?: ReturnType<typeof setInterval>;
  private watchId?: number;
  private socket?: RideSocket;
  private sub?: Subscription;

  protected secondsLeft = computed(() => {
    const o = this.offer();
    return o ? Math.max(0, Math.ceil((new Date(o.expires_at).getTime() - this.now()) / 1000)) : 0;
  });
  protected statusLabel = computed(() => (this.ride() ? RIDE_STATUS[this.ride()!.status].label : ''));
  protected statusTone = computed(() => (this.ride() ? RIDE_STATUS[this.ride()!.status].tone : ''));
  protected markers = computed<MapMarker[]>(() => {
    const out: MapMarker[] = [{ id: 'me', kind: 'driver', label: 'You', ...this.position() }];
    const trip = this.ride() ?? this.offer();
    if (trip) {
      out.push({ id: 'pickup', kind: 'pickup', label: 'Pickup', ...trip.pickup });
      out.push({ id: 'drop', kind: 'drop', label: 'Drop-off', ...trip.dropoff });
    }
    return out;
  });

  async ngOnInit(): Promise<void> {
    try {
      const s = await this.api.driverStatus();
      this.status.set(s);
      if (s.position) this.position.set(s.position);
      if (s.active_ride_id) this.attach(await this.api.ride(s.active_ride_id));
    } catch (e) {
      this.toasts.error(errorMessage(e));
    }
    this.timer = setInterval(() => this.tick(), TICK_MS);
    this.clock = setInterval(() => this.now.set(Date.now()), 500);
  }

  /** One loop drives everything: offer polling, heartbeats, simulated movement, GPS streaming. */
  private async tick(): Promise<void> {
    this.ticks++;
    const r = this.ride();
    try {
      if (r && (r.status === 'accepted' || r.status === 'ongoing')) {
        if (this.source === 'sim' && this.simDrive) {
          const target = r.status === 'accepted' ? r.pickup : r.dropoff;
          const next = stepToward(this.position(), target, (SIM_SPEED_MPS * TICK_MS) / 1000);
          this.position.set({ lat: next.lat, lng: next.lng });
        }
        this.socket?.send(this.position());
        if (this.ticks % 5 === 0) this.ride.set(await this.api.ride(r.id)); // refresh route/ETA
      } else if (this.status()?.is_online && !r) {
        this.offer.set(await this.api.currentOffer());
        if (this.ticks % HEARTBEAT_EVERY_TICKS === 0) await this.api.reportLocation(this.position());
      }
    } catch {
      /* transient network error; next tick retries */
    }
    const o = this.offer();
    if (o && this.secondsLeft() === 0) this.offer.set(null);
    if (o && o.ride_id !== this.announcedOffer) {
      this.announcedOffer = o.ride_id;
      this.announcement.set(`New ride request from ${o.rider_name}. ${this.secondsLeft()} seconds to accept.`);
    }
  }

  protected applySource(): void {
    if (this.watchId !== undefined) navigator.geolocation.clearWatch(this.watchId);
    this.watchId = undefined;
    if (this.source === 'gps') {
      if (!navigator.geolocation) {
        this.toasts.error('GPS is not available in this browser');
        this.source = 'sim';
        return;
      }
      this.watchId = navigator.geolocation.watchPosition(
        (p) => this.position.set({ lat: p.coords.latitude, lng: p.coords.longitude }),
        () => this.toasts.error('Could not read GPS'),
        { enableHighAccuracy: true },
      );
    }
  }

  protected placeMe(at: LatLng): void {
    if (this.source === 'sim' && !this.ride()) {
      this.position.set(at);
      if (this.status()?.is_online) this.api.reportLocation(at).catch(() => undefined);
    }
  }

  protected async toggleOnline(): Promise<void> {
    this.busy.set(true);
    try {
      const s = this.status()?.is_online ? await this.api.goOffline() : await this.api.goOnline(this.position());
      this.status.set(s);
      this.offer.set(null);
      this.toasts.show(s.is_online ? "You're online. Ride requests will appear here." : "You're offline");
    } catch (e) {
      this.toasts.error(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  protected async accept(): Promise<void> {
    const o = this.offer();
    if (!o) return;
    this.busy.set(true);
    try {
      this.attach(await this.api.rideAction(o.ride_id, 'accept'));
      this.offer.set(null);
      this.announcement.set('Ride accepted. Head to the pickup.');
    } catch (e) {
      this.offer.set(null);
      this.toasts.error(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  protected async decline(): Promise<void> {
    const o = this.offer();
    this.offer.set(null);
    if (o) await this.api.rejectOffer(o.ride_id).catch(() => undefined);
  }

  protected async act(action: 'start' | 'complete'): Promise<void> {
    this.busy.set(true);
    try {
      this.ride.set(await this.api.rideAction(this.ride()!.id, action));
      if (action === 'complete') {
        this.socket?.close();
        this.toasts.success('Ride completed');
      }
    } catch (e) {
      this.toasts.error(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  protected async handBack(): Promise<void> {
    const reason = prompt('Why are you handing this ride back? (shared with dispatch)');
    if (reason === null) return;
    try {
      await this.api.cancelRide(this.ride()!.id, reason);
      this.socket?.close();
      this.ride.set(null);
      this.toasts.show('Ride handed back. Dispatch is reassigning it.');
    } catch (e) {
      this.toasts.error(errorMessage(e));
    }
  }

  protected async rateRider(n: number): Promise<void> {
    await this.api.rate(this.ride()!.id, n).catch(() => undefined);
    this.ride.set(null);
    this.toasts.success('Thanks!');
  }

  protected async sos(): Promise<void> {
    if (!confirm('Alert campus security with your live location?')) return;
    try {
      this.toasts.success((await this.api.sos(this.ride()!.id)).message);
    } catch (e) {
      this.toasts.error(errorMessage(e));
    }
  }

  private attach(ride: Ride): void {
    this.ride.set(ride);
    this.socket?.close();
    this.sub?.unsubscribe();
    this.socket = this.tracking.connect(ride.id);
    this.sub = this.socket.events.subscribe(async (e) => {
      if (e.type !== 'status') return;
      if (e.status === 'cancelled') {
        this.toasts.error('The rider cancelled this ride');
        this.ride.set(null);
        this.socket?.close();
      } else {
        this.ride.set(await this.api.ride(ride.id));
      }
    });
  }

  protected dist = distance;
  protected eta = duration;
  protected kind = (k: keyof typeof VEHICLE_KIND) => VEHICLE_KIND[k];

  ngOnDestroy(): void {
    clearInterval(this.timer);
    clearInterval(this.clock);
    if (this.watchId !== undefined) navigator.geolocation.clearWatch(this.watchId);
    this.sub?.unsubscribe();
    this.socket?.close();
  }
}

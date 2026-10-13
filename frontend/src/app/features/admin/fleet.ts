import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormBuilder, FormsModule, ReactiveFormsModule, Validators } from '@angular/forms';

import { Api, errorMessage } from '../../core/api';
import { VEHICLE_KIND } from '../../core/format';
import { FleetDriver, Vehicle, VehicleKind } from '../../core/models';
import { Toasts } from '../../core/toast';

@Component({
  selector: 'app-admin-fleet',
  imports: [ReactiveFormsModule, FormsModule],
  template: `
    <h1>Fleet</h1>
    <div class="grid-2">
      <section class="card stack" aria-labelledby="veh">
        <h2 id="veh">Vehicles</h2>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>Plate</th><th>Type</th><th>Seats</th><th>Status</th></tr></thead>
            <tbody>
              @for (v of vehicles(); track v.id) {
                <tr>
                  <td><strong>{{ v.plate_no }}</strong></td>
                  <td>{{ kind(v.kind) }}</td>
                  <td>{{ v.capacity }}</td>
                  <td>
                    <button class="btn btn-outline" type="button" (click)="toggleVehicle(v)"
                            [attr.aria-label]="(v.is_active ? 'Deactivate ' : 'Activate ') + v.plate_no">
                      {{ v.is_active ? 'Active' : 'Inactive' }}
                    </button>
                  </td>
                </tr>
              } @empty {
                <tr><td colspan="4" class="empty">No vehicles yet</td></tr>
              }
            </tbody>
          </table>
        </div>
        <form class="inline-form" [formGroup]="vehicleForm" (ngSubmit)="addVehicle()">
          <h3>Add vehicle</h3>
          <div class="field"><label for="plate">Plate number</label><input id="plate" formControlName="plate_no" placeholder="UP70EV0003" /></div>
          <div class="field">
            <label for="vkind">Type</label>
            <select id="vkind" formControlName="kind">
              <option value="wheelchair_accessible">Wheelchair accessible</option>
              <option value="standard">Standard</option>
            </select>
          </div>
          <div class="field"><label for="cap">Seats</label><input id="cap" type="number" min="1" max="60" formControlName="capacity" /></div>
          <button class="btn btn-primary" type="submit" [disabled]="vehicleForm.invalid">Add vehicle</button>
        </form>
      </section>

      <section class="card stack" aria-labelledby="drv">
        <h2 id="drv">Drivers</h2>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>Driver</th><th>Status</th><th>Vehicle</th></tr></thead>
            <tbody>
              @for (d of drivers(); track d.id) {
                <tr>
                  <td><strong>{{ d.full_name }}</strong><br /><span class="small muted">{{ d.phone }}</span></td>
                  <td><span class="badge" [class.badge-success]="d.is_online">{{ d.is_online ? 'Online' : 'Offline' }}</span></td>
                  <td>
                    <label class="sr-only" [for]="'veh-' + d.id">Vehicle for {{ d.full_name }}</label>
                    <select [id]="'veh-' + d.id" [ngModel]="d.vehicle?.id ?? ''" (ngModelChange)="assign(d, $event)"
                            [disabled]="d.is_online" [title]="d.is_online ? 'Driver must be offline to change vehicle' : ''">
                      <option value="">Unassigned</option>
                      @for (v of assignable(d); track v.id) {
                        <option [value]="v.id">{{ v.plate_no }}{{ v.kind === 'wheelchair_accessible' ? ' ♿' : '' }}</option>
                      }
                    </select>
                  </td>
                </tr>
              } @empty {
                <tr><td colspan="3" class="empty">No drivers yet</td></tr>
              }
            </tbody>
          </table>
        </div>
        <form class="inline-form" [formGroup]="driverForm" (ngSubmit)="addDriver()">
          <h3>Add driver</h3>
          <div class="field"><label for="dname">Full name</label><input id="dname" formControlName="full_name" /></div>
          <div class="field"><label for="demail">Email (login)</label><input id="demail" type="email" formControlName="email" /></div>
          <div class="field"><label for="dphone">Phone</label><input id="dphone" type="tel" formControlName="phone" placeholder="+919800000003" /></div>
          <div class="field"><label for="dpw">Temporary password</label><input id="dpw" type="password" formControlName="password" autocomplete="new-password" /></div>
          <button class="btn btn-primary" type="submit" [disabled]="driverForm.invalid">Add driver</button>
        </form>
      </section>
    </div>
  `,
  styles: `
    .inline-form { display: grid; gap: 0.75rem; border-top: 1px solid var(--border); padding-top: 1rem; }
    select { min-width: 150px; }
  `,
})
export class AdminFleet implements OnInit {
  private api = inject(Api);
  private toasts = inject(Toasts);
  private fb = inject(FormBuilder).nonNullable;
  protected vehicles = signal<Vehicle[]>([]);
  protected drivers = signal<FleetDriver[]>([]);
  private taken = computed(() => new Set(this.drivers().map((d) => d.vehicle?.id).filter(Boolean)));

  protected vehicleForm = this.fb.group({
    plate_no: ['', [Validators.required, Validators.minLength(4)]],
    kind: ['wheelchair_accessible' as VehicleKind],
    capacity: [4, [Validators.required, Validators.min(1)]],
  });
  protected driverForm = this.fb.group({
    full_name: ['', Validators.required],
    email: ['', [Validators.required, Validators.email]],
    phone: ['', [Validators.required, Validators.pattern(/^\+?\d{10,15}$/)]],
    password: ['', [Validators.required, Validators.minLength(8)]],
  });

  async ngOnInit(): Promise<void> {
    await this.load();
  }

  private async load(): Promise<void> {
    try {
      const [v, d] = await Promise.all([this.api.vehicles(), this.api.fleetDrivers()]);
      this.vehicles.set(v);
      this.drivers.set(d);
    } catch (e) {
      this.toasts.error(errorMessage(e));
    }
  }

  protected assignable(d: FleetDriver): Vehicle[] {
    return this.vehicles().filter((v) => v.is_active && (!this.taken().has(v.id) || v.id === d.vehicle?.id));
  }

  protected async assign(d: FleetDriver, vehicleId: string): Promise<void> {
    await this.run(() => this.api.assignVehicle(d.id, vehicleId || null), 'Vehicle assignment updated');
  }

  protected async toggleVehicle(v: Vehicle): Promise<void> {
    await this.run(() => this.api.updateVehicle(v.id, { is_active: !v.is_active }), `${v.plate_no} updated`);
  }

  protected async addVehicle(): Promise<void> {
    const v = this.vehicleForm.getRawValue();
    if (await this.run(() => this.api.addVehicle({ ...v, capacity: Number(v.capacity) }), 'Vehicle added')) {
      this.vehicleForm.reset();
    }
  }

  protected async addDriver(): Promise<void> {
    if (await this.run(() => this.api.createDriver(this.driverForm.getRawValue()), 'Driver account created')) {
      this.driverForm.reset();
    }
  }

  private async run(call: () => Promise<unknown>, ok: string): Promise<boolean> {
    try {
      await call();
      this.toasts.success(ok);
      return true;
    } catch (e) {
      this.toasts.error(errorMessage(e));
      return false;
    } finally {
      await this.load();
    }
  }

  protected kind = (k: VehicleKind) => VEHICLE_KIND[k];
}

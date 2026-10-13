import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';

import { Api, errorMessage } from '../../core/api';
import { Auth } from '../../core/auth';
import { Toasts } from '../../core/toast';

@Component({
  selector: 'app-settings',
  imports: [FormsModule],
  template: `
    <h1>Accessibility preferences</h1>
    <p class="muted">Saved preferences are applied to every new ride and shown to your driver before pickup.</p>
    <form class="card stack narrow" (ngSubmit)="save()">
      <label class="check">
        <input type="checkbox" name="wc" [(ngModel)]="needsWheelchair" />
        Always request a wheelchair-accessible vehicle
      </label>
      <div class="field">
        <label for="notes">Default pickup instructions</label>
        <textarea id="notes" name="notes" [(ngModel)]="pickupNotes" maxlength="300"
                  placeholder="e.g. Ramp needed; I use a manual wheelchair; please wait at the side entrance"></textarea>
      </div>
      <div class="field">
        <label for="ec">Emergency contact phone</label>
        <input id="ec" name="ec" type="tel" [(ngModel)]="emergency" placeholder="+91…" />
        <span class="hint">Included when you press SOS during a ride.</span>
      </div>
      <button class="btn btn-primary" type="submit" [disabled]="busy()">Save preferences</button>
    </form>

    @if (auth.me(); as me) {
      <section class="card narrow account">
        <h2>Account</h2>
        <dl class="small">
          <dt>Name</dt><dd>{{ me.full_name }}</dd>
          <dt>Email</dt><dd>{{ me.email }}</dd>
          <dt>Enrollment</dt><dd>{{ me.student?.enrollment_no }}</dd>
          <dt>College</dt><dd>{{ me.college_name }}</dd>
        </dl>
      </section>
    }
  `,
  styles: `
    .narrow { max-width: 640px; }
    .account { margin-top: 1rem; }
    dl { display: grid; grid-template-columns: auto 1fr; gap: 0.3rem 1rem; margin: 0; }
    dt { color: var(--text-muted); } dd { margin: 0; }
  `,
})
export class Settings {
  private api = inject(Api);
  private toasts = inject(Toasts);
  protected auth = inject(Auth);
  protected busy = signal(false);
  private student = this.auth.me()?.student;
  protected needsWheelchair = this.student?.needs_wheelchair ?? false;
  protected pickupNotes = this.student?.pickup_notes ?? '';
  protected emergency = this.student?.emergency_contact_phone ?? '';

  protected async save(): Promise<void> {
    this.busy.set(true);
    try {
      const me = await this.api.updateAccessibility({
        needs_wheelchair: this.needsWheelchair,
        pickup_notes: this.pickupNotes.trim() || null,
        emergency_contact_phone: this.emergency.trim() || null,
      });
      this.auth.me.set(me);
      this.toasts.success('Preferences saved');
    } catch (e) {
      this.toasts.error(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}

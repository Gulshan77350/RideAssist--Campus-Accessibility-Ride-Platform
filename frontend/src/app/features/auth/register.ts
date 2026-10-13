import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { Api, errorMessage } from '../../core/api';

@Component({
  selector: 'app-register',
  imports: [ReactiveFormsModule, RouterLink],
  template: `
    <div class="auth-page">
      <section class="hero">
        <div class="brand"><span class="logo" aria-hidden="true">♿</span> RideAssist</div>
        <h1>Create your student account</h1>
        <p>Use your official college email. We check your enrollment number against your college's
          disability cell records. If it isn't there yet, you can upload your UDID card instead.</p>
        <ul>
          <li>Step 1: college email + enrollment number</li>
          <li>Step 2: enter the 6-digit code we email you</li>
          <li>Step 3: you're verified (or upload a certificate for review)</li>
        </ul>
      </section>

      <section class="panel">
        <div class="card stack">
          <h2>Sign up</h2>
          @if (error()) {
            <div class="alert alert-danger" role="alert">{{ error() }}</div>
          }
          <form [formGroup]="form" (ngSubmit)="submit()" novalidate>
            <div class="field">
              <label for="email">College email</label>
              <input id="email" type="email" formControlName="email" autocomplete="email"
                     (blur)="lookupCollege()" aria-describedby="email-hint" required />
              <span id="email-hint" class="hint" aria-live="polite">
                @if (college()) { ✓ {{ college() }} } @else { e.g. yourrollno&#64;iiita.ac.in }
              </span>
            </div>
            <div class="field">
              <label for="enrollment">Enrollment / roll number</label>
              <input id="enrollment" formControlName="enrollment_no" autocomplete="off" required />
            </div>
            <div class="field">
              <label for="name">Full name (as in college records)</label>
              <input id="name" formControlName="full_name" autocomplete="name" required />
            </div>
            <div class="field">
              <label for="phone">Phone <span class="muted">(optional, shared with your driver)</span></label>
              <input id="phone" type="tel" formControlName="phone" autocomplete="tel" placeholder="+91…" />
            </div>
            <div class="field">
              <label for="password">Password</label>
              <input id="password" type="password" formControlName="password" autocomplete="new-password"
                     aria-describedby="pw-hint" required />
              <span id="pw-hint" class="hint">At least 8 characters.</span>
            </div>
            <button class="btn btn-primary btn-lg" type="submit" [disabled]="busy()">
              {{ busy() ? 'Creating account…' : 'Continue' }}
            </button>
          </form>
          <p class="small">Already registered? <a routerLink="/login">Log in</a></p>
        </div>
      </section>
    </div>
  `,
  styleUrl: './auth-layout.scss',
})
export class Register {
  private api = inject(Api);
  private router = inject(Router);
  protected busy = signal(false);
  protected error = signal<string | null>(null);
  protected college = signal<string | null>(null);
  protected form = inject(FormBuilder).nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    enrollment_no: ['', [Validators.required, Validators.minLength(3)]],
    full_name: ['', [Validators.required, Validators.minLength(2)]],
    phone: [''],
    password: ['', [Validators.required, Validators.minLength(8), Validators.maxLength(72)]],
  });

  protected async lookupCollege(): Promise<void> {
    const domain = this.form.controls.email.value.split('@')[1];
    this.college.set(null);
    if (!domain) return;
    try {
      this.college.set((await this.api.collegeLookup(domain)).name);
    } catch {
      /* unknown domain: the submit will explain */
    }
  }

  protected async submit(): Promise<void> {
    if (this.form.invalid) {
      this.form.markAllAsTouched();
      this.error.set('Please fill every required field. Passwords need at least 8 characters.');
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    const v = this.form.getRawValue();
    try {
      await this.api.register({ ...v, phone: v.phone.trim() || null });
      await this.router.navigate(['/verify'], { queryParams: { email: v.email.trim().toLowerCase() } });
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}

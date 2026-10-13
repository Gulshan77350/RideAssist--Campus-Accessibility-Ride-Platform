import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { errorMessage } from '../../core/api';
import { Auth, homeFor } from '../../core/auth';

const DEMO = {
  student: ['iit2023004@iiita.ac.in', 'Student@123'],
  driver: ['ramesh.driver@iiita.ac.in', 'Driver@12345'],
  admin: ['admin@iiita.ac.in', 'Admin@12345'],
} as const;

@Component({
  selector: 'app-login',
  imports: [ReactiveFormsModule, RouterLink],
  template: `
    <div class="auth-page">
      <section class="hero">
        <div class="brand"><span class="logo" aria-hidden="true">♿</span> RideAssist</div>
        <h1>Campus rides for students who need them most.</h1>
        <p>
          On-demand, free shuttle rides for verified PwD students. Book from your phone, see your driver
          arrive live, and travel with someone who already knows your accessibility needs.
        </p>
        <ul>
          <li>Verified through your college's disability cell</li>
          <li>Wheelchair-accessible vehicles matched automatically</li>
          <li>Live tracking, ETA and an SOS button on every ride</li>
        </ul>
      </section>

      <section class="panel">
        <div class="card stack">
          <div>
            <h2>Log in</h2>
            <p class="muted small">Students, drivers and disability-cell staff all sign in here.</p>
          </div>

          @if (error()) {
            <div class="alert alert-danger" role="alert">{{ error() }}</div>
          }

          <form [formGroup]="form" (ngSubmit)="submit()" novalidate>
            <div class="field">
              <label for="email">Email</label>
              <input id="email" type="email" formControlName="email" autocomplete="username" required />
            </div>
            <div class="field">
              <label for="password">Password</label>
              <input id="password" type="password" formControlName="password" autocomplete="current-password" required />
            </div>
            <button class="btn btn-primary btn-lg" type="submit" [disabled]="busy()">
              {{ busy() ? 'Signing in…' : 'Log in' }}
            </button>
          </form>

          <p class="small">New student? <a routerLink="/register">Create your account</a></p>

          <div class="divider">or try a demo account</div>
          <div class="demo">
            <button class="btn btn-outline" type="button" (click)="demo('student')">Student</button>
            <button class="btn btn-outline" type="button" (click)="demo('driver')">Driver</button>
            <button class="btn btn-outline" type="button" (click)="demo('admin')">Admin</button>
          </div>
        </div>
      </section>
    </div>
  `,
  styleUrl: './auth-layout.scss',
})
export class Login {
  private auth = inject(Auth);
  private router = inject(Router);
  protected busy = signal(false);
  protected error = signal<string | null>(null);
  protected form = inject(FormBuilder).nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    password: ['', Validators.required],
  });

  protected demo(who: keyof typeof DEMO): void {
    const [email, password] = DEMO[who];
    this.form.setValue({ email, password });
    this.submit();
  }

  protected async submit(): Promise<void> {
    if (this.form.invalid) {
      this.error.set('Enter your email and password.');
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    try {
      const { email, password } = this.form.getRawValue();
      const me = await this.auth.login(email.trim(), password);
      await this.router.navigateByUrl(homeFor(me.role));
    } catch (e) {
      const msg = errorMessage(e);
      if (msg.includes('Verify your college email')) {
        await this.router.navigate(['/verify'], { queryParams: { email: this.form.value.email } });
        return;
      }
      this.error.set(msg);
    } finally {
      this.busy.set(false);
    }
  }
}

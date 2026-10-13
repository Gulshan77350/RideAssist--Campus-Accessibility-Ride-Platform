import { Component, OnDestroy, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';

import { Api, errorMessage } from '../../core/api';
import { Auth } from '../../core/auth';
import { Toasts } from '../../core/toast';

@Component({
  selector: 'app-verify-otp',
  imports: [FormsModule, RouterLink],
  template: `
    <div class="auth-page">
      <section class="hero">
        <div class="brand"><span class="logo" aria-hidden="true">♿</span> RideAssist</div>
        <h1>Check your college inbox</h1>
        <p>We sent a 6-digit code to <strong>{{ email() }}</strong>. It proves this inbox is yours,
          so nobody can register with your roll number.</p>
        <p class="small">Running locally? Every email lands in Mailpit at localhost:8025.</p>
      </section>
      <section class="panel">
        <div class="card stack">
          <h2>Enter verification code</h2>
          @if (error()) {
            <div class="alert alert-danger" role="alert">{{ error() }}</div>
          }
          <form (ngSubmit)="confirm()">
            <div class="field">
              <label for="code">6-digit code</label>
              <input id="code" class="otp" name="code" [(ngModel)]="code" inputmode="numeric"
                     autocomplete="one-time-code" maxlength="6" pattern="[0-9]{6}" required />
            </div>
            <button class="btn btn-primary btn-lg" type="submit" [disabled]="busy() || code.length !== 6">
              {{ busy() ? 'Checking…' : 'Verify' }}
            </button>
          </form>
          <div class="spread small">
            <button class="btn btn-ghost" type="button" (click)="resend()" [disabled]="cooldown() > 0">
              {{ cooldown() > 0 ? 'Resend in ' + cooldown() + 's' : 'Resend code' }}
            </button>
            <a routerLink="/login">Back to login</a>
          </div>
        </div>
      </section>
    </div>
  `,
  styleUrl: './auth-layout.scss',
})
export class VerifyOtp implements OnDestroy {
  readonly email = input('');
  private api = inject(Api);
  private auth = inject(Auth);
  private router = inject(Router);
  private toasts = inject(Toasts);
  protected code = '';
  protected busy = signal(false);
  protected error = signal<string | null>(null);
  protected cooldown = signal(60);
  private timer = setInterval(() => this.cooldown.update((c) => Math.max(0, c - 1)), 1000);

  protected async confirm(): Promise<void> {
    this.busy.set(true);
    this.error.set(null);
    try {
      const res = await this.api.confirmOtp(this.email(), this.code);
      this.auth.setTokens(res);
      await this.auth.loadMe();
      this.toasts.success(res.message);
      await this.router.navigateByUrl(res.verification_status === 'verified' ? '/rider' : '/rider/verification');
    } catch (e) {
      this.error.set(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }

  protected async resend(): Promise<void> {
    try {
      const res = await this.api.sendOtp(this.email());
      this.toasts.show(res.message);
      this.cooldown.set(60);
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }

  ngOnDestroy(): void {
    clearInterval(this.timer);
  }
}

import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { Api, errorMessage } from '../../core/api';
import { VERIFICATION_STATUS, duration } from '../../core/format';
import { Analytics, VerificationStatus } from '../../core/models';
import { BarChart, BarDatum } from '../../shared/bar-chart';

const FUNNEL: VerificationStatus[] = ['pending_otp', 'awaiting_document', 'pending_review', 'verified', 'rejected'];

@Component({
  selector: 'app-admin-overview',
  imports: [BarChart, RouterLink],
  template: `
    <div class="spread">
      <h1>Overview</h1>
      <span class="small muted" aria-live="polite">Refreshes every 15 s</span>
    </div>
    @if (error()) {
      <div class="alert alert-danger" role="alert">{{ error() }}</div>
    }
    @if (data(); as a) {
      <div class="kpis">
        <div class="card kpi"><span class="k">{{ a.rides_today }}</span><span class="l">Rides today</span></div>
        <div class="card kpi"><span class="k">{{ a.completed_today }}</span><span class="l">Completed today</span></div>
        <div class="card kpi"><span class="k">{{ wait(a.avg_wait_seconds_7d) }}</span><span class="l">Avg. wait for a driver (7 d)</span></div>
        <div class="card kpi"><span class="k">{{ wait(a.avg_pickup_seconds_7d) }}</span><span class="l">Avg. time to pickup (7 d)</span></div>
        <div class="card kpi"><span class="k">{{ a.active_riders_30d }}</span><span class="l">Active riders (30 d)</span></div>
        <div class="card kpi"><span class="k">{{ a.online_drivers }}</span><span class="l">Drivers online now</span></div>
        <div class="card kpi" [class.warn]="a.unmatched_today > 0"><span class="k">{{ a.unmatched_today }}</span><span class="l">Unmatched requests today</span></div>
        <a class="card kpi link" routerLink="/admin/verifications"
          ><span class="k">{{ a.verification_funnel.pending_review }}</span><span class="l">Documents awaiting review →</span></a
        >
      </div>

      <div class="grid-2">
        <section class="card">
          <h2>Rides per day (last 14 days)</h2>
          @if (daily().length) {
            <app-bar-chart [data]="daily()" title="Rides per day" [legend]="['Requested', 'Completed']" />
          } @else {
            <p class="empty">No rides in the last two weeks.</p>
          }
        </section>
        <section class="card">
          <h2>Verification funnel</h2>
          <ul class="funnel">
            @for (f of funnel(); track f.key) {
              <li>
                <span class="fl">{{ f.label }}</span>
                <span class="track" aria-hidden="true"><span class="fill" [style.width.%]="f.pct"></span></span>
                <strong>{{ f.value }}</strong>
              </li>
            }
          </ul>
        </section>
      </div>
    } @else if (!error()) {
      <p class="muted">Loading…</p>
    }
  `,
  styles: `
    .kpis { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 0.75rem; margin: 1rem 0; }
    .kpi { display: flex; flex-direction: column; gap: 0.25rem; padding: 1rem; }
    .kpi .k { font-size: 1.8rem; font-weight: 800; font-variant-numeric: tabular-nums; }
    .kpi .l { color: var(--text-muted); font-size: 0.9rem; }
    .kpi.warn .k { color: var(--danger); }
    .kpi.link { text-decoration: none; color: inherit; border-color: var(--primary); }
    .funnel { list-style: none; padding: 0; margin: 0; display: grid; gap: 0.7rem; }
    .funnel li { display: grid; grid-template-columns: 150px 1fr 40px; gap: 0.75rem; align-items: center; }
    .track { height: 12px; background: var(--surface-2); border-radius: 999px; overflow: hidden; }
    .fill { display: block; height: 100%; background: var(--primary); border-radius: 999px; }
    .fl { font-size: 0.9rem; }
  `,
})
export class AdminOverview implements OnInit, OnDestroy {
  private api = inject(Api);
  protected data = signal<Analytics | null>(null);
  protected error = signal<string | null>(null);
  private timer?: ReturnType<typeof setInterval>;

  protected daily = computed<BarDatum[]>(() =>
    (this.data()?.rides_per_day ?? []).map((d) => ({ label: d.day, value: d.rides, secondary: d.completed })),
  );
  protected funnel = computed(() => {
    const f = this.data()?.verification_funnel;
    if (!f) return [];
    const max = Math.max(1, ...FUNNEL.map((k) => f[k] ?? 0));
    return FUNNEL.map((k) => ({ key: k, label: VERIFICATION_STATUS[k].label, value: f[k] ?? 0, pct: ((f[k] ?? 0) / max) * 100 }));
  });

  async ngOnInit(): Promise<void> {
    await this.load();
    this.timer = setInterval(() => this.load(), 15000);
  }

  private async load(): Promise<void> {
    try {
      this.data.set(await this.api.analytics());
      this.error.set(null);
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }

  protected wait = duration;

  ngOnDestroy(): void {
    clearInterval(this.timer);
  }
}

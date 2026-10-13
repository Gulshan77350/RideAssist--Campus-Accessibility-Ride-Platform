import { Component, OnInit, inject, signal } from '@angular/core';
import { JsonPipe } from '@angular/common';

import { Api, errorMessage } from '../../core/api';
import { dateTime } from '../../core/format';
import { Anomaly } from '../../core/models';

const LABELS: Record<string, string> = {
  'anomaly.duplicate_enrollment': 'Second claim on a registered enrollment number',
  'anomaly.otp_locked': 'OTP locked after repeated wrong codes',
  'anomaly.driver_cancelled': 'Driver handed back an accepted ride',
};

@Component({
  selector: 'app-admin-anomalies',
  imports: [JsonPipe],
  template: `
    <h1>Anomalies</h1>
    <p class="muted">Rule-based signals worth a human look. None of them blocks anyone automatically.</p>
    @if (error()) {
      <div class="alert alert-danger" role="alert">{{ error() }}</div>
    }
    <div class="card table-wrap">
      <table class="table">
        <thead><tr><th>When</th><th>Signal</th><th>Target</th><th>Details</th><th>IP</th></tr></thead>
        <tbody>
          @for (a of items(); track a.id) {
            <tr>
              <td class="nowrap">{{ when(a.created_at) }}</td>
              <td>{{ label(a.action) }}</td>
              <td class="small">{{ a.target_type }} {{ a.target_id }}</td>
              <td class="small"><code>{{ a.details | json }}</code></td>
              <td class="small">{{ a.ip }}</td>
            </tr>
          } @empty {
            <tr><td colspan="5" class="empty">No anomalies recorded</td></tr>
          }
        </tbody>
      </table>
    </div>
  `,
  styles: `.nowrap { white-space: nowrap; } code { word-break: break-all; }`,
})
export class AdminAnomalies implements OnInit {
  private api = inject(Api);
  protected items = signal<Anomaly[]>([]);
  protected error = signal<string | null>(null);

  async ngOnInit(): Promise<void> {
    try {
      this.items.set(await this.api.anomalies());
    } catch (e) {
      this.error.set(errorMessage(e));
    }
  }

  protected label = (a: string) => LABELS[a] ?? a;
  protected when = dateTime;
}

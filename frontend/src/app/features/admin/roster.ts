import { Component, inject, signal } from '@angular/core';

import { Api, errorMessage } from '../../core/api';
import { RosterUploadOut } from '../../core/models';
import { Toasts } from '../../core/toast';

const TEMPLATE = 'enrollment_no,full_name,email,is_pwd\nIIT2024001,Student Name,iit2024001@iiita.ac.in,yes\n';

@Component({
  selector: 'app-admin-roster',
  template: `
    <h1>PwD roster</h1>
    <div class="grid-2">
      <section class="card stack">
        <h2>Upload roster CSV</h2>
        <p class="muted small">
          Upload the disability office's list of PwD-registered students. Existing rows are updated
          (an idempotent upsert). Students who already confirmed their email but weren't on the old
          roster are verified automatically when they now match.
        </p>
        <div class="field">
          <label for="csv">CSV file</label>
          <input id="csv" type="file" accept=".csv,text/csv" (change)="pick($event)" aria-describedby="csv-hint" />
          <span id="csv-hint" class="hint">Columns: enrollment_no, full_name, and optionally email and is_pwd.</span>
        </div>
        <div class="row">
          <button class="btn btn-primary" type="button" (click)="upload()" [disabled]="!file() || busy()">
            {{ busy() ? 'Uploading…' : 'Upload' }}
          </button>
          <a class="btn btn-outline" [href]="templateHref" download="roster-template.csv">Download template</a>
        </div>
      </section>

      @if (result(); as r) {
        <section class="card stack" aria-live="polite">
          <h2>Result</h2>
          <p><strong>{{ r.rows_processed }}</strong> rows imported ·
            <strong>{{ r.auto_verified_students }}</strong> waiting students auto-verified</p>
          @if (r.rows_rejected.length) {
            <div class="alert alert-warning">
              <strong>{{ r.rows_rejected.length }} rows skipped:</strong>
              <ul>
                @for (line of r.rows_rejected; track line) { <li>{{ line }}</li> }
              </ul>
            </div>
          }
        </section>
      }
    </div>
  `,
})
export class AdminRoster {
  private api = inject(Api);
  private toasts = inject(Toasts);
  protected file = signal<File | null>(null);
  protected busy = signal(false);
  protected result = signal<RosterUploadOut | null>(null);
  protected templateHref = 'data:text/csv;charset=utf-8,' + encodeURIComponent(TEMPLATE);

  protected pick(e: Event): void {
    this.file.set((e.target as HTMLInputElement).files?.[0] ?? null);
  }

  protected async upload(): Promise<void> {
    this.busy.set(true);
    try {
      this.result.set(await this.api.uploadRoster(this.file()!));
      this.toasts.success('Roster uploaded');
    } catch (e) {
      this.toasts.error(errorMessage(e));
    } finally {
      this.busy.set(false);
    }
  }
}

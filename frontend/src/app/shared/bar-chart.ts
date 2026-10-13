import { Component, computed, input } from '@angular/core';

export interface BarDatum {
  label: string;
  value: number;
  secondary?: number;
}

/** Dependency-free SVG bar chart with a screen-reader table carrying the same numbers. */
@Component({
  selector: 'app-bar-chart',
  template: `
    <figure>
      <svg [attr.viewBox]="'0 0 ' + width + ' ' + height" role="img" [attr.aria-label]="title()">
        @for (b of bars(); track b.label) {
          <g>
            <rect class="bar" [attr.x]="b.x" [attr.y]="b.y" [attr.width]="b.w" [attr.height]="b.h" rx="4">
              <title>{{ b.label }}: {{ b.value }}</title>
            </rect>
            @if (b.h2 > 0) {
              <rect class="bar2" [attr.x]="b.x" [attr.y]="b.y2" [attr.width]="b.w" [attr.height]="b.h2" rx="4" />
            }
            <text class="val" [attr.x]="b.x + b.w / 2" [attr.y]="b.y - 6">{{ b.value }}</text>
            <text class="lbl" [attr.x]="b.x + b.w / 2" [attr.y]="height - 6">{{ b.short }}</text>
          </g>
        }
      </svg>
      @if (legend()) {
        <figcaption class="small muted legend">
          <span><i class="sw bar"></i>{{ legend()![0] }}</span>
          <span><i class="sw bar2"></i>{{ legend()![1] }}</span>
        </figcaption>
      }
      <table class="sr-only">
        <caption>{{ title() }}</caption>
        <tr><th>Label</th><th>{{ legend()?.[0] ?? 'Value' }}</th>@if (legend()) {<th>{{ legend()![1] }}</th>}</tr>
        @for (d of data(); track d.label) {
          <tr><td>{{ d.label }}</td><td>{{ d.value }}</td>@if (legend()) {<td>{{ d.secondary ?? 0 }}</td>}</tr>
        }
      </table>
    </figure>
  `,
  styles: `
    figure { margin: 0; }
    svg { width: 100%; height: auto; display: block; }
    .bar { fill: var(--primary-soft); stroke: var(--primary); stroke-width: 1; }
    .bar2 { fill: var(--primary); }
    .val { font-size: 12px; text-anchor: middle; fill: var(--text); font-weight: 700; }
    .lbl { font-size: 11px; text-anchor: middle; fill: var(--text-muted); }
    .legend { display: flex; gap: 1rem; margin-top: 0.5rem; }
    .sw { display: inline-block; width: 12px; height: 12px; border-radius: 3px; margin-right: 0.35rem; vertical-align: -1px; }
    .sw.bar { background: var(--primary-soft); border: 1px solid var(--primary); }
    .sw.bar2 { background: var(--primary); }
  `,
})
export class BarChart {
  readonly data = input<BarDatum[]>([]);
  readonly title = input('Chart');
  readonly legend = input<[string, string] | null>(null);
  protected readonly width = 640;
  protected readonly height = 220;

  protected bars = computed(() => {
    const d = this.data();
    const max = Math.max(1, ...d.map((x) => x.value));
    const plotH = this.height - 44;
    const slot = this.width / Math.max(d.length, 1);
    const w = Math.min(48, slot * 0.6);
    return d.map((x, i) => {
      const h = (x.value / max) * plotH;
      const h2 = ((x.secondary ?? 0) / max) * plotH;
      const base = this.height - 24;
      return {
        label: x.label,
        short: x.label.length > 6 ? x.label.slice(-5) : x.label,
        value: x.value,
        x: i * slot + (slot - w) / 2,
        w,
        y: base - h,
        h,
        y2: base - h2,
        h2,
      };
    });
  });
}

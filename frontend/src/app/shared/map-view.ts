import {
  Component,
  ElementRef,
  OnDestroy,
  afterNextRender,
  effect,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';
import * as L from 'leaflet';

import { LatLng } from '../core/models';

export interface MapMarker {
  id: string;
  lat: number;
  lng: number;
  kind: 'pickup' | 'drop' | 'driver' | 'vehicle' | 'rider';
  label: string;
}

const GLYPH: Record<MapMarker['kind'], string> = {
  pickup: 'P',
  drop: 'D',
  driver: '🚐',
  vehicle: '🚐',
  rider: '●',
};

/**
 * Thin Leaflet wrapper driven by signals. Uses free OpenStreetMap tiles (no API key) and
 * CSS divIcons (no image assets). Everything shown here is also given as text elsewhere on
 * each page, so the map is never the only way to get information.
 */
@Component({
  selector: 'app-map',
  template: `<div #host class="map" [style.height]="height()" [attr.aria-label]="label()" role="region"></div>`,
  styles: `
    :host { display: block; min-width: 0; }
    .map {
      width: 100%;
      border-radius: var(--radius);
      border: 1px solid var(--border);
      overflow: hidden;
      z-index: 0;
    }
    :host-context(.picking) .map { cursor: crosshair; }
  `,
})
export class MapView implements OnDestroy {
  readonly center = input<LatLng | null>(null);
  readonly zoom = input(16);
  readonly markers = input<MapMarker[]>([]);
  readonly route = input<[number, number][] | null>(null);
  readonly circle = input<{ lat: number; lng: number; radius_m: number } | null>(null);
  readonly height = input('380px');
  readonly label = input('Map');
  readonly autoFit = input(true);
  readonly mapClick = output<LatLng>();

  private host = viewChild.required<ElementRef<HTMLDivElement>>('host');
  private map?: L.Map;
  private ready = signal(false);
  private markerLayers = new Map<string, L.Marker>();
  private routeLayer?: L.Polyline;
  private circleLayer?: L.Circle;
  private fittedKeys = '';
  private resize?: ResizeObserver;

  constructor() {
    afterNextRender(() => {
      const c = this.center() ?? { lat: 25.4304, lng: 81.7713 };
      this.map = L.map(this.host().nativeElement, { zoomControl: true }).setView([c.lat, c.lng], this.zoom());
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19,
        attribution: '&copy; OpenStreetMap contributors',
      }).addTo(this.map);
      this.map.on('click', (e: L.LeafletMouseEvent) => this.mapClick.emit({ lat: e.latlng.lat, lng: e.latlng.lng }));
      this.resize = new ResizeObserver(() => this.map?.invalidateSize());
      this.resize.observe(this.host().nativeElement);
      this.ready.set(true);
    });

    effect(() => {
      const markers = this.markers();
      if (this.ready()) this.syncMarkers(markers);
    });
    effect(() => {
      const route = this.route();
      if (!this.ready() || !this.map) return;
      this.routeLayer?.remove();
      this.routeLayer = route?.length
        ? L.polyline(route, { color: '#0a58ca', weight: 5, opacity: 0.75, dashArray: '8 6' }).addTo(this.map)
        : undefined;
    });
    effect(() => {
      const circle = this.circle();
      if (!this.ready() || !this.map) return;
      this.circleLayer?.remove();
      this.circleLayer = circle
        ? L.circle([circle.lat, circle.lng], {
            radius: circle.radius_m,
            color: '#0a58ca',
            weight: 1,
            fillOpacity: 0.04,
            interactive: false,
          }).addTo(this.map)
        : undefined;
    });
    effect(() => {
      const c = this.center();
      if (this.ready() && c && !this.markers().length) this.map?.setView([c.lat, c.lng]);
    });
  }

  private syncMarkers(markers: MapMarker[]): void {
    const map = this.map!;
    const seen = new Set<string>();
    for (const m of markers) {
      seen.add(m.id);
      const existing = this.markerLayers.get(m.id);
      if (existing) {
        existing.setLatLng([m.lat, m.lng]);
        continue;
      }
      const icon = L.divIcon({
        className: '',
        html: `<div class="map-pin ${m.kind}" aria-hidden="true">${GLYPH[m.kind]}</div>`,
        iconSize: [34, 34],
        iconAnchor: [17, 17],
      });
      this.markerLayers.set(
        m.id,
        L.marker([m.lat, m.lng], { icon, title: m.label, alt: m.label, keyboard: false }).addTo(map),
      );
    }
    for (const [id, layer] of this.markerLayers) {
      if (!seen.has(id)) {
        layer.remove();
        this.markerLayers.delete(id);
      }
    }
    // Re-fit only when the *set* of markers changes, not every time a driver pin moves.
    const keys = markers.map((m) => m.id).sort().join('|');
    if (this.autoFit() && keys !== this.fittedKeys && markers.length) {
      this.fittedKeys = keys;
      if (markers.length === 1) map.setView([markers[0].lat, markers[0].lng], Math.max(map.getZoom(), 16));
      else map.fitBounds(L.latLngBounds(markers.map((m) => [m.lat, m.lng])), { padding: [40, 40], maxZoom: 17 });
    }
  }

  ngOnDestroy(): void {
    this.resize?.disconnect();
    this.map?.remove();
  }
}

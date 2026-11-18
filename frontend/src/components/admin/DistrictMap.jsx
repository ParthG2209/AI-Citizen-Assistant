import { CircleMarker, MapContainer, Popup, TileLayer, Tooltip } from 'react-leaflet';

export const MP_CENTER = [23.2599, 77.4126];

export const coverageColor = (pct) => (pct < 50 ? '#e11d48' : pct < 65 ? '#d97706' : '#059669');

export default function DistrictMap({ districts, selectedId, onSelect }) {
  const maxEligible = Math.max(...districts.map((d) => d.eligible));

  return (
    <MapContainer center={MP_CENTER} zoom={7} scrollWheelZoom className="h-[460px] w-full rounded-lg">
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      {districts.map((d) => {
        const pct = Math.round((d.enrolled / d.eligible) * 100);
        const color = coverageColor(pct);
        const selected = d.id === selectedId;
        return (
          <CircleMarker
            key={d.id}
            center={d.position}
            radius={12 + (d.eligible / maxEligible) * 18}
            pathOptions={{ color: selected ? '#0f172a' : color, weight: selected ? 3 : 1.5, fillColor: color, fillOpacity: selected ? 0.6 : 0.4 }}
            eventHandlers={{ click: () => onSelect(d.id) }}
          >
            <Tooltip direction="top" offset={[0, -8]}>
              <strong>{d.name}</strong> · {pct}% coverage
            </Tooltip>
            <Popup>
              <div className="space-y-1 text-xs">
                <p className="text-sm font-bold">{d.name}</p>
                <p>Coverage: <strong>{pct}%</strong></p>
                <p>
                  Eligible {d.eligible.toLocaleString('en-IN')} · Enrolled {d.enrolled.toLocaleString('en-IN')}
                </p>
                <p>Gap: <strong>{(d.eligible - d.enrolled).toLocaleString('en-IN')}</strong> citizens</p>
                <p>Top missing documents: {d.topMissingDocs.join(', ')}</p>
              </div>
            </Popup>
          </CircleMarker>
        );
      })}
    </MapContainer>
  );
}

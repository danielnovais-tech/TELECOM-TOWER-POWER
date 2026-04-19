import { useEffect, useState, useCallback, useRef } from "react";
import { MapContainer, TileLayer, Marker, Popup, Polyline, useMapEvents, useMap } from "react-leaflet";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";
import "leaflet.markercluster";

/* ---------- custom icons ---------- */
const towerIcon = new L.Icon({
  iconUrl: "https://cdn.jsdelivr.net/gh/pointhi/leaflet-color-markers@master/img/marker-icon-red.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34],
});

const receiverIcon = new L.Icon({
  iconUrl: "https://cdn.jsdelivr.net/gh/pointhi/leaflet-color-markers@master/img/marker-icon-blue.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34],
});

const repeaterIcon = new L.Icon({
  iconUrl: "https://cdn.jsdelivr.net/gh/pointhi/leaflet-color-markers@master/img/marker-icon-gold.png",
  shadowUrl: "https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png",
  iconSize: [25, 41], iconAnchor: [12, 41], popupAnchor: [1, -34],
});

/* ---------- click handler component ---------- */
function ClickHandler({ onMapClick }) {
  useMapEvents({ click: (e) => onMapClick(e.latlng) });
  return null;
}

/* ---------- clustered tower layer ---------- */
function ClusteredTowers({ towers, onTowerSelect }) {
  const map = useMap();
  const clusterRef = useRef(null);
  const prevTowersRef = useRef(null);

  useEffect(() => {
    if (prevTowersRef.current === towers) return;
    prevTowersRef.current = towers;

    if (clusterRef.current) {
      map.removeLayer(clusterRef.current);
    }

    const cluster = L.markerClusterGroup({
      chunkedLoading: true,
      chunkInterval: 100,
      chunkDelay: 10,
      maxClusterRadius: 60,
      disableClusteringAtZoom: 16,
      spiderfyOnMaxZoom: true,
    });

    const markers = towers.map((t) => {
      const m = L.marker([t.lat, t.lon], { icon: towerIcon });
      m.bindPopup(
        `<strong>${t.id}</strong><br/>${t.operator} &middot; ${t.height_m}m<br/>${(t.bands || []).join(", ")}<br/>${t.power_dbm} dBm`
      );
      m.on("click", () => onTowerSelect(t));
      return m;
    });

    cluster.addLayers(markers);
    map.addLayer(cluster);
    clusterRef.current = cluster;

    return () => {
      if (clusterRef.current) {
        map.removeLayer(clusterRef.current);
        clusterRef.current = null;
      }
    };
  }, [towers, map, onTowerSelect]);

  return null;
}

/* ---------- main map component ---------- */
export default function TowerMap({
  towers,
  receiverPos,
  onMapClick,
  onTowerSelect,
  selectedTower,
  analysisResult,
  repeaterChain,
}) {
  const [center, setCenter] = useState([-15.80, -47.88]);

  useEffect(() => {
    if (towers.length > 0) {
      const avgLat = towers.reduce((s, t) => s + t.lat, 0) / towers.length;
      const avgLon = towers.reduce((s, t) => s + t.lon, 0) / towers.length;
      setCenter([avgLat, avgLon]);
    }
  }, [towers]);

  /* build link line from selected tower to receiver */
  const linkLine = selectedTower && receiverPos
    ? [[selectedTower.lat, selectedTower.lon], [receiverPos.lat, receiverPos.lng]]
    : null;

  /* colour by feasibility */
  const linkColor = analysisResult
    ? analysisResult.feasible ? "#22c55e" : "#ef4444"
    : "#3b82f6";

  /* repeater chain polyline */
  const chainPositions = repeaterChain.length > 0
    ? repeaterChain.map((r) => [r.lat, r.lon])
    : [];
  const fullChain = selectedTower && chainPositions.length > 0 && receiverPos
    ? [[selectedTower.lat, selectedTower.lon], ...chainPositions, [receiverPos.lat, receiverPos.lng]]
    : [];

  return (
    <MapContainer center={center} zoom={11} style={{ height: "100%", width: "100%" }}>
      <TileLayer
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
      />
      <ClickHandler onMapClick={onMapClick} />

      {/* towers – clustered for performance */}
      <ClusteredTowers towers={towers} onTowerSelect={onTowerSelect} />

      {/* receiver */}
      {receiverPos && (
        <Marker position={[receiverPos.lat, receiverPos.lng]} icon={receiverIcon}>
          <Popup>Receiver<br />{receiverPos.lat.toFixed(5)}, {receiverPos.lng.toFixed(5)}</Popup>
        </Marker>
      )}

      {/* link line */}
      {linkLine && (
        <Polyline positions={linkLine} pathOptions={{ color: linkColor, weight: 3, dashArray: "8 4" }} />
      )}

      {/* repeater chain */}
      {repeaterChain.map((r, i) => (
        <Marker key={`rep-${i}`} position={[r.lat, r.lon]} icon={repeaterIcon}>
          <Popup>Repeater #{i + 1}<br />{r.id}<br />{r.operator}</Popup>
        </Marker>
      ))}
      {fullChain.length > 1 && (
        <Polyline positions={fullChain} pathOptions={{ color: "#f59e0b", weight: 3 }} />
      )}
    </MapContainer>
  );
}

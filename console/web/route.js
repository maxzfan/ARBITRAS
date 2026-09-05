/* Route kinematics -- pure functions, no DOM. Mirrors console/mission.py.
 *
 * The route is a PRESENTATION FRAME: the receiver (USN8) is a static station.
 *   TRUE     = routePoint(m, s)
 *   BELIEVED = TRUE + D, D = ENU(position) - ENU(_truth)   <- measured, from data
 * Nothing here invents displacement; it only places a scripted frame under it.
 *
 * Loads as a classic <script> (defines window.Route) or via require() in node.
 * All functions take the /mission object `m` so Python stays the single source
 * of truth for the geometry.
 */
(function (root) {
  function segLengths(m) {
    const r = m.route, out = [];
    for (let i = 0; i + 1 < r.length; i++) out.push(Math.hypot(r[i + 1].e - r[i].e, r[i + 1].n - r[i].n));
    return out;
  }
  function routeLength(m) { return segLengths(m).reduce((a, b) => a + b, 0); }

  // -> {e, n, heading}. heading in radians, 0 = +east, pi/2 = +north, CCW positive.
  function routePoint(m, s) {
    const r = m.route, segs = segLengths(m), total = segs.reduce((a, b) => a + b, 0);
    s = Math.min(Math.max(s, 0), total);
    let acc = 0;
    for (let i = 0; i < segs.length; i++) {
      const a = r[i], b = r[i + 1], seg = segs[i];
      if (s <= acc + seg || i === segs.length - 1) {
        const t = seg === 0 ? 0 : Math.min(Math.max((s - acc) / seg, 0), 1);
        return { e: a.e + t * (b.e - a.e), n: a.n + t * (b.n - a.n), heading: Math.atan2(b.n - a.n, b.e - a.e) };
      }
      acc += seg;
    }
  }

  // Signed metres from the polyline; positive = LEFT of travel direction.
  function lateralOffset(m, e, n) {
    const r = m.route; let best = null;
    for (let i = 0; i + 1 < r.length; i++) {
      const de = r[i + 1].e - r[i].e, dn = r[i + 1].n - r[i].n, L2 = de * de + dn * dn;
      let t = L2 === 0 ? 0 : ((e - r[i].e) * de + (n - r[i].n) * dn) / L2;
      t = Math.min(Math.max(t, 0), 1);
      const pe = r[i].e + t * de, pn = r[i].n + t * dn, d = Math.hypot(e - pe, n - pn);
      if (best === null || d < best.d) {
        const cross = de * (n - pn) - dn * (e - pe);
        best = { d, signed: cross === 0 ? d : Math.sign(cross) * d };
      }
    }
    return best.signed;
  }

  function enuToLatLon(m, e, n) {
    return { lat: m.surveyed.lat + n / m.m_per_deg_lat, lon: m.surveyed.lon + e / m.m_per_deg_lon };
  }
  function latLonToEnu(m, lat, lon) {
    return { e: (lon - m.surveyed.lon) * m.m_per_deg_lon, n: (lat - m.surveyed.lat) * m.m_per_deg_lat };
  }
  // Measured displacement vector for one epoch, ENU metres. Null if no truth channel.
  function displacement(m, epoch) {
    if (!epoch || !epoch.position || !epoch._truth) return null;
    const b = latLonToEnu(m, epoch.position.lat, epoch.position.lon);
    const t = latLonToEnu(m, epoch._truth.lat, epoch._truth.lon);
    return { e: b.e - t.e, n: b.n - t.n, mag: Math.hypot(b.e - t.e, b.n - t.n) };
  }

  const api = { segLengths, routeLength, routePoint, lateralOffset, enuToLatLon, latLonToEnu, displacement };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.Route = api;
})(typeof self !== "undefined" ? self : this);

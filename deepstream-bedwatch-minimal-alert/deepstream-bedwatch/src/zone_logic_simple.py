
from dataclasses import dataclass, field
from typing import Dict, Tuple, List, Optional
import time
from .geometry import point_in_polygon, dist_point_to_polygon

@dataclass
class ThresholdsSimple:
    d2_edge: float = 45.0      # pixels: near-edge if < d2_edge OR outside bed
    T_alert: float = 10.0      # seconds in pre-fall zone to raise ALERT
    cooldown_sec: float = 30.0 # suppress repeats after alert

@dataclass
class ZoneConfigSimple:
    bed_polygon: List[Tuple[float, float]]
    thresholds: ThresholdsSimple

@dataclass
class TrackState:
    last_ts: float = field(default_factory=time.time)
    dwell: float = 0.0
    cooldown_until: float = 0.0

class SimpleZoneMonitor:
    def __init__(self, cfg: ZoneConfigSimple):
        self.cfg = cfg
        self.tracks: Dict[int, TrackState] = {}

    def in_prefall(self, bottom_center: Tuple[float, float]) -> bool:
        inside_bed = point_in_polygon(bottom_center, self.cfg.bed_polygon)
        if not inside_bed:
            return True
        edge_dist = dist_point_to_polygon(bottom_center, self.cfg.bed_polygon)
        return edge_dist < self.cfg.thresholds.d2_edge

    def update(self, track_id: int, bottom_center: Tuple[float, float], bbox_wh: Tuple[float, float], now_ts: float, fps_hint: float=30.0) -> Optional[str]:
        st = self.tracks.get(track_id)
        if st is None:
            st = TrackState(); self.tracks[track_id] = st

        dt = max(1.0 / fps_hint, now_ts - st.last_ts if st.last_ts else 1.0 / fps_hint)
        st.last_ts = now_ts

        if self.in_prefall(bottom_center):
            st.dwell += dt
            if time.time() >= st.cooldown_until and st.dwell >= self.cfg.thresholds.T_alert:
                st.cooldown_until = time.time() + self.cfg.thresholds.cooldown_sec
                st.dwell = 0.0  # reset after firing
                return "ALERT"
        else:
            st.dwell = max(0.0, st.dwell - dt*0.5)  # decay dwell

        return None

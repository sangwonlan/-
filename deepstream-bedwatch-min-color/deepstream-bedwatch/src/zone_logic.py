
from dataclasses import dataclass, field
from typing import Dict, Tuple, List, Optional
import time
from .geometry import point_in_polygon, dist_point_to_polygon
@dataclass
class Thresholds:
    d1_safe_min: float = 60.0
    d2_edge: float = 45.0
    T1_heads_up: float = 8.0
    T2_alert: float = 18.0
    cooldown_sec: float = 45.0
@dataclass
class ZoneConfig:
    bed_polygon: List[Tuple[float, float]]
    thresholds: Thresholds
@dataclass
class TrackState:
    last_state: str = "SAFE"
    last_ts: float = field(default_factory=time.time)
    t_prefall: float = 0.0
    cooldown_until: float = 0.0
class ZoneMonitor:
    def __init__(self, cfg: ZoneConfig):
        self.cfg = cfg
        self.tracks: Dict[int, TrackState] = {}
    def update(self, track_id: int, bottom_center: Tuple[float, float], bbox_wh: Tuple[float, float], now_ts: float, fps_hint: float=30.0) -> Optional[Tuple[str, str]]:
        st = self.tracks.get(track_id)
        if st is None:
            st = TrackState(); self.tracks[track_id] = st
        dt = max(1.0 / fps_hint, now_ts - st.last_ts if st.last_ts else 1.0 / fps_hint)
        st.last_ts = now_ts
        w, h = bbox_wh
        aspect = (h / w) if w > 1e-6 else 10.0
        inside_bed = point_in_polygon(bottom_center, self.cfg.bed_polygon)
        edge_dist = dist_point_to_polygon(bottom_center, self.cfg.bed_polygon)
        in_safe = inside_bed and (edge_dist >= self.cfg.thresholds.d1_safe_min) and (aspect < 1.8)
        prefall_cond = (not inside_bed) or (edge_dist < self.cfg.thresholds.d2_edge) or (aspect >= 2.2 and edge_dist < (self.cfg.thresholds.d1_safe_min + 10))
        event = None
        if in_safe:
            st.last_state = "SAFE"; st.t_prefall = 0.0
        else:
            if prefall_cond:
                st.last_state = "PREFALL"; st.t_prefall += dt
                if time.time() < st.cooldown_until:
                    if st.t_prefall >= self.cfg.thresholds.T1_heads_up:
                        event = ("HEADS_UP", f"t={st.t_prefall:.1f}s (cooldown)")
                else:
                    if st.t_prefall >= self.cfg.thresholds.T2_alert:
                        st.cooldown_until = time.time() + self.cfg.thresholds.cooldown_sec
                        event = ("ALERT", f"t={st.t_prefall:.1f}s")
                    elif st.t_prefall >= self.cfg.thresholds.T1_heads_up:
                        event = ("HEADS_UP", f"t={st.t_prefall:.1f}s")
            else:
                st.t_prefall = max(0.0, st.t_prefall - dt*0.5)
        return (st.last_state, event[0]) if event else None

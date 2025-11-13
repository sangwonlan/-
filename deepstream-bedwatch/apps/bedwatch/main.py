import argparse
import time
from pathlib import Path

import cv2
import numpy as np

from src.zone_logic_simple import load_zone_config, SimpleZoneMonitor
from src.storage import write_status, append_timeline_row


def detect_person_bboxes(frame):
    """
    ⚠️ 임시 예시용 사람 검출 함수.
    지금은 화면 중앙에 대충 하나의 박스를 만든다.
    실제 사용할 때는 네 프로토타입의 사람 검출 결과로 교체하면 된다.

    반환 형식: [(x, y, w, h), ...]
    """
    h, w = frame.shape[:2]
    bw = int(w * 0.25)
    bh = int(h * 0.45)
    x = w // 2 - bw // 2
    y = h // 2 - bh // 2
    return [(x, y, bw, bh)]


def draw_bed_polygon(frame, bed_polygon, color=(0, 255, 255), thickness=2):
    """
    침대 영역(bed_polygon)을 화면에 선으로 표시 (디버깅/설명용)
    """
    pts = np.array(bed_polygon, dtype=np.int32)
    cv2.polylines(frame, [pts], isClosed=True, color=color, thickness=thickness)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Zone1(엣지 근처)만 위험구역으로 사용하는 단순 모니터"
    )
    parser.add_argument(
        "--source",
        type=str,
        default="0",
        help="영상 소스: '0'이면 웹캠, 그 외에는 영상 파일 경로",
    )
    parser.add_argument(
        "--zones",
        type=str,
        default="configs/zones/minimal_room.yaml",
        help="침대/임계 설정 YAML 경로",
    )
    parser.add_argument(
        "--display",
        type=int,
        default=1,
        help="1이면 화면 표시(cv2.imshow), 0이면 표시 안 함",
    )
    parser.add_argument(
        "--output_status",
        type=str,
        default="output/status.json",
        help="현재 상태를 저장할 JSON 경로",
    )
    parser.add_argument(
        "--output_timeline",
        type=str,
        default="output/timeline.csv",
        help="타임라인 로그 CSV 경로",
    )
    return parser.parse_args()


def open_capture(source: str):
    """
    source가 '0', '1' 같은 숫자면 웹캠, 아니면 영상 파일로 연다.
    """
    if source.isdigit():
        cap = cv2.VideoCapture(int(source))
    else:
        cap = cv2.VideoCapture(source)

    if not cap.isOpened():
        raise RuntimeError(f"영상/카메라를 열 수 없습니다: {source}")
    return cap


def main():
    args = parse_args()

    # 1) 설정 로드 (bed_polygon, d2_edge, T_alert 등)
    cfg = load_zone_config(args.zones)
    monitor = SimpleZoneMonitor(cfg)

    # 2) 영상/카메라 열기
    cap = open_capture(args.source)

    # FPS 추정 (설정값과 다를 수 있음)
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0:
        fps = cfg.fps  # YAML에 적어둔 fps 사용
    print(f"[INFO] Using FPS = {fps:.2f}")

    prev_time = time.time()
    cam_id = cfg.camera_id
    track_id = 1  # 단일 대상이라고 가정

    # 출력 디렉토리 생성
    Path(args.output_status).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_timeline).parent.mkdir(parents=True, exist_ok=True)

    while True:
        ret, frame = cap.read()
        if not ret:
            print("[INFO] 영상 끝 또는 프레임 읽기 실패, 종료합니다.")
            break

        now = time.time()
        dt = now - prev_time
        prev_time = now

        # 3) 사람 검출 (현재는 임시 더미 → 여기만 네 프로토타입 함수로 교체)
        bboxes = detect_person_bboxes(frame)

        # 4) 각 사람에 대해 Zone1 모니터 업데이트
        for bbox in bboxes:
            res = monitor.update(bbox, dt=dt)

            # 색상 결정
            if res["level"] == "SAFE":
                color = (0, 255, 0)       # 초록
            elif res["level"] == "PREFALL_SHORT":
                color = (0, 255, 255)     # 노랑(임계 미만 Zone1)
            else:
                color = (0, 0, 255)       # 빨강(임계 이상 Zone1)

            x, y, w, h = map(int, bbox)
            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)

            label = f"{res['level']} {res['dwell']:.1f}s"
            cv2.putText(
                frame,
                label,
                (x, max(0, y - 10)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                color,
                1,
                cv2.LINE_AA,
            )

            # 5) 상태/타임라인 파일 기록
            try:
                # prefall: Zone1 안인지 여부 (in_zone1)
                write_status(
                    args.output_status,
                    cam_id,
                    track_id,
                    res.get("in_zone1", False),
                    res.get("dwell", 0.0),
                )
            except Exception as e:
                print(f"[WARN] write_status 실패: {e}")

            try:
                append_timeline_row(
                    args.output_timeline,
                    cam_id,
                    track_id,
                    res.get("in_zone1", False),
                    res.get("dwell", 0.0),
                )
            except Exception as e:
                print(f"[WARN] append_timeline_row 실패: {e}")

        # 6) 침대 폴리곤 시각화 (디버깅용)
        try:
            draw_bed_polygon(frame, cfg.bed_polygon)
        except Exception as e:
            print(f"[WARN] 침대 폴리곤 그리기 실패: {e}")

        # 7) 화면 표시
        if args.display:
            cv2.imshow("Zone1 Monitor", frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                print("[INFO] 'q' 입력으로 종료합니다.")
                break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()

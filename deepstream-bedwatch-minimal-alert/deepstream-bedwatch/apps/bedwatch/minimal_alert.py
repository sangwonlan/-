
#!/usr/bin/env python3
import sys, time, argparse, yaml
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GObject', '2.0')
from gi.repository import Gst, GObject
import pyds

from src.zone_logic_simple import SimpleZoneMonitor, ZoneConfigSimple, ThresholdsSimple
from src.alerts import console_alert

Gst.init(None)
PERSON_CLASS_ID = 0

def load_zone_cfg_simple(path: str) -> ZoneConfigSimple:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    th = cfg.get("thresholds", {})
    thresholds = ThresholdsSimple(
        d2_edge=th.get("d2_edge", 45.0),
        T_alert=th.get("T_alert", 10.0),
        cooldown_sec=th.get("cooldown_sec", 30.0),
    )
    bed_poly = [(float(x), float(y)) for x, y in cfg["bed_polygon"]]
    return ZoneConfigSimple(bed_polygon=bed_poly, thresholds=thresholds)

def make_source_bin(index: int, uri: str):
    bin_name = f"source-bin-{index}"
    nbin = Gst.Bin.new(bin_name)
    if uri.startswith(("rtsp://","rtmp://")):
        src = Gst.ElementFactory.make("rtspsrc", f"rtsp-src-{index}")
        src.set_property("latency", 200)
        depay = Gst.ElementFactory.make("rtph264depay", f"depay-{index}")
        h264parse = Gst.ElementFactory.make("h264parse", f"h264parse-{index}")
        decoder = Gst.ElementFactory.make("nvv4l2decoder", f"decoder-{index}")
        for el in (src, depay, h264parse, decoder): nbin.add(el)
        src.connect("pad-added", lambda src, pad: pad.link(depay.get_static_pad("sink")))
        depay.link(h264parse); h264parse.link(decoder)
        nbin.add_pad(Gst.GhostPad.new("src", decoder.get_static_pad("src")))
    else:
        src = Gst.ElementFactory.make("filesrc", f"file-src-{index}")
        src.set_property("location", uri)
        demux = Gst.ElementFactory.make("qtdemux", f"demux-{index}")
        h264parse = Gst.ElementFactory.make("h264parse", f"h264parse-{index}")
        decoder = Gst.ElementFactory.make("nvv4l2decoder", f"decoder-{index}")
        for el in (src, demux, h264parse, decoder): nbin.add(el)
        src.link(demux)
        demux.connect("pad-added", lambda demux, pad: pad.link(h264parse.get_static_pad("sink")))
        h264parse.link(decoder)
        nbin.add_pad(Gst.GhostPad.new("src", decoder.get_static_pad("src")))
    return nbin

def osd_sink_pad_buffer_probe(pad, info, u_data):
    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(info.get_buffer()))
    l_frame = batch_meta.frame_meta_list
    zm = u_data["zone_monitor"]
    cam_id = u_data["camera_id"]
    fps_hint = u_data["fps_hint"]

    while l_frame is not None:
        try: frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration: break
        l_obj = frame_meta.obj_meta_list
        while l_obj is not None:
            try: obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
            except StopIteration: break
            if obj_meta.class_id == u_data["person_class_id"]:
                left = obj_meta.rect_params.left; top = obj_meta.rect_params.top
                width = obj_meta.rect_params.width; height = obj_meta.rect_params.height
                bottom_center = (left + width * 0.5, top + height)
                track_id = obj_meta.object_id; now = time.time()
                ev = zm.update(track_id, bottom_center, (width, height), now, fps_hint=fps_hint)
                if ev == "ALERT":
                    console_alert(cam_id, track_id, "ALERT", f"bc={bottom_center}")
            try: l_obj = l_obj.next
            except StopIteration: break
        try: l_frame = l_frame.next
        except StopIteration: break
    return Gst.PadProbeReturn.OK

def build_pipeline(args, zm: SimpleZoneMonitor):
    pipeline = Gst.Pipeline.new("bedwatch-minimal")
    srcbin = make_source_bin(0, args.source)
    streammux = Gst.ElementFactory.make("nvstreammux", "stream-muxer")
    streammux.set_property("batch-size", 1)
    streammux.set_property("width", 1280)
    streammux.set_property("height", 720)
    pgie = Gst.ElementFactory.make("nvinfer", "primary-infer")
    pgie.set_property("config-file-path", args.pgie_config)
    tracker = Gst.ElementFactory.make("nvtracker", "tracker")
    tracker.set_property("ll-config-file", args.tracker_config)
    nvosd = Gst.ElementFactory.make("nvdsosd", "osd")
    sink = Gst.ElementFactory.make("nveglglessink" if args.display else "fakesink", "sink")

    for el in [streammux, pgie, tracker, nvosd, sink]:
        if not el:
            raise RuntimeError("DeepStream element create failed")
    for el in (srcbin, streammux, pgie, tracker, nvosd, sink):
        pipeline.add(el)

    sinkpad = streammux.get_request_pad("sink_0")
    srcpad = srcbin.get_static_pad("src")
    srcpad.link(sinkpad)
    streammux.link(pgie); pgie.link(tracker); tracker.link(nvosd); nvosd.link(sink)

    osd_sink_pad = nvosd.get_static_pad("sink")
    appctx = {"zone_monitor": zm, "camera_id": "cam01", "fps_hint": args.fps, "person_class_id": 0}
    osd_sink_pad.add_probe(Gst.PadProbeType.BUFFER, osd_sink_pad_buffer_probe, appctx)
    return pipeline

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--zones", required=True)
    parser.add_argument("--pgie-config", default="deepstream_configs/pgie_peoplenet_config.txt")
    parser.add_argument("--tracker-config", default="deepstream_configs/tracker_config.txt")
    parser.add_argument("--display", type=int, default=1)
    parser.add_argument("--fps", type=float, default=30.0)
    args = parser.parse_args()

    zcfg = load_zone_cfg_simple(args.zones)
    zm = SimpleZoneMonitor(zcfg)
    pipeline = build_pipeline(args, zm)

    loop = GObject.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    def on_message(bus, msg):
        t = msg.type
        if t == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print("[ERROR]", err, dbg, file=sys.stderr); loop.quit()
        elif t == Gst.MessageType.EOS:
            loop.quit()
    bus.connect("message", on_message)

    print("[INFO] minimal alert app starting...")
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    except KeyboardInterrupt:
        pass
    finally:
        pipeline.set_state(Gst.State.NULL)
        print("[INFO] stopped.")

if __name__ == "__main__":
    main()

"""Demo car controller: rides the arbitrated epoch stream and drives the car.

    python -m firmware.controller --car-ip 172.20.10.4 --id 10
    python -m firmware.controller --no-car          # dry run, prints motion

Architecture (see tracks/TRACK_E.md for the car side):

  console.server (:8420)  --one SSE replay-->  this process (:8421)
                                                 |-- motion loop --UDP--> car
                                                 `-- fan-out ----SSE--> browsers

The console server spawns a fresh Arbitras per /events connection, so a browser
and a controller connecting separately would ride desynced replays. This
process therefore opens ONE upstream /events stream per run (keypress g) and
tees every payload to (a) the motion loop and (b) any browsers attached to
:8421. Point the projected console at http://localhost:8421 — all other paths
are proxied to :8420 verbatim.

The arbitras/console product never commands motion (CLAUDE.md: advisory only).
This file is the demo harness that CONSUMES the §5 contract, the same way a
real autonomy stack would; all command authority lives here and in the car.

Keys:  g = start run   o = layer-off pre-run   r = reset (stop, zero pose)
       q = quit (zeros the car first)

State -> motion policy (arbitras owns all hysteresis; none is added here):

  NOMINAL                     track believed position       speed cap 1.0
  DEGRADED + correction_ok    track corrected_position      cap speed_scale
  DEGRADED, no correction     dead-reckon along route       cap 0.3
  RESTRICTED                  finish current leg, hold      cap 0.2
  SURRENDERED / end / lost    zeros for 1 s, then SILENCE (ESP32 500 ms
                              failsafe is the independent second stop path)

Only DEGRADED consults the correction block (tracks/TRACK_D.md).
"""
import argparse
import http.client
import json
import math
import select
import socket
import sys
import termios
import threading
import time
import tty
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from console import mission

# ---- Demo constants ------------------------------------------------------
UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 8420
LISTEN_PORT = 8421
UDP_PORT = 4210               # frozen, tracks/TRACK_E.md
SEND_HZ = 20.0                # command rate to the car (TRACK_E jog rate)
DEMO_RATE_EPS = 4.25          # 510 epochs -> 120 s
PRERUN_RATE_EPS = 8.5         # layer-off pre-run, ~35 s to the kill point

FLOOR_SCALE = 1.0 / 250.0     # 820 m route -> 3.28 m; 260 m veer -> 1.04 m

# Floor-calibrated kinematics (see firmware/DEMO.md calibration procedure).
K_V = 0.55                    # m/s at full throttle       -- CALIBRATE
K_W = 3.5                     # rad/s at full steer        -- CALIBRATE
THR_BURST = 0.45              # burst-gait throttle, clears TT-motor stall zone
BURST_WINDOW_S = 0.5          # gait period
ARRIVE_M = 0.03               # close enough: stop pursuing
PIVOT_ERR_RAD = math.radians(60)
CRUISE_MPS = 0.12             # floor speed ceiling at cap 1.0
ZERO_TAIL_S = 1.0             # explicit zeros before going silent
# --------------------------------------------------------------------------

SPEED_CAPS = {"NOMINAL": 1.0, "DEGRADED_CORR": None,  # None -> use speed_scale
              "DEGRADED": 0.3, "RESTRICTED": 0.2, "SURRENDERED": 0.0}


def wrap(a: float) -> float:
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def clamp(v, lo=-1.0, hi=1.0):
    return max(lo, min(hi, v))


class CarLink:
    """20 Hz UDP command sender. Modes: active / zeros / silent."""

    def __init__(self, ip, car_id, enabled=True):
        self.addr = (ip, UDP_PORT)
        self.car_id = car_id
        self.enabled = enabled and ip is not None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.lock = threading.Lock()
        self.throttle = 0.0
        self.steer = 0.0
        self.mode = "zeros"            # active | zeros | silent
        self.last_sent = (0.0, 0.0)
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def command(self, throttle, steer):
        with self.lock:
            self.throttle, self.steer = throttle, steer
            self.mode = "active"

    def zeros_then_silence(self):
        with self.lock:
            self.throttle = self.steer = 0.0
            self.mode = "zeros"
        threading.Timer(ZERO_TAIL_S, self._go_silent).start()

    def _go_silent(self):
        with self.lock:
            if self.mode == "zeros":   # not restarted meanwhile
                self.mode = "silent"

    def _loop(self):
        period = 1.0 / SEND_HZ
        while self.running:
            with self.lock:
                mode, t, s = self.mode, self.throttle, self.steer
            if mode != "silent":
                self.last_sent = (t, s) if mode == "active" else (0.0, 0.0)
                if self.enabled:
                    pkt = f"{self.car_id},{self.last_sent[0]:.2f},{self.last_sent[1]:.2f}\n"
                    try:
                        self.sock.sendto(pkt.encode("ascii"), self.addr)
                    except OSError:
                        pass
            else:
                self.last_sent = (0.0, 0.0)
            time.sleep(period)


class DeadReckoner:
    """Unicycle pose from the last SENT command (no encoders on the car)."""

    def __init__(self):
        self.reset()

    def reset(self, heading=None):
        _, _, h0 = mission.route_point(0.0)
        self.x = 0.0
        self.y = 0.0
        self.theta = heading if heading is not None else h0
        self._t = time.monotonic()

    def update(self, throttle, steer):
        now = time.monotonic()
        dt = now - self._t
        self._t = now
        v = K_V * throttle
        w = -K_W * steer               # steer right-positive, theta CCW-positive
        self.theta = wrap(self.theta + w * dt)
        self.x += v * math.cos(self.theta) * dt
        self.y += v * math.sin(self.theta) * dt


class MotionPolicy:
    """Decision payload -> (floor target (x,y) or None, speed cap)."""

    def __init__(self):
        self.route_len = mission.route_length()
        self._restricted_hold_s = None    # arc length frozen on RESTRICTED entry

    @staticmethod
    def _enu_delta(nav_latlon, truth):
        if not nav_latlon or not truth:
            return (0.0, 0.0)
        ne = mission.latlon_to_enu(nav_latlon["lat"], nav_latlon["lon"])
        te = mission.latlon_to_enu(truth["lat"], truth["lon"])
        return (ne[0] - te[0], ne[1] - te[1])

    def _leg_end_s(self, s):
        acc = 0.0
        for (e0, n0), (e1, n1) in zip(mission.ROUTE_ENU, mission.ROUTE_ENU[1:]):
            acc += math.hypot(e1 - e0, n1 - n0)
            if s <= acc:
                return acc
        return self.route_len

    def target(self, payload):
        """Returns (target_xy | None, cap, label). None target = hold/stop."""
        state = payload.get("state")
        s = min((payload.get("epoch_index") or 0) * mission.ROUTE_SPEED_M_PER_EPOCH,
                self.route_len)
        geom = payload.get("geometry") or {}
        corr = geom.get("correction") or {}
        truth = payload.get("_truth")

        if state != "RESTRICTED":
            self._restricted_hold_s = None

        if state == "SURRENDERED":
            return None, 0.0, "surrendered"

        if state == "RESTRICTED":
            if self._restricted_hold_s is None:
                self._restricted_hold_s = self._leg_end_s(s)
            s_t = min(s, self._restricted_hold_s)
            e, n, _ = mission.route_point(s_t)
            return (e * FLOOR_SCALE, n * FLOOR_SCALE), SPEED_CAPS["RESTRICTED"], "restricted_leg"

        if state == "DEGRADED":
            if corr.get("correction_ok") and corr.get("corrected_position"):
                de, dn = self._enu_delta(corr["corrected_position"], truth)
                e, n, _ = mission.route_point(s)
                cap = corr.get("speed_scale")
                cap = float(cap) if cap is not None else SPEED_CAPS["DEGRADED"]
                return ((e - de) * FLOOR_SCALE, (n - dn) * FLOOR_SCALE), cap, "corrected"
            e, n, _ = mission.route_point(s)   # dead-reckon along route
            return (e * FLOOR_SCALE, n * FLOOR_SCALE), SPEED_CAPS["DEGRADED"], "coast"

        # NOMINAL (or layer off): track the believed position.
        de, dn = self._enu_delta(payload.get("position"), truth)
        e, n, _ = mission.route_point(s)
        return ((e - de) * FLOOR_SCALE, (n - dn) * FLOOR_SCALE), SPEED_CAPS["NOMINAL"], "believed"


class PurePursuit:
    """Pose + target -> (throttle, steer) with burst gait for slow cruise."""

    def __init__(self):
        self._window_t0 = time.monotonic()

    def command(self, pose: DeadReckoner, target, cap):
        if target is None or cap <= 0.0:
            return 0.0, 0.0
        dx, dy = target[0] - pose.x, target[1] - pose.y
        dist = math.hypot(dx, dy)
        if dist < ARRIVE_M:
            return 0.0, 0.0
        err = wrap(math.atan2(dy, dx) - pose.theta)
        steer = clamp(-2.0 * err)      # positive err (target left) -> steer left
        if abs(err) > PIVOT_ERR_RAD:
            return 0.0, clamp(steer, -0.6, 0.6)   # pivot in place

        v_target = min(CRUISE_MPS * cap, dist * 2.0)
        # Burst gait: TT motors stall below ~30% duty, and demo cruise speeds
        # are below the stall floor. Drive at THR_BURST for a fraction of each
        # window, zeros otherwise (zeros are valid packets: failsafe stays fed).
        duty_frac = clamp(v_target / (K_V * THR_BURST), 0.0, 1.0)
        phase = ((time.monotonic() - self._window_t0) % BURST_WINDOW_S) / BURST_WINDOW_S
        if phase > duty_frac:
            return 0.0, 0.0
        return THR_BURST, clamp(steer, -0.5, 0.5)


class Run:
    """One replay: single upstream SSE, teed to motion loop + browsers."""

    def __init__(self, rate, layer_on, car: CarLink, dry: bool, browsers):
        self.rate = rate
        self.layer_on = layer_on
        self.car = car
        self.dry = dry
        self.browsers = browsers       # shared list of client wfiles
        self.latest = None
        self.alive = True
        self._lock = threading.Lock()
        self.pose = DeadReckoner()
        self.policy = MotionPolicy()
        self.pursuit = PurePursuit()
        self._last_state = None
        threading.Thread(target=self._sse_loop, daemon=True).start()
        threading.Thread(target=self._motion_loop, daemon=True).start()

    def stop(self):
        self.alive = False
        self.car.zeros_then_silence()

    # -- upstream SSE ------------------------------------------------------
    def _sse_loop(self):
        qs = f"/events?rate={self.rate}&layer={'on' if self.layer_on else 'off'}"
        try:
            conn = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=30)
            conn.request("GET", qs)
            resp = conn.getresponse()
            event = "message"
            for raw in resp:
                if not self.alive:
                    break
                line = raw.decode("utf-8", "replace").rstrip("\n").rstrip("\r")
                self._fan_out(raw)
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    if event == "end":
                        break
                    try:
                        payload = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    with self._lock:
                        self.latest = payload
                elif line == "":
                    event = "message"
            conn.close()
        except OSError as e:
            print(f"\n[run] upstream lost: {e}")
        finally:
            if self.alive:
                self.alive = False
                self.car.zeros_then_silence()
                print("\n[run] stream ended — car zeroed, then silent (failsafe)")

    def _fan_out(self, raw: bytes):
        dead = []
        for wf in list(self.browsers):
            try:
                wf.write(raw)
                wf.flush()
            except (OSError, ValueError):
                # ValueError: write/flush on a client wfile already closed by
                # the http.server machinery after the browser disconnected.
                dead.append(wf)
        for wf in dead:
            try:
                self.browsers.remove(wf)
            except ValueError:
                pass

    # -- motion ------------------------------------------------------------
    def _motion_loop(self):
        period = 1.0 / SEND_HZ
        while self.alive:
            with self._lock:
                payload = self.latest
            if payload is not None:
                state = payload.get("state")
                target, cap, label = self.policy.target(payload)
                if state == "SURRENDERED":
                    if self._last_state != "SURRENDERED":
                        self.car.zeros_then_silence()
                        print(f"\n[motion] e{payload.get('epoch_index')} SURRENDERED "
                              f"({payload.get('reason')}) — zeros then silence")
                else:
                    t, s = self.pursuit.command(self.pose, target, cap)
                    self.car.command(t, s)
                    if self.dry and state != self._last_state:
                        print(f"\n[motion] e{payload.get('epoch_index')} {state} "
                              f"[{label}] cap={cap:.2f} target={target}")
                self._last_state = state
            lt, ls = self.car.last_sent
            self.pose.update(lt, ls)
            time.sleep(period)


# ---- Tee HTTP server (console proxy on :8421) ----------------------------

class TeeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    ctl = None                        # set in main()

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/events"):
            return self._attach()
        # Everything else proxied verbatim to the console server.
        try:
            conn = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=10)
            conn.request("GET", self.path)
            resp = conn.getresponse()
            body = resp.read()
            conn.close()
        except OSError:
            return self.send_error(502, "console server not running on :8420")
        self.send_response(resp.status)
        for k, v in resp.getheaders():
            if k.lower() in ("content-type", "cache-control"):
                self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _attach(self):
        """Browsers attach here and receive the shared run's SSE frames."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.close_connection = True
        self.end_headers()
        self.ctl.browsers.append(self.wfile)
        # Hold the connection; the run thread writes to it. Detect close.
        try:
            while True:
                r, _, _ = select.select([self.rfile], [], [], 1.0)
                if r and not self.rfile.read(1):
                    break
        except OSError:
            pass
        finally:
            try:
                self.ctl.browsers.remove(self.wfile)
            except ValueError:
                pass


class Controller:
    def __init__(self, args):
        self.args = args
        self.browsers = []
        self.run = None
        self.car = CarLink(args.car_ip, args.id, enabled=not args.no_car)

    def start_run(self, layer_on: bool):
        if self.run and self.run.alive:
            self.run.stop()
            time.sleep(0.1)
        rate = self.args.rate if layer_on else PRERUN_RATE_EPS
        mode = "layer ON" if layer_on else "layer OFF pre-run"
        print(f"\n[ctl] starting run: {mode}, rate {rate} eps "
              f"(place car on the start T, heading arrow)")
        self.run = Run(rate, layer_on, self.car, self.args.no_car, self.browsers)

    def reset(self):
        if self.run:
            self.run.stop()
            self.run = None
        print("\n[ctl] reset — car zeroed; reload browser, re-place car, press g")

    def quit(self):
        if self.run:
            self.run.stop()
        self.car.zeros_then_silence()
        time.sleep(ZERO_TAIL_S + 0.2)
        self.car.running = False


def main():
    ap = argparse.ArgumentParser(description="ARBITRAS demo car controller")
    ap.add_argument("--car-ip", help="car IP from the READY line")
    ap.add_argument("--id", type=int, default=10, help="CAR_ID (default 10)")
    ap.add_argument("--rate", type=float, default=DEMO_RATE_EPS)
    ap.add_argument("--no-car", action="store_true", help="dry run: no UDP, print motion")
    ap.add_argument("--port", type=int, default=LISTEN_PORT)
    args = ap.parse_args()
    if not args.no_car and not args.car_ip:
        ap.error("--car-ip is required unless --no-car")

    ctl = Controller(args)
    TeeHandler.ctl = ctl
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), TeeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print(f"controller  console tee at http://localhost:{args.port} "
          f"(upstream :{UPSTREAM_PORT} must be running)")
    print(f"car         {'DRY RUN (--no-car)' if args.no_car else f'{args.car_ip} id={args.id}'}")
    print("keys        g=start  o=layer-off pre-run  r=reset  q=quit")

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            r, _, _ = select.select([sys.stdin], [], [], 0.2)
            if not r:
                continue
            ch = sys.stdin.read(1)
            if ch == "g":
                ctl.start_run(layer_on=True)
            elif ch == "o":
                ctl.start_run(layer_on=False)
            elif ch == "r":
                ctl.reset()
            elif ch == "q":
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        ctl.quit()
        print("\nbye")


if __name__ == "__main__":
    main()

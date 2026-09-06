"""Keyboard jogger for the ARBITRAS demo car. Spec: tracks/TRACK_E.md.

    python -m firmware.tools.jog --ip 172.20.10.4 --id 10

Arrow keys set throttle/steer in 0.1 steps, spacebar zeroes both, q quits
and sends a final zero. Sends the frozen packet format at 20 Hz continuously.
Stdlib only.
"""

import argparse
import select
import socket
import sys
import termios
import threading
import time
import tty

UDP_PORT = 4210
RATE_HZ = 20.0
STEP = 0.1

ARROW = {"A": "up", "B": "down", "C": "right", "D": "left"}


def clamp(v: float) -> float:
    return max(-1.0, min(1.0, v))


class Jog:
    def __init__(self, ip: str, car_id: int):
        self.addr = (ip, UDP_PORT)
        self.car_id = car_id
        self.throttle = 0.0
        self.steer = 0.0
        self.running = True
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.lock = threading.Lock()

    def packet(self) -> bytes:
        with self.lock:
            t, s = self.throttle, self.steer
        return f"{self.car_id},{t:.2f},{s:.2f}\n".encode("ascii")

    def send_loop(self):
        period = 1.0 / RATE_HZ
        while self.running:
            self.sock.sendto(self.packet(), self.addr)
            time.sleep(period)

    def status(self):
        with self.lock:
            t, s = self.throttle, self.steer
        sys.stdout.write(
            f"\r-> {self.addr[0]}:{self.addr[1]} id={self.car_id} "
            f"throttle={t:+.2f} steer={s:+.2f}   (arrows, space=stop, q=quit) "
        )
        sys.stdout.flush()

    def adjust(self, key: str):
        with self.lock:
            if key == "up":
                self.throttle = clamp(self.throttle + STEP)
            elif key == "down":
                self.throttle = clamp(self.throttle - STEP)
            elif key == "right":
                self.steer = clamp(self.steer + STEP)
            elif key == "left":
                self.steer = clamp(self.steer - STEP)
            elif key == "zero":
                self.throttle = 0.0
                self.steer = 0.0


def read_key(timeout: float = 0.1) -> str | None:
    """Return 'up'/'down'/'left'/'right'/'zero'/'quit' or None."""
    r, _, _ = select.select([sys.stdin], [], [], timeout)
    if not r:
        return None
    ch = sys.stdin.read(1)
    if ch == "q":
        return "quit"
    if ch == " ":
        return "zero"
    if ch == "\x1b":  # escape sequence
        r, _, _ = select.select([sys.stdin], [], [], 0.05)
        if not r:
            return None
        if sys.stdin.read(1) != "[":
            return None
        r, _, _ = select.select([sys.stdin], [], [], 0.05)
        if not r:
            return None
        return ARROW.get(sys.stdin.read(1))
    return None


def main():
    ap = argparse.ArgumentParser(description="ARBITRAS demo car jogger")
    ap.add_argument("--ip", required=True, help="car IP from the READY line")
    ap.add_argument("--id", type=int, required=True, help="CAR_ID (10 or 11)")
    args = ap.parse_args()

    jog = Jog(args.ip, args.id)
    sender = threading.Thread(target=jog.send_loop, daemon=True)

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        sender.start()
        jog.status()
        while True:
            key = read_key()
            if key == "quit":
                break
            if key:
                jog.adjust(key)
                jog.status()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        jog.running = False
        sender.join(timeout=0.5)
        # Final zero: releasing keys + failsafe both stop the car.
        with jog.lock:
            jog.throttle = 0.0
            jog.steer = 0.0
        jog.sock.sendto(jog.packet(), jog.addr)
        print("\nsent final zero, bye")


if __name__ == "__main__":
    main()
